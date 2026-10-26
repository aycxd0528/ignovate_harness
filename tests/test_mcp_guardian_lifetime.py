"""A dead SDK launcher must not shorten its independent guardian's lifetime."""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from nailong.core import posix_process
from nailong.mcp.stdio_owner import StdioOwnership
from platform_fixtures import process_exists


@unittest.skipUnless(os.name=='posix','native POSIX guardian and inherited descriptors')
class MCPGuardianLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_launcher_death_keeps_health_channel_open_until_guardian_finishes(self):
        with tempfile.TemporaryDirectory(prefix='MCP guardian lifetime 原生 ') as temporary:
            root=Path(temporary).resolve()
            pid_file=root/'service.pid';entered=root/'cleanup-entered';release=root/'cleanup-release'
            bootstrap=root/'gated-guardian.py'
            bootstrap.write_text(f'''import os,runpy,sys,time
from pathlib import Path
native_read=os.read
def gated_read(fd,size):
    data=native_read(fd,size)
    if fd==int(sys.argv[1]) and not data:
        Path({str(entered)!r}).touch()
        limit=time.monotonic()+5
        while not Path({str(release)!r}).exists() and time.monotonic()<limit:time.sleep(.005)
    return data
os.read=gated_read
runpy.run_path({str(posix_process.CHILD)!r},run_name='__main__')
''')
            # Run the actual launcher with the actual guard, gating only native
            # lifeline EOF so its still-running cleanup is directly observable.
            owner=root/'_mcp_stdio_owner.py'
            owner.write_bytes(Path(posix_process.__file__).with_name('_mcp_stdio_owner.py').read_bytes())
            (root/'posix_process.py').write_text(f'''import runpy
from pathlib import Path
namespace=runpy.run_path({posix_process.__file__!r})
PosixProcessGuard=namespace['PosixProcessGuard']
PosixProcessGuard.__init__.__globals__['CHILD']=Path({str(bootstrap)!r})
''')
            connection=SimpleNamespace(closing=True,descriptors=(),status='closing',error='',
                ready=asyncio.get_running_loop().create_future())
            connection.ready.set_result(None)
            ownership=StdioOwnership(connection)
            await ownership.__aenter__()
            process=None
            async def wait_file(path):
                async with asyncio.timeout(3):
                    while not path.exists():await asyncio.sleep(.005)
            try:
                command=("import os,time;from pathlib import Path;"
                    f"Path({str(pid_file)!r}).write_text(str(os.getpid()));time.sleep(10)")
                process=await asyncio.create_subprocess_exec(sys.executable,'-I','-S',str(owner),
                    str(os.getpid()),*ownership.arguments,sys.executable,'-c',command,
                    cwd=root,start_new_session=True,stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                await asyncio.wait_for(ownership.attach(),3)
                await wait_file(pid_file)
                process.kill();await asyncio.wait_for(process.wait(),3)
                await wait_file(entered)
                self.assertTrue(process_exists(int(pid_file.read_text())))
                # Native EOF is level-triggered: it is absent only while a real
                # owner still holds the peer. No timer or eventual-exit retry.
                with self.assertRaises(BlockingIOError):ownership.peer.recv(1,socket.MSG_PEEK)
                release.touch()
                await asyncio.wait_for(ownership.__aexit__(),3)
                self.assertFalse(process_exists(int(pid_file.read_text())))
                self.assertEqual(ownership.receipt,b'')
                self.assertIn('清理无法核实',connection.error)
            finally:
                release.touch()
                if process is not None:
                    if process.returncode is None:process.kill()
                    await asyncio.wait_for(process.wait(),3)
                await asyncio.wait_for(ownership.__aexit__(),3)
