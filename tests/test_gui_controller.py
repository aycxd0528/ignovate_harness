import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_service import TurnEvent
from nailong.core.sessions import ProjectSessionStore


class ControlledService:
    api_key = 'sentinel-secret'
    permission_mode = 'default'

    def __init__(self, sessions):
        self.session_store = sessions
        self.runtime_factory = SimpleNamespace(settings=SimpleNamespace(model='unknown', api_base='https://example.test'))
        self.task_store = None

    async def stream_turn(self, message, config, *, approval_handler, **kwargs):
        identity = config['configurable']['thread_id']
        self.session_store.append_event(identity, 'user', {'text': message})
        if message == 'approval':
            decision = await approval_handler({'name': 'run_command', 'args': {'command': 'echo test'}}, 1, 1)
            answer = decision.kind
        elif message in {'tools','tools_wait'}:
            data={'call_id':'cmd-1','name':'run_command','preview':{'command':'python demo.py'}}
            self.session_store.append_event(identity,'tool_start',data)
            yield TurnEvent('tool_start',data)
            await asyncio.sleep(.1 if message=='tools' else 60)
            data={'call_id':'cmd-1','name':'run_command','ok':True,'elapsed_ms':100,'output_preview':'完成'}
            self.session_store.append_event(identity,'tool_end',data)
            yield TurnEvent('tool_end',data)
            answer='工具执行完成'
        elif message == 'wait':
            await asyncio.sleep(60)
            answer = 'done'
        else:
            answer = 'answer: ' + message
        for kind, data in [('token', {'text': answer}), ('final', {'text': answer})]:
            self.session_store.append_event(identity, kind, data)
            yield TurnEvent(kind, data)

    def get_context_summary(self, identity):
        return {'estimated_tokens': 100}

    def set_permission_mode(self, mode):
        self.permission_mode = mode


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from nailong.gui.controller import GUIController
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()
        self.sessions = ProjectSessionStore(self.project, base_dir=Path(self.temp.name) / 'data', api_key='sentinel-secret')
        self.controller = GUIController(self.project, sessions=self.sessions, service=ControlledService(self.sessions))
        self.branch = await self.controller.command('create', {'name': '测试会话'})

    async def asyncTearDown(self):
        if hasattr(self, 'controller'):
            await self.controller.close()
        if hasattr(self, 'temp'):
            self.temp.cleanup()

    async def until(self, predicate):
        async def poll():
            while not predicate():
                await asyncio.sleep(.01)
        await asyncio.wait_for(poll(), 3)

    async def test_duplicate_send_runs_once_and_payload_reuse_fails(self):
        payload = {'branch_id': self.branch['id'], 'message': 'hello', 'request_id': 'stable-request'}
        a = await self.controller.command('send', payload)
        b = await self.controller.command('send', payload)
        self.assertEqual(a['run_id'], b['run_id'])
        await self.until(lambda: self.controller.runs[a['run_id']]['status'] == 'completed')
        self.assertEqual(len(self.controller.branches.path(self.branch['id'])), 1)
        with self.assertRaisesRegex(ValueError, '请求'):
            await self.controller.command('send', {**payload, 'message': 'different'})

    async def test_stop_invalidates_approval_and_pauses_other_branch_queue(self):
        second = await self.controller.command('create', {'name': '另外会话'})
        first_run = await self.controller.command('send', {'branch_id': self.branch['id'], 'message': 'approval', 'request_id': 'a'})
        await self.until(lambda: bool(self.controller.approvals))
        approval_id = next(iter(self.controller.approvals))
        queued = await self.controller.command('send', {'branch_id': second['id'], 'message': 'second', 'request_id': 'b'})
        await self.controller.command('stop', {'run_id': first_run['run_id']})
        with self.assertRaisesRegex(ValueError, '审批'):
            await self.controller.command('approve', {'action_id': approval_id, 'run_id': first_run['run_id'], 'decision': 'approve_once'})
        self.assertEqual(self.controller.runs[queued['run_id']]['status'], 'queued')
        self.assertEqual(len(self.controller.branches.path(second['id'])), 0)
        await self.controller.command('resume_queue', {})
        await self.until(lambda: self.controller.runs[queued['run_id']]['status'] == 'completed')
        self.assertEqual(self.controller.branches.path(second['id'])[0]['prompt'], 'second')

    async def test_draft_is_redacted_without_model_execution(self):
        await self.controller.command('draft', {'branch_id': self.branch['id'], 'text': 'quote sentinel-secret'})
        self.assertEqual(self.controller.branches.draft(self.branch['id']), 'quote [密钥已隐藏]')
        self.assertEqual(len(self.sessions.read_events(self.branch['thread_id'])), 0)
        self.assertEqual(self.controller.snapshot()['project']['path'], str(self.project.resolve()))

    async def test_request_id_cannot_change_command_kind(self):
        payload = {'branch_id': self.branch['id'], 'message': 'hello', 'request_id': 'same-operation-id'}
        await self.controller.command('send', payload)
        with self.assertRaisesRegex(ValueError, '请求'):
            await self.controller.command('verify', payload)

    async def test_stale_project_command_cannot_mutate_layout(self):
        with self.assertRaisesRegex(ValueError, '项目'):
            await self.controller.command('layout', {'project_id': 'old-project', 'layout': {}, 'revision': 0})
        self.assertEqual(self.controller.branches.layout()['revision'], 0)

    async def test_client_resume_flag_cannot_run_unrecoverable_legacy_history(self):
        legacy = self.controller.branches.create('旧日志', legacy=True)
        self.sessions.append_event(legacy['thread_id'], 'user', {'text': 'old'})
        self.sessions.append_event(legacy['thread_id'], 'final', {'text': 'answer'})
        self.controller.branches.commit_turn(legacy['id'], 0, None)
        self.controller.branches.db.execute('UPDATE branches SET legacy=1 WHERE id=?', (legacy['id'],))
        self.controller.branches.db.commit()
        with self.assertRaisesRegex(ValueError, '旧会话'):
            await self.controller.command('send', {'branch_id': legacy['id'], 'message': 'continue', 'request_id': 'bad-resume', 'resume': True})

    async def test_permission_control_belongs_to_branch_and_captures_queued_run(self):
        await self.controller.command('permission', {'branch_id': self.branch['id'], 'mode': 'acceptEdits'})
        self.controller.paused = True
        queued = await self.controller.command('send', {'branch_id': self.branch['id'], 'message': 'queued', 'request_id': 'mode-capture'})
        self.assertEqual(self.controller.runs[queued['run_id']]['payload']['permission_mode'], 'acceptEdits')
        with self.assertRaisesRegex(ValueError, '运行|排队'):
            await self.controller.command('permission', {'branch_id': self.branch['id'], 'mode': 'bypassPermissions'})
        other = await self.controller.command('create', {'name': 'other'})
        modes = {b['id']: b['permission_mode'] for b in self.controller.snapshot()['branches']}
        self.assertEqual(modes[other['id']], 'default')
        self.assertEqual(modes[self.branch['id']], 'acceptEdits')

    async def test_permission_control_validates_mode_without_saving_model_configuration(self):
        with self.assertRaisesRegex(ValueError, '权限'):
            await self.controller.command('permission', {'branch_id': self.branch['id'], 'mode': 'unknown'})
        self.assertIsNotNone(self.controller.service)
        self.assertEqual(self.controller.runs, {})

    async def test_token_weather_uses_main_context_sample_not_child_or_bill_total(self):
        (self.project/'.nailong').mkdir()
        (self.project/'.nailong/settings.json').write_text('{"context_windows":{"unknown":65536}}')
        for index, sample in enumerate((10000,40000)):
            start=len(self.sessions.read_events(self.branch['thread_id']))
            for kind,data in [('user',{'text':str(index)}),('turn_start',{'profile':'chat'}),
                ('usage',{'scope':'main','input_tokens':sample,'output_tokens':1}),
                ('usage',{'scope':'child','input_tokens':999999,'output_tokens':1}),('final',{'text':'answer'})]:
                self.sessions.append_event(self.branch['thread_id'],kind,data)
            self.controller.branches.commit_turn(self.branch['id'],start,'cp')
        weather=self.controller.snapshot()['branches'][0]['weather']
        self.assertEqual(weather['history'],[10000,40000])
        self.assertEqual(weather['last_input_tokens'],40000)
        self.assertEqual(weather['context_window'],65536)
        self.assertEqual(weather['level'],'showers')
        self.assertIn('Showers',weather['text'])

    async def test_conversation_layouts_do_not_overwrite_each_other(self):
        other=await self.controller.command('create',{'name':'other'})
        first=await self.controller.command('layout',{'conversation_id':self.branch['conversation_id'],'layout':{'positions':{'a':{'x':10,'y':20}}},'revision':0})
        second=await self.controller.command('layout',{'conversation_id':other['conversation_id'],'layout':{'positions':{},'viewport':{'x':20,'y':50,'zoom':.8}},'revision':0})
        layouts=self.controller.snapshot()['layouts']
        self.assertEqual(layouts[self.branch['conversation_id']]['positions'],first['positions'])
        self.assertEqual(layouts[other['conversation_id']]['viewport'],second['viewport'])

    async def test_enqueue_sets_initial_title_before_answer_and_keeps_it_after_next_turn(self):
        branch=await self.controller.command('create',{})
        self.controller.paused=True
        await self.controller.command('send',{'branch_id':branch['id'],'message':'修复登录页面','request_id':'first-title'})
        self.assertEqual(self.controller.branches.branch(branch['id'])['name'],'修复登录页面')
        await self.controller.command('send',{'branch_id':branch['id'],'message':'后续补充','request_id':'second-title'})
        self.assertEqual(self.controller.branches.branch(branch['id'])['name'],'修复登录页面')

    async def test_live_tool_status_and_elapsed_are_saved_and_frozen_after_reload(self):
        run=await self.controller.command('send',{'branch_id':self.branch['id'],'message':'tools','request_id':'live-tools'})
        await self.until(lambda:bool(self.controller.snapshot()['runs'][0].get('tools')))
        live=self.controller.snapshot()['runs'][0]
        self.assertEqual(live['tools'][0]['status'],'running')
        self.assertEqual(live['tools'][0]['preview']['command'],'python demo.py')
        self.assertIsNotNone(live['started_at'])
        await self.until(lambda:self.controller.runs[run['run_id']]['status']=='completed')
        complete=self.controller.snapshot()['runs'][0]
        self.assertGreaterEqual(complete['elapsed_ms'],80)
        self.assertEqual(complete['tools'][0]['status'],'completed')
        await self.controller.close()
        from nailong.gui.controller import GUIController
        self.controller=GUIController(self.project,sessions=self.sessions,service=ControlledService(self.sessions))
        restored=self.controller.snapshot()['runs'][0]
        self.assertEqual(restored['elapsed_ms'],complete['elapsed_ms'])
        node=self.controller.snapshot()['nodes'][0]
        self.assertEqual(node['timing']['elapsed_ms'],complete['elapsed_ms'])
        self.assertEqual(node['artifacts'][0]['kind'],'command')

    async def test_stopped_tool_is_interrupted_instead_of_kept_running(self):
        run=await self.controller.command('send',{'branch_id':self.branch['id'],'message':'tools_wait','request_id':'stop-tools'})
        await self.until(lambda:bool(self.controller.snapshot()['runs'][0].get('tools')))
        await self.controller.command('stop',{'run_id':run['run_id']})
        await self.until(lambda:self.controller.runs[run['run_id']]['status']=='stopped')
        public=self.controller.snapshot()['runs'][0]
        self.assertEqual(public['tools'][0]['status'],'interrupted')
        self.assertIsNotNone(public['finished_at'])


if __name__ == '__main__':
    unittest.main()
