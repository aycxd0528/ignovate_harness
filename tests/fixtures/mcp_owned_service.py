"""Real SDK service with finite descendants for ownership fault injection."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from mcp.server.fastmcp import FastMCP

server=FastMCP('owned-service-test',log_level='ERROR')


@server.tool()
def spawn(marker:str,seconds:float=1.0)->dict:
    source=f"import time;from pathlib import Path;time.sleep({seconds!r});Path({marker!r}).write_text('unowned service descendant')"
    child=subprocess.Popen([sys.executable,'-B','-c',source],stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return {'pid':os.getpid(),'parent':os.getppid(),'child':child.pid}


@server.tool()
def echo(payload:dict)->dict:return payload


@server.tool()
async def hold(started:str,release:str,completed:str)->str:
    Path(started).touch()
    while not Path(release).exists():await asyncio.sleep(.01)
    Path(completed).touch()
    return 'done'


@server.tool()
def facts(path:str)->str:
    ports=[]
    if os.name=='posix':
        import socket
        for descriptor in range(128):
            try:
                with socket.fromfd(descriptor,socket.AF_INET,socket.SOCK_STREAM) as candidate:
                    for address in (candidate.getsockname(),candidate.getpeername()):
                        if isinstance(address,tuple) and len(address)>1:ports.append(address[1])
            except OSError:pass
    Path(path).write_text(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),
        'socket_ports':ports,
        'token':os.environ.get('MCP_TEST_TOKEN'),'model_key_present':'DEEPSEEK_API_KEY' in os.environ,
        'pid':os.getpid(),'parent':os.getppid()}))
    return 'written'


@server.tool()
def destroy_owner(marker:str)->str:
    # Only this finite fault fixture targets its still-live own group leader.
    # C keeps that group ID allocated throughout this operation.
    import signal
    import threading
    import time
    parent=os.getppid()
    assert parent>1 and parent==os.getpgrp()==os.getsid(0)
    def finish():
        time.sleep(3);os._exit(0)
    threading.Thread(target=finish,daemon=True).start()
    os.kill(parent,signal.SIGKILL)
    Path(marker).touch()
    return 'injected inner-owner death'


server.run()
