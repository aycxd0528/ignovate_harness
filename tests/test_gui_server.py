import http.client
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class ServerTests(unittest.TestCase):
    def setUp(self):
        from nailong.gui.server import GUIServer
        self.temp = tempfile.TemporaryDirectory()
        # load_settings supports legacy .env; keep its environment side effects
        # out of later CLI onboarding tests and keep all fixture data temporary.
        environment = patch.dict(os.environ)
        environment.start(); self.addCleanup(environment.stop)
        from nailong.core.sessions import ProjectSessionStore
        from nailong.gui.controller import GUIController
        self.server = GUIServer(Path(self.temp.name), port=0, token='test-token', controller_factory=lambda project:
            GUIController(project, sessions=ProjectSessionStore(project, base_dir=Path(self.temp.name)/'data')))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, headers=None):
        self.connection.request('GET', path, headers=headers or {})
        response = self.connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    def test_api_requires_token_and_rejects_untrusted_origin_or_host(self):
        self.assertEqual(self.request('/api/snapshot')[0], 401)
        headers = {'Authorization': 'Bearer test-token'}
        self.assertEqual(self.request('/api/snapshot', headers)[0], 200)
        self.assertEqual(self.request('/api/snapshot', {**headers, 'Origin': 'https://evil.test'})[0], 403)
        self.assertEqual(self.request('/api/snapshot', {**headers, 'Host': 'evil.test'})[0], 403)

    def test_static_page_uses_csp_and_path_traversal_is_rejected(self):
        status, body, headers = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn('script-src', headers['Content-Security-Policy'])
        self.assertNotIn('test-token', body.decode())
        self.assertEqual(self.request('/../../config.py')[0], 404)

    def test_mcp_connect_http_wait_covers_native_120_second_handshake(self):
        import json
        from unittest.mock import Mock
        future = Mock()
        future.result.return_value = {'status': 'connected'}
        def dispatch(coroutine, _loop):
            coroutine.close()
            return future
        payload = {'name': 'plugin', 'payload': {'operation': 'mcp_connect', 'name': 'test',
                   'project_id': self.server.controller.sessions.root.name}}
        with patch('nailong.gui.server.asyncio.run_coroutine_threadsafe', side_effect=dispatch):
            self.connection.request('POST', '/api/command', json.dumps(payload),
                                    {'Authorization': 'Bearer test-token', 'Content-Type': 'application/json'})
            response = self.connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        future.result.assert_called_once_with(timeout=150)


if __name__ == '__main__':
    unittest.main()
