"""Cancellable bounded process execution; command approval is not an OS sandbox."""
from __future__ import annotations
import asyncio
import codecs
import http.client
import ipaddress
import os
import signal
from urllib.parse import urlsplit
from nailong.tools.results import HeadTailBuffer

MAX_OUTPUT = 12_000


class ProcessCancelled(asyncio.CancelledError):
    def __init__(self,result):
        super().__init__('进程已取消并清理。')
        self.result=result


def probe_address(url: str):
    parsed = urlsplit(url)
    try:
        address = ipaddress.ip_address(parsed.hostname or '')
        port = parsed.port or 80
    except ValueError as error:
        raise ValueError('启动探测必须使用有效的回环 IP 地址。') from error
    if (parsed.scheme != 'http' or not address.is_loopback or parsed.username is not None
            or parsed.password is not None or parsed.fragment or not 1 <= port <= 65535):
        raise ValueError('启动探测仅支持不含认证的 HTTP 回环 IP 地址。')
    return parsed.hostname, port, (parsed.path or '/') + ('?' + parsed.query if parsed.query else '')


def _http_status(url):
    host, port, path = probe_address(url)
    connection = http.client.HTTPConnection(host, port, timeout=.25)
    try:
        connection.request('GET', path)
        return connection.getresponse().status
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()


async def execute_process(command, cwd, *, timeout=30, stdout_contains=None,
                          http_url=None, expected_status=200):
    if http_url:
        host, port, _ = probe_address(http_url)
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), .3)
        except (OSError, asyncio.TimeoutError):
            pass
        else:
            writer.close()
            await writer.wait_closed()
            return {'ok':False,'exit_code':None,'output':'探测端口在启动前已被占用。',
                    'timed_out':False,'observed':False,'started':False}
    process = await asyncio.create_subprocess_shell(command, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        env={key:value for key,value in os.environ.items() if key!='DEEPSEEK_API_KEY'})
    output = HeadTailBuffer(MAX_OUTPUT)
    matched = False
    window = ''
    async def consume():
        nonlocal matched, window
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        while True:
            chunk = await process.stdout.read(4096)
            text = decoder.decode(chunk, final=not chunk)
            if stdout_contains:
                window = (window + text)[-max(8192, len(stdout_contains)*2):]
                matched = matched or stdout_contains in window
            output.append(text)
            if not chunk: break
    pump = asyncio.create_task(consume())
    timed_out = False
    observed = False
    loop = asyncio.get_running_loop()
    deadline = loop.time()+timeout
    cancelled=False
    async def cleanup():
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try:
            await asyncio.wait_for(process.wait(), .5)
            await asyncio.wait_for(asyncio.shield(pump), .2)
        except asyncio.TimeoutError:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            await process.wait()
            try: await asyncio.wait_for(asyncio.shield(pump), .5)
            except asyncio.TimeoutError: pump.cancel()
        if not pump.done(): pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    try:
        while True:
            if http_url:
                observed = await asyncio.to_thread(_http_status, http_url) == expected_status
            else:
                observed = matched
            if (stdout_contains or http_url) and observed: break
            if process.returncode is not None:
                await asyncio.sleep(0)  # Let the output pump observe the last bytes.
                observed = observed or matched
                break
            if loop.time() >= deadline:
                timed_out = True
                break
            await asyncio.sleep(.025)
    except asyncio.CancelledError:
        cancelled=True
    finally:
        # Join owned cleanup even when repeated Ctrl+C cancels this task again.
        cleanup_task=asyncio.create_task(cleanup())
        while not cleanup_task.done():
            try: await asyncio.shield(cleanup_task)
            except asyncio.CancelledError: cancelled=True
        cleanup_task.result()
    observed = observed or matched
    is_run = stdout_contains is not None or http_url is not None
    ok = not timed_out and (observed if is_run else process.returncode == 0)
    result={'ok':ok and not cancelled,'started':True,'exit_code':process.returncode,'timed_out':timed_out,
            'observed':observed if is_run else None,
            'output_truncated':output.truncated,'cancelled':cancelled,
            'output':output.value()}
    if cancelled: raise ProcessCancelled(result)
    return result
