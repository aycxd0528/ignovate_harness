"""Windows consoles must select the same welcome and composer as macOS."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nailong.core.diagnostics import terminal_capabilities


class WindowsTerminalSelectionTests(unittest.TestCase):
    def capabilities(self, environment, *, tty=True, platform='win32'):
        stream = SimpleNamespace(isatty=lambda: tty)
        with patch.object(sys, 'stdin', stream), patch.object(sys, 'stdout', stream), \
                patch.object(sys, 'platform', platform), patch.dict(os.environ, environment, clear=True):
            return terminal_capabilities()

    def test_windows_terminal_and_classic_console_do_not_require_posix_term(self):
        for environment in ({'WT_SESSION': 'fixture-session'}, {}):
            with self.subTest(environment=environment):
                capabilities = self.capabilities(environment)
                self.assertTrue(capabilities['tty'])
                self.assertTrue(capabilities['fullscreen'], capabilities)

    def test_windows_ide_redirected_io_and_explicit_dumb_terminal_keep_fallbacks(self):
        for environment, tty in (({'PYCHARM_HOSTED': '1'}, True),
                                 ({'WT_SESSION': 'fixture-session'}, False),
                                 ({'TERM': 'dumb'}, True)):
            with self.subTest(environment=environment, tty=tty):
                self.assertFalse(self.capabilities(environment, tty=tty)['fullscreen'])

    def test_unknown_posix_terminal_still_keeps_inline_fallback(self):
        capabilities = self.capabilities({}, platform='linux')
        self.assertTrue(capabilities['inline'])
        self.assertFalse(capabilities['fullscreen'])


@unittest.skipUnless(os.name == 'nt', 'Requires the actual Windows Textual console driver.')
class NativeWindowsVisualTests(unittest.TestCase):
    def test_real_console_without_term_renders_outlined_welcome_and_colored_weather(self):
        # This console belongs only to the fixture, never the developer's terminal.
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        with tempfile.TemporaryDirectory(prefix='ignovate-native-ui-') as directory:
            report = Path(directory)/'report.json'
            environment = {key: value for key, value in os.environ.items()
                           if key not in {'NO_COLOR', 'LC_ALL', 'PYCHARM_HOSTED', 'WT_SESSION'}}
            environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                               HOME=directory, USERPROFILE=directory,
                               IGNOVATE_CONFIG_DIR=str(Path(directory)/'config'),
                               NAILONG_DATA_DIR=str(Path(directory)/'data'))
            result = subprocess.run([sys.executable, '-c', _NATIVE_UI_PROBE, directory],
                capture_output=True, text=True, encoding='utf-8', timeout=30,
                creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
                env=environment)
            payload = json.loads(report.read_text()) if report.exists() else {}
            self.assertEqual(result.returncode, 0, (payload, result.stderr))
            self.assertTrue(payload['capabilities']['fullscreen'], payload)
            self.assertEqual(payload['welcome']['driver'], 'WindowsDriver')
            self.assertIn('╔', payload['welcome']['logo'])
            self.assertIn('║', payload['welcome']['logo'])
            self.assertIn('═', payload['welcome']['logo'])
            self.assertEqual(payload['composer']['driver'], 'WindowsDriver')
            self.assertIn('Clear 2%', payload['composer']['weather'])
            self.assertTrue(payload['composer']['colored'])
            self.assertTrue(payload['composer']['aligned'], payload['composer'])
            self.assertTrue(payload['console_modes_restored'], payload)


_NATIVE_UI_PROBE = r'''
import ctypes,json,os,sys,traceback
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
import msvcrt
root=Path(sys.argv[1]); report=root/'report.json'; payload={}
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.SetStdHandle.argtypes=[wintypes.DWORD,wintypes.HANDLE]
k.SetStdHandle.restype=wintypes.BOOL
k.GetConsoleMode.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
k.GetConsoleMode.restype=wintypes.BOOL
def mode(stream):
    value=wintypes.DWORD()
    if not k.GetConsoleMode(msvcrt.get_osfhandle(stream.fileno()),ctypes.byref(value)):
        raise ctypes.WinError(ctypes.get_last_error())
    return value.value
try:
    os.environ.pop('TERM',None); os.environ.pop('TERM_PROGRAM',None)
    os.environ.pop('PYCHARM_HOSTED',None)
    sys.stdin=sys.__stdin__=open('CONIN$','r',encoding='utf-8')
    sys.stdout=sys.__stdout__=open('CONOUT$','w',encoding='utf-8',buffering=1)
    sys.stderr=sys.__stderr__=sys.stdout
    for number,stream in ((-10,sys.stdin),(-11,sys.stdout),(-12,sys.stderr)):
        if not k.SetStdHandle(number,msvcrt.get_osfhandle(stream.fileno())):
            raise ctypes.WinError(ctypes.get_last_error())
    from nailong.core.diagnostics import terminal_capabilities
    payload['capabilities']=terminal_capabilities()
    assert payload['capabilities']['fullscreen'],payload
    before=(mode(sys.stdin),mode(sys.stdout))
    from ui.setup import SetupApp
    from nailong.core.bootstrap import BootstrapStore
    class WelcomeProbe(SetupApp):
        def on_mount(self):
            # Textual dispatches base-class mount handlers itself.
            self.call_after_refresh(self.measure)
        def measure(self):
            payload['welcome']={'driver':type(self._driver).__name__,
                'logo':self.query_one('#setup-logo').render().plain}
            self.exit(None)
    WelcomeProbe(store=BootstrapStore(root/'config'),project_root=root).run(size=(100,32))
    from tui import TerminalAgentApp
    from config import Settings
    from agent_service import TurnEvent
    class ComposerProbe(TerminalAgentApp):
        def on_mount(self):
            self.call_after_refresh(self.seed_usage)
        def seed_usage(self):
            self._append_user('first')
            self._observe_usage(TurnEvent('usage',{'input_tokens':11200,'output_tokens':10}))
            self._append_user('second')
            self._observe_usage(TurnEvent('usage',{'input_tokens':20300,'output_tokens':10}))
            self.call_after_refresh(self.measure)
        def measure(self):
            weather=self.query_one('#token-weather'); stats=self.query_one('#composer-stats')
            composer=self.query_one('#composer-frame')
            payload['composer']={'driver':type(self._driver).__name__,
                'weather':weather.content.plain,'colored':bool(weather.content.spans),
                'aligned':weather.region.y==stats.region.y and weather.region.right<=stats.region.x
                    and weather.region.bottom<=composer.region.y and weather.size.height==1}
            self.exit(None)
    ComposerProbe(SimpleNamespace(runtime_factory=None,session_store=None),
        Settings('fixture-key','https://api.invalid','deepseek-flash',root)).run(size=(100,32))
    payload['console_modes_restored']=(mode(sys.stdin),mode(sys.stdout))==before
except BaseException:
    payload['error']=traceback.format_exc()
    report.write_text(json.dumps(payload,ensure_ascii=True),encoding='utf-8')
    raise SystemExit(1)
report.write_text(json.dumps(payload,ensure_ascii=True),encoding='utf-8')
'''
