"""Read-only project control projections and explicit, revision-bound commands."""
from __future__ import annotations

import json

from nailong.core.control_ledger import ControlLedger
from nailong.core.feedback import FeedbackStore
from nailong.core.task_state import TaskStore


def task_store(controller):
    return getattr(controller.service, 'task_store', None) or TaskStore(controller.sessions, api_key=controller.sessions.api_key)


def task_summary(task):
    if not task:
        return None
    return {key: task.get(key) for key in ('task_id', 'revision', 'thread_id', 'owner_thread_id',
        'source_thread_id', 'objective', 'lifecycle', 'phase', 'wait_condition', 'association',
        'associated_thread_ids', 'steps', 'acceptance')}


def projection(controller, branches=None):
    branches = controller.branches.branches() if branches is None else branches
    by_thread = {branch['thread_id']: branch for branch in branches}
    items, attention, diagnostics = [], [], []
    tasks = task_store(controller)
    try:
        for task in tasks.list_work_items()[:128]:
            row = task_summary(task)
            branch = next((by_thread[t] for t in task.get('associated_thread_ids', [task['thread_id']]) if t in by_thread), None)
            row['branch_id'] = branch['id'] if branch else None
            items.append(row)
            origin = {'branch_id': row['branch_id'], 'task_id': task['task_id'], 'expected_revision': task['revision']}
            wait = task.get('wait_condition')
            if wait:
                attention.append({**origin, 'id': f"wait:{task['task_id']}:{task['revision']}", 'kind': 'wait',
                    'title': wait['reason'], 'reason': wait['condition'], 'action': 'clear_wait'})
            assessment = None
            if any(c['kind'] == 'manual' and c['required'] and c['status'] in {'passed', 'waived'} for c in task.get('acceptance', [])):
                from nailong.core.delivery import build_delivery_report
                from nailong.core.verification import input_fingerprint
                try:
                    fingerprint = input_fingerprint(controller.project, task.get('verification_generated_paths', []))
                except (ValueError, OSError):
                    fingerprint = None
                assessment = build_delivery_report(task, current_input_fingerprint=fingerprint)
            for criterion in task.get('acceptance', []):
                if criterion['kind'] != 'manual' or not criterion['required']:
                    continue
                effective = criterion['status']
                if effective in {'passed', 'waived'}:
                    if not any(c['id'] == criterion['id'] for c in assessment.get('satisfied', []) + assessment.get('waived', [])):
                        effective = 'stale' if fingerprint else 'unknown'
                    else:
                        effective = 'passed'
                if effective != 'passed':
                    attention.append({**origin, 'id': f"manual:{task['task_id']}:{criterion['id']}:{task['revision']}",
                        'kind': 'manual', 'status': effective, 'title': '人工验收需重新核对' if effective in {'stale', 'unknown'} else '等待人工验收', 'reason': criterion['description'],
                        'acceptance_id': criterion['id'], 'action': 'confirm_acceptance'})
    except (ValueError, OSError) as error:
        diagnostics.append(str(error))
    for identity, entry in controller.approvals.items():
        value = entry['public']
        attention.append({'id': 'approval:' + identity, 'kind': 'approval', 'title': '等待批准',
            'reason': value['action'].get('name', '工具操作'), 'action_id': identity,
            'run_id': value['run_id'], 'branch_id': value['branch_id'], 'action': 'show_approval'})
    for run in controller.runs.values():
        if run['status'] in {'failed', 'interrupted'}:
            attention.append({'id': 'run:' + run['id'], 'kind': run['status'], 'title': '运行失败' if run['status'] == 'failed' else '运行已中断',
                'reason': run.get('error') or '核对已发生的操作和结果，再明确下一步。',
                'run_id': run['id'], 'branch_id': run['branch_id'], 'node_id': run.get('node_id'), 'action': 'open_branch'})
    try:
        receipts = ControlLedger(controller.sessions).list()
        for row in receipts:
            branch = by_thread.get(row['thread_id'])
            row['branch_id'] = branch['id'] if branch else None
            row['node_id'] = controller.runs.get(row['run_id'], {}).get('node_id')
    except (ValueError, OSError) as error:
        receipts = []; diagnostics.append(str(error))
    try:
        feedback = FeedbackStore(controller.sessions).list()
        for row in feedback:
            reference = row.get('source_ref', '').partition('#node:')
            branch = by_thread.get(reference[0].removeprefix('session:'))
            row['branch_id'] = branch['id'] if branch else None
            row['node_id'] = reference[2] if reference[1] else None
    except (ValueError, OSError) as error:
        feedback = []; diagnostics.append(str(error))
    return controller.safe({'schema_version': 1, 'items': items, 'attention': attention[:256],
        'receipts': receipts, 'feedback': feedback, 'diagnostics': diagnostics,
        'truncated': len(attention) > 256})


async def execute(controller, payload):
    operation = payload.get('operation')
    if operation in {None, 'status'}:
        return projection(controller)
    if controller.current_task or controller.queue:
        raise ValueError('请等待当前运行与队列结束后修改工作控制状态。')
    branch = controller.branches.branch(payload['branch_id'])
    tasks = task_store(controller)
    thread = branch['thread_id']
    task = tasks.snapshot(thread)
    revision = payload.get('expected_revision')
    if operation == 'link_task':
        result = tasks.link_work_item(thread, payload['task_id'], expected_revision=revision)
    elif operation == 'set_wait':
        result = tasks.set_wait(thread, reason=payload['reason'], condition=payload['condition'], expected_revision=revision)
    elif operation == 'clear_wait':
        if 'expected_wait_condition' in payload and payload['expected_wait_condition'] != (task or {}).get('wait_condition'):
            raise ValueError('等待条件已变化，请重新核对。')
        result = tasks.clear_wait(thread, expected_revision=revision)
    elif operation == 'confirm_acceptance':
        if task is None or task['revision'] != revision:
            raise ValueError('人工事项任务版本已变化。')
        from nailong.core.verification import input_fingerprint
        fingerprint = input_fingerprint(controller.project, task.get('verification_generated_paths', []))
        with tasks.bound_task(thread, task['task_id'], revision) as bound:
            if bound is None:
                raise ValueError('人工事项已失效。')
            result = tasks.user_decision(thread, payload['acceptance_id'], payload['reason'], current_input_fingerprint=fingerprint)
    elif operation == 'feedback_stage':
        if task is None or task['revision'] != revision:
            raise ValueError('反馈来源任务版本已变化。')
        node = next((node for node in controller.branches.path(branch['id']) if node['id'] == payload.get('node_id')), None)
        if node is None:
            raise ValueError('请选择真实来源回合。')
        source_task = json.loads(controller.branches.node(node['id']).get('task_snapshot') or 'null') or {}
        if (source_task.get('task_id'), source_task.get('revision')) != (task['task_id'], task['revision']):
            raise ValueError('来源回合的任务或要求版本与当前任务不一致。')
        with tasks.bound_task(thread, task['task_id'], task['revision']) as bound:
            if bound is None:
                raise ValueError('反馈来源任务已变化。')
            source_input = source_task.get('input_fingerprint') or None
            if source_input:
                from nailong.core.verification import input_fingerprint
                if source_input != input_fingerprint(controller.project, source_task.get('verification_generated_paths', [])):
                    raise ValueError('来源回合的输入版本已变化，请选择当前来源重新整理。')
            result = FeedbackStore(controller.sessions).propose(task=bound,
                source_ref=f"session:{node['thread_id']}#node:{node['id']}",
                source_input_fingerprint=source_input,
                context=payload['context'], outcome=payload['outcome'], behavior=payload['behavior'],
                boundary=payload['boundary'], request_id=payload['request_id'])
    elif operation == 'feedback_confirm':
        if task is None:
            raise ValueError('当前会话没有反馈来源任务。')
        with tasks.bound_task(thread, task['task_id'], task['revision']) as bound:
            if bound is None:
                raise ValueError('反馈来源任务已变化。')
            result = FeedbackStore(controller.sessions).confirm(payload['id'], task=bound, expected_revision=revision)
    elif operation == 'feedback_revoke':
        result = FeedbackStore(controller.sessions).revoke(payload['id'], expected_revision=revision)
    else:
        raise ValueError('不支持的工作控制动作。')
    controller.sessions.append_event(thread, 'work_control', {'operation': operation,
        'task_id': result.get('task_id'), 'revision': result.get('revision'), 'source': 'user'})
    controller.emit('work_control', {'operation': operation, 'branch_id': branch['id']})
    return controller.safe(result)


def closeout(controller, run, branch):
    ledger = ControlLedger(controller.sessions)
    receipt = ledger.get(run['id'])
    if receipt is None:
        return
    existing = {s['stage'] for s in receipt['stages']}
    def fill(stage, details):
        if stage not in existing:
            ledger.record(run['id'], stage, details=details)
            existing.add(stage)
    # Node history is immutable and bounded to this run. Neither the current
    # task nor the session tail may certify an earlier run during recovery.
    node = controller.branches.node(run['node_id']) if run.get('node_id') else None
    events = controller.branches.node_events(node) if node else []
    from nailong.core.branches import project_turn
    turn = project_turn(node, events) if node else {}
    tools = turn.get('tools', run.get('tools', []))
    owned = bool(node) and (all(e.get('run_id') == run['id'] for e in events) and bool(events)
        or node.get('status') == 'completed' and run['status'] == 'completed')
    unknown = not owned or any(t.get('status') in {'unknown', 'interrupted', 'running'} for t in tools)
    fill('effects_observed', {'tool_calls': len(tools), 'effect_state': 'unknown' if unknown else 'observed'})
    task = json.loads(node.get('task_snapshot') or 'null') if node else None
    if task:
        from nailong.core.delivery import build_delivery_report
        report = build_delivery_report(task, current_input_fingerprint=task.get('input_fingerprint'))
        if report['status'] in {'verified', 'reviewed'}:
            fill('evidence_validated', {'task_id': task['task_id'], 'task_revision': task['revision'],
                'delivery_status': report['status'], 'input_fingerprint': task.get('input_fingerprint'),
                'evidence_ids': list(dict.fromkeys(e for row in report['satisfied'] for e in row['evidence_ids']))[:128]})
        fill('task_committed', {'task_id': task['task_id'], 'task_revision': task['revision']})
    usage = turn.get('usage', {})
    if owned and usage.get('calls', 0) > 0 and usage.get('cost_complete') is True:
        fill('cost_settled', {'usage_ref': f"session:{node['thread_id']}#node:{node['id']}",
            'calls': usage['calls'], 'usage_complete': True})
    fill('closed', {'status': run['status']})
