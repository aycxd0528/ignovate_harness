"""Installed nailong entry point, using the caller's current project directory."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path


def doctor_cli(argv):
    argv=[arg for arg in argv if arg!='doctor']
    parser=argparse.ArgumentParser(description='ignovate harness 本地诊断')
    parser.add_argument('--doctor',action='store_true')
    parser.add_argument('--project',default=str(Path.cwd()))
    parser.add_argument('--output-format',choices=('text','json','stream-json'),default='text')
    args=parser.parse_args(argv)
    from nailong.core.diagnostics import diagnose,format_diagnostics
    report=diagnose(args.project)
    print(json.dumps(report,ensure_ascii=False) if args.output_format!='text' else format_diagnostics(report))
    return 0 if report['ok'] else 1


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if '--doctor' in argv or (argv and argv[0]=='doctor'): return doctor_cli(argv)
    if '--project' not in argv and not any(arg.startswith('--project=') for arg in argv):
        argv+=['--project',str(Path.cwd())]
    from main import main as run
    return run(argv)


if __name__=='__main__': raise SystemExit(main())
