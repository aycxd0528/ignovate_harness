import asyncio
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from config import load_settings
from agent import AgentRuntimeFactory
from agent_service import AgentService
from headless import run_print
from nailong.core.budgets import GoalCostBudget
from nailong.core.costs import CostEstimator
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore
from nailong.core.verification import VerificationService


async def main():
    original = load_settings()
    results = []
    with tempfile.TemporaryDirectory(prefix='nailong-api-smoke-') as directory:
        base = Path(directory)
        root = base / 'project'
        root.mkdir()
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
        estimator = CostEstimator(factory.settings.model, root)
        budget = GoalCostBudget(estimator, .15)
        request = '修复 calc.py 中 add 函数把加法写成减法的错误。只读 calc.py 后用 edit_file 改一处，不扫描目录、不创建其他文件。修改后返回简短结果，验证由核心单独执行。'
        factory.task_store.begin('api-fix', request, scope=['calc.py', 'check.py'])
        factory.task_store.add_acceptance('api-fix', 'add-correct', 'add(2,3) 实际返回 5', kind='test')
        verifier = VerificationService(root, sessions, factory.goal_store,
            api_key=settings.api_key, task_store=factory.task_store)
        factory.task_store.configure_verification('api-fix', verifier.list_steps())
        factory.task_store.bind_verification('api-fix', 'add-correct', 'behavior', ['calc.py', 'check.py'])
        approval_actions = []
        async def approve(action, index, total):
            allowed = ((action.get('name') == 'edit_file' and action.get('args', {}).get('path') == 'calc.py')
                or (action.get('name') == 'run_command' and action.get('args', {}).get('command') == 'python3 check.py'))
            approval_actions.append({'name': action.get('name'), 'allowed': allowed})
            return 'approve' if allowed else 'reject'
        async def run_turn(prompt, thread, **options):
            async def collect():
                answer = ''
                async for event in service.stream_turn(prompt,
                        {'configurable': {'thread_id': thread}}, cost_budget=budget, **options):
                    if event.kind == 'final':
                        answer = event.data['text']
                return answer
            return await asyncio.wait_for(collect(), timeout=60)
        try:
            await run_turn(request, 'api-fix', allowed_tools={'read_file', 'edit_file'}, approval_handler=approve)
            changed = 'return a + b' in (root / 'calc.py').read_text()
            verification = await verifier.run('api-fix', permission_engine=service.permission_engine,
                approval=approve, permission_mode='default')
            delivery = await service.delivery_report('api-fix')
            results.append({'scenario': 'real_read_edit_and_bound_verify', 'ok': changed
                and verification['status'] == 'passed' and delivery['status'] == 'verified',
                'file_changed': changed, 'verification': verification['status'],
                'delivery': delivery['status'], 'approvals': approval_actions})
            expected_id = factory.task_store.snapshot('api-fix')['task_id']
            await run_turn('读取 calc.py 全文，静态审查此函数。不要运行命令；给出简短结果和范围。',
                'api-review', profile='review', target_path='calc.py', allowed_tools={'read_file'})
            review = await service.delivery_report('api-review')
            results.append({'scenario': 'real_static_review_input_coverage', 'ok': review['status'] == 'reviewed',
                'delivery': review['status']})
            denied = []
            async def reject(action, index, total):
                denied.append(action.get('name'))
                return 'reject'
            await run_turn('用 write_file 在项目创建 rejected.txt，内容是 smoke。',
                'api-denial', allowed_tools={'write_file'}, approval_handler=reject)
            task = factory.task_store.snapshot('api-denial')
            results.append({'scenario': 'real_approval_denial_zero_write',
                'ok': bool(denied) and not (root / 'rejected.txt').exists(),
                'approval_count': len(denied), 'file_exists': (root / 'rejected.txt').exists(),
                'evidence_status': [row['status'] for row in task['evidence']] if task else []})
        finally:
            await factory.aclose()
            factory.close()
        child_code = '''import json,sys
from pathlib import Path
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.core.delivery import build_delivery_report
from nailong.core.verification import input_fingerprint
root=Path(sys.argv[1]); task=TaskStore(ProjectSessionStore(root,base_dir=Path(sys.argv[2]))).snapshot('api-fix')
report=build_delivery_report(task,current_input_fingerprint=input_fingerprint(root,task.get('verification_generated_paths',[])))
print(json.dumps({'task_id':task['task_id'],'delivery':report['status'],'objective_preserved':bool(task['objective']),'criteria':len(task['acceptance'])}))
'''
        child = subprocess.run([sys.executable, '-c', child_code, str(root), str(base / 'private')],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=True)
        resumed = json.loads(child.stdout)
        results.append({'scenario': 'separate_process_recovery', 'ok': resumed['task_id'] == expected_id
            and resumed['delivery'] == 'verified', **resumed})
        output, errors = StringIO(), StringIO()
        with patch.dict(os.environ, {'NAILONG_DATA_DIR': str(base / 'headless-private')}):
            status = await asyncio.wait_for(run_print(settings, '请仅回答“冒烟成功”，不要调用工具。',
                output_format='json', stdout=output, stderr=errors), timeout=60)
        headless = json.loads(output.getvalue())
        results.append({'scenario': 'real_headless_plain_answer', 'ok': status == 0
            and not headless.get('tools') and headless.get('delivery') is None,
            'exit_code': status, 'status': headless.get('status'), 'tools': len(headless.get('tools', [])),
            'usage': headless.get('usage')})
        print(json.dumps({'model': factory.settings.model, 'managed_budget_usd': .15,
            'managed_spent_usd': budget.spent, 'scenarios': results,
            'all_passed': all(row['ok'] for row in results)}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    asyncio.run(main())
