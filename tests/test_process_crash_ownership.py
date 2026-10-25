"""Native controller death must not leave ordinary owned command trees running."""
import json
import asyncio
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch


CONTROLLER = '''
import asyncio,os,sys,subprocess,time
from pathlib import Path
root=Path(sys.argv[1]);mode=sys.argv[2];command=sys.argv[3]
killed=mode.startswith('killed-');mode=mode.removeprefix('killed-')
async def main():
    if mode=='owned':
        from nailong.core.process_io import OwnedProcess
        owner=OwnedProcess([sys.executable,'-B','-c',command],cwd=root,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    elif mode=='raw':
        from nailong.core.process_io import capture_bytes
        task=asyncio.create_task(asyncio.to_thread(capture_bytes,
            [sys.executable,'-B','-c',command],cwd=root,timeout=10))
    else:
        import shlex
        from nailong.core.processes import execute_process
        task=asyncio.create_task(execute_process(shlex.join([sys.executable,'-B','-c',command]),root,timeout=10))
    limit=asyncio.get_running_loop().time()+5
    while not (root/'started').exists():
        if asyncio.get_running_loop().time()>limit:raise TimeoutError('command never started')
        await asyncio.sleep(.01)
    if killed:
        import signal
        os.kill(os.getpid(),signal.SIGKILL)
    os._exit(81)
asyncio.run(main())
'''


@unittest.skipUnless(os.name=='posix','native POSIX lifeline and process groups')
class ProcessCrashOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='owned crash 原生 ')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve()

    def crash(self,mode):
        # Both generations self-exit after a short delay even against broken
        # code. The test never scans or kills unknown processes during cleanup.
        grandchild="from pathlib import Path;import time;time.sleep(1.2);Path('grandchild-late').write_text('leak')"
        command=("from pathlib import Path;import subprocess,sys,time;"
            f"subprocess.Popen([sys.executable,'-B','-c',{grandchild!r}]);"
            "Path('started').write_text('ready');time.sleep(1.2);Path('command-late').write_text('leak')")
        result=subprocess.run([sys.executable,'-B','-c',CONTROLLER,str(self.root),mode,command],
            capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,-signal.SIGKILL if mode.startswith('killed-') else 81,result.stderr)
        self.assertTrue((self.root/'started').exists(),'test never exercised a running command')
        time.sleep(1.5)
        self.assertFalse((self.root/'command-late').exists(),'command outlived its controller')
        self.assertFalse((self.root/'grandchild-late').exists(),'grandchild outlived its controller')

    def test_owned_process_hard_exit_reclaims_command_and_grandchild(self):self.crash('owned')

    def test_raw_capture_hard_exit_reclaims_command_and_grandchild(self):self.crash('raw')

    def test_verification_process_hard_exit_reclaims_command_and_grandchild(self):self.crash('verification')

    def test_sigkill_of_verification_controller_reclaims_command_and_grandchild(self):self.crash('killed-verification')

    def test_controller_exit_before_admission_never_executes_user_command(self):
        code="""import os,sys
from pathlib import Path
from nailong.core import posix_process
from nailong.core.process_io import OwnedProcess
native=posix_process.subprocess.Popen
def interrupted(*args,**kwargs):
    process=native(*args,**kwargs)
    Path('monitor-created').write_text(str(process.pid))
    os._exit(81)
posix_process.subprocess.Popen=interrupted
OwnedProcess([sys.executable,'-c',"from pathlib import Path;Path('unowned-user-command').touch()"])
"""
        from nailong.core import posix_process
        environment={**os.environ,'PYTHONPATH':str(Path(posix_process.__file__).resolve().parents[2])}
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=self.root,env=environment,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
        self.assertEqual(result.returncode,81,result.stderr)
        self.assertTrue((self.root/'monitor-created').exists())
        time.sleep(.3)
        self.assertFalse((self.root/'unowned-user-command').exists())

    def test_gate_preserves_native_default_sigpipe_exit_status(self):
        from nailong.core.process_io import capture_bytes
        output,code,truncated=capture_bytes(['/bin/sh','-c','kill -PIPE $$'],cwd=self.root)
        self.assertEqual(code,-signal.SIGPIPE)
        self.assertEqual(output,b'');self.assertFalse(truncated)

    def test_signed_user_exit_codes_are_not_the_monitors_sigkill_code(self):
        from nailong.core.process_io import capture_bytes
        for code,wanted in (('pass',0),('raise SystemExit(7)',7),('import os,signal;os.kill(os.getpid(),signal.SIGTERM)',-15)):
            with self.subTest(wanted=wanted):
                output,actual,_=capture_bytes([sys.executable,'-c',code],cwd=self.root)
                self.assertEqual(actual,wanted);self.assertEqual(output,b'')

    def test_soft_termination_reaches_user_command_while_monitor_retains_ownership(self):
        from nailong.core.process_io import OwnedProcess
        owner=OwnedProcess([sys.executable,'-c',"import time;from pathlib import Path;Path('soft-started').touch();time.sleep(5)"],cwd=self.root)
        try:
            limit=time.monotonic()+2
            while not (self.root/'soft-started').exists() and time.monotonic()<limit:time.sleep(.01)
            self.assertTrue((self.root/'soft-started').exists())
            owner.terminate(force=False)
            self.assertEqual(owner.process.wait(timeout=2),-15)
        finally:owner.close()

    def test_process_handle_signal_reaches_the_actual_command(self):
        from nailong.core.process_io import OwnedProcess
        owner=OwnedProcess([sys.executable,'-c',"import signal,time;from pathlib import Path;signal.signal(signal.SIGUSR1,lambda *args:exit(23));Path('signal-started').touch();time.sleep(5)"],cwd=self.root)
        try:
            limit=time.monotonic()+2
            while not (self.root/'signal-started').exists() and time.monotonic()<limit:time.sleep(.01)
            self.assertTrue((self.root/'signal-started').exists())
            owner.process.send_signal(signal.SIGUSR1)
            try:actual=owner.process.wait(timeout=.5)
            except subprocess.TimeoutExpired:self.fail('signal reached only the monitor, not the command')
            self.assertEqual(actual,23)
        finally:owner.close()

    def test_bootstrap_missing_file_is_start_failure_and_does_not_run_user_code(self):
        import nailong.core.posix_process as guard
        from nailong.core.process_io import capture_bytes
        with patch.object(guard,'CHILD',self.root/'missing-bootstrap.py'):
            with self.assertRaises(OSError):
                capture_bytes([sys.executable,'-c',"from pathlib import Path;Path('must-not-start').touch()"],cwd=self.root)
        self.assertFalse((self.root/'must-not-start').exists())

    def test_missing_user_executable_preserves_native_start_failure(self):
        from nailong.core.process_io import capture_bytes
        missing=self.root/'does-not-exist'
        with self.assertRaises(FileNotFoundError) as caught:capture_bytes([str(missing)],cwd=self.root)
        self.assertEqual(caught.exception.filename,str(missing))

    def test_helper_ignores_modules_in_command_cwd(self):
        from nailong.core.process_io import capture_bytes
        for name in ('json','signal','subprocess'):
            (self.root/(name+'.py')).write_text("from pathlib import Path;Path('bootstrap-injected').touch();raise RuntimeError('cwd injection')")
        output,code,_=capture_bytes([sys.executable,'-c',"print('actual command')"],cwd=self.root)
        self.assertEqual((output,code),(b'actual command\n',0))
        self.assertFalse((self.root/'bootstrap-injected').exists())

    @unittest.skipUnless(Path('/dev/fd').exists(),'native descriptor baseline')
    def test_repeated_success_and_start_failure_close_all_parent_descriptors(self):
        from nailong.core.process_io import capture_bytes
        before=len(os.listdir('/dev/fd'))
        for _ in range(12):
            self.assertEqual(capture_bytes([sys.executable,'-c','pass'],cwd=self.root)[1],0)
            with self.assertRaises(FileNotFoundError):capture_bytes([str(self.root/'missing')],cwd=self.root)
        self.assertEqual(len(os.listdir('/dev/fd')),before)

    def test_concurrent_command_cannot_keep_another_owners_lifeline_alive(self):
        from nailong.core.process_io import OwnedProcess
        first=OwnedProcess([sys.executable,'-c',"import time;from pathlib import Path;time.sleep(.5);Path('first-late').touch()"],cwd=self.root)
        second=None
        try:
            second=OwnedProcess([sys.executable,'-c',"import time;from pathlib import Path;time.sleep(.7);Path('second-done').touch()"],cwd=self.root)
            first.close()
            self.assertEqual(second.process.wait(timeout=2),0)
            self.assertFalse((self.root/'first-late').exists())
            self.assertTrue((self.root/'second-done').exists())
        finally:
            first.close()
            if second is not None:second.close()

    def test_unexpected_monitor_death_cannot_acknowledge_command_tree_cleanup(self):
        from nailong.core.process_io import OwnedProcess
        owner=OwnedProcess([sys.executable,'-c','import time;time.sleep(.5)'],cwd=self.root)
        try:
            owner.guard.raw.kill()  # Fault the independent monitor, not its command.
            with self.assertRaisesRegex(RuntimeError,'清理.*核实'):
                owner.process.wait(timeout=2)
            with self.assertRaisesRegex(RuntimeError,'清理.*核实'):owner.close()
        finally:
            # The explicit fault is outside the controller-death guarantee.
            # Its finite user command self-exits; no historical PID is killed.
            time.sleep(.7)

    def test_cleanup_acknowledgement_requires_direct_child_to_be_reaped(self):
        from nailong.core import posix_process
        bootstrap=self.root/'audited-owner.py'
        pid_file=self.root/'direct-child.pid'
        audit_file=self.root/'cleanup-audit.json'
        # Observe the real waitpid state at the public cleanup acknowledgement.
        # The wrapper leaves native process creation and group teardown intact.
        bootstrap.write_text(f'''import json,os,runpy,sys
from pathlib import Path
native_write=os.write
def audited_write(fd,data):
    if fd==int(sys.argv[2]) and json.loads(data).get('type')=='stopping':
        pid=int(Path({str(pid_file)!r}).read_text())
        try:
            reaped,status=os.waitpid(pid,os.WNOHANG)
            state='unreaped' if reaped else 'running'
        except ChildProcessError:
            state='already-reaped'
        Path({str(audit_file)!r}).write_text(json.dumps({{'state':state}}))
    return native_write(fd,data)
os.write=audited_write
runpy.run_path({str(posix_process.CHILD)!r},run_name='__main__')
''')
        command=(f"import os,time;from pathlib import Path;"
            f"Path({str(pid_file)!r}).write_text(str(os.getpid()));time.sleep(10)")
        with patch.object(posix_process,'CHILD',bootstrap):
            owner=posix_process.PosixProcessGuard([sys.executable,'-c',command],cwd=self.root,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            limit=time.monotonic()+3
            while not pid_file.exists() and time.monotonic()<limit:time.sleep(.01)
            self.assertTrue(pid_file.exists(),'test requires a running native child')
            owner.close()
            self.assertEqual(json.loads(audit_file.read_text()),{'state':'already-reaped'})
        finally:
            owner.close()
            owner.raw.stderr.close()

    def test_failed_direct_child_wait_cannot_acknowledge_cleanup(self):
        from nailong.core import posix_process
        bootstrap=self.root/'failed-wait-owner.py'
        pid_file=self.root/'direct-child.pid'
        attempted=self.root/'wait-attempted'
        acknowledgement=self.root/'cleanup-acknowledged'
        bootstrap.write_text(f'''import json,os,runpy,subprocess,sys
from pathlib import Path
native_write=os.write
def audited_write(fd,data):
    if fd==int(sys.argv[2]) and json.loads(data).get('type')=='stopping':
        Path({str(acknowledgement)!r}).touch()
    return native_write(fd,data)
def failed_wait(self,timeout=None):
    Path({str(attempted)!r}).touch()
    raise subprocess.TimeoutExpired(self.args,timeout)
os.write=audited_write
subprocess.Popen.wait=failed_wait
runpy.run_path({str(posix_process.CHILD)!r},run_name='__main__')
''')
        grandchild="from pathlib import Path;import time;time.sleep(.6);Path('wait-failure-grandchild-late').touch()"
        command=(f"import os,subprocess,sys,time;from pathlib import Path;"
            f"subprocess.Popen([sys.executable,'-c',{grandchild!r}]);"
            f"Path({str(pid_file)!r}).write_text(str(os.getpid()));"
            "time.sleep(.6);Path('wait-failure-child-late').touch()")
        with patch.object(posix_process,'CHILD',bootstrap):
            owner=posix_process.PosixProcessGuard([sys.executable,'-c',command],cwd=self.root,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            limit=time.monotonic()+3
            while not pid_file.exists() and time.monotonic()<limit:time.sleep(.01)
            self.assertTrue(pid_file.exists(),'test requires a running native child')
            with self.assertRaisesRegex(RuntimeError,'清理.*核实'):owner.close()
            self.assertTrue(attempted.exists(),'real command wait must be attempted')
            self.assertFalse(acknowledgement.exists(),'failed reaping cannot claim clean shutdown')
            time.sleep(.8)
            self.assertFalse((self.root/'wait-failure-child-late').exists())
            self.assertFalse((self.root/'wait-failure-grandchild-late').exists(),
                'failed direct-child wait must still kill the remaining group')
        finally:
            try:owner.close()
            except RuntimeError:pass
            owner.raw.stderr.close()

    def test_queued_public_stop_cannot_suspend_the_cleanup_owner(self):
        from nailong.core.process_io import OwnedProcess
        owner=OwnedProcess([sys.executable,'-c','import time;time.sleep(3)'],cwd=self.root)
        try:
            owner.process.send_signal(signal.SIGSTOP)
            started=time.monotonic()
            try:owner.close()
            except subprocess.TimeoutExpired:self.fail('queued STOP suspended the monitor before it could clean up')
            self.assertLess(time.monotonic()-started,1)
        finally:
            # Only the still-owned direct child is resumed on the broken code.
            owner.guard.raw.send_signal(signal.SIGCONT)
            owner.close()

    def test_public_stop_followed_by_lifeline_eof_still_reclaims_the_group(self):
        from nailong.core.process_io import OwnedProcess
        owner=OwnedProcess([sys.executable,'-c','import time;time.sleep(3)'],cwd=self.root)
        try:
            owner.process.send_signal(signal.SIGSTOP)
            descriptor,owner.guard.lifeline=owner.guard.lifeline,None
            os.close(descriptor)  # Exact EOF the controller's hard exit causes.
            try:owner.process.wait(timeout=.5)
            except subprocess.TimeoutExpired:self.fail('paused monitor could not consume controller EOF')
        finally:
            owner.guard.raw.send_signal(signal.SIGCONT)
            owner.close()

    def test_global_loop_shutdown_cannot_abandon_eventual_native_creation(self):
        import local_tools
        entered=threading.Event();captures=[]
        native=local_tools._start_command_capture
        def delayed(*args,**kwargs):
            entered.set();time.sleep(.2)
            value=native(*args,**kwargs);captures.append(value[0]);return value
        async def main():
            asyncio.create_task(local_tools.run_command_async(argv=[sys.executable,'-c',
                "import time;from pathlib import Path;time.sleep(.4);Path('loop-shutdown-late').touch();time.sleep(.5)"]))
            self.assertTrue(await asyncio.to_thread(entered.wait,1))
        try:
            with patch.object(local_tools,'_start_command_capture',side_effect=delayed),local_tools.use_project_root(self.root):
                asyncio.run(main())  # Cancels every pending Task, including hidden creation Tasks.
            self.assertEqual(len(captures),1)
            self.assertIsNotNone(captures[0].process.poll(),'loop shutdown lost the newly created command')
            time.sleep(.5)
            self.assertFalse((self.root/'loop-shutdown-late').exists())
        finally:
            for capture in captures:capture.close()

    def test_pipe_reader_start_failure_reclaims_the_already_started_command(self):
        from nailong.core import process_io
        native=process_io.OwnedProcess;owners=[]
        def observed(*args,**kwargs):
            owner=native(*args,**kwargs);owners.append(owner);return owner
        command=[sys.executable,'-c',"import time;from pathlib import Path;time.sleep(.3);Path('reader-failure-late').write_text('leak')"]
        try:
            with patch.object(process_io,'OwnedProcess',side_effect=observed),patch.object(process_io.threading.Thread,'start',side_effect=RuntimeError('reader startup')):
                with self.assertRaisesRegex(RuntimeError,'reader startup'):
                    process_io.PipeCapture(command,cwd=self.root)
            time.sleep(.5)
            self.assertFalse((self.root/'reader-failure-late').exists(),'failed capture constructor left command running')
        finally:
            for owner in owners:
                owner.close()
                for stream in (owner.process.stdin,owner.process.stdout,owner.process.stderr):
                    if stream is not None:stream.close()

    def test_controlling_terminal_input_retains_the_original_session_isolation(self):
        import pty
        import selectors
        master,slave=pty.openpty()
        code="""import os,fcntl,termios,sys
fcntl.ioctl(0,termios.TIOCSCTTY,0)
os.tcsetpgrp(0,os.getpgrp())
from nailong.core.process_io import capture_bytes
output,code,truncated=capture_bytes([sys.executable,'-c','import sys;print(sys.stdin.readline().strip())'],timeout=.5)
print(repr((output,code)),flush=True)
"""
        process=subprocess.Popen([sys.executable,'-B','-c',code],stdin=slave,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,start_new_session=True)
        try:
            os.write(master,b'hello\n')
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout,selectors.EVENT_READ)
                self.assertTrue(selector.select(7),'terminal command did not return a result')
                self.assertEqual(process.stdout.readline(),b"(b'hello\\n', 0)\n")
        finally:
            # Darwin may keep a session leader exiting until its pty closes.
            os.close(master);os.close(slave)
            if process.poll() is None:process.kill()
            process.wait(timeout=3)
            process.stdout.close();process.stderr.close()


@unittest.skipUnless(os.name=='posix','native POSIX owned startup')
class AsyncOwnedStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_native_start_failure_remains_cancellation(self):
        import local_tools
        import nailong.core.processes as processes
        from platform_fixtures import shell_join
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);entered=threading.Event();release=threading.Event()
            native=local_tools._start_command_capture
            def delayed(*args,**kwargs):
                entered.set();release.wait(2);return native(*args,**kwargs)
            with patch.object(local_tools,'_start_command_capture',side_effect=delayed),local_tools.use_project_root(root):
                task=asyncio.create_task(local_tools.run_command_async(argv=[str(root/'missing-executable')]))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait,1))
                    task.cancel();await asyncio.sleep(.01);release.set()
                    with self.assertRaises(asyncio.CancelledError) as caught:await task
                    self.assertIsInstance(caught.exception.__cause__,FileNotFoundError)
                finally:
                    release.set()
                    if not task.done():task.cancel()
                    await asyncio.gather(task,return_exceptions=True)

    async def test_editor_cancel_during_creation_joins_owner_and_removes_private_copy(self):
        import nailong.core.plan as plan
        from platform_fixtures import editor_command
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);entered=threading.Event();release=threading.Event();copies=[]
            native=plan.OwnedProcess
            def delayed(arguments,**kwargs):
                owner=native(arguments,**kwargs);copies.append(Path(arguments[-1]));entered.set();release.wait(2);return owner
            code=f"import time;from pathlib import Path;time.sleep(.4);Path({str(root/'editor-late')!r}).touch()"
            selected=editor_command([sys.executable,'-c',code])
            with patch.object(plan,'OwnedProcess',side_effect=delayed),patch.dict(os.environ,{'VISUAL':selected}):
                task=asyncio.create_task(plan.edit_plan_with_editor_async('private original'))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait,1))
                    self.assertTrue(copies[0].exists())
                    task.cancel();await asyncio.sleep(.01);release.set()
                    with self.assertRaises(asyncio.CancelledError):await task
                    self.assertFalse(copies[0].exists())
                    await asyncio.sleep(.5)
                    self.assertFalse((root/'editor-late').exists())
                finally:
                    release.set()
                    if not task.done():task.cancel()
                    await asyncio.gather(task,return_exceptions=True)

    async def test_slow_creation_stays_off_loop_and_repeated_cancel_joins_the_owned_command(self):
        import local_tools
        import nailong.core.processes as processes
        from nailong.core.processes import ProcessCancelled
        from platform_fixtures import shell_join
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for kind in ('verification','tool'):
                with self.subTest(kind=kind):
                    entered=threading.Event();release=threading.Event()
                    module,attribute=(processes,'PipeCapture') if kind=='verification' else (local_tools,'_start_command_capture')
                    native=getattr(module,attribute)
                    def delayed(*args,**kwargs):
                        value=native(*args,**kwargs);entered.set();release.wait(2);return value
                    code="import time;from pathlib import Path;time.sleep(.4);Path('startup-late').touch()"
                    with patch.object(module,attribute,side_effect=delayed),local_tools.use_project_root(root):
                        start=time.monotonic()
                        coroutine=(processes.execute_process(shell_join([sys.executable,'-c',code]),root)
                            if kind=='verification' else local_tools.run_command_async(argv=[sys.executable,'-c',code]))
                        task=asyncio.create_task(coroutine)
                        try:
                            self.assertTrue(await asyncio.to_thread(entered.wait,1))
                            self.assertLess(time.monotonic()-start,1,'owned creation blocked the event loop')
                            for _ in range(3):task.cancel();await asyncio.sleep(.005)
                            self.assertFalse(task.done(),'cancellation abandoned an in-flight owned constructor')
                            release.set()
                            with self.assertRaises(ProcessCancelled) as caught:await task
                            self.assertTrue(caught.exception.result['cancelled'])
                            self.assertFalse(caught.exception.result['ok'])
                            await asyncio.sleep(.5)
                            self.assertFalse((root/'startup-late').exists())
                        finally:
                            release.set()
                            if not task.done():task.cancel()
                            await asyncio.gather(task,return_exceptions=True)


@unittest.skipUnless(os.name=='nt','native Windows kill-on-close job')
class WindowsControllerDeathTests(unittest.TestCase):
    setUp=ProcessCrashOwnershipTests.setUp
    crash=ProcessCrashOwnershipTests.crash

    def test_os_exit_closes_job_and_stops_started_command_tree(self):self.crash('owned')
