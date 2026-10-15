"""Closing the shell joins owned tool processes before releasing the project."""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from platform_fixtures import process_exists


class ShutdownTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Windows termination does not deliver POSIX SIGTERM.')
    def test_sigterm_closes_running_tool_before_adapter_exit(self):
        self.check_shutdown('sigterm')

    def test_parent_pipe_closes_running_tool_before_adapter_exit(self):
        self.check_shutdown('pipe')

    def test_parent_exit_closes_running_tool_before_adapter_exit(self):
        self.check_shutdown('eof')

    def check_shutdown(self, mode):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = temporary.name
        with self.subTest(mode=mode):
            project = Path(directory) / 'project'; project.mkdir()
            script = Path(directory) / 'adapter.py'
            script.write_text('''
import json, sys
from pathlib import Path
from types import SimpleNamespace
from nailong.gui.server import GUIServer
from nailong.gui.controller import GUIController
from nailong.core.sessions import ProjectSessionStore
from nailong.core.processes import execute_process
from platform_fixtures import python_command
from agent_service import TurnEvent
import nailong.gui.__main__ as entry
class Service:
    task_store=None
    runtime_factory=SimpleNamespace(settings=SimpleNamespace(model='unknown',api_base='https://example.test'))
    async def stream_turn(self,message,config,**kwargs):
        identity=config['configurable']['thread_id']
        sessions.append_event(identity,'user',{'text':message})
        command=python_command("import os,time;open('started','w').write(str(os.getpid()));time.sleep(30);open('survived','w').write('bad')")
        result = await execute_process(command,project,timeout=35)
        (project/'process-result.json').write_text(json.dumps(result),encoding='utf-8')
        yield TurnEvent('final',{'text':'done'})
project=Path(sys.argv[1])
sessions=ProjectSessionStore(project,base_dir=project.parent/'data')
entry.GUIServer=lambda path,port: GUIServer(path,port=port,controller_factory=lambda p:GUIController(p,sessions=sessions,service=Service()))
entry.main(['--stdio','--project',str(project),*sys.argv[2:]])
''')
            root = Path(__file__).resolve().parents[1]
            env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(root), str(root/'tests')]), 'PYTHONUTF8': '1'}
            arguments = [] if mode == 'sigterm' else ['--parent-pipe']
            process = subprocess.Popen([sys.executable, str(script), str(project), *arguments], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, env=env)
            for pipe in (process.stdin, process.stdout, process.stderr):
                self.addCleanup(pipe.close)
            def cleanup_process():
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            self.addCleanup(cleanup_process)
            line = process.stdout.readline()
            if not line:
                process.wait(timeout=5)
                self.fail(process.stderr.read())
            url = urlsplit(json.loads(line)['url'])
            token = parse_qs(url.fragment)['token'][0]
            connection = http.client.HTTPConnection('127.0.0.1', url.port, timeout=5)
            self.addCleanup(connection.close)
            headers = {'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'}
            connection.request('GET', '/api/snapshot', headers=headers)
            project_id = json.loads(connection.getresponse().read())['project']['id']
            def command(name, payload):
                connection.request('POST', '/api/command', json.dumps({'name': name, 'payload': {'project_id': project_id, **payload}}), headers)
                response = connection.getresponse(); body=json.loads(response.read())
                self.assertEqual(response.status, 200, body)
                return body
            branch = command('create', {'name': 'shutdown'})
            command('send', {'branch_id': branch['id'], 'message': 'wait', 'request_id': 'shutdown-test'})
            deadline = time.monotonic()+5
            while not (project/'started').exists() and time.monotonic()<deadline:
                time.sleep(.01)
            if not (project/'started').exists():
                connection.request('GET', '/api/snapshot', headers=headers)
                self.fail(connection.getresponse().read().decode('utf-8') +
                    ((project/'process-result.json').read_text(encoding='utf-8') if (project/'process-result.json').exists() else ''))
            tool_pid = int((project/'started').read_text())
            if mode == 'sigterm':
                process.terminate()
            elif mode == 'pipe':
                process.stdin.write('shutdown\n'); process.stdin.flush()
            else:
                process.stdin.close(); process.stdin = None
            output, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, error)
            self.assertFalse(process_exists(tool_pid))
            self.assertFalse((project/'survived').exists())
