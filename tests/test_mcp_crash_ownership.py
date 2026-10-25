"""Native MCP service ownership, including death of the SDK's direct process."""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from nailong.mcp.client import MCPManager
from nailong.mcp.config import MCPConfigStore
from nailong.core import posix_process

SERVICE=Path(__file__).resolve().parent/'fixtures/mcp_owned_service.py'
OWNER=Path(posix_process.__file__).with_name('_mcp_stdio_owner.py')

CONTROLLER='''
import asyncio,os,sys,signal
from pathlib import Path
from nailong.mcp.client import MCPManager
from nailong.mcp.config import MCPConfigStore
async def main():
    root=Path(sys.argv[1]);store=MCPConfigStore(root)
    store.add('local',{'transport':'stdio','command':sys.executable,'args':['-B',sys.argv[2]],'timeout_seconds':5})
    manager=MCPManager(store)
    await manager.connect('local')
    assert manager.status('local')['status']=='connected'
    result=await manager.call_tool('local','spawn',{'marker':str(root/'late')})
    assert result['ok'],result
    assert not (root/'late').exists(),'fixture child already completed before controller exit'
    (root/'connected').touch()
    if sys.argv[3]=='kill':os.kill(os.getpid(),signal.SIGKILL)
    os._exit(81)
asyncio.run(main())
'''


@unittest.skipUnless(os.name=='posix','native POSIX parent identity and lifeline')
class MCPControllerDeathTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='MCP crash 原生 ')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve()

    def crash(self,mode):
        import nailong.mcp.client as client
        environment={**os.environ,'PYTHONPATH':str(Path(client.__file__).resolve().parents[2])}
        result=subprocess.run([sys.executable,'-B','-c',CONTROLLER,str(self.root),str(SERVICE),mode],
            capture_output=True,text=True,env=environment,timeout=12)
        self.assertEqual(result.returncode,-signal.SIGKILL if mode=='kill' else 81,result.stderr)
        self.assertTrue((self.root/'connected').exists(),'native MCP never initialized and executed its tool')
        time.sleep(1.3)
        self.assertFalse((self.root/'late').exists(),'MCP descendant continued after controller death')

    def test_os_exit_of_connected_controller_reclaims_service_descendant(self):self.crash('exit')

    def test_sigkill_of_connected_controller_reclaims_service_descendant(self):self.crash('kill')

    def test_existing_standard_owner_wire_still_runs_the_real_command(self):
        # A previously imported ordinary guard must not lose argv[0] when a
        # later helper adds an opt-in MCP-only resource policy.
        from nailong.core.posix_process import CHILD
        life_read,life_write=os.pipe();status_read,status_write=os.pipe()
        process=None
        try:
            process=subprocess.Popen([sys.executable,'-I','-S',str(CHILD),str(life_read),str(status_write),
                '0','1','',sys.executable,'-c',"print('legacy owner command');raise SystemExit(7)"],
                start_new_session=True,pass_fds=(life_read,status_write),stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            os.close(life_read);life_read=None;os.close(status_write);status_write=None
            os.write(life_write,b'G')
            output,error=process.communicate(timeout=3)
            self.assertEqual(output,b'legacy owner command\n',error)
            messages=[json.loads(row) for row in os.read(status_read,16384).splitlines()]
            self.assertIn({'type':'exit','returncode':7},messages)
        finally:
            for descriptor in (life_read,life_write,status_read,status_write):
                if descriptor is not None:os.close(descriptor)
            if process is not None:process.wait(timeout=3)


@unittest.skipUnless(os.name=='posix','native POSIX SDK process')
class MCPDirectProcessDeathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='MCP lifecycle 原生 ')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve()
        self.store=MCPConfigStore(self.root)
        self.store.add('local',{'transport':'stdio','command':sys.executable,'args':['-B',str(SERVICE)],'timeout_seconds':5})
        self.manager=MCPManager(self.store);self.addAsyncCleanup(self.manager.aclose)

    async def spawn(self,server='local',marker=None):
        marker=marker or self.root/'late'
        value=await self.manager.call_tool(server,'spawn',{'marker':str(marker)})
        self.assertTrue(value['ok'],value)
        return marker

    async def wait_file(self,path):
        async with asyncio.timeout(2):
            while not path.exists():await asyncio.sleep(.005)

    async def test_inner_owner_death_seals_admission_and_reports_unknown_cleanup(self):
        await self.manager.connect('local')
        marker=self.root/'owner-destroyed'
        value=await self.manager.call_tool('local','destroy_owner',{'marker':str(marker)})
        await self.wait_file(marker)
        limit=asyncio.get_running_loop().time()+1
        while (self.manager.status('local')['status']=='connected'
                and asyncio.get_running_loop().time()<limit):await asyncio.sleep(.005)
        self.assertNotEqual(self.manager.status('local')['status'],'connected',value)
        self.assertEqual(self.manager.tools('local'),())
        self.assertIn('清理无法核实',self.manager.status('local')['error'])
        result=await self.manager.call_tool('local','echo',{'payload':{'unowned':'must not execute'}})
        self.assertFalse(result['ok'],result)
        self.assertFalse(result.get('mcp_admitted',False),result)

    async def test_sdk_teardown_failure_preserves_known_unknown_cleanup_diagnostic(self):
        from mcp import ClientSession
        native_exit=ClientSession.__aexit__;settled=[]
        async def exit_with_failure(session,*args):
            try:return await native_exit(session,*args)
            finally:
                settled.append(True)
                raise OSError('native SDK teardown failure')
        with patch.object(ClientSession,'__aexit__',new=exit_with_failure):
            await self.manager.connect('local')
            await self.manager.call_tool('local','destroy_owner',{'marker':str(self.root/'owner-exit-failure')})
            async with asyncio.timeout(3):
                await asyncio.gather(self.manager._connections['local'].task,return_exceptions=True)
        self.assertTrue(settled,'original SDK context must finish its native teardown')
        self.assertIn('清理无法核实',self.manager.status('local')['error'])
        self.assertEqual(self.manager.tools('local'),())
        result=await self.manager.call_tool('local','echo',{'payload':{'unowned':'must not execute'}})
        self.assertFalse(result['ok'],result)
        self.assertFalse(result.get('mcp_admitted',False),result)
        # A deliberately requested new generation does not inherit old errors.
        await self.manager.connect('local')
        self.assertEqual(self.manager.status('local')['status'],'connected')
        self.assertEqual(self.manager.status('local')['error'],'')

    async def test_normal_disconnect_stops_the_services_background_descendant(self):
        await self.manager.connect('local');marker=await self.spawn()
        await self.manager.disconnect('local')
        self.assertEqual(self.manager.status('local')['status'],'disconnected')
        await asyncio.sleep(1.3)
        self.assertFalse(marker.exists(),'normal stdin-EOF shutdown leaked a background descendant')

    async def test_active_request_cancellation_stops_the_services_background_descendant(self):
        await self.manager.connect('local');marker=await self.spawn()
        started,release,completed=[self.root/name for name in ('started','release','completed')]
        call=asyncio.create_task(self.manager.call_tool('local','hold',{
            'started':str(started),'release':str(release),'completed':str(completed)}))
        try:
            await self.wait_file(started);call.cancel()
            with self.assertRaises(asyncio.CancelledError):await call
            await asyncio.sleep(1.3)
            self.assertFalse(marker.exists());self.assertFalse(completed.exists())
        finally:
            if not call.done():call.cancel()
            await asyncio.gather(call,return_exceptions=True)

    async def test_disconnect_of_one_service_keeps_another_connection_usable(self):
        self.store.add('second',{'transport':'stdio','command':sys.executable,'args':['-B',str(SERVICE)]})
        await self.manager.connect('local');await self.manager.connect('second')
        marker=await self.spawn()
        await self.manager.disconnect('local')
        value=await self.manager.call_tool('second','echo',{'payload':{'other connection':'仍可用'}})
        self.assertTrue(value['ok'],value)
        self.assertEqual(json.loads(value['content']),{'other connection':'仍可用'})
        await asyncio.sleep(1.3);self.assertFalse(marker.exists())

    async def test_sdk_original_arguments_cwd_and_environment_boundary_are_preserved(self):
        arguments=['','空 格','$(touch injected)','a"b','尾\\']
        self.store.remove('local')
        self.store.add('local',{'transport':'stdio','command':sys.executable,
            'args':['-B',str(SERVICE),*arguments],'env':{'MCP_TEST_TOKEN':'${MCP_OWNER_TOKEN}'}})
        with patch.dict(os.environ,{'MCP_OWNER_TOKEN':'local-fixture-token','DEEPSEEK_API_KEY':'must-not-inherit'}):
            await self.manager.connect('local')
            path=self.root/'facts.json'
            self.assertTrue((await self.manager.call_tool('local','facts',{'path':str(path)}))['ok'])
        facts=json.loads(path.read_text())
        self.assertEqual(facts['argv'],arguments);self.assertEqual(Path(facts['cwd']),self.root)
        self.assertEqual(facts['token'],'local-fixture-token');self.assertFalse(facts['model_key_present'])
        self.assertFalse((self.root/'injected').exists())
        self.assertEqual(self.store.list_servers()['local']['command'],sys.executable)
        self.assertEqual(self.store.list_servers()['local']['args'],['-B',str(SERVICE),*arguments])

    @unittest.skipUnless(Path('/dev/fd').exists(),'native parent FD baseline')
    async def test_repeated_reconnect_and_close_release_parent_descriptors(self):
        await self.manager.connect('local');await self.manager.disconnect('local')
        before=len(os.listdir('/dev/fd'))
        for _ in range(4):
            await self.manager.connect('local')
            self.assertTrue((await self.manager.call_tool('local','echo',{'payload':{}}))['ok'])
            await self.manager.disconnect('local')
            self.assertFalse(self.manager.has_live_connections)
        self.assertEqual(len(os.listdir('/dev/fd')),before)

    async def test_service_does_not_inherit_the_private_health_socket(self):
        import mcp.client.stdio as stdio
        native=stdio._create_platform_compatible_process;parameters=[]
        async def observed(*args,**kwargs):
            parameters.append(kwargs['args'])
            return await native(*args,**kwargs)
        with patch.object(stdio,'_create_platform_compatible_process',side_effect=observed):
            await self.manager.connect('local')
        path=self.root/'descriptors.json'
        self.assertTrue((await self.manager.call_tool('local','facts',{'path':str(path)}))['ok'])
        arguments=parameters[0];port=int(arguments[arguments.index('--owner-channel-v1')+1])
        self.assertNotIn(port,json.loads(path.read_text())['socket_ports'])

    async def test_unavailable_private_channel_fails_before_starting_user_code(self):
        marker=self.root/'must-not-start'
        self.store.add('denied',{'transport':'stdio','command':sys.executable,
            'args':['-c',f"from pathlib import Path;Path({str(marker)!r}).touch()"]})
        with patch('nailong.mcp.stdio_owner.socket.socket',side_effect=PermissionError('local IPC denied')):
            with self.assertRaises(ValueError):await self.manager.connect('denied')
        self.assertEqual(self.manager.tools('denied'),())
        self.assertFalse(marker.exists());self.assertFalse(self.manager.has_live_connections)

    async def test_sdk_killing_launcher_before_handshake_cannot_publish_tools(self):
        import mcp.client.stdio as stdio
        self.store.remove('local')
        self.store.add('local',{'transport':'stdio','command':sys.executable,
            'args':['-B',str(SERVICE)],'timeout_seconds':1})
        native=stdio._create_platform_compatible_process
        async def killed(*args,**kwargs):
            process=await native(*args,**kwargs);process.kill();return process
        with patch.object(stdio,'_create_platform_compatible_process',side_effect=killed):
            with self.assertRaises(ValueError):await self.manager.connect('local')
        self.assertEqual(self.manager.tools('local'),())
        self.assertFalse(self.manager.has_live_connections)

    async def test_missing_executable_cannot_register_fake_tools(self):
        self.store.add('missing',{'transport':'stdio','command':str(self.root/'missing-program'),'timeout_seconds':1})
        with self.assertRaises(ValueError):await self.manager.connect('missing')
        self.assertEqual(self.manager.tools('missing'),())
        self.assertFalse(self.manager.has_live_connections)

    async def test_sdk_killing_only_its_direct_process_still_reclaims_descendant(self):
        import mcp.client.stdio as stdio
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store=MCPConfigStore(root)
            store.add('local',{'transport':'stdio','command':sys.executable,'args':['-B',str(SERVICE)],'timeout_seconds':5})
            manager=MCPManager(store);processes=[];native=stdio._create_platform_compatible_process
            async def observed(*args,**kwargs):
                process=await native(*args,**kwargs);processes.append(process);return process
            try:
                with patch.object(stdio,'_create_platform_compatible_process',side_effect=observed):
                    await manager.connect('local')
                    value=await manager.call_tool('local','spawn',{'marker':str(root/'late')})
                    self.assertTrue(value['ok'],value);self.assertEqual(len(processes),1)
                    processes[0].kill()
                    await asyncio.wait_for(processes[0].wait(),2)
                    await asyncio.sleep(1.3)
                    self.assertFalse((root/'late').exists(),'SDK direct-process kill leaked its descendant')
            finally:await manager.aclose()


@unittest.skipUnless(os.name=='posix','native isolated MCP launcher')
class MCPBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='MCP bootstrap 原生 ')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve()

    def launch(self,command,*,parent=None,new_session=True):
        return subprocess.Popen([sys.executable,'-I','-S',str(OWNER),str(parent or os.getpid()),*command],
            cwd=self.root,start_new_session=new_session,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,bufsize=0)

    def finish(self,process):
        if process.poll() is None:process.terminate()
        process.communicate(timeout=3)

    def test_parent_watch_covers_blocked_guard_module_loading(self):
        # The outer controller dies while A is blocked before creating B. An
        # inherited stdout lets communicate observe when A really exits.
        wrapper = """
import os,runpy,sys,time
from pathlib import Path
namespace=runpy.run_path(sys.argv[1]);original=runpy.run_path
entered=Path(sys.argv[3])
def delayed(path):
    entered.touch();time.sleep(2)
    return original(path)
runpy.run_path=delayed
os._exit(namespace['run'](int(sys.argv[2]),[sys.executable,'-c',"raise SystemExit(0)"]))
"""
        controller = """
import os,subprocess,sys,time
from pathlib import Path
process=subprocess.Popen([sys.executable,'-I','-S','-c',sys.argv[1],sys.argv[2],str(os.getpid()),sys.argv[3]],start_new_session=True)
limit=time.monotonic()+3
while not Path(sys.argv[3]).exists() and time.monotonic()<limit:time.sleep(.005)
assert Path(sys.argv[3]).exists(),'launcher did not reach module load'
os._exit(81)
"""
        started=time.monotonic()
        result=subprocess.run([sys.executable,'-I','-S','-c',controller,wrapper,str(OWNER),str(self.root/'entered')],
            capture_output=True,timeout=7)
        self.assertEqual(result.returncode,81,result.stderr)
        self.assertLess(time.monotonic()-started,1.5,'A retained stdio after its parent died during module load')

    def test_parent_watch_failure_closes_the_lifeline_instead_of_silently_stopping(self):
        wrapper = """
import os,runpy,sys,threading
namespace=runpy.run_path(sys.argv[1]);original=os.getppid
parent=original()
def failing():
    if threading.current_thread() is not threading.main_thread():raise OSError('native identity unavailable')
    return original()
os.getppid=failing
os._exit(namespace['run'](parent,[sys.executable,'-c',"import time;from pathlib import Path;time.sleep(.3);Path('late').touch()"]))
"""
        process=subprocess.Popen([sys.executable,'-I','-S','-c',wrapper,str(OWNER)],
            cwd=self.root,start_new_session=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        output,error=process.communicate(timeout=3)
        self.assertEqual(process.returncode,125,error);self.assertEqual(output,b'')
        time.sleep(.4)
        self.assertFalse((self.root/'late').exists())

    def test_stale_parent_rejects_execution_without_signalling_any_parent_pid(self):
        process=self.launch([sys.executable,'-c',"from pathlib import Path;Path('forbidden').touch()"],parent=os.getpid()+100000)
        output,error=process.communicate(timeout=2)
        self.assertEqual(process.returncode,125,error);self.assertEqual(output,b'')
        self.assertFalse((self.root/'forbidden').exists())

    def test_inherited_session_is_rejected_before_user_code_runs(self):
        process=self.launch([sys.executable,'-c',"from pathlib import Path;Path('forbidden').touch()"],new_session=False)
        output,error=process.communicate(timeout=2)
        self.assertEqual(process.returncode,125,error);self.assertEqual(output,b'')
        self.assertFalse((self.root/'forbidden').exists())

    def test_trusted_guard_and_stdlib_cannot_be_replaced_from_cwd(self):
        for name in ('posix_process','runpy','threading','signal'):
            (self.root/(name+'.py')).write_text("from pathlib import Path;Path('injected').touch();raise RuntimeError('injection')")
        process=self.launch([sys.executable,'-c',"print('actual MCP program')"])
        output,error=process.communicate(timeout=3)
        self.assertEqual((output,process.returncode),(b'actual MCP program\n',0),error)
        self.assertFalse((self.root/'injected').exists())

    def test_real_service_positive_and_signal_exit_codes_are_preserved(self):
        for source,wanted in (('raise SystemExit(7)',7),('import os,signal;os.kill(os.getpid(),signal.SIGTERM)',-15)):
            with self.subTest(wanted=wanted):
                process=self.launch([sys.executable,'-c',source])
                output,error=process.communicate(timeout=3)
                self.assertEqual(process.returncode,wanted,error);self.assertEqual(output,b'')

    def test_actual_service_stdout_close_is_not_held_open_by_monitors(self):
        import selectors
        process=self.launch([sys.executable,'-c',"import os,time;from pathlib import Path;os.close(1);Path('closed').touch();time.sleep(2)"])
        try:
            limit=time.monotonic()+1
            while not (self.root/'closed').exists() and time.monotonic()<limit:time.sleep(.005)
            self.assertTrue((self.root/'closed').exists())
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout,selectors.EVENT_READ)
                self.assertTrue(selector.select(.5),'unused monitor stdout masked the real service EOF')
                self.assertEqual(process.stdout.read(1),b'')
        finally:self.finish(process)

    def test_actual_service_stdin_close_reaches_the_client_as_broken_pipe(self):
        process=self.launch([sys.executable,'-c',"import os,time;from pathlib import Path;os.close(0);Path('closed').touch();time.sleep(2)"])
        try:
            limit=time.monotonic()+1
            while not (self.root/'closed').exists() and time.monotonic()<limit:time.sleep(.005)
            self.assertTrue((self.root/'closed').exists())
            with self.assertRaises(BrokenPipeError):process.stdin.write(b'closed protocol input')
        finally:
            process.stdin.close();process.stdin=None
            self.finish(process)


@unittest.skipUnless(os.name=='posix','local POSIX ownership health channel')
class MCPHealthChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_incorrect_channel_token_fails_before_admission(self):
        from types import SimpleNamespace
        from nailong.mcp.stdio_owner import StdioOwnership
        connection=SimpleNamespace(closing=False,ready=asyncio.get_running_loop().create_future())
        async with StdioOwnership(connection) as channel:
            peer=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            peer.setblocking(False)
            try:
                await asyncio.get_running_loop().sock_connect(peer,('127.0.0.1',int(channel.arguments[1])))
                await asyncio.get_running_loop().sock_sendall(peer,b'x'*32)
                with self.assertRaisesRegex(OSError,'authentication failed'):await channel.attach()
                self.assertIsNone(channel.watcher)
            finally:peer.close()
        self.assertFalse(connection.closing)
        connection.ready.cancel()

    async def test_verified_cleanup_receipt_is_distinguished_from_unknown_owner_loss(self):
        from types import SimpleNamespace
        from nailong.mcp.stdio_owner import StdioOwnership
        ready=asyncio.get_running_loop().create_future();ready.set_result(None)
        connection=SimpleNamespace(closing=False,ready=ready,descriptors=('registered',),status='connected',error='')
        async with StdioOwnership(connection) as channel:
            peer=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            peer.setblocking(False)
            try:
                loop=asyncio.get_running_loop()
                await loop.sock_connect(peer,('127.0.0.1',int(channel.arguments[1])))
                await loop.sock_sendall(peer,channel.token)
                await channel.attach()
                self.assertEqual(await loop.sock_recv(peer,1),b'G')
                await loop.sock_sendall(peer,b'C');peer.close()
                with self.assertRaises(asyncio.CancelledError):await asyncio.sleep(.1)
                self.assertTrue(connection.closing);self.assertEqual(connection.descriptors,())
                self.assertEqual(connection.error,'MCP 服务已退出；连接已关闭。')
            finally:peer.close()


@unittest.skipUnless(os.name=='nt','requires native Windows SDK Job ownership')
class MCPWindowsControllerDeathTests(unittest.TestCase):
    def test_connected_controller_hard_exit_reclaims_service_descendant(self):
        import nailong.mcp.client as client
        with tempfile.TemporaryDirectory(prefix='MCP native Windows ') as temporary:
            root=Path(temporary).resolve()
            environment={**os.environ,'PYTHONPATH':str(Path(client.__file__).resolve().parents[2])}
            result=subprocess.run([sys.executable,'-B','-c',CONTROLLER,str(root),str(SERVICE),'exit'],
                capture_output=True,text=True,env=environment,timeout=12)
            self.assertEqual(result.returncode,81,result.stderr)
            self.assertTrue((root/'connected').exists())
            time.sleep(1.3)
            self.assertFalse((root/'late').exists(),'native Windows MCP Job did not reclaim descendant')
