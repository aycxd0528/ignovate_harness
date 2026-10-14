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


@unittest.skipIf(os.name == 'nt', 'POSIX SIGTERM; Windows desktop requires separate release validation.')
class ShutdownTests(unittest.TestCase):
    def test_sigterm_closes_running_tool_before_adapter_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'project'; project.mkdir()
            script = Path(directory) / 'adapter.py'
            script.write_text('''
import json, sys, shlex
from pathlib import Path
from types import SimpleNamespace
from nailong.gui.server import GUIServer
from nailong.gui.controller import GUIController
from nailong.core.sessions import ProjectSessionStore
from nailong.core.processes import execute_process
from agent_service import TurnEvent
import nailong.gui.__main__ as entry
class Service:
    task_store=None
    runtime_factory=SimpleNamespace(settings=SimpleNamespace(model='unknown',api_base='https://example.test'))
    async def stream_turn(self,message,config,**kwargs):
        identity=config['configurable']['thread_id']
        sessions.append_event(identity,'user',{'text':message})
        command=shlex.join([sys.executable,'-c',"import os,time;open('started','w').write(str(os.getpid()));time.sleep(30);open('survived','w').write('bad')"])
        await execute_process(command,project,timeout=35)
        yield TurnEvent('final',{'text':'done'})
project=Path(sys.argv[1])
sessions=ProjectSessionStore(project,base_dir=project.parent/'data')
entry.GUIServer=lambda path,port: GUIServer(path,port=port,controller_factory=lambda p:GUIController(p,sessions=sessions,service=Service()))
entry.main(['--stdio','--project',str(project)])
''')
            env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
            process = subprocess.Popen([sys.executable, str(script), str(project)], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, env=env)
            self.addCleanup(lambda: process.kill() if process.poll() is None else None)
            url = urlsplit(json.loads(process.stdout.readline())['url'])
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
            self.assertTrue((project/'started').exists())
            tool_pid = int((project/'started').read_text())
            process.terminate()
            output, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, error)
            with self.assertRaises(ProcessLookupError):
                os.kill(tool_pid, 0)
            self.assertFalse((project/'survived').exists())
