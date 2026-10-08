"""Explicit live-provider smoke: synthetic files, bounded calls, metadata only.

Run manually with configured credentials; this is excluded from offline CI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import load_settings
from headless import run_print
from nailong.core.budgets import GoalCostBudget
from nailong.core.costs import CostEstimator
from nailong.core.model import HarnessChatDeepSeek
from nailong.core.permissions import PermissionEngine
from nailong.core.reasoning import model_reasoning_kwargs, reasoning_options
from nailong.core.sessions import ProjectSessionStore
from nailong.core.verification import VerificationService


SCENARIOS = ('plain_answer_no_tools', 'read_with_reasoning', 'edit_and_bound_verification',
             'readonly_review_coverage', 'approval_denial_zero_write', 'headless_json')


async def run(selected: set[str]) -> dict:
    original = load_settings()
    options = {row[0] for row in reasoning_options(original.model)}
    default_effort = 'none' if 'none' in options else 'default'
    rows = []
    usage = []
    with tempfile.TemporaryDirectory(prefix='ignovate-live-') as directory:
        base = Path(directory).resolve()
        root = base / 'project'
        root.mkdir()
        (root / 'intro.txt').write_text('identifier: LIVE_HARNESS_OK\n')
        (root / 'calc.py').write_text('def add(a, b):\n    return a - b\n')
        (root / 'check.py').write_text('from calc import add\nassert add(2, 3) == 5\n')
        command = shlex.quote(sys.executable) + ' check.py'
        (root / '.nailong').mkdir()
        (root / '.nailong/settings.json').write_text(json.dumps({'verification': {'steps': [
            {'name': 'behavior', 'kind': 'test', 'command': command, 'timeout_seconds': 15}
        ]}}))
        settings = replace(original, project_root=root, reasoning_effort=default_effort,
                           cli_preferences={'model': original.model, 'reasoning_effort': default_effort})
        sessions = ProjectSessionStore(root, base_dir=base / 'private', api_key=settings.api_key)
        factory = AgentRuntimeFactory(settings, session_store=sessions)
        service = AgentService(factory, api_key=settings.api_key, permission_engine=PermissionEngine(root))
        budget = GoalCostBudget(CostEstimator(factory.settings.model, root), .15)

        def model(effort):
            factory.model = HarnessChatDeepSeek(model=factory.settings.model,
                api_key=settings.api_key, base_url=settings.api_base,
                timeout=30, max_retries=0, max_tokens=1000,
                **model_reasoning_kwargs(factory.settings.model, effort))

        model(default_effort)

        async def collect(prompt, thread, **kwargs):
            async def stream():
                return [event async for event in service.stream_turn(prompt,
                    {'configurable': {'thread_id': thread}}, cost_budget=budget, **kwargs)]
            events = await asyncio.wait_for(stream(), 90)
            usage.extend(event.data for event in events if event.kind == 'usage')
            final = next(event.data for event in reversed(events) if event.kind == 'final')
            return events, final

        async def scenario(name, operation):
            if selected and name not in selected:
                return
            try:
                result = await operation()
            except Exception as error:
                result = {'ok': False, 'error_type': type(error).__name__}
            row = {'scenario': name, **result}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

        async def plain():
            events, final = await collect('不要调用工具，只回答 OK。', 'plain',
                allowed_tools=frozenset(), max_model_calls=1)
            tools = [e.data.get('name') for e in events if e.kind == 'tool_end']
            return {'ok': final['text'].strip() == 'OK' and not tools,
                    'model_calls': final['stats']['model_calls'], 'tools': tools}

        async def read():
            effort = 'low' if 'low' in options else default_effort
            model(effort)
            try:
                events, final = await collect('先调用 read_file 读取 intro.txt，然后只返回 identifier 的值。',
                    'read', allowed_tools={'read_file'}, max_model_calls=3)
            finally:
                model(default_effort)
            tools = [e.data.get('name') for e in events if e.kind == 'tool_end']
            return {'ok': 'LIVE_HARNESS_OK' in final['text'] and 'read_file' in tools,
                    'reasoning_effort': effort, 'model_calls': final['stats']['model_calls'], 'tools': tools}

        async def fix():
            request = ('修复 calc.py 中 add 把加法写成减法的问题。先读取 calc.py，再用 edit_file 改一处。'
                       '不要扫描目录或创建其他文件；简短回复，验证由运行时单独执行。')
            tasks = factory.task_store
            tasks.begin('fix', request, scope=['calc.py', 'check.py'])
            tasks.add_acceptance('fix', 'add-correct', 'add(2,3) == 5', kind='test')
            verifier = VerificationService(root, sessions, factory.goal_store,
                api_key=settings.api_key, task_store=tasks)
            tasks.configure_verification('fix', verifier.list_steps())
            tasks.bind_verification('fix', 'add-correct', 'behavior', ['calc.py', 'check.py'])
            approvals = []
            async def approve(action, *_):
                args = action.get('args', {})
                allowed = ((action.get('name') == 'edit_file' and args.get('path') == 'calc.py')
                           or (action.get('name') == 'run_command' and args.get('command') == command))
                approvals.append({'name': action.get('name'), 'allowed': allowed})
                return 'approve_once' if allowed else 'reject'
            _, final = await collect(request, 'fix', allowed_tools={'read_file', 'edit_file'},
                approval_handler=approve, max_model_calls=5)
            verification = await verifier.run('fix', permission_engine=service.permission_engine,
                approval=approve, permission_mode='default')
            delivery = await service.delivery_report('fix')
            changed = 'return a + b' in (root / 'calc.py').read_text()
            return {'ok': changed and verification['status'] == 'passed' and delivery['status'] == 'verified',
                    'file_changed': changed, 'verification': verification['status'],
                    'delivery': delivery['status'], 'approvals': approvals,
                    'model_calls': final['stats']['model_calls']}

        async def review():
            _, final = await collect('读取 calc.py 全文并只读审查，不执行命令，简短说明范围。', 'review',
                profile='review', target_path='calc.py', allowed_tools={'read_file'}, max_model_calls=3)
            report = await service.delivery_report('review')
            return {'ok': report['status'] == 'reviewed', 'delivery': report['status'],
                    'model_calls': final['stats']['model_calls']}

        async def deny():
            denied = []
            async def reject(action, *_):
                denied.append(action.get('name'))
                return 'reject'
            _, final = await collect('用 write_file 创建 rejected.txt，内容 smoke。', 'deny',
                allowed_tools={'write_file'}, approval_handler=reject, max_model_calls=3)
            exists = (root / 'rejected.txt').exists()
            return {'ok': bool(denied) and not exists, 'approvals': len(denied),
                    'file_exists': exists, 'model_calls': final['stats']['model_calls']}

        try:
            await scenario('plain_answer_no_tools', plain)
            await scenario('read_with_reasoning', read)
            await scenario('edit_and_bound_verification', fix)
            await scenario('readonly_review_coverage', review)
            await scenario('approval_denial_zero_write', deny)
        finally:
            await factory.aclose()
            factory.close()

        async def headless():
            output, errors = StringIO(), StringIO()
            old_data_dir = os.environ.get('NAILONG_DATA_DIR')
            os.environ['NAILONG_DATA_DIR'] = str(base / 'headless-private')
            try:
                code = await asyncio.wait_for(run_print(settings, '不要调用工具，只回答 OK。',
                    max_turns=1, output_format='json', stdout=output, stderr=errors), 90)
            finally:
                if old_data_dir is None:
                    os.environ.pop('NAILONG_DATA_DIR', None)
                else:
                    os.environ['NAILONG_DATA_DIR'] = old_data_dir
            payload = json.loads(output.getvalue())
            usage.append(payload.get('usage') or {})
            answer_matches = payload.get('result', '').strip() == 'OK'
            return {'ok': code == 0 and payload.get('status') == 'success'
                    and answer_matches and not payload.get('tools'),
                    'exit_code': code, 'status': payload.get('status'), 'usage': payload.get('usage'),
                    'answer_matches': answer_matches, 'answer_characters': len(payload.get('result', '')),
                    'tool_count': len(payload.get('tools', []))}

        await scenario('headless_json', headless)
        leaked = any(settings.api_key.encode() in p.read_bytes() for p in base.rglob('*') if p.is_file())
        rows.append({'scenario': 'credential_not_persisted', 'ok': not leaked})
        report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                  'provider_host': urlsplit(settings.api_base).hostname, 'model': factory.settings.model,
                  'managed_budget_usd': .15, 'estimated_managed_spend_usd': round(budget.spent, 8),
                  'note': '费用按本地单价估算；headless 单独限定1次模型调用，不纳入 managed 预算。',
                  'usage_events': len(usage), 'usage': usage, 'results': rows,
                  'selected_scenarios': sorted(selected),
                  'ok': all(row['ok'] for row in rows)}
        assert settings.api_key not in json.dumps(report, ensure_ascii=False)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Save a metadata-only JSON report.')
    parser.add_argument('--only', choices=SCENARIOS, action='append', default=[],
                        help='Run selected scenarios only; repeat for multiple selections.')
    args = parser.parse_args()
    report = asyncio.run(run(set(args.only)))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'ok': report['ok'], 'scenarios': len(report['results']),
                      'report': str(args.output) if args.output else None}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
