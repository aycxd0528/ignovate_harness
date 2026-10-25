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


def _needs_observation(task):
    return any(c['kind'] == 'manual' and c['required'] and c['status'] in {'passed', 'waived'}
               for c in task.get('acceptance', []))


async def projection_async(controller):
    """Observe filesystem inputs off the GUI thread, then project fresh state."""
    from nailong.core.verification import _owned_worker, current_evidence
    identity = controller._project_identity()
    observations = {}
    try:
        tasks = task_store(controller).list_work_items()[:128]
    except (ValueError, OSError):
        # The synchronous projection preserves the authority-read diagnostic.
        return projection(controller, observations={})
    # Tasks with identical exclusions can share the same current observation.
    fingerprints = {}
    for task in tasks:
        if not _needs_observation(task):
            continue
        generated = (tuple(task.get('verification_generated_paths', [])),
                     json.dumps(task.get('verification_manifest'), sort_keys=True))
        if generated not in fingerprints:
            try:
                fingerprints[generated] = await _owned_worker(current_evidence, identity[0], task, controller.sessions.root)
            except (ValueError, OSError):
                fingerprints[generated] = None
            controller._check_project_identity(identity)
        observations[task['task_id']] = (task, fingerprints[generated])
    controller._check_project_identity(identity)
    return projection(controller, observations=observations)


def projection(controller, branches=None, *, observations=None):
    branches = controller.branches.branches() if branches is None else branches
    by_thread = {branch['thread_id']: branch for branch in branches}
    items, attention, diagnostics = [], [], []
    tasks = task_store(controller)
    try:
        for task in tasks.list_work_items()[:128]:
            row = task_summary(task)
            associated = task.get('associated_thread_ids', [task['thread_id']])
            owner = task.get('owner_thread_id') or task['thread_id']
            # Association order does not identify the recorded task source.
            # An owner that moved to a new task is no longer an active route.
            branch = by_thread.get(owner) if owner in associated else None
            if branch is None:
                branch = next((by_thread[t] for t in associated if t in by_thread), None)
            row['branch_id'] = branch['id'] if branch else None
            items.append(row)
            origin = {'branch_id': row['branch_id'], 'task_id': task['task_id'], 'expected_revision': task['revision']}
            wait = task.get('wait_condition')
            if wait:
                attention.append({**origin, 'id': f"wait:{task['task_id']}:{task['revision']}", 'kind': 'wait',
                    'title': wait['reason'], 'reason': wait['condition'], 'action': 'clear_wait'})
            assessment = None
            if _needs_observation(task):
                from nailong.core.delivery import build_delivery_report
                from nailong.core.verification import current_evidence
                if observations is None:
                    try:
                        fingerprint = current_evidence(controller.project, task, controller.sessions.root)
                    except (ValueError, OSError):
                        fingerprint = None
                else:
                    observed_task, fingerprint = observations.get(task['task_id'], (None, None))
                    if observed_task != task:
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


def _check_control_task(task, payload):
    if task is None or 'task_id' in payload and payload['task_id'] != task['task_id']:
        raise ValueError('任务身份已变化，请重新核对。')


async def execute(controller, payload):
    """Keep locks around state reads/writes, never around input scanning."""
    operation = payload.get('operation')
    if operation in {None, 'status'}:
        async with controller.command_lock:
            controller._validate_command_payload(payload)
        return await projection_async(controller)
    async with controller.command_lock:
        controller._validate_command_payload(payload)
        if operation not in {'confirm_acceptance', 'feedback_stage', 'feedback_confirm'}:
            return _execute(controller, payload)
        if controller.current_task or controller.queue:
            raise ValueError('请等待当前运行与队列结束后修改工作控制状态。')
        identity = controller._project_identity()
        branch = controller.branches.branch(payload['branch_id'])
        tasks = task_store(controller)
        task = tasks.snapshot(branch['thread_id'])
        _check_control_task(task, payload)
        if task is None or operation != 'feedback_confirm' and task['revision'] != payload.get('expected_revision'):
            raise ValueError('任务版本已变化，请重新核对。')
        source = None
        generated = None
        feedback_store = None
        feedback_context = None
        if operation == 'feedback_stage':
            source, source_task = _feedback_source(controller, branch, task, payload)
            if source_task.get('input_fingerprint'):
                generated = source_task
            feedback_store = FeedbackStore(controller.sessions)
            feedback_context = feedback_store._prepare_proposal(task=task, request_id=payload['request_id'])
        elif operation == 'feedback_confirm':
            feedback_store = FeedbackStore(controller.sessions)
            feedback_context = feedback_store._prepare_confirmation(payload['id'], task=task)
        else:
            generated = task
    from nailong.core.verification import _owned_worker, current_evidence
    fingerprint = None
    if generated is not None:
        fingerprint = await _owned_worker(current_evidence, identity[0], generated, controller.sessions.root)
        controller._check_project_identity(identity)
    feedback_observation = None
    if feedback_context is not None:
        observed = None
        if feedback_context.needs_scan:
            try:
                observed = await _owned_worker(current_evidence, feedback_context.project,
                    feedback_context.task, feedback_context.boundary, feedback_context.generated)
            except (ValueError, OSError):
                if operation != 'feedback_stage':
                    raise
            controller._check_project_identity(identity)
        feedback_observation = feedback_context.observed(observed)
    async with controller.command_lock:
        controller._check_project_identity(identity)
        controller._validate_command_payload(payload)
        if controller.branches.branch(branch['id']) != branch:
            raise ValueError('来源会话已变化，请重新核对。')
        if source is not None and controller.branches.node(source['id']) != source:
            raise ValueError('来源回合已变化，请重新核对。')
        with tasks.bound_task(branch['thread_id'], task['task_id'], task['revision']) as bound:
            # State changes need not increment the requirements revision. Bind
            # the observation to all inputs, exclusions and acceptance details.
            if bound != task:
                raise ValueError('任务状态已变化，请重新核对。')
            return _execute(controller, payload, tasks=tasks, fingerprint=fingerprint,
                            feedback_store=feedback_store, feedback_observation=feedback_observation)


def _feedback_source(controller, branch, task, payload):
    node = next((node for node in controller.branches.path(branch['id']) if node['id'] == payload.get('node_id')), None)
    if node is None:
        raise ValueError('请选择真实来源回合。')
    node = controller.branches.node(node['id'])
    source_task = json.loads(node.get('task_snapshot') or 'null') or {}
    if (source_task.get('task_id'), source_task.get('revision')) != (task['task_id'], task['revision']):
        raise ValueError('来源回合的任务或要求版本与当前任务不一致。')
    return node, source_task


def _execute(controller, payload, *, tasks=None, fingerprint=None, feedback_store=None, feedback_observation=None):
    operation = payload.get('operation')
    if operation in {None, 'status'}:
        return projection(controller)
    if controller.current_task or controller.queue:
        raise ValueError('请等待当前运行与队列结束后修改工作控制状态。')
    branch = controller.branches.branch(payload['branch_id'])
    # Reuse the observation owner under its reentrant native task lock.
    # A lazy controller would otherwise create a second file-lock owner.
    if tasks is None:
        tasks = task_store(controller)
    thread = branch['thread_id']
    task = tasks.snapshot(thread)
    revision = payload.get('expected_revision')
    if operation in {'set_wait', 'clear_wait', 'confirm_acceptance', 'feedback_stage', 'feedback_confirm'}:
        _check_control_task(task, payload)
    if operation == 'link_task':
        result = tasks.link_work_item(thread, payload['task_id'], expected_revision=revision)
    elif operation == 'set_wait':
        with tasks.bound_task(thread, task['task_id'], revision) as bound:
            if bound is None:
                raise ValueError('任务身份或版本已变化，请重新核对。')
            result = tasks.set_wait(thread, reason=payload['reason'], condition=payload['condition'], expected_revision=revision)
    elif operation == 'clear_wait':
        with tasks.bound_task(thread, task['task_id'], revision) as bound:
            if bound is None:
                raise ValueError('任务身份或版本已变化，请重新核对。')
            if 'expected_wait_condition' in payload and payload['expected_wait_condition'] != bound.get('wait_condition'):
                raise ValueError('等待条件已变化，请重新核对。')
            result = tasks.clear_wait(thread, expected_revision=revision)
    elif operation == 'confirm_acceptance':
        if task is None or task['revision'] != revision:
            raise ValueError('人工事项任务版本已变化。')
        with tasks.bound_task(thread, task['task_id'], revision) as bound:
            if bound is None:
                raise ValueError('人工事项已失效。')
            result = tasks.user_decision(thread, payload['acceptance_id'], payload['reason'], current_input_fingerprint=fingerprint)
    elif operation == 'feedback_stage':
        if task is None or task['revision'] != revision:
            raise ValueError('反馈来源任务版本已变化。')
        node, source_task = _feedback_source(controller, branch, task, payload)
        with tasks.bound_task(thread, task['task_id'], task['revision']) as bound:
            if bound is None:
                raise ValueError('反馈来源任务已变化。')
            source_input = source_task.get('input_fingerprint') or None
            if source_input:
                if source_input != fingerprint:
                    raise ValueError('来源回合的输入版本已变化，请选择当前来源重新整理。')
            result = (feedback_store or FeedbackStore(controller.sessions)).propose(task=bound,
                source_ref=f"session:{node['thread_id']}#node:{node['id']}",
                source_input_fingerprint=source_input,
                context=payload['context'], outcome=payload['outcome'], behavior=payload['behavior'],
                boundary=payload['boundary'], request_id=payload['request_id'], _observation=feedback_observation)
    elif operation == 'feedback_confirm':
        if task is None:
            raise ValueError('当前会话没有反馈来源任务。')
        with tasks.bound_task(thread, task['task_id'], task['revision']) as bound:
            if bound is None:
                raise ValueError('反馈来源任务已变化。')
            result = (feedback_store or FeedbackStore(controller.sessions)).confirm(payload['id'], task=bound,
                expected_revision=revision, _observation=feedback_observation)
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
