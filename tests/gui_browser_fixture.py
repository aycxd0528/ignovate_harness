"""Offline browser fixture: real Harness with a deterministic local model."""
import signal
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore
from nailong.gui.controller import GUIController
from nailong.gui.server import GUIServer


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        prompt = next((str(m.content) for m in reversed(messages) if m.type == 'human' and not m.name), '')
        if '失败聊天测试' in prompt:
            raise RuntimeError('离线测试：首轮聊天失败')
        if '延迟地图追问' in prompt:
            __import__('time').sleep(1.5)
        text = '先明确内核接口，再实现会话地图。\n\n当前方案保留共享起点，同时允许独立探索。\n\n引用这一段可以进一步展开。'
        if '并行工具展示测试' in prompt:
            finished = set()
            for message in reversed(messages):
                if message.type == 'human' and not message.name:
                    break
                if message.type == 'tool':
                    finished.add(message.tool_call_id)
            if not {'parallel-one', 'parallel-two'} <= finished:
                calls = []
                for identity, delay, result in [('parallel-one', 1.8, '结果一'), ('parallel-two', 2.6, '结果二')]:
                    command = f'{sys.executable} -c ' + __import__('shlex').quote(f'import time; time.sleep({delay}); print("{result}")')
                    calls.append({'name': 'run_command', 'args': {'command': command}, 'id': identity})
                return ChatResult(generations=[ChatGeneration(message=AIMessage('', tool_calls=calls))])
            __import__('time').sleep(1)
            return ChatResult(generations=[ChatGeneration(message=AIMessage('并行工具已完成。'))])
        if '修改产物测试' in prompt:
            current = []
            for message in reversed(messages):
                if message.type == 'human' and not message.name:
                    break
                if message.type == 'tool':
                    current.append(message.name)
            if 'read_file' not in current:
                answer = AIMessage('', tool_calls=[{'name':'read_file','args':{'path':'report.md'},'id':'diff-read'}])
            elif 'edit_file' not in current:
                answer = AIMessage('', tool_calls=[{'name':'edit_file','args':{'path':'report.md','old_string':'真实原生工具生成。','new_string':'更新后的产物内容。'},'id':'diff-edit'}])
            else:
                answer = AIMessage('文件已修改。')
            return ChatResult(generations=[ChatGeneration(message=answer)])
        if any('工具状态与产物测试' in str(message.content) for message in messages if message.type=='human'):
            finished={message.name for message in messages if message.type=='tool'}
            if 'run_command' not in finished:
                command=f'{sys.executable} -c '+__import__('shlex').quote('import time; time.sleep(1.2); print("工具执行结果")')
                answer=AIMessage('',tool_calls=[{'name':'run_command','args':{'command':command},'id':'live-command'}])
                return ChatResult(generations=[ChatGeneration(message=answer)])
            if 'write_file' not in finished:
                answer=AIMessage('',tool_calls=[{'name':'write_file','args':{'path':'report.md','content':'# 测试产物\n真实原生工具生成。'},'id':'live-file'}])
                return ChatResult(generations=[ChatGeneration(message=answer)])
            text='命令已完成，并生成 report.md。'
        if any('Markdown' in str(message.content) for message in messages if message.type == 'human'):
            text = '# 实施方案\n\n**明确边界**，再组织路径。\n\n- 主线推进\n- 分叉探索\n\n> 保留原始上下文\n\n```python\nprint("hello")\n```\n\n| 分支 | 状态 |\n| --- | --- |\n| A | 就绪 |\n\n<img src=x onerror=alert(1)>\n[危险链接](javascript:alert(1))'
        if '长回答' in prompt:
            text += '\n'+('完整正文测试，' * 4000)+'正文末尾标记'
        answer = AIMessage(text, usage_metadata={'input_tokens': 120, 'output_tokens': 40, 'total_tokens': 160})
        return ChatResult(generations=[ChatGeneration(message=answer)])


def main():
    temp = tempfile.TemporaryDirectory()
    home = Path(temp.name).resolve()/'home'; home.mkdir()
    root = Path(temp.name) / 'GUI 验证项目'
    root.mkdir()
    (root / 'README.md').write_text('# 临时项目\n', encoding='utf-8')
    (root / '.nailong').mkdir()
    (root / '.nailong/settings.json').write_text('{"context_windows":{"deepseek-flash":65536}}')
    def build(project):
        sessions = ProjectSessionStore(project, base_dir=Path(temp.name) / 'data')
        if __import__('os').environ.get('IGNOVATE_GUI_NO_CONNECTION') == '1':
            return GUIController(project, sessions=sessions)
        factory = AgentRuntimeFactory(Settings('offline-test-key', 'https://example.test', 'deepseek-flash', Path(project)), session_store=sessions)
        controller = GUIController(project, sessions=sessions, service=AgentService(factory, api_key='offline-test-key'))
        if __import__('os').environ.get('IGNOVATE_GUI_CONTROL_HISTORY') == '1':
            branch = controller.branches.create('控制流程验证')
            thread = branch['thread_id']
            task = factory.task_store.begin(thread, '修复 README 启动流程')
            task = factory.task_store.add_acceptance(thread, 'manual-output', '检查实际输出', kind='manual')
            factory.task_store.set_step(thread, 'inspect', title='核对输入', state='done')
            factory.task_store.set_step(thread, 'fix', title='修复故障路径', dependencies=['inspect'])
            task = factory.task_store.snapshot(thread)
            sessions.append_event(thread, 'user', {'text': '控制流程的隔离历史来源'})
            sessions.append_event(thread, 'final', {'text': '保留原生任务身份与验收条件。'})
            controller.branches.commit_turn(branch['id'], 0, None, task_snapshot=task)
        if __import__('os').environ.get('IGNOVATE_GUI_TRACE_HISTORY') == '1':
            branch = controller.branches.create('轨迹历史测试')
            for number in range(81):
                start = len(sessions.read_events(branch['thread_id']))
                sessions.append_event(branch['thread_id'], 'user', {'text': f'历史问题 {number + 1}'})
                sessions.append_event(branch['thread_id'], 'final', {'text': f'历史回答 {number + 1}'})
                controller.branches.commit_turn(branch['id'], start, None)
        return controller
    def offline_settings():
        from nailong.core.bootstrap import BootstrapStore
        provider = BootstrapStore().read().get('provider', {})
        if __import__('os').environ.get('IGNOVATE_GUI_NO_CONNECTION') == '1' and not provider:
            raise ValueError('模型连接尚未配置。')
        model = provider.get('model', 'deepseek-flash')
        return Settings(provider.get('api_key', 'offline-test-key'), provider.get('api_base', 'https://example.test'),
                        model, root, environment_model=model)
    # Keep model construction offline when the native reasoning setting changes.
    with patch('agent.ChatDeepSeek', side_effect=lambda **kwargs: OfflineModel(responses=[])), patch('pathlib.Path.home', return_value=home), patch('config.load_settings', side_effect=offline_settings), patch.dict('os.environ', {'IGNOVATE_CONFIG_DIR':str(home/'config'),'NAILONG_DATA_DIR':str(Path(temp.name).resolve()/'data')}):
        if __import__('os').environ.get('IGNOVATE_GUI_FIRST_RUN') != '1':
            from nailong.core.bootstrap import BootstrapStore
            from nailong.core.preferences import atomic_json
            bootstrap = BootstrapStore()
            atomic_json(bootstrap.path.with_name('onboarding.json'), {'version': 1, 'status': 'completed'}, bootstrap.boundary)
        server = GUIServer(root, port=int(__import__('os').environ.get('IGNOVATE_GUI_TEST_PORT', '8767')), token='browser-test-only', controller_factory=build)
        try:
            print('Browser fixture ready on '+server.origin, flush=True)
            server.serve_forever()
        finally:
            server.server_close()
            temp.cleanup()


if __name__ == '__main__':
    main()
