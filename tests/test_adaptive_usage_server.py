"""New UI assets must be served by the real server, not only browser mocks."""
import http.client
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class AdaptiveUsageServerTests(unittest.TestCase):
    def test_real_gui_serves_usage_details_module_with_javascript_mime(self):
        from nailong.gui.controller import GUIController
        from nailong.gui.server import GUIServer
        from nailong.core.sessions import ProjectSessionStore
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            root=Path(directory)
            server=GUIServer(root,port=0,token='fixture-token',controller_factory=lambda project:
                GUIController(project,sessions=ProjectSessionStore(project,base_dir=root/'data')))
            worker=threading.Thread(target=server.serve_forever,daemon=True)
            worker.start()
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                connection.request('GET','/usage-details.js')
                result=connection.getresponse()
                body=result.read()
                self.assertEqual(result.status,200)
                self.assertIn('text/javascript',result.getheader('Content-Type'))
                self.assertIn(b'export function renderUsageDetails',body)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)
