"""Isolated stdlib-only process owner. Run by absolute filename with -I -S."""
import errno
import json
import os
import select
import signal
import subprocess
import sys


def notify(descriptor, **message):
    data=json.dumps(message,ensure_ascii=True).encode()+b'\n'
    os.write(descriptor,data)


def own(lifeline,status,shell,restore_signals,ignored,release_stdio,arguments):
    # start_new_session is established by Popen, before this script runs.
    # Never signal an inherited group if the bootstrap is invoked incorrectly.
    if os.getpgrp()!=os.getpid() or os.getsid(0)!=os.getpid():os._exit(125)
    os.set_inheritable(lifeline,False);os.set_inheritable(status,False)
    try:
        for number in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
            signal.signal(number,signal.SIG_IGN)
        if os.read(lifeline,1)!=b'G':return
        if select.select([lifeline],[],[],0)[0] and not os.read(lifeline,1):return
        def child_signals():
            # Only this isolated single-threaded owner uses preexec_fn. The
            # controller never runs Python code after fork from worker threads.
            for name in ('SIGTERM','SIGINT','SIGHUP','SIGPIPE','SIGXFZ','SIGXFSZ'):
                number=getattr(signal,name,None)
                if number is not None:
                    chosen=signal.SIG_IGN if number in ignored else signal.SIG_DFL
                    if restore_signals and name in ('SIGPIPE','SIGXFZ','SIGXFSZ'):chosen=signal.SIG_DFL
                    signal.signal(number,chosen)
        try:
            command=arguments if not shell else (arguments[0] if len(arguments)==1 else arguments)
            child=subprocess.Popen(command,shell=shell,preexec_fn=child_signals,
                restore_signals=restore_signals)
        except OSError as error:
            notify(status,type='error',errno=error.errno or errno.EIO,filename=error.filename)
            return
        if release_stdio:
            for descriptor in (0,1,2):
                if descriptor not in (lifeline,status):
                    try:os.close(descriptor)
                    except OSError:pass
        notify(status,type='started')
        while True:
            if select.select([lifeline],[],[],.02)[0]:
                request=os.read(lifeline,32)
                if not request:return
                for number in request:
                    if not number:continue
                    if number==signal.SIGKILL:return
                    if number==signal.SIGSTOP:
                        # Popen.send_signal pauses the user process. Its
                        # independent cleanup owner must remain runnable.
                        child.send_signal(number)
                        continue
                    # Only the already-running user command gets the
                    # signal's original disposition. Keep its owner alive.
                    handler=(lambda *args:None) if number==signal.SIGCHLD else signal.SIG_IGN
                    signal.signal(number,handler)
                    os.killpg(os.getpgrp(),number)
            code=child.poll()
            if code is not None:
                notify(status,type='exit',returncode=code)
                return
    finally:
        try:notify(status,type='stopping')
        except OSError:pass
        # The live owner is the group leader, so this ID cannot be recycled.
        # Closing the parent's pipe is enough even after SIGKILL/os._exit there.
        os.killpg(os.getpgrp(),signal.SIGKILL)


if __name__=='__main__':
    policy=sys.argv[5]
    own(int(sys.argv[1]),int(sys.argv[2]),sys.argv[3]=='1',sys.argv[4]=='1',
        set(map(int,filter(None,policy.removeprefix('stdio-v1:').split(',')))),
        policy.startswith('stdio-v1:'),sys.argv[6:])
