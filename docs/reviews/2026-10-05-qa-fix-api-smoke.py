"""Explicitly invoked real-provider checks, with changes confined to a temp project."""
import asyncio
import json
import os
import sys
import tempfile
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import load_settings
from headless import run_print
from nailong.core.budgets import GoalCostBudget
from nailong.core.costs import CostEstimator
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore
from nailong.core.verification import VerificationService
from nailong.core.traces import TraceActions
from nailong.tools.execution import current_tool_execution


async def main():
    original = load_settings()
    results = []
    selected = set(filter(None, os.environ.get('NAILONG_SMOKE_ONLY', '').split(',')))
    with tempfile.TemporaryDirectory(prefix='nailong-qa-api-') as directory:
        base = Path(directory)
        root = base / 'project'
        root.mkdir()
        (root / 'README.md').write_text('# Tiny calculator\nPython calculator entry: calc.py.\n')
        (root / 'calc.py').write_text('def add(a, b):\n    return a - b\n')
        (root / 'check.py').write_text('from calc import add\nassert add(2, 3) == 5\nprint("ADD_CHECK_OK")\n')
        (root / '.nailong').mkdir()
        (root / '.nailong/settings.json').write_text(json.dumps({'verification': {'steps': [
            {'name': 'behavior', 'kind': 'test', 'command': 'python3 check.py', 'timeout_seconds': 10}
        ]}}))
        settings = replace(original, project_root=root)
        sessions = ProjectSessionStore(root, base_dir=base / 'private', api_key=settings.api_key)
        factory = AgentRuntimeFactory(settings, session_store=sessions)
        service = AgentService(factory, api_key=settings.api_key, permission_engine=PermissionEngine(root))
        budget = GoalCostBudget(CostEstimator(factory.settings.model, root), .15)

        async def collect(prompt, thread, **options):
            async def run():
                return [event async for event in service.stream_turn(prompt,
                    {'configurable': {'thread_id': thread}}, cost_budget=budget, **options)]
            return await asyncio.wait_for(run(), 60)

        def final(events):
            return next(event.data for event in reversed(events) if event.kind == 'final')

        async def scenario(name, operation):
            if selected and name not in selected:
                return
            try:
                row = await operation()
                results.append({'scenario': name, **row})
            except Exception as error:
                results.append({'scenario': name, 'ok': False,
                    'error': f'{type(error).__name__}: {error}'.replace(settings.api_key, '[密钥已隐藏]')})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)

        async def introduce_twice():
            runs = []
            for index in range(2):
                events = await collect('介绍一下这个项目。简短说用途和入口，不执行命令。',
                    f'intro-{index}', max_model_calls=6)
                reply = final(events)
                runs.append({'has_answer': bool(reply['text']), 'stats': reply['stats'],
                    'used_subagent': any(event.kind == 'tool_start' and event.data.get('name') == 'task' for event in events),
                    'usage_events': sum(event.kind == 'usage' for event in events)})
            return {'ok': all(row['has_answer'] and not row['used_subagent']
                and 0 < row['stats']['model_calls'] <= 6 for row in runs),
                'runs': runs}

        async def last_request_summary():
            events = await collect('先读取 README.md，再读取 calc.py，然后概述。不要运行命令。',
                'last-summary', allowed_tools={'read_file'}, max_model_calls=2)
            reply = final(events)
            calls = [row['data'] for row in sessions.read_events('last-summary') if row['kind'] == 'model_call']
            last_call = max(i for i, row in enumerate(sessions.read_events('last-summary')) if row['kind'] == 'model_call')
            no_late_tools = not any(row['kind'] == 'tool_start' for row in sessions.read_events('last-summary')[last_call + 1:])
            return {'ok': reply['stats']['model_calls'] == 2 and reply['stats']['summary_only']
                and no_late_tools and bool(reply['text']), 'stats': reply['stats'],
                'calls': calls, 'no_tool_after_last_call': no_late_tools,
                'usage_events': sum(event.kind == 'usage' for event in events)}

        approvals = []

        async def approve(action, index, total):
            args = action.get('args', {})
            allowed = ((action.get('name') == 'edit_file' and args.get('path') == 'calc.py')
                or (action.get('name') == 'run_command' and args.get('command') == 'python3 check.py'))
            approvals.append({'name': action.get('name'), 'allowed': allowed})
            return 'approve' if allowed else 'reject'

        async def fix_and_verify():
            request = '修复 calc.py 的 add 函数，把减法改为加法。只读取 calc.py 后使用 edit_file 改一处，不扫描目录、不创建文件。简短回复，验证由核心单独执行。'
            tasks = factory.task_store
            tasks.begin('real-fix', request, scope=['calc.py', 'check.py'])
            tasks.add_acceptance('real-fix', 'add-correct', 'add(2,3) 实际返回5', kind='test')
            verifier = VerificationService(root, sessions, factory.goal_store,
                api_key=settings.api_key, task_store=tasks)
            tasks.configure_verification('real-fix', verifier.list_steps())
            tasks.bind_verification('real-fix', 'add-correct', 'behavior', ['calc.py', 'check.py'])
            events = await collect(request, 'real-fix', allowed_tools={'read_file', 'edit_file'},
                approval_handler=approve, max_model_calls=6)
            verification = await verifier.run('real-fix', permission_engine=service.permission_engine,
                approval=approve, permission_mode='default')
            report = await service.delivery_report('real-fix')
            return {'ok': 'return a + b' in (root / 'calc.py').read_text()
                and verification['status'] == 'passed' and report['status'] == 'verified',
                'verification': verification['status'], 'delivery': report['status'],
                'approvals': approvals, 'stats': final(events)['stats']}

        async def query_preserves_real_evidence():
            tasks = factory.task_store
            tasks.set_state('real-fix', lifecycle='paused', blockers=['冒烟：等待用户继续'])
            before = tasks.snapshot('real-fix')
            before_bytes = tasks._path('real-fix').read_bytes()
            queries = []
            for text in ('现在进展如何？', '你好'):
                events = await collect(text, 'real-fix')
                queries.append({'local': final(events).get('local'), 'stats': final(events)['stats']})
            after = tasks.snapshot('real-fix')
            return {'ok': before == after and before_bytes == tasks._path('real-fix').read_bytes()
                and all(row['local'] and row['stats']['model_calls'] == 0 for row in queries)
                and any(row['status'] == 'passed' for row in after['acceptance']),
                'snapshot_identical': before == after, 'lifecycle': after['lifecycle'],
                'revision': after['revision'], 'queries': queries}

        async def deny_write():
            denied = []
            async def reject(action, index, total):
                denied.append(action['name'])
                return 'reject'
            events = await collect('用 write_file 创建 rejected.txt，内容 smoke。', 'real-denial',
                allowed_tools={'write_file'}, approval_handler=reject, max_model_calls=3)
            exists = (root / 'rejected.txt').exists()
            return {'ok': bool(denied) and not exists, 'approval_count': len(denied),
                'file_exists': exists, 'stats': final(events)['stats']}

        async def parallel_children():
            runner = factory.task_runner
            runner.begin_turn('parallel-children')
            token = current_tool_execution.set({'thread_id': 'parallel-children',
                'context': factory.tool_execution_context, 'profile': 'chat'})
            try:
                replies = await asyncio.wait_for(asyncio.gather(
                    runner.run('parallel-children', '只读取 README.md，然后用一句话说明用途，不查其他文件。'),
                    runner.run('parallel-children', '只读取 calc.py，然后用一句话说明 add 函数，不查其他文件。'),
                ), 90)
            finally:
                current_tool_execution.reset(token)
            usage = runner.drain_usage('parallel-children') or {}
            diagnostics = [row['data'] for row in sessions.read_events('parallel-children')
                if row['kind'] == 'subagent_budget']
            return {'ok': all(text and not any(word in text for word in ('预算不足', '执行失败', '停止调研'))
                for text in replies) and usage.get('total_tokens', 0) > 0,
                'answer_count': len(replies), 'usage': usage,
                'budget_failures': [text for text in replies if '预算' in text or '失败' in text],
                'diagnostics': diagnostics,
                'remaining_budget': runner.remaining_budget('parallel-children'),
                'diagnostic_event_count': len(diagnostics)}

        async def unrestricted_files_network_and_trace():
            external = base / 'outside.py'
            external.write_text('value = 1\n')
            created = base / 'created.txt'
            full_factory = AgentRuntimeFactory(settings, session_store=sessions)
            full = AgentService(full_factory, api_key=settings.api_key,
                permission_engine=PermissionEngine(root), permission_mode='bypassPermissions')
            command = 'curl -I -sS --max-time 10 https://www.python.org'
            prompt = (f'执行这三项明确授权的冒烟操作：先 read_file 读取 {external}，'
                f'再 edit_file 把 value = 1 改为 value = 2；write_file 创建 {created}，内容 FULL_ACCESS_OK；'
                f'run_command 执行 {command} 来检查外部互联网连接。不要扫描目录，不操作其他文件。简短报告真实结果。')
            try:
                events = await asyncio.wait_for(collect_full(full, prompt), 90)
                ends = [row.data for row in events if row.kind == 'tool_end']
                trace = TraceActions(root, sessions, api_key=settings.api_key)
                report = trace.latest('real-full')
                export = await trace.export('real-full', '.nailong/exports/smoke-trace.jsonl',
                    permission_engine=full.permission_engine, permission_mode='bypassPermissions')
                exported = Path(export['path']).read_text() if export['written'] else ''
                raw_content_absent = 'FULL_ACCESS_OK' not in exported and prompt not in exported
                return {'ok': external.read_text() == 'value = 2\n'
                    and created.exists() and created.read_text().strip() == 'FULL_ACCESS_OK'
                    and any(row.get('name') == 'run_command' and row.get('ok') for row in ends)
                    and not any(row.kind == 'approval_needed' for row in events)
                    and export['written'] and raw_content_absent,
                    'tools': [{'name': row.get('name'), 'ok': row.get('ok'), 'exit_code': row.get('exit_code')} for row in ends],
                    'approval_count': sum(row.kind == 'approval_needed' for row in events),
                    'stats': final(events)['stats'], 'trace_state': report['state'],
                    'trace_event_count': len(report['events']), 'trace_exported': export['written'],
                    'trace_raw_content_omitted': raw_content_absent}
            finally:
                await full_factory.aclose()
                full_factory.close()

        async def collect_full(full, prompt):
            return [row async for row in full.stream_turn(prompt,
                {'configurable': {'thread_id': 'real-full'}}, cost_budget=budget,
                allowed_tools={'read_file', 'edit_file', 'write_file', 'run_command'}, max_model_calls=6)]

        try:
            await scenario('same_intro_two_fresh_sessions', introduce_twice)
            await scenario('last_model_request_summary_without_tools', last_request_summary)
            await scenario('real_read_edit_bound_verification', fix_and_verify)
            await scenario('query_preserves_real_evidence_and_pause', query_preserves_real_evidence)
            await scenario('approval_denial_zero_write', deny_write)
            await scenario('two_parallel_readonly_children', parallel_children)
            await scenario('unrestricted_external_files_network_and_trace', unrestricted_files_network_and_trace)
        finally:
            await factory.aclose()
            factory.close()

        async def headless():
            output, errors = StringIO(), StringIO()
            with patch.dict(os.environ, {'NAILONG_DATA_DIR': str(base / 'headless-private')}):
                code = await asyncio.wait_for(run_print(settings, '仅回答“冒烟成功”，不要调用工具。',
                    max_turns=1, output_format='json', stdout=output, stderr=errors), 60)
            payload = json.loads(output.getvalue())
            return {'ok': code == 0 and payload.get('status') == 'success'
                and not payload.get('tools') and bool(payload.get('result')),
                'exit_code': code, 'status': payload.get('status'), 'usage': payload.get('usage')}

        await scenario('headless_json_with_one_model_call', headless)
        leaked = any(settings.api_key.encode() in path.read_bytes() for path in base.rglob('*') if path.is_file())
        results.append({'scenario': 'secret_not_persisted', 'ok': not leaked})
        summary = {'model': factory.settings.model, 'main_budget_usd': .15,
            'estimated_main_spend_usd': round(budget.spent, 8),
            'note': 'headless 单独限定1次模型调用；费用为本地单价估算。',
            'ok': all(row['ok'] for row in results), 'selected_scenarios': sorted(selected), 'results': results}
        target = REPO / ('docs/reviews/2026-10-05-qa-fix-api-smoke'
            + ('-targeted' if selected else '') + '.json')
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'result_file': str(target), 'ok': summary['ok'],
            'scenarios': len(results), 'estimated_main_spend_usd': summary['estimated_main_spend_usd']}, ensure_ascii=False))
        return 0 if summary['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
