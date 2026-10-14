"""Message projections must not repeat execution observations or hook effects."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
from agent_service import AgentService
from nailong.core.permissions import PermissionEngine


def result(identity, *, message_id, ok=True, result_id=None):
    return ToolMessage(id=message_id, name='read_file', tool_call_id=identity,
        content=json.dumps({'ok':ok,'content':'actual result','started':True, **({'tool_result_id':result_id} if result_id else {})}))


class BoundaryAgent:
    def __init__(self, boundary, updates):
        self.boundary = boundary
        self.updates = updates
        self.step = 0

    def get_state(self, config):
        return SimpleNamespace(values={'messages':self.boundary}, metadata={'step':self.step})

    async def astream(self, value, config, *, stream_mode):
        self.step += 1
        for messages in self.updates:
            yield ('updates',{'model':{'messages':messages}})


class EffectService(AgentService):
    """Test-only concrete side effects expose misplaced observer/hook calls."""
    def __init__(self, agent, root):
        super().__init__(lambda **kwargs:agent, permission_engine=PermissionEngine(root))
        self.root = root
        self.observed = []

    async def _observe_task_tool(self, thread_id, message, args):
        self.observed.append(message.tool_call_id)
        return await super()._observe_task_tool(thread_id,message,args)

    async def _run_hook(self, event, **kwargs):
        answer = await super()._run_hook(event,**kwargs)
        if event == 'PostToolUse':
            with (self.root/'hook-effects.txt').open('a') as f:
                f.write(kwargs.get('name','')+'\n')
        return answer


class ToolReplayTests(unittest.IsolatedAsyncioTestCase):
    def ai(self, identity, message_id):
        return AIMessage(id=message_id,content='',tool_calls=[
            {'id':identity,'name':'read_file','args':{'path':'a.txt'}}])

    async def drive(self, boundary, updates):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        service = EffectService(BoundaryAgent(boundary,updates),root)
        events = [e async for e in service.stream_turn('继续检查文件',
            {'configurable':{'thread_id':'replay'}})]
        hooks = (root/'hook-effects.txt').read_text().splitlines() if (root/'hook-effects.txt').exists() else []
        return service, events, hooks

    async def test_old_results_with_new_projection_ids_do_not_repeat_hooks_or_counts(self):
        old_ai = self.ai('old','old-ai')
        old_result = result('old',message_id='old-result',ok=False)
        # Real compaction may replace the message ID while retaining the call ID.
        projected = result('old',message_id='projection-result',ok=False)
        fresh = self.ai('new','new-ai')
        service, events, hooks = await self.drive([old_ai,old_result],[
            [old_ai,projected,fresh,result('new',message_id='new-result')],
            [AIMessage(id='answer',content='Current result.')]])
        self.assertEqual([e.data['call_id'] for e in events if e.kind=='tool_start'],['new'])
        self.assertEqual([e.data['call_id'] for e in events if e.kind=='tool_end'],['new'])
        self.assertEqual(service.observed,['new'])
        self.assertEqual(hooks,['read_file'])
        self.assertEqual(events[-1].data['stats']['tool_calls'],1)

    async def test_duplicate_current_result_runs_observer_and_hook_once(self):
        ai=self.ai('new','new-ai');done=result('new',message_id='done')
        service,events,hooks=await self.drive([],[[ai,done],[done],
            [AIMessage(content='Done.')]])
        self.assertEqual(len([e for e in events if e.kind=='tool_end']),1)
        self.assertEqual(service.observed,['new'])
        self.assertEqual(hooks,['read_file'])

    async def test_new_ai_can_legitimately_reuse_an_old_call_id(self):
        old_ai=self.ai('same','old-ai');old_result=result('same',message_id='old-result')
        new_ai=self.ai('same','new-ai');new_result=result('same',message_id='new-result')
        service,events,hooks=await self.drive([old_ai,old_result],[[old_ai,old_result],
            [new_ai,new_result],[AIMessage(content='New execution.')]])
        self.assertEqual([e.data['call_id'] for e in events if e.kind=='tool_end'],['same'])
        self.assertEqual(service.observed,['same'])
        self.assertEqual(hooks,['read_file'])

    async def test_current_unfinished_call_result_is_preserved_after_repeated_ai_update(self):
        ai=self.ai('pending','new-ai')
        service,events,hooks=await self.drive([],[[ai],[ai],
            [result('pending',message_id='done')],[AIMessage(content='Resumed.')]])
        self.assertEqual([e.data['call_id'] for e in events if e.kind=='tool_start'],['pending'])
        self.assertEqual([e.data['call_id'] for e in events if e.kind=='tool_end'],['pending'])
        self.assertEqual(service.observed,['pending'])
        self.assertEqual(hooks,['read_file'])

    async def test_reprojected_native_result_cannot_steal_reused_call_completion(self):
        old_ai=self.ai('same','old-ai');old=result('same',message_id='old-result',ok=False,result_id='old-native-result')
        new=self.ai('same','new-ai')
        service,events,hooks=await self.drive([old_ai,old],[[new],
            [result('same',message_id='old-projection',ok=False,result_id='old-native-result')],
            [result('same',message_id='fresh-result',result_id='fresh-native-result')],
            [AIMessage(content='New success.')]])
        ends=[e for e in events if e.kind=='tool_end']
        self.assertEqual(len(ends),1);self.assertTrue(ends[0].data['ok'])
        self.assertEqual(service.observed,['same']);self.assertEqual(hooks,['read_file'])

    def test_boundary_pending_reuse_is_paired_chronologically(self):
        from nailong.core.tool_events import TurnToolLedger
        ledger=TurnToolLedger([self.ai('same','old-ai'),result('same',message_id='old-result'),
            self.ai('same','pending-ai')],'turn')
        self.assertEqual(ledger.consume(result('same',message_id='resumed-result')),'current')
        self.assertEqual(ledger.request_count,1)
        self.assertEqual(ledger.metadata(result('same',message_id='meta'),'same','started')['resumed'],True)
        self.assertEqual(ledger.consume(result('same',message_id='duplicate-resume')),'duplicate')
        self.assertEqual(ledger.request_count,1)


if __name__=='__main__':unittest.main()
