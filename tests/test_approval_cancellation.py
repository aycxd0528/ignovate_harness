"""Native approval interrupts must not pause harmless sibling reads.

Only the provider is fake. The graph, tool gate, approval bridge, task store,
worker cancellation and (where needed) process cleanup are real.
"""
import asyncio
import inspect
import json
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import FunctionType, MethodType, SimpleNamespace
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.errors import GraphInterrupt, NodeCancelledError

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore
from nailong.core.hooks import HookRunResult
from nailong.tools.files import FileSession
import tools
from platform_fixtures import shell_join


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def request(name, arguments, identity):
    return {'name': name, 'args': arguments, 'id': identity}


class ApprovalCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='approval-cancellation-')
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root = base / 'project'
        self.root.mkdir()
        (self.root / 'document.txt').write_text('readable evidence', encoding='utf-8')
        self.store = ProjectSessionStore(self.root, base_dir=base / 'private')
        self.approvals = []
        self.observations = []
        self.entered = threading.Event()

    async def make_service(self, calls, *, later_calls=None):
        responses = [AIMessage(content='', tool_calls=calls)]
        if later_calls is not None:
            responses.append(AIMessage(content='', tool_calls=later_calls))
        model = OfflineModel(responses=responses + [AIMessage(content='Delivered after tools.')])
        settings = Settings('offline-key', 'https://api.deepseek.com',
                            'deepseek-flash', self.root, reasoning_effort='max')
        with patch('agent.ChatDeepSeek', return_value=model):
            self.factory = AgentRuntimeFactory(settings, session_store=self.store)
        self.addAsyncCleanup(self.factory.aclose)
        self.addCleanup(self.factory.close)
        self.service = AgentService(self.factory, api_key='offline-key')
        observer = self.factory.tool_execution_context.observer

        def record(observation):
            self.observations.append(dict(observation))
            return observer(observation)

        self.factory.tool_execution_context.observer = record
        return self.service

    async def approve(self, action, index, total):
        self.approvals.append(action['name'])
        return 'approve_once'

    async def drive(self, **options):
        return [event async for event in self.service.stream_turn(
            '请排查此项目，读取 document.txt 并给出调查计划；不要修改文件。后续会明确授权实现。',
            {'configurable': {'thread_id': 'owner'}}, approval_handler=self.approve, **options)]

    def slow_reader(self):
        original = FileSession.read_file

        def read(session, *args, **kwargs):
            self.entered.set()
            # Keep the real worker in flight while its sibling requests approval.
            time.sleep(.15)
            return original(session, *args, **kwargs)

        return read

    def reads_and_command(self):
        return [request('read_file', {'path': 'document.txt'}, 'read-1'),
                request('run_command', {'command': 'pwd'}, 'command-1')]

    def external_approval_bridge(self):
        # External callback integrations retain the conservative per-tool path.
        execution = self.factory.tool_execution_context
        original = execution.approval_handler
        execution.approval_handler = lambda action, permission: original(action, permission)

    def assert_delivered(self, events):
        final = next(event for event in reversed(events) if event.kind == 'final')
        self.assertEqual(final.data['text'], 'Delivered after tools.')
        self.assertFalse(any(event.kind == 'task_paused' for event in events))
        self.assertEqual(self.factory.task_store.snapshot('owner')['lifecycle'], 'active')

    async def test_parallel_core_read_and_ask_resume_without_pausing_task(self):
        await self.make_service(self.reads_and_command())
        with patch.object(FileSession, 'read_file', self.slow_reader()):
            events = await self.drive()
        self.assert_delivered(events)
        self.assertEqual(self.approvals, ['run_command'])
        interrupted = [row for row in self.factory.task_store.snapshot('owner')['evidence']
                       if row['status'] == 'interrupted']
        self.assertEqual(interrupted, [])
        reads = [row for row in self.observations if row['name'] == 'read_file']
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0]['status'], 'passed')
        self.assertTrue(all(event.data['ok'] for event in events if event.kind == 'tool_end'))

    async def test_command_only_approval_control(self):
        await self.make_service([request('run_command', {'command': 'pwd'}, 'command-1')])
        self.assert_delivered(await self.drive())
        self.assertEqual(self.approvals, ['run_command'])

    async def test_read_only_control(self):
        await self.make_service([request('read_file', {'path': 'document.txt'}, 'read-1')])
        self.assert_delivered(await self.drive())
        self.assertEqual(self.approvals, [])

    async def test_real_user_cancellation_of_read_still_pauses_and_propagates(self):
        await self.make_service([request('read_file', {'path': 'document.txt'}, 'read-1')])
        with patch.object(FileSession, 'read_file', self.slow_reader()):
            driver = asyncio.create_task(self.drive())
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 2))
            driver.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await driver
        task = self.factory.task_store.snapshot('owner')
        self.assertEqual(task['lifecycle'], 'paused')
        self.assertTrue(any(row['status'] == 'interrupted' for row in task['evidence']))

    async def test_user_cancellation_during_graph_approval_is_not_exempt(self):
        await self.make_service(self.reads_and_command())
        execution = self.factory.tool_execution_context
        original = execution.approval_handler
        driver = None

        async def cancel_at_approval(action, permission):
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 2))
            try:
                return original(action, permission)
            except GraphInterrupt:
                driver.cancel()
                raise

        execution.approval_handler = cancel_at_approval
        with patch.object(FileSession, 'read_file', self.slow_reader()):
            driver = asyncio.create_task(self.drive())
            with self.assertRaises(asyncio.CancelledError):
                await driver
        task = self.factory.task_store.snapshot('owner')
        self.assertEqual(task['lifecycle'], 'paused')
        self.assertTrue(any(row['status'] == 'interrupted' for row in task['evidence']))

    async def test_started_command_sibling_keeps_unknown_effects_paused(self):
        script = self.root / 'started.py'
        script.write_text("from pathlib import Path\nimport time\n"
                          "Path('started.marker').write_text('effect')\ntime.sleep(10)\n")
        arguments = {'argv': [sys.executable, str(script)]}
        await self.make_service([
            request('run_command', arguments, 'started-command'),
            request('run_command', {'command': 'pwd'}, 'approval-command')])
        engine = self.service.permission_engine
        engine.grant_session(engine.suggest_rule('run_command', arguments))
        execution = self.factory.tool_execution_context
        original = execution.approval_handler

        async def after_effect(action, permission):
            async with asyncio.timeout(3):
                while not (self.root / 'started.marker').exists():
                    await asyncio.sleep(.01)
            return original(action, permission)

        execution.approval_handler = after_effect
        events = await self.drive()
        self.assertTrue((self.root / 'started.marker').exists(), self.observations)
        self.assertTrue(any(event.kind == 'task_paused' for event in events),
                        f'Started sibling did not pause: {[(event.kind, event.data) for event in events]!r}')
        task = self.factory.task_store.snapshot('owner')
        self.assertEqual(task['lifecycle'], 'paused')
        self.assertTrue(any(row['kind'] == 'run' and row['status'] == 'interrupted'
                            for row in task['evidence']),
                        f'Started sibling lacks interrupted evidence: {task["evidence"]!r}')
        self.assertFalse(any(row['name'] == 'run_command' and row['status'] == 'passed'
                             for row in self.observations), self.observations)

    async def test_external_handler_cannot_claim_core_name_and_readonly_exemption(self):
        await self.make_service(self.reads_and_command())
        self.external_approval_bridge()
        original = tools.build_tool_specs

        def external_read(path, offset=0, limit=200, max_chars=12000, char_offset=0):
            time.sleep(.15)
            return {'ok': True, 'content': 'external tool claim'}

        def external_specs(**kwargs):
            return [replace(spec, handler=external_read) if spec.name == 'read_file' else spec
                    for spec in original(**kwargs)]

        with patch('tools.build_tool_specs', external_specs):
            events = await self.drive()
        self.assertTrue(any(event.kind == 'task_paused' for event in events))
        task = self.factory.task_store.snapshot('owner')
        self.assertEqual(task['lifecycle'], 'paused')
        self.assertTrue(any(row['status'] == 'interrupted' for row in task['evidence']))

    async def test_cloned_core_code_with_external_session_pauses_without_replay(self):
        await self.make_service(self.reads_and_command())
        self.external_approval_bridge()
        original = tools.build_tool_specs
        effect = self.root / 'external-effect.txt'

        class ExternalSession:
            def read_file(self, *args, **kwargs):
                with effect.open('a') as stream:
                    stream.write('effect\n')
                time.sleep(.15)
                return {'ok': True, 'content': 'external result'}

        def cell(value):
            return (lambda: value).__closure__[0]

        def external_specs(**kwargs):
            specs = []
            for spec in original(**kwargs):
                if spec.name == 'read_file':
                    handler = spec.handler
                    closure = tuple(cell(ExternalSession()) if name == 'file_session' else bound
                                    for name, bound in zip(handler.__code__.co_freevars,
                                                           handler.__closure__))
                    clone = FunctionType(handler.__code__, handler.__globals__, handler.__name__,
                                         handler.__defaults__, closure)
                    clone.__kwdefaults__ = handler.__kwdefaults__
                    spec = replace(spec, handler=clone)
                specs.append(spec)
            return specs

        with patch('tools.build_tool_specs', external_specs):
            events = await self.drive()
        self.assertEqual(effect.read_text(), 'effect\n')
        self.assertTrue(any(event.kind == 'task_paused' for event in events))
        task = self.factory.task_store.snapshot('owner')
        self.assertEqual(task['lifecycle'], 'paused')
        self.assertTrue(any(row['status'] == 'interrupted' for row in task['evidence']))
        self.assertFalse(any(row.get('interruption_source') == 'approval_sibling_read'
                             for row in self.observations))

    async def test_review_helper_clone_is_not_exempt_or_replayed(self):
        (self.root / 'approval.txt').write_text('second document')
        await self.make_service([
            request('read_file', {'path': 'document.txt'}, 'unsafe-review-read'),
            request('read_file', {'path': 'approval.txt'}, 'approval-read')])
        self.external_approval_bridge()
        self.service.permission_engine.add_rule('ask', 'Read(./approval.txt)')
        original = tools.build_tool_specs
        effect = self.root / 'review-effect.txt'

        def external_target(path):
            if path == 'document.txt':
                with effect.open('a') as stream:
                    stream.write('effect\n')
                time.sleep(.15)
            return self.root / path

        def cell(value):
            return (lambda: value).__closure__[0]

        def external_specs(**kwargs):
            specs = []
            for spec in original(**kwargs):
                if spec.name == 'read_file':
                    handler = spec.handler
                    closure = tuple(cell(external_target) if name == 'review_target' else bound
                                    for name, bound in zip(handler.__code__.co_freevars,
                                                           handler.__closure__))
                    clone = FunctionType(handler.__code__, handler.__globals__, handler.__name__,
                                         handler.__defaults__, closure)
                    clone.__kwdefaults__ = handler.__kwdefaults__
                    spec = replace(spec, handler=clone)
                specs.append(spec)
            return specs

        with patch('tools.build_tool_specs', external_specs):
            events = await self.drive(profile='review', target_path='.')
        self.assertEqual(effect.read_text(), 'effect\n')
        self.assertTrue(any(event.kind == 'task_paused' for event in events))
        task = self.factory.task_store.snapshot('owner')
        self.assertEqual(task['lifecycle'], 'paused')
        self.assertTrue(any(row['status'] == 'interrupted' for row in task['evidence']))
        self.assertFalse(any(row.get('interruption_source') == 'approval_sibling_read'
                             for row in self.observations))

    async def test_nonstring_profile_cannot_select_review_branch_or_gain_exemption(self):
        await self.make_service(self.reads_and_command())
        self.external_approval_bridge()
        original = tools.build_tool_specs
        effect = self.root / 'profile-effect.txt'

        class ExternalProfile:
            def __eq__(self, other):
                return other in {'chat', 'review'}

        def external_target(path):
            with effect.open('a') as stream:
                stream.write('effect\n')
            time.sleep(.15)
            return self.root / path

        def cell(value):
            return (lambda: value).__closure__[0]

        def external_specs(**kwargs):
            specs = []
            for spec in original(**kwargs):
                if spec.name == 'read_file':
                    handler = spec.handler
                    replacements = {'profile': ExternalProfile(), 'review_target': external_target}
                    closure = tuple(cell(replacements[name]) if name in replacements else bound
                                    for name, bound in zip(handler.__code__.co_freevars,
                                                           handler.__closure__))
                    clone = FunctionType(handler.__code__, handler.__globals__, handler.__name__,
                                         handler.__defaults__, closure)
                    clone.__kwdefaults__ = handler.__kwdefaults__
                    spec = replace(spec, handler=clone)
                specs.append(spec)
            return specs

        with patch('tools.build_tool_specs', external_specs):
            events = await self.drive()
        self.assertEqual(effect.read_text(), 'effect\n')
        self.assertTrue(any(event.kind == 'task_paused' for event in events))
        self.assertEqual(self.factory.task_store.snapshot('owner')['lifecycle'], 'paused')
        self.assertFalse(any(row.get('interruption_source') == 'approval_sibling_read'
                             for row in self.observations))

    def configure_read_hook(self, *, sleep_seconds=0):
        effect = self.root / 'hook-effect.txt'
        script = self.root / 'pre-read.py'
        script.write_text("from pathlib import Path\nimport time\n"
                          "with Path('hook-effect.txt').open('a') as stream: stream.write('effect\\n')\n"
                          f"time.sleep({sleep_seconds})\n")
        command = shell_join([sys.executable, str(script)])
        (self.root / '.nailong').mkdir()
        (self.root / '.nailong' / 'settings.json').write_text(json.dumps({'hooks': {
            'PreToolUse': [{'matcher': '^read_file$', 'hooks': [
                {'type': 'command', 'command': command}]}]}}))
        return effect

    def assert_unknown_read_paused(self, events, effect):
        self.assertEqual(effect.read_text(), 'effect\n')
        self.assertTrue(any(event.kind == 'task_paused' for event in events))
        task = self.factory.task_store.snapshot('owner')
        self.assertEqual(task['lifecycle'], 'paused')
        self.assertTrue(any(row['kind'] == 'read' and row['status'] == 'interrupted'
                            for row in task['evidence']))
        self.assertFalse(any(row.get('interruption_source') == 'approval_sibling_read'
                             for row in self.observations))

    async def test_inflight_pretool_hook_pauses_without_replaying_effect(self):
        effect = self.configure_read_hook(sleep_seconds=.5)
        await self.make_service(self.reads_and_command())
        execution = self.factory.tool_execution_context
        original = execution.approval_handler

        async def after_effect(action, permission):
            async with asyncio.timeout(3):
                while not effect.exists():
                    await asyncio.sleep(.005)
            return original(action, permission)

        execution.approval_handler = after_effect
        events = await self.drive()
        self.assert_unknown_read_paused(events, effect)
        self.assertTrue(any(row['name'] == 'read_file' and not row['started']
                            and row['status'] == 'interrupted' for row in self.observations))

    async def test_completed_pretool_hook_pauses_before_read_can_replay_hook(self):
        effect = self.configure_read_hook()
        await self.make_service(self.reads_and_command())
        execution = self.factory.tool_execution_context
        original = execution.approval_handler

        async def after_read_started(action, permission):
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 2))
            return original(action, permission)

        execution.approval_handler = after_read_started
        with patch.object(FileSession, 'read_file', self.slow_reader()):
            events = await self.drive()
        self.assert_unknown_read_paused(events, effect)
        self.assertTrue(any(row['name'] == 'read_file' and row['started']
                            and row['status'] == 'interrupted' for row in self.observations))

    async def test_unknown_pretool_wrapper_cannot_inherit_native_read_exemption(self):
        await self.make_service(self.reads_and_command())
        effect = self.root / 'wrapper-effect.txt'
        execution = self.factory.tool_execution_context
        native = execution.pre_tool_hook
        original_approval = execution.approval_handler

        async def unknown_pretool(action):
            answer = native(action)
            if inspect.isawaitable(answer):
                answer = await answer
            if action['name'] == 'read_file' and not getattr(answer, 'blocked', False):
                if isinstance(answer, dict) and answer.get('blocked'):
                    return answer
                with effect.open('a') as stream:
                    stream.write('effect\n')
            return answer

        async def after_read_started(action, permission):
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 2))
            return original_approval(action, permission)

        execution.pre_tool_hook = unknown_pretool
        execution.approval_handler = after_read_started
        with patch.object(FileSession, 'read_file', self.slow_reader()):
            events = await self.drive()
        self.assert_unknown_read_paused(events, effect)

    async def test_native_hook_method_bound_to_external_owner_is_not_exempt(self):
        await self.make_service(self.reads_and_command())
        effect = self.root / 'foreign-hook-effect.txt'

        class ForeignRunner:
            async def run_event(self, event, **kwargs):
                if event == 'PreToolUse' and kwargs.get('tool_name') == 'read_file':
                    with effect.open('a') as stream:
                        stream.write('effect\n')
                return HookRunResult()

        foreign = SimpleNamespace(permission_mode='default',
            permission_engine=self.service.permission_engine, hook_runner=ForeignRunner())
        self.service._run_hook = MethodType(AgentService._run_hook, foreign)
        execution = self.factory.tool_execution_context
        original = execution.approval_handler

        async def after_read_started(action, permission):
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 2))
            return original(action, permission)

        execution.approval_handler = after_read_started
        with patch.object(FileSession, 'read_file', self.slow_reader()):
            events = await self.drive()
        self.assert_unknown_read_paused(events, effect)

    async def test_started_write_sibling_is_not_replayed_after_approval_interrupt(self):
        await self.make_service([
            request('write_file', {'path': 'effect.txt', 'content': 'written'}, 'started-write'),
            request('run_command', {'command': 'pwd'}, 'approval-command')])
        engine = self.service.permission_engine
        engine.grant_session(engine.suggest_rule('write_file', {'path': 'effect.txt'}))
        execution = self.factory.tool_execution_context
        original_approval = execution.approval_handler
        original_write = FileSession.write_file

        def slow_write(session, *args, **kwargs):
            result = original_write(session, *args, **kwargs)
            self.entered.set()
            time.sleep(.15)
            return result

        async def after_effect(action, permission):
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 2))
            return original_approval(action, permission)

        execution.approval_handler = after_effect
        with patch.object(FileSession, 'write_file', slow_write):
            events = await self.drive()
        self.assertEqual((self.root / 'effect.txt').read_text(), 'written')
        self.assertTrue(any(event.kind == 'task_paused' for event in events))
        task = self.factory.task_store.snapshot('owner')
        self.assertEqual(task['lifecycle'], 'paused')
        self.assertTrue(any(row['kind'] == 'edit' and row['status'] == 'interrupted'
                            for row in task['evidence']))
        self.assertEqual(len([row for row in self.observations
                              if row['name'] == 'write_file' and row['started']]), 1)

    async def test_prior_approval_does_not_exempt_later_tool_cancellation(self):
        await self.make_service([request('run_command', {'command': 'pwd'}, 'approval-command')],
                                later_calls=[request('read_file', {'path': 'document.txt'}, 'later-read')])

        def cancelled_read(*args, **kwargs):
            raise asyncio.CancelledError()

        with patch.object(FileSession, 'read_file', cancelled_read):
            with self.assertRaises(NodeCancelledError):
                await self.drive()
        task = self.factory.task_store.snapshot('owner')
        self.assertEqual(self.approvals, ['run_command'])
        self.assertEqual(task['lifecycle'], 'paused')
        self.assertTrue(any(row['id'] == 'interrupted-later-read' for row in task['evidence']))


if __name__ == '__main__':
    unittest.main()
