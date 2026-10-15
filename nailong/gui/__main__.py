"""Run with `ignovate gui`, or `python -m nailong.gui --browser`."""
import argparse
import json
import signal
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from nailong.gui.server import GUIServer


def main(argv=None):
    parser = argparse.ArgumentParser(description='Ignovate 本地 GUI 工作台')
    parser.add_argument('--project', default=str(Path.cwd()))
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--stdio', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--parent-pipe', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.parent_pipe and not args.stdio:
        parser.error('--parent-pipe requires --stdio')
    desktop = Path(__file__).resolve().parents[2] / 'desktop'
    electron = desktop / 'node_modules' / '.bin' / ('electron.cmd' if sys.platform == 'win32' else 'electron')
    if not (args.browser or args.no_browser or args.stdio) and electron.exists():
        return subprocess.call([str(electron), str(desktop), '--project', args.project])
    server = GUIServer(args.project, port=args.port)
    previous_term = signal.getsignal(signal.SIGTERM)
    def terminate(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    if args.parent_pipe:
        def watch_parent():
            # Windows TerminateProcess cannot run Python signal handlers. The
            # private inherited pipe also closes when the desktop shell dies.
            for line in sys.stdin:
                if line.strip() == 'shutdown':
                    break
            server.shutdown()
        threading.Thread(target=watch_parent, daemon=True, name='desktop-parent').start()
    if args.stdio:
        print(json.dumps({'url': server.url}), flush=True)
    else:
        print(f'Ignovate GUI 正在 {server.origin} 运行；Ctrl+C 关闭。', flush=True)
        if not args.no_browser:
            webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, previous_term)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
