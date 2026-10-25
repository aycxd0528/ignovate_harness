"""Cancellable bounded process execution; command approval is not an OS sandbox."""
from __future__ import annotations
import asyncio
import http.client
import ipaddress
from urllib.parse import urlsplit
from nailong.core.process_io import PipeCapture, command_environment, join_cleanup, prepare_owned

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
    return await _execute_owned(command,cwd,timeout=timeout,
        stdout_contains=stdout_contains,http_url=http_url,expected_status=expected_status)


async def _execute_owned(command, cwd, *, timeout, stdout_contains, http_url, expected_status):
    """All platforms share the owned process boundary and bounded pipe readers."""
    matched, window = False, ''
    def observe(text):
        nonlocal matched, window
        if stdout_contains:
            window = (window + text)[-max(8192, len(stdout_contains)*2):]
            matched = matched or stdout_contains in window
    capture,cancelled = await prepare_owned(PipeCapture,command,cwd=cwd,shell=True,env=command_environment(),
        max_output_chars=MAX_OUTPUT,on_stdout=observe)
    timed_out, observed = False, False
    loop = asyncio.get_running_loop()
    deadline = loop.time()+timeout
    try:
        while not cancelled:
            observed = (await asyncio.to_thread(_http_status, http_url) == expected_status
                        if http_url else matched)
            if (stdout_contains or http_url) and observed:
                break
            if capture.finished:
                observed = observed or matched
                break
            if loop.time() >= deadline:
                timed_out = True
                break
            await asyncio.sleep(.025)
    except asyncio.CancelledError:
        cancelled = True
    finally:
        cleanup = asyncio.create_task(asyncio.to_thread(capture.close))
        cancelled = await join_cleanup(cleanup) or cancelled
    if capture.errors:
        raise capture.errors[0]
    observed = observed or matched
    is_run = stdout_contains is not None or http_url is not None
    result = {'ok': not cancelled and not timed_out and (observed if is_run else capture.process.returncode == 0),
              'started': True, 'exit_code': capture.process.returncode, 'timed_out': timed_out,
              'observed': observed if is_run else None, 'output_truncated': capture.stdout.truncated,
              'cancelled': cancelled, 'output': capture.stdout.value()}
    if cancelled:
        raise ProcessCancelled(result)
    return result
