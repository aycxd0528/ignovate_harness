import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from nailong.core.sessions import ProjectSessionStore
from nailong.gui.controller import GUIController
from test_gui_controller import ControlledService


class GUITraceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.sessions = ProjectSessionStore(self.root, base_dir=Path(self.temp.name) / 'data', api_key='sentinel-secret')
        self.controller = GUIController(self.root, sessions=self.sessions, service=ControlledService(self.sessions))
        self.branch = await self.controller.command('create', {'name': '轨迹测试'})

    async def asyncTearDown(self):
        await self.controller.close()
        self.temp.cleanup()

    async def until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), 3)

    async def trace(self, **payload):
        return await self.controller.command('trace', {'branch_id': self.branch['id'], **payload})

    async def test_saved_trace_preserves_event_order_and_safe_visible_bodies(self):
        for kind, data in [('user', {'text': '介绍项目'}), ('status', {'status': 'thinking'}),
                           ('token', {'text': '先检查', 'reasoning': 'private-thought'}),
                           ('tool_start', {'call_id': 'read', 'name': 'read_file', 'preview': {'path': 'README.md'}}),
                           ('tool_end', {'call_id': 'read', 'name': 'read_file', 'ok': False, 'elapsed_ms': 20, 'summary': 'sentinel-secret 文件不存在'}),
                           ('final', {'text': '请提供 README.md', 'thinking': 'private-thought'}),
                           ('gui_run_timing', {'elapsed_ms': 2000})]:
            self.sessions.append_event(self.branch['thread_id'], kind, data)
        node = self.controller.branches.commit_turn(self.branch['id'], 0, 'checkpoint')
        result = await self.trace()
        self.assertEqual(result['branch_id'], self.branch['id'])
        self.assertEqual(result['total_turns'], 1)
        turn = result['turns'][0]
        self.assertEqual(turn['id'], node['id'])
        self.assertEqual([row['role'] for row in turn['rows']], ['user', 'model', 'assistant', 'tool', 'assistant'])
        tool = next(row for row in turn['rows'] if row['role'] == 'tool')
        self.assertEqual(tool['status'], 'failed')
        self.assertIn('[密钥已隐藏]', json.dumps(tool, ensure_ascii=False))
        self.assertEqual(turn['elapsed_ms'], 2000)
        self.assertNotIn('private-thought', json.dumps(result))

    async def test_live_tools_become_the_same_saved_turn_without_duplication(self):
        run = await self.controller.command('send', {'branch_id': self.branch['id'], 'message': 'tools', 'request_id': 'trace-live'})
        await self.until(lambda: bool(self.controller.runs[run['run_id']]['tools']))
        live = (await self.trace())['turns'][0]
        self.assertEqual(live['status'], 'running')
        self.assertEqual(next(row for row in live['rows'] if row['role'] == 'tool')['status'], 'running')
        await self.until(lambda: self.controller.runs[run['run_id']]['status'] == 'completed')
        saved = await self.trace()
        self.assertEqual(len(saved['turns']), 1)
        self.assertEqual(live['id'], saved['turns'][0]['id'])
        self.assertEqual(next(row for row in saved['turns'][0]['rows'] if row['role'] == 'tool')['status'], 'completed')
        self.assertEqual(saved['tool_calls'], 1)

    async def test_trace_scope_pagination_and_empty_are_read_only(self):
        before = self.sessions.read_events(self.branch['thread_id'])
        self.assertEqual((await self.trace())['turns'], [])
        self.assertEqual(self.sessions.read_events(self.branch['thread_id']), before)
        for prompt in ['第一轮', '第二轮']:
            start = len(self.sessions.read_events(self.branch['thread_id']))
            self.sessions.append_event(self.branch['thread_id'], 'user', {'text': prompt})
            self.sessions.append_event(self.branch['thread_id'], 'final', {'text': '回答'})
            self.controller.branches.commit_turn(self.branch['id'], start, 'checkpoint')
        other = self.controller.branches.create('其他对话')
        self.sessions.append_event(other['thread_id'], 'user', {'text': '不应显示的对话'})
        self.controller.branches.commit_turn(other['id'], 0, None)
        result = await self.trace(limit=1)
        self.assertEqual(result['total_turns'], 2)
        self.assertEqual(result['turns'][0]['round'], 2)
        self.assertEqual(result['has_more'], True)
        self.assertNotIn('不应显示', json.dumps(result, ensure_ascii=False))
        with self.assertRaises((ValueError, KeyError)):
            await self.trace(branch_id='not-a-branch')

    async def test_failure_before_logging_cannot_claim_the_next_request_events(self):
        class FailingService(ControlledService):
            async def stream_turn(self, message, *args, **kwargs):
                if message == 'fail-before-log':
                    raise RuntimeError('离线请求失败')
                async for event in super().stream_turn(message, *args, **kwargs):
                    yield event
        self.controller.service = FailingService(self.sessions)
        failed = await self.controller.command('send', {'branch_id': self.branch['id'], 'message': 'fail-before-log', 'request_id': 'trace-fail'})
        await self.until(lambda: self.controller.runs[failed['run_id']]['status'] == 'failed')
        self.assertIsNone(self.controller.runs[failed['run_id']]['node_id'])
        await self.controller.command('resume_queue', {})
        good = await self.controller.command('send', {'branch_id': self.branch['id'], 'message': '下一次请求', 'request_id': 'trace-next'})
        await self.until(lambda: self.controller.runs[good['run_id']]['status'] == 'completed')
        trace = await self.trace()
        prompts = [next(row['text'] for row in turn['rows'] if row['role'] == 'user') for turn in trace['turns']]
        self.assertEqual(prompts, ['fail-before-log', '下一次请求'])
        self.assertFalse(any(row['role'] == 'assistant' for row in trace['turns'][0]['rows']))
        self.assertEqual(trace['turns'][0]['status'], 'failed')

    async def test_tool_results_require_matching_name_scope_and_pending_call(self):
        from nailong.gui.trace import timeline_rows
        events = [{'kind': kind, 'data': data} for kind, data in [
            ('tool_start', {'call_id': 'shared', 'name': 'read_file', 'scope': 'main'}),
            ('tool_start', {'call_id': 'shared', 'name': 'run_command', 'scope': 'subagent'}),
            ('tool_end', {'call_id': 'shared', 'name': 'read_file', 'scope': 'main', 'ok': True, 'output_preview': 'README 内容'}),
            ('tool_end', {'call_id': 'shared', 'name': 'read_file', 'scope': 'main', 'ok': False, 'summary': '缺少开始事件'})]]
        rows = timeline_rows(events, 'turn')
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['status'], 'completed')
        self.assertEqual(rows[1]['status'], 'running')
        self.assertNotIn('README 内容', json.dumps(rows[1], ensure_ascii=False))
        self.assertEqual(rows[2]['status'], 'failed')

    async def test_queued_input_retains_identity_and_usage_does_not_duplicate_answer(self):
        self.controller.paused = True
        queued = await self.controller.command('send', {'branch_id': self.branch['id'], 'message': 'queued-input', 'request_id': 'trace-queued'})
        first = (await self.trace())['turns'][0]['rows'][0]
        await self.controller.command('resume_queue', {})
        await self.until(lambda: self.controller.runs[queued['run_id']]['status'] == 'completed')
        saved = next(row for row in (await self.trace())['turns'][0]['rows'] if row['role'] == 'user')
        self.assertEqual(first['id'], saved['id'])
        from nailong.gui.trace import timeline_rows
        rows = timeline_rows([{'kind': kind, 'data': data} for kind, data in [
            ('token', {'text': '完整回答'}), ('usage', {'input_tokens': 2}), ('final', {'text': '完整回答'})]], 'turn')
        self.assertEqual([row['text'] for row in rows], ['完整回答'])
