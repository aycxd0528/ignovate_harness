"""A private lifeline owns a separate command session beyond controller death."""
import errno
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time


CHILD=Path(__file__).with_name('_posix_process_child.py').resolve()


class _OwnedPopen:
    """Popen stdio/owner PID with the actual user's signed command exit status."""
    def __init__(self,guard):self._guard=guard

    def __getattr__(self,name):return getattr(self._guard.raw,name)

    @property
    def returncode(self):
        code=self._guard.raw.returncode
        return self._guard.result(code) if code is not None else None

    def poll(self):
        code=self._guard.raw.poll()
        return self._guard.result(code) if code is not None else None

    def wait(self,timeout=None):return self._guard.result(self._guard.raw.wait(timeout=timeout))

    def kill(self):self._guard.close()

    def terminate(self):self._guard.terminate(force=False)

    def send_signal(self,number):self._guard.send_signal(number)


class PosixProcessGuard:
    def __init__(self,command,*,cwd=None,env=None,shell=False,release_stdio=False,keepalive_fds=(),**kwargs):
        self.raw=None;self.process=None;self.lifeline=None;self.status=None
        self._lock=threading.RLock();self._buffer=bytearray();self._result=None
        self._admitted=False;self._result_error=None
        descriptors=[]
        try:
            life_read,life_write=os.pipe();descriptors.extend((life_read,life_write))
            status_read,status_write=os.pipe();descriptors.extend((status_read,status_write))
            self.lifeline=life_write;descriptors.remove(life_write)
            self.status=status_read;descriptors.remove(status_read)
            arguments=[command] if isinstance(command,(str,bytes,os.PathLike)) else list(command)
            arguments=list(map(os.fsdecode,arguments))
            if not arguments:raise ValueError('Command arguments cannot be empty.')
            ignored=[]
            for name in ('SIGTERM','SIGINT','SIGHUP','SIGPIPE','SIGXFZ','SIGXFSZ'):
                number=getattr(signal,name,None)
                if number is not None and signal.getsignal(number)==signal.SIG_IGN:ignored.append(str(number))
            policy=','.join(ignored)
            if release_stdio:policy='stdio-v1:'+policy
            bootstrap=[sys.executable,'-I','-S',str(CHILD),str(life_read),str(status_write),
                '1' if shell else '0','1' if kwargs.get('restore_signals',True) else '0',policy,*arguments]
            self.raw=subprocess.Popen(bootstrap,cwd=cwd,env=env,start_new_session=True,
                # Private liveness handles survive the outer launcher's death
                # until this guardian exits. Its user child closes extra fds.
                pass_fds=(life_read,status_write,*keepalive_fds),**kwargs)
            self.raw.args=command;self.process=_OwnedPopen(self)
            for descriptor in (life_read,status_write):
                os.close(descriptor);descriptors.remove(descriptor)
            os.write(self.lifeline,b'G')
            message=self._message(deadline=time.monotonic()+5)
            if message.get('type')=='error':
                number=message.get('errno',errno.EIO)
                raise OSError(number,os.strerror(number),message.get('filename'))
            if message!={'type':'started'}:raise OSError('Owned command bootstrap ended before execution.')
            self._admitted=True
        except BaseException:
            try:self.close()
            finally:
                if self.raw is not None:
                    for stream in (self.raw.stdin,self.raw.stdout,self.raw.stderr):
                        if stream is not None:stream.close()
            raise
        finally:
            for descriptor in descriptors:os.close(descriptor)

    def _message(self,*,deadline=None):
        while b'\n' not in self._buffer:
            if self.status is None:return {}
            if deadline is not None:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Owned command startup did not complete.')
                with selectors.DefaultSelector() as selector:
                    selector.register(self.status,selectors.EVENT_READ)
                    if not selector.select(remaining):continue
            chunk=os.read(self.status,8192)
            if not chunk:return {}
            self._buffer.extend(chunk)
            if len(self._buffer)>16384:raise OSError('Invalid owned command response.')
        raw,_,tail=self._buffer.partition(b'\n');self._buffer=bytearray(tail)
        try:message=json.loads(raw)
        except ValueError:raise OSError('Invalid owned command response.') from None
        if not isinstance(message,dict):raise OSError('Invalid owned command response.')
        return message

    def result(self,code):
        with self._lock:
            if self._result_error is not None:raise self._result_error
            if self._result is None:
                try:
                    message=self._message()
                    value=message.get('returncode')
                    stopped=self._message() if message.get('type')=='exit' else message
                    if self._admitted and stopped!={'type':'stopping'}:
                        self._result_error=RuntimeError('命令监护进程异常结束；进程树清理无法核实。')
                        raise self._result_error
                    self._result=value if message.get('type')=='exit' and type(value) is int else code
                finally:
                    if self.status is not None:
                        descriptor,self.status=self.status,None;os.close(descriptor)
            return self._result

    def terminate(self,*,force=True):
        if force:return self.close()
        self.send_signal(signal.SIGTERM)

    def send_signal(self,number):
        if number==signal.SIGKILL:return self.close()
        if number not in signal.valid_signals() and number!=0:raise ValueError('Invalid process signal.')
        with self._lock:
            if number in (0,signal.SIGCONT):self.raw.send_signal(number)
            if self.lifeline is not None:
                try:os.write(self.lifeline,bytes((number,)))
                except BrokenPipeError:pass

    def close(self):
        with self._lock:
            if self.lifeline is not None:
                if self.raw is not None:self.raw.send_signal(signal.SIGCONT)
                descriptor,self.lifeline=self.lifeline,None;os.close(descriptor)
            if self.raw is not None:
                # The live owner signals its own group. Never send a signal to
                # an old group ID after Popen has reaped the group's leader.
                code=self.raw.wait(timeout=5)
                self.result(code)
            elif self.status is not None:
                descriptor,self.status=self.status,None;os.close(descriptor)
