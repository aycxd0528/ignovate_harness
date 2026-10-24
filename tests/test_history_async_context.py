"""Complete archive work has an async owner; cancellation never reaches provider."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import Tool
from config import Settings
from nailong.core.context import UsageCalibration
from nailong.core.context_runtime import ContextManagerMiddleware
from nailong.core.history_archive import HistoryArchive, MAX_ARCHIVE_BYTES
from nailong.core.input_snapshot import active_snapshot_cancellation
from nailong.core.sessions import ProjectSessionStore


class PausedArchivePublish:
    def __init__(self, original):
        self.original = original
        self.entered = threading.Event(); self.release = threading.Event()
        self.closed = threading.Event(); self.expired = False; self.cancellation = None
        self.thread = None

    def __call__(self, directory, name, payload):
        self.original(directory, name, payload)
        if name.endswith('.bin') and not self.entered.is_set():
            self.thread = threading.get_ident()
            self.cancellation = active_snapshot_cancellation.get()
            self.entered.set()
            try:
                self.expired = not self.release.wait(2)
            finally:
                self.closed.set()


class HistoryAsyncContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'project'; self.root.mkdir()
        self.store = ProjectSessionStore(self.root, base_dir=self.root.parent / 'private')
        self.archive = HistoryArchive(self.store.root / 'history')
        self.settings = Settings('key', 'https://example.invalid', 'private', self.root)
        self.model = FakeMessagesListChatModel(responses=[AIMessage(content='unused')])
        self.manager = ContextManagerMiddleware(self.settings, [], 'constraints', 'chat', 'one',
            self.store, self.archive, {}, UsageCalibration())
        self.manager._policy = lambda requested_output: {'input_limit':None,'trigger_tokens':10**9,
            'output_reserve':requested_output,'context_window':None,'safety_margin':0}
        self.source = ToolMessage(id='source', name='read_file', tool_call_id='read',
            content='x' * (MAX_ARCHIVE_BYTES + 4000) + 'TOOL_TAIL')
        self.messages = [HumanMessage(content='old request'),
            AIMessage(content='', tool_calls=[{'name':'read_file','id':'read','args':{'path':'a.txt'}}]),
            self.source, AIMessage(content='old conclusion'), HumanMessage(content='current')]

    async def start_paused(self, coroutine, gate):
        pending = asyncio.create_task(coroutine)
        async def cleanup():
            gate.release.set()
            await asyncio.gather(pending, return_exceptions=True)
        self.addAsyncCleanup(cleanup)
        self.assertTrue(await asyncio.to_thread(gate.entered.wait, 3))
        self.assertNotEqual(gate.thread, threading.get_ident(), 'archive blocked the event loop')
        self.assertFalse(gate.expired, 'event loop could not release the archive worker')
        return pending

    async def test_async_preflight_keeps_loop_live_and_retains_original_messages(self):
        before = copy.deepcopy(self.messages); gate = PausedArchivePublish(self.archive._publish)
        with patch.object(self.archive, '_publish', side_effect=gate):
            pending = await self.start_paused(self.manager.abefore_model({'messages':self.messages}, None), gate)
            self.assertFalse(pending.done())
            self.assertIsNotNone(gate.cancellation)
            gate.release.set(); result = await pending
        self.assertIsNone(result); self.assertEqual(self.messages, before)
        reference = next(path.stem for path in self.archive.root.rglob('hist_*.json'))
        self.assertEqual(self.archive.read('one', reference, offset=len(self.source.content)-9)['content'], 'TOOL_TAIL')

    async def test_native_preflight_demotes_old_pin_without_deleting_original(self):
        self.manager.goal_store=SimpleNamespace(active=lambda:SimpleNamespace(thread_id='one'))
        for pin in ('goal','plan'):
            with self.subTest(pin=pin):
                old=HumanMessage(id='old-'+pin,content=self.source.content,
                    additional_kwargs={'nailong_pin':pin})
                messages=[old,AIMessage(content='previous progress'),HumanMessage(content='latest',
                    additional_kwargs={'nailong_pin':pin})]
                update=await self.manager.abefore_model({'messages':messages},None)
                retained=next(m for m in update['messages'] if m.id==old.id)
                self.assertEqual(retained.content,old.content)
                self.assertNotIn('nailong_pin',retained.additional_kwargs)
                self.assertEqual(old.additional_kwargs['nailong_pin'],pin)

    async def test_async_request_preparation_archives_prior_turn_and_provider_stays_on_loop(self):
        (self.root / '.nailong').mkdir(exist_ok=True)
        (self.root / '.nailong/settings.json').write_text(json.dumps({'context':{'history_input_target_tokens':1}}))
        messages = [HumanMessage(content='r' * (MAX_ARCHIVE_BYTES + 4000) + 'REQUEST_TAIL'),
                    AIMessage(content='old conclusion'), HumanMessage(content='current')]
        request = ModelRequest(model=self.model, messages=messages, tools=[Tool(
            name='read_history_result', description='Recover complete history', func=str)],
            system_message=SystemMessage(content='constraints'), model_settings={'max_tokens':100})
        before = copy.deepcopy(messages); called = []
        async def handler(prepared):
            called.append((threading.get_ident(), prepared))
            return SimpleNamespace(result=[AIMessage(content='done')])
        gate = PausedArchivePublish(self.archive._publish)
        with patch.object(self.archive, '_publish', side_effect=gate):
            pending = await self.start_paused(self.manager.awrap_model_call(request, handler), gate)
            self.assertEqual(called, []); gate.release.set(); await pending
        self.assertEqual(called[0][0], threading.get_ident())
        self.assertEqual(request.messages, before)
        summary = next(m for m in called[0][1].messages if m.additional_kwargs.get('nailong_compact_summary'))
        refs = summary.additional_kwargs['nailong_compact_state']['evidence']
        self.assertTrue(any(self.archive.read('one', ref, offset=len(messages[0].content)-12)['content']=='REQUEST_TAIL'
                            for ref in refs))

    async def test_repeated_cancel_joins_preflight_and_never_publishes_a_manifest(self):
        before = copy.deepcopy(self.messages); gate = PausedArchivePublish(self.archive._publish)
        with patch.object(self.archive, '_publish', side_effect=gate):
            pending = await self.start_paused(self.manager.abefore_model({'messages':self.messages}, None), gate)
            pending.cancel()
            self.assertTrue(await asyncio.to_thread(gate.cancellation.wait, 1))
            pending.cancel(); await asyncio.sleep(0)
            self.assertFalse(pending.done()); self.assertFalse(gate.closed.is_set())
            gate.release.set()
            with self.assertRaises(asyncio.CancelledError): await pending
        self.assertTrue(gate.closed.is_set()); self.assertEqual(self.messages, before)
        self.assertEqual(list(self.archive.root.rglob('hist_*.json')), [])
        reference = self.archive.save('one', self.source, {'path':'a.txt'})
        self.assertFalse(self.archive.read('one', reference)['archive_truncated'])

    async def test_cancel_during_request_preparation_never_calls_provider(self):
        (self.root / '.nailong').mkdir(exist_ok=True)
        (self.root / '.nailong/settings.json').write_text(json.dumps({'context':{'history_input_target_tokens':1}}))
        request = ModelRequest(model=self.model, messages=[HumanMessage(content=self.source.content),
            AIMessage(content='old conclusion'), HumanMessage(content='current')], tools=[Tool(
                name='read_history_result', description='Recover complete history', func=str)],
            system_message=SystemMessage(content='constraints'), model_settings={'max_tokens':100})
        before = copy.deepcopy(request.messages); called = []
        async def handler(prepared):
            called.append(prepared)
            return SimpleNamespace(result=[AIMessage(content='must not run')])
        gate = PausedArchivePublish(self.archive._publish)
        with patch.object(self.archive, '_publish', side_effect=gate):
            pending = await self.start_paused(self.manager.awrap_model_call(request, handler), gate)
            pending.cancel()
            self.assertTrue(await asyncio.to_thread(gate.cancellation.wait, 1))
            gate.release.set()
            with self.assertRaises(asyncio.CancelledError): await pending
        self.assertEqual(called, []); self.assertEqual(request.messages, before)
        self.assertEqual(list(self.archive.root.rglob('hist_*.json')), [])

    async def test_native_graph_cancel_keeps_checkpoint_original_and_skips_provider(self):
        from agent import AgentRuntimeFactory
        from test_context_runtime import RecordingModel
        model = RecordingModel(responses=[AIMessage(content='must not run')])
        with patch('agent.ChatDeepSeek', return_value=model):
            factory = AgentRuntimeFactory(self.settings, session_store=self.store)
        self.addCleanup(factory.close); self.addAsyncCleanup(factory.aclose)
        runtime = await factory.async_runtime(thread_id='one', allowed_tools={'read_file','read_history_result'})
        config = {'configurable':{'thread_id':'one'}}
        await runtime.aupdate_state(config, {'messages':self.messages})
        gate = PausedArchivePublish(factory.history_archive._publish)
        with patch.object(factory.history_archive, '_publish', side_effect=gate):
            pending = await self.start_paused(runtime.ainvoke(None, config, version='v2'), gate)
            pending.cancel()
            self.assertTrue(await asyncio.to_thread(gate.cancellation.wait, 1))
            gate.release.set()
            with self.assertRaises(asyncio.CancelledError): await pending
        state = await runtime.aget_state(config)
        original = next(m for m in state.values['messages'] if m.id=='source')
        self.assertEqual(original.content, self.source.content)
        self.assertFalse(original.additional_kwargs.get('nailong_tool_cleared'))
        self.assertEqual(model.requests, [])
        self.assertEqual(list(factory.history_archive.root.rglob('hist_*.json')), [])

    async def test_native_graph_rechecks_task_after_both_context_waits(self):
        from agent import AgentRuntimeFactory, TaskExecutionStopped
        from test_context_runtime import RecordingModel
        (self.root / '.nailong').mkdir(exist_ok=True)
        (self.root / '.nailong/settings.json').write_text(json.dumps({'context':{
            'soft_threshold_tokens':2_000_000,'history_input_target_tokens':1}}))
        model=RecordingModel(responses=[AIMessage(content='progress response')])
        with patch('agent.ChatDeepSeek',return_value=model):
            factory=AgentRuntimeFactory(self.settings,session_store=self.store)
        self.addCleanup(factory.close);self.addAsyncCleanup(factory.aclose)
        for stage in ('preflight','request'):
            for mutation in ('paused','blocked','revision','replacement','progress'):
                with self.subTest(stage=stage,mutation=mutation):
                    thread=stage+'-'+mutation
                    factory.task_store.begin(thread,'original task')
                    runtime=await factory.async_runtime(thread_id=thread,allowed_tools={'read_file','read_history_result'})
                    messages=(self.messages if stage=='preflight' else [HumanMessage(id='source',content=self.source.content),
                        AIMessage(content='old conclusion'),HumanMessage(content='current')])
                    before=copy.deepcopy(messages);calls=len(model.requests)
                    gate=PausedArchivePublish(factory.history_archive._publish)
                    with patch.object(factory.history_archive,'_publish',side_effect=gate):
                        pending=await self.start_paused(runtime.ainvoke({'messages':messages},
                            {'configurable':{'thread_id':thread}},version='v2'),gate)
                        if mutation in {'paused','blocked'}:
                            factory.task_store.set_state(thread,lifecycle=mutation,blockers=['user stopped'])
                        elif mutation in {'revision','replacement'}:
                            factory.task_store.begin(thread,'changed requirements',new=mutation=='replacement')
                        else:
                            factory.task_store.set_state(thread,progress='normal progress')
                        gate.release.set()
                        if mutation=='progress':
                            await pending
                        else:
                            with self.assertRaises(TaskExecutionStopped) as caught:await pending
                            self.assertEqual(caught.exception.task_replaced,mutation in {'revision','replacement'})
                    self.assertEqual(len(model.requests)-calls,int(mutation=='progress'))
                    # Graph reducers may assign missing message IDs; original
                    # historical bodies and metadata must remain unchanged.
                    self.assertEqual([m.content for m in messages],[m.content for m in before])
                    self.assertEqual([m.additional_kwargs for m in messages],[m.additional_kwargs for m in before])

    async def test_task_stop_after_budget_reservation_releases_hold_without_attempt(self):
        from agent import AgentRuntimeFactory, TaskExecutionStopped
        from nailong.core.budgets import TurnModelBudget,active_turn_budget
        from nailong.core.cumulative_budget import SessionTokenBudget,active_session_token_budget
        from nailong.core.usage import collect_model_usage
        from test_context_runtime import RecordingModel
        model=RecordingModel(responses=[AIMessage(content='must not run')])
        with patch('agent.ChatDeepSeek',return_value=model):
            factory=AgentRuntimeFactory(self.settings,session_store=self.store)
        self.addCleanup(factory.close);self.addAsyncCleanup(factory.aclose)
        factory.task_store.begin('one','original task')
        runtime=await factory.async_runtime(thread_id='one',allowed_tools=set())
        budget=SessionTokenBudget(100000);self.addCleanup(budget.close)
        turn=TurnModelBudget(3)
        entered=asyncio.Event();release=asyncio.Event();original_reserve=budget.reserve
        async def reserve(request):
            hold,cap=await original_reserve(request)
            entered.set();await release.wait()
            return hold,cap
        session_token=active_session_token_budget.set(budget);turn_token=active_turn_budget.set(turn)
        try:
            with patch.object(budget,'reserve',side_effect=reserve),collect_model_usage() as usage:
                pending=asyncio.create_task(runtime.ainvoke({'messages':[HumanMessage(content='continue')]},
                    {'configurable':{'thread_id':'one'}},version='v2'))
                async def cleanup():
                    release.set();await asyncio.gather(pending,return_exceptions=True)
                self.addAsyncCleanup(cleanup)
                await asyncio.wait_for(entered.wait(),3)
                self.assertGreater(budget.reserved,0)
                factory.task_store.set_state('one',lifecycle='paused',blockers=['user stopped'])
                release.set()
                with self.assertRaises(TaskExecutionStopped):await pending
                self.assertEqual(usage.attempts,0);self.assertTrue(usage.complete)
        finally:
            active_session_token_budget.reset(session_token);active_turn_budget.reset(turn_token)
        self.assertEqual(model.requests,[]);self.assertEqual(turn.calls,0)
        self.assertEqual(budget.reserved,0);self.assertEqual(budget.spent,0);self.assertFalse(budget.stopped)


class ManualHistoryCompactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from agent import AgentRuntimeFactory
        from agent_service import AgentService
        from test_context_runtime import RecordingModel
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()/'project';self.root.mkdir()
        self.store=ProjectSessionStore(self.root,base_dir=self.root.parent/'private')
        self.model=RecordingModel(responses=[AIMessage(content='must not run')])
        with patch('agent.ChatDeepSeek',return_value=self.model):
            self.factory=AgentRuntimeFactory(Settings('key','https://example.invalid','private',self.root),
                session_store=self.store)
        self.addCleanup(self.factory.close);self.addAsyncCleanup(self.factory.aclose)
        self.runtime=await self.factory.async_runtime(thread_id='one',allowed_tools={'read_file','read_history_result'})
        self.config={'configurable':{'thread_id':'one'}}
        self.source=ToolMessage(id='source',name='read_file',tool_call_id='old-read',
            content='x'*(MAX_ARCHIVE_BYTES+4000)+'TOOL_TAIL')
        self.messages=[HumanMessage(id='old-request',content='earlier task'),AIMessage(id='old-call',content='',
            tool_calls=[{'name':'read_file','id':'old-read','args':{'path':'old.txt'}}]),self.source,
            HumanMessage(id='current-request',content='current task'),AIMessage(id='current-call',content='',
            tool_calls=[{'name':'read_file','id':'current-read','args':{'path':'current.txt'}}]),
            ToolMessage(id='current-result',name='read_file',tool_call_id='current-read',content='current bytes')]
        await self.runtime.aupdate_state(self.config,{'messages':self.messages})
        self.service=AgentService(self.factory)

    start_paused=HistoryAsyncContextTests.start_paused

    async def start_manual(self,gate):
        return await self.start_paused(self.service.compact_context('one'),gate)

    async def test_manual_compaction_keeps_loop_live_and_archive_recoverable(self):
        gate=PausedArchivePublish(self.factory.history_archive._publish)
        with patch.object(self.factory.history_archive,'_publish',side_effect=gate):
            pending=await self.start_manual(gate)
            current=await self.runtime.aget_state(self.config)
            self.assertEqual(next(m for m in current.values['messages'] if m.id=='source').content,self.source.content)
            gate.release.set();report=await pending
        self.assertTrue(report['compacted'],report)
        current=await self.runtime.aget_state(self.config)
        cleared=next(m for m in current.values['messages'] if m.id=='source')
        ref=cleared.additional_kwargs['nailong_tool_reference']
        self.assertEqual(self.factory.history_archive.read('one',ref,offset=len(self.source.content)-9)['content'],'TOOL_TAIL')
        self.assertEqual(current.values['messages'][-1].content,'current bytes')
        self.assertEqual(self.model.requests,[])

    async def test_manual_repeated_cancel_preserves_original_checkpoint_and_releases_execution(self):
        from nailong.core.project_execution import project_execution
        gate=PausedArchivePublish(self.factory.history_archive._publish)
        with patch.object(self.factory.history_archive,'_publish',side_effect=gate):
            pending=await self.start_manual(gate)
            pending.cancel();self.assertTrue(await asyncio.to_thread(gate.cancellation.wait,1))
            pending.cancel();await asyncio.sleep(0)
            self.assertFalse(pending.done());gate.release.set()
            with self.assertRaises(asyncio.CancelledError):await pending
        current=await self.runtime.aget_state(self.config)
        self.assertEqual(next(m for m in current.values['messages'] if m.id=='source').content,self.source.content)
        self.assertEqual(list(self.factory.history_archive.root.rglob('hist_*.json')),[])
        with project_execution(self.store):pass
        self.assertEqual(self.model.requests,[])

    async def test_manual_wait_cannot_replace_a_newer_checkpoint(self):
        gate=PausedArchivePublish(self.factory.history_archive._publish)
        with patch.object(self.factory.history_archive,'_publish',side_effect=gate):
            pending=await self.start_manual(gate)
            await self.runtime.aupdate_state(self.config,{'messages':[HumanMessage(id='late-request',content='new user edit')]})
            gate.release.set();report=await pending
        self.assertFalse(report['compacted'],report);self.assertEqual(report['reason'],'checkpoint_changed')
        current=await self.runtime.aget_state(self.config)
        self.assertEqual(current.values['messages'][-1].content,'new user edit')
        self.assertEqual(next(m for m in current.values['messages'] if m.id=='source').content,self.source.content)

    async def test_manual_compaction_holds_project_execution_until_worker_cleanup(self):
        from nailong.core.project_execution import project_execution
        gate=PausedArchivePublish(self.factory.history_archive._publish)
        with patch.object(self.factory.history_archive,'_publish',side_effect=gate):
            pending=await self.start_manual(gate)
            with self.assertRaises(BlockingIOError):
                with project_execution(self.store):pass
            gate.release.set();await pending
        with project_execution(self.store):pass


if __name__ == '__main__': unittest.main()
