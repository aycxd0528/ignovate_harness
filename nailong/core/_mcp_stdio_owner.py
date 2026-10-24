"""SDK stdio launcher; isolated stdlib code with an independent pipe owner."""
import os
from pathlib import Path
import runpy
import signal
import socket
import sys
import threading


def run(parent,arguments,channel=None):
    # The SDK gives its direct process a new session. Never run a service with
    # invalid ownership, or signal a group inherited from the application.
    if (os.name!='posix' or parent<1 or os.getppid()!=parent
            or os.getpgrp()!=os.getpid() or os.getsid(0)!=os.getpid()):return 125
    stopped=threading.Event();guard=None;pending=None
    def watch_parent():
        try:
            while not stopped.wait(.02):
                if os.getppid()!=parent:os._exit(125)
        except BaseException:
            os._exit(125)
    watcher=threading.Thread(target=watch_parent,daemon=True)
    try:
        # Start before the potentially blocking process creation. If this outer
        # process exits anywhere below, the OS closes the inner owner's lifeline.
        watcher.start()
        if channel is not None:
            channel[0].sendall(channel[1])
            if channel[0].recv(1)!=b'G':return 125
        namespace=runpy.run_path(str(Path(__file__).with_name('posix_process.py')))
        if os.getppid()!=parent:return 125
        guard=namespace['PosixProcessGuard'](arguments,release_stdio=True)
        # Neither monitor consumes the protocol. Keeping a spare read/write end
        # open would hide the actual server closing stdin/stdout from the SDK.
        for descriptor in (0,1,2):
            try:os.close(descriptor)
            except OSError:pass
        def request(number,frame):
            nonlocal pending
            pending=number
        # Install after constructing the guard, so the actual server retains
        # the SDK's original inherited signal dispositions.
        for number in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(number,request)
        while True:
            if pending is not None:
                number,pending=pending,None
                guard.process.send_signal(number)
            code=guard.process.poll()
            if code is not None:return code
            stopped.wait(.02)
    finally:
        try:
            if guard is not None:guard.close()
        finally:
            stopped.set()
            if watcher.ident is not None:watcher.join(timeout=1)
        # This receipt follows verified inner cleanup. Exceptions skip it.
        if channel is not None:channel[0].sendall(b'C')


if __name__=='__main__':
    channel=None
    try:
        arguments=sys.argv[2:]
        if arguments and arguments[0]=='--owner-channel-v1':
            # Loopback only; a fresh per-connection token admits the launcher.
            channel=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            channel.settimeout(5)
            channel.connect(('127.0.0.1',int(arguments[1])))
            token=bytes.fromhex(arguments[2])
            if len(token)!=32:raise ValueError('Invalid ownership token.')
            arguments=arguments[3:]
            code=run(int(sys.argv[1]),arguments,(channel,token))
        else:
            code=run(int(sys.argv[1]),arguments)
    except Exception:code=127
    finally:
        if channel is not None:channel.close()
    if code<0:
        number=-code
        try:signal.signal(number,signal.SIG_DFL)
        except (OSError,ValueError):pass
        os.kill(os.getpid(),number)
    os._exit(code)
