"""Proactive history saving must preserve current provider tool exchange."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import Tool
from config import Settings
from nailong.core.context import UsageCalibration
from nailong.core.context_runtime import ContextManagerMiddleware
from nailong.core.history_archive import HistoryArchive
from nailong.core.sessions import ProjectSessionStore


class HistoryRequestViewTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'project';self.root.mkdir()
        config=self.root/'.nailong';config.mkdir()
        (config/'settings.json').write_text(json.dumps({'context':{'history_input_target_tokens':5000}}))
        self.store=ProjectSessionStore(self.root,base_dir=Path(temp.name)/'private')
        self.archive=HistoryArchive(self.store.root/'history')
        self.manager=ContextManagerMiddleware(Settings('key','https://example.invalid','private',self.root),
            [],'BASE','chat','one',self.store,self.archive,{},UsageCalibration())
        self.model=FakeMessagesListChatModel(responses=[AIMessage(content='unused')])

    def request(self, recoverable=True):
        self.old_ai=AIMessage(id='old-ai',content='old analysis '*5000,
            additional_kwargs={'reasoning_content':'old reasoning '*5000},
            tool_calls=[{'id':'old-call','name':'run_command','args':{'command':'check'}}])
        self.current=[HumanMessage(id='current',content='Continue implementation.'),
            AIMessage(id='current-ai',content='current analysis',additional_kwargs={'reasoning_content':'current essential reasoning'},
                tool_calls=[{'id':'current-call','name':'read_file','args':{'path':'src.py'}}]),
            ToolMessage(id='current-result',tool_call_id='current-call',name='read_file',content='current bytes '*2000)]
        messages=[HumanMessage(id='old-user',content='Never alter fixture files.'),self.old_ai,
            ToolMessage(id='old-result',tool_call_id='old-call',name='run_command',content=json.dumps({'ok':False,'exit_code':1,'output':'FAILED (failures=1)'})),
            AIMessage(id='old-answer',content='Still failing.'),*self.current]
        tools=[Tool(name='read_history_result',description='Recover history',func=str)] if recoverable else []
        return ModelRequest(model=self.model,messages=messages,tools=tools,system_message=SystemMessage(content='BASE'))

    def test_old_reasoning_is_archived_and_current_exchange_is_byte_exact(self):
        request=self.request();before=copy.deepcopy(request.messages)
        prepared,report=self.manager._prepare_request(request)
        self.assertFalse(any(m.content == 'old analysis '*5000 for m in prepared.messages))
        self.assertEqual(prepared.messages[-3:],self.current)
        self.assertEqual(request.messages,before)
        summary=next(m for m in prepared.messages if m.additional_kwargs.get('nailong_compact_summary'))
        self.assertIn('Never alter fixture files.',summary.content)
        self.assertIn('"exit_code": 1',summary.content)
        self.assertIn('"ok": false',summary.content)
        ref=self.archive.save('one',self.old_ai)
        page=self.archive.read('one',ref,max_chars=1)
        self.assertFalse(page['archive_truncated'])
        self.assertTrue(report['history_compaction']['applied'])

    def cost_budget(self):
        from nailong.core.budgets import GoalCostBudget, active_cost_budget
        from nailong.core.costs import CostEstimator
        (self.root/'.nailong/settings.json').write_text(json.dumps({
            'context':{'history_input_target_tokens':1500000},
            'pricing':{'private':{'input_per_million':1,'cache_hit_per_million':0,'output_per_million':1}}}))
        budget=GoalCostBudget(CostEstimator('private',self.root),.08)
        token=active_cost_budget.set(budget)
        self.addCleanup(active_cost_budget.reset,token)
        return budget

    def test_cost_pressure_archives_closed_turns_below_soft_threshold(self):
        from nailong.core.budgets import _input_bound
        budget=self.cost_budget()
        request=self.request().override(model_settings={'max_tokens':1000})
        before=copy.deepcopy(request.messages)
        prepared,_=self.manager._prepare_request(request)
        self.assertLess(_input_bound(prepared)+1000,80000)
        self.assertEqual(prepared.messages[-3:],self.current)
        self.assertEqual(prepared.model_settings['max_tokens'],1000)
        self.assertEqual(request.messages,before)
        summary=next(m for m in prepared.messages if m.additional_kwargs.get('nailong_compact_summary'))
        records=[self.archive.load('one',ref) for ref in summary.additional_kwargs['nailong_compact_state']['evidence']]
        original=next(r['message']['data'] for r in records if r['message']['data'].get('id')=='old-ai')
        self.assertEqual(original['additional_kwargs']['reasoning_content'],'old reasoning '*5000)
        self.assertIn('Never alter fixture files.',summary.content)
        self.assertEqual(budget.spent,0)
        self.assertEqual(budget.reserved,0)
        self.assertFalse(budget.stopped)

    def test_cost_pressure_cannot_remove_history_without_recovery_tool(self):
        self.cost_budget()
        request=self.request(False).override(model_settings={'max_tokens':1000})
        prepared,_=self.manager._prepare_request(request)
        self.assertEqual(prepared.messages,request.messages)

    def test_cost_pressure_keeps_originals_if_archive_is_incomplete(self):
        self.cost_budget()
        request=self.request().override(model_settings={'max_tokens':1000})
        with patch('nailong.core.history_archive.MAX_ARCHIVE_BYTES',4096):
            prepared,_=self.manager._prepare_request(request)
        self.assertEqual(prepared.messages,request.messages)

    def test_cost_pressure_rejects_dangling_reused_tool_archive(self):
        self.cost_budget()
        request=self.request().override(model_settings={'max_tokens':1000})
        old=next(m for m in request.messages if m.id=='old-result')
        old.additional_kwargs.update(nailong_tool_cleared=True,nailong_tool_reference='hist_'+'0'*64)
        prepared,report=self.manager._prepare_request(request)
        self.assertEqual(prepared.messages,request.messages)
        self.assertFalse(report['cost_compaction']['applied'])

    def test_unfundable_current_reasoning_is_never_trimmed(self):
        self.cost_budget()
        request=self.request().override(model_settings={'max_tokens':1000})
        self.current[1].additional_kwargs['reasoning_content']='essential-current-reasoning '*10000
        before=copy.deepcopy(request.messages)
        prepared,_=self.manager._prepare_request(request)
        self.assertEqual(prepared.messages,before)

    def test_missing_recovery_tool_keeps_large_originals(self):
        request=self.request(False)
        prepared,_=self.manager._prepare_request(request)
        self.assertEqual(prepared.messages,request.messages)

    def test_truncated_reasoning_archive_cannot_replace_original(self):
        request=self.request()
        with patch('nailong.core.history_archive.MAX_ARCHIVE_BYTES',4096):
            prepared,_=self.manager._prepare_request(request)
        self.assertEqual(prepared.messages,request.messages)

    def test_unfinished_current_call_is_preserved(self):
        request=self.request();request=request.override(messages=request.messages[:-1])
        prepared,_=self.manager._prepare_request(request)
        self.assertEqual(prepared.messages[-2:],self.current[:2])
        self.assertFalse(any(m.content == 'old analysis '*5000 for m in prepared.messages))

    def test_unfinished_prior_exchange_prevents_proactive_replacement(self):
        request=self.request();request=request.override(messages=[m for m in request.messages if m.id!='old-result'])
        prepared,_=self.manager._prepare_request(request)
        self.assertEqual(prepared.messages,request.messages)

if __name__=='__main__':unittest.main()
