"""Native GUI storage and loop ownership under startup/shutdown faults."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nailong.core.branches import BranchStore
from nailong.core.sessions import ProjectSessionStore
from nailong.gui.controller import GUIController


class GUIStorageLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.sessions = ProjectSessionStore(self.root, base_dir=self.root / 'state')

    def test_layout_open_failure_closes_first_native_database(self):
        native_connect, connections = sqlite3.connect, []

        def connect(*args, **kwargs):
            if connections:
                raise OSError('layout database open failed')
            connection = native_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        failure = None
        try:
            with patch('sqlite3.connect', side_effect=connect):
                try:
                    BranchStore(self.sessions)
                except OSError as error:
                    failure = error
            self.assertIsInstance(failure, OSError)
            with self.assertRaises(sqlite3.ProgrammingError):
                connections[0].execute('SELECT 1')
        finally:
            for connection in connections:
                connection.close()

    def test_first_database_close_error_still_closes_layout_database(self):
        class Connection(sqlite3.Connection):
            attempted = False

            def close(self):
                if not self.attempted:
                    self.attempted = True
                    raise OSError('branch database close failed')
                super().close()

        native_connect, connections = sqlite3.connect, []

        def connect(*args, **kwargs):
            if not connections:
                kwargs['factory'] = Connection
            connection = native_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with patch('sqlite3.connect', side_effect=connect):
            branches = BranchStore(self.sessions)
        try:
            with self.assertRaisesRegex(OSError, 'branch database close failed'):
                branches.close()
            with self.assertRaises(sqlite3.ProgrammingError):
                branches.view.execute('SELECT 1')
        finally:
            branches.close()

    def test_controller_recovery_failure_closes_both_native_databases(self):
        native_connect, connections = sqlite3.connect, []

        def connect(*args, **kwargs):
            connection = native_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        failure = None
        try:
            with patch('sqlite3.connect', side_effect=connect), \
                    patch.object(GUIController, '_recover_runs', side_effect=OSError('bad recovery')):
                try:
                    GUIController(self.root, sessions=self.sessions)
                except OSError as error:
                    failure = error
            self.assertIsInstance(failure, OSError)
            self.assertEqual(len(connections), 2)
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute('SELECT 1')
        finally:
            for connection in connections:
                connection.close()


SERVER_PROBE = r'''
import asyncio, json, sys, tempfile, threading, time
from pathlib import Path
from unittest.mock import patch
from nailong.gui.server import GUIServer
from nailong.gui.controller import GUIController
from nailong.core.sessions import ProjectSessionStore
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    factory = lambda project: GUIController(project, sessions=ProjectSessionStore(project, base_dir=root/'state'))
    if sys.argv[1] == 'bind':
        server = GUIServer.__new__(GUIServer)
        try:
            with patch('http.server.ThreadingHTTPServer.__init__', side_effect=OSError('bind failed')):
                server.__init__(root, controller_factory=factory)
        except OSError:
            pass
        server.loop_thread.join(.2)
        print(json.dumps({'thread_closed': not server.loop_thread.is_alive(), 'loop_closed': server.loop.is_closed()}))
    elif sys.argv[1] == 'retry':
        server = GUIServer(root, controller_factory=factory)
        native_close, attempts = server.controller.close, []
        async def close():
            attempts.append('close')
            if len(attempts) == 1: raise OSError('transient controller close failure')
            await native_close()
        server.controller.close = close
        try: server.server_close()
        except OSError: pass
        retained = server.loop.is_running()
        server.server_close()
        print(json.dumps({'retained': retained, 'attempts': len(attempts), 'loop_closed': server.loop.is_closed()}))
    elif sys.argv[1] == 'scan':
        from nailong.core.verification import _owned_worker
        from nailong.core.input_snapshot import active_snapshot_cancellation
        server = GUIServer(root, controller_factory=factory)
        entered, released, cancelled, cleaned = (threading.Event() for _ in range(4))
        def scan():
            cancellation = active_snapshot_cancellation.get()
            entered.set()
            while not released.is_set() and not cancellation.wait(.005): pass
            if cancellation.is_set(): cancelled.set()
        async def observe():
            try: await _owned_worker(scan)
            finally: cleaned.set()
        def request():
            try: server.call(observe)
            except BaseException: pass
        caller = threading.Thread(target=request, daemon=True)
        caller.start()
        if not entered.wait(2): raise RuntimeError('scan did not start')
        server.server_close()
        result = {'cancelled': cancelled.is_set(), 'cleaned': cleaned.is_set(), 'loop_closed': server.loop.is_closed()}
        released.set()
        caller.join(.2)
        print(json.dumps(result))
    else:
        server = GUIServer(root, controller_factory=factory)
        entered, release, cleaned = threading.Event(), threading.Event(), threading.Event()
        native_close = server.controller.close
        async def close():
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.005)
            await native_close()
            cleaned.set()
        server.controller.close = close
        native_call = server.call
        def short_default(function, *args, timeout=.03):
            return native_call(function, *args, timeout=timeout)
        server.call = short_default
        errors = []
        def closing():
            try: server.server_close()
            except BaseException as error: errors.append(type(error).__name__)
        thread = threading.Thread(target=closing)
        thread.start()
        if not entered.wait(2): raise RuntimeError('close was never entered')
        time.sleep(.1)
        premature = not thread.is_alive()
        running_during_cleanup = server.loop.is_running()
        release.set()
        thread.join(2)
        print(json.dumps({'premature': premature, 'running_during_cleanup': running_during_cleanup,
            'cleaned': cleaned.is_set(), 'thread_closed': not thread.is_alive(),
            'loop_closed': server.loop.is_closed(), 'errors': errors}))
'''


class GUIServerLifecycleTests(unittest.TestCase):
    def probe(self, mode):
        result = subprocess.run([sys.executable, '-c', SERVER_PROBE, mode],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_shutdown_keeps_loop_until_controller_cleanup_finishes(self):
        result = self.probe('close')
        self.assertFalse(result['premature'], result)
        self.assertTrue(result['running_during_cleanup'], result)
        self.assertTrue(result['cleaned'], result)
        self.assertTrue(result['thread_closed'], result)
        self.assertTrue(result['loop_closed'], result)
        self.assertEqual(result['errors'], [], result)

    def test_http_bind_failure_reclaims_controller_and_loop(self):
        result = self.probe('bind')
        self.assertTrue(result['thread_closed'], result)
        self.assertTrue(result['loop_closed'], result)

    def test_shutdown_joins_active_http_scan_before_stopping_loop(self):
        result = self.probe('scan')
        self.assertTrue(result['cancelled'], result)
        self.assertTrue(result['cleaned'], result)
        self.assertTrue(result['loop_closed'], result)

    def test_failed_shutdown_retains_loop_for_resource_retry(self):
        result = self.probe('retry')
        self.assertTrue(result['retained'], result)
        self.assertEqual(result['attempts'], 2, result)
        self.assertTrue(result['loop_closed'], result)
