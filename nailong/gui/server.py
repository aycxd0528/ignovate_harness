"""Loopback HTTP transport with explicit per-launch authority."""
from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from nailong.gui.controller import GUIController


class GUIServer(ThreadingHTTPServer):
    daemon_threads = True
    # A browser refresh opens styles, scripts and API connections in one burst.
    request_queue_size = 64

    def __init__(self, project, *, port=0, token=None, controller_factory=GUIController):
        self.token = token or secrets.token_urlsafe(32)
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()
        failure = []
        def run():
            asyncio.set_event_loop(self.loop)
            try:
                self.controller = controller_factory(project)
            except Exception as error:
                failure.append(error)
                ready.set()
                return
            ready.set()
            self.loop.run_forever()
            self.loop.close()
        self.loop_thread = threading.Thread(target=run, daemon=True)
        self.loop_thread.start()
        ready.wait(10)
        if failure:
            raise failure[0]
        if not ready.is_set():
            raise RuntimeError('本地内核初始化超时。')
        super().__init__(('127.0.0.1', port), GUIHandler)
        self.origin = f'http://127.0.0.1:{self.server_port}'

    @property
    def url(self):
        return self.origin + '/#token=' + self.token

    def call(self, function, *args, timeout=60):
        async def invoke():
            value = function(*args)
            if hasattr(value, '__await__'):
                return await value
            return value
        return asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(timeout=timeout)

    def server_close(self):
        super().server_close()
        if self.loop.is_running():
            try:
                self.call(self.controller.close)
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.loop_thread.join(5)


class GUIHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # Never log token-bearing URLs, prompts, or configuration payloads.

    def respond(self, status, value, *, content_type='application/json; charset=utf-8'):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, *, api=False):
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
            self.respond(403, {'error': '连接主机无效。'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin != self.server.origin:
            self.respond(403, {'error': '连接来源无效。'})
            return False
        if api and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+self.server.token):
            self.respond(401, {'error': '缺少本次 GUI 会话凭据，请从启动器重新打开。'})
            return False
        return True

    def do_GET(self):
        url = urlsplit(self.path)
        api = url.path.startswith('/api/')
        if not self.authorized(api=api):
            return
        try:
            if url.path == '/api/snapshot':
                self.respond(200, self.server.call(self.server.controller.snapshot))
            elif url.path == '/api/events':
                after = int(parse_qs(url.query).get('after', ['0'])[0])
                self.respond(200, self.server.call(self.server.controller.events, after))
            elif not api:
                name = 'index.html' if url.path == '/' else url.path.removeprefix('/')
                types = {'index.html': 'text/html', 'app.js': 'text/javascript', 'map.js': 'text/javascript', 'map-control.js': 'text/javascript',
                         'style.css': 'text/css', 'settings.css': 'text/css', 'settings.js': 'text/javascript',
                         'trace.css': 'text/css', 'trace.js': 'text/javascript',
                         'react-bits.css': 'text/css', 'react-bits.js': 'text/javascript',
                         'react-bits-islands.css': 'text/css', 'react-bits-islands.js': 'text/javascript',
                         'preferences.js': 'text/javascript', 'rendering.js': 'text/javascript', 'startup.js': 'text/javascript', 'motion.css': 'text/css', 'ui.js': 'text/javascript', 'icons.js': 'text/javascript',
                         'markdown-engine.js': 'text/javascript','conversation.js':'text/javascript','plugins.js':'text/javascript','reasoning.js':'text/javascript','sidebar.js':'text/javascript','effort-fire.js':'text/javascript','effort-shaders.js':'text/javascript','model-picker.js':'text/javascript','diff.js':'text/javascript',
                         'usage-details.js':'text/javascript'}
                if name not in types:
                    self.respond(404, {'error': '资源不存在。'})
                else:
                    body = (Path(__file__).parent / 'static' / name).read_bytes()
                    self.respond(200, body, content_type=types[name]+'; charset=utf-8')
            else:
                self.respond(404, {'error': '接口不存在。'})
        except (ValueError, KeyError, OSError) as error:
            self.respond(400, {'error': self.server.controller.safe(str(error))})
        except Exception as error:
            self.respond(500, {'error': self.server.controller.safe(str(error))})

    def do_POST(self):
        if not self.authorized(api=True):
            return
        if self.path != '/api/command':
            self.respond(404, {'error': '接口不存在。'})
            return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('请求须为 JSON。')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1_048_576:
                raise ValueError('请求大小无效。')
            value = json.loads(self.rfile.read(size))
            if not isinstance(value, dict) or not isinstance(value.get('name'), str):
                raise ValueError('命令格式无效。')
            payload = value.get('payload', {})
            if not isinstance(payload, dict) or payload.get('project_id') != self.server.controller.sessions.root.name:
                raise ValueError('项目已切换，请重新加载后再操作。')
            # Native MCP permits a 120-second handshake. Keep the HTTP request
            # open through that bounded operation and its cleanup, so a valid
            # connection cannot appear as failed while still mutating state.
            timeout = 150 if value['name'] == 'plugin' and payload.get('operation') == 'mcp_connect' else 60
            result = self.server.call(self.server.controller.command, value['name'], payload, timeout=timeout)
            self.respond(200, result)
        except (ValueError, KeyError, OSError, TypeError) as error:
            self.respond(400, {'error': self.server.controller.safe(str(error))})
        except Exception as error:
            self.respond(500, {'error': self.server.controller.safe(str(error))})
