"""Pure continuation decisions over TaskStore-owned, current observations.

Snapshots must come from TaskStore; ``current_input_fingerprint`` is supplied by
an independent runtime observation, never by model prose. Like delivery, these
functions cannot authenticate the producer of an arbitrary Python dictionary.
They do not execute checks, change acceptance, or grant permission to any tool.
"""
from __future__ import annotations

from collections import Counter

from nailong.core.delivery import (build_delivery_report, _applicable,
    _identity_matches, _known, _paths)

PROGRESS_WINDOW = 12
REPLAN_AFTER = 3


def _identity(task):
    return (task.get('task_id'), task.get('revision'), task.get('project_root'))


def _base(task):
    task = task if isinstance(task, dict) else {}
    return {'status': 'unknown', 'reason': 'task_snapshot_unavailable',
        'task_id': task.get('task_id'), 'task_revision': task.get('revision'),
        'project_root': task.get('project_root'),
        'current_input_fingerprint': task.get('current_input_fingerprint'),
        'evidence_ids': [], 'acceptance_ids': []}


def _observed(task):
    """Get only current applicable evidence and owner-confirmed acceptance."""
    report = build_delivery_report(task,
        current_input_fingerprint=task.get('current_input_fingerprint'))
    if 'lifecycle' not in report or any(row['kind'] == 'unknown' for row in report['pending']):
        return {}, {}, True
    evidence = task['evidence']
    ids = [row['id'] for row in evidence if isinstance(row, dict) and _known(row.get('id'))]
    duplicates = {identifier for identifier, count in Counter(ids).items() if count > 1}
    index = {row['id']: row for row in evidence if isinstance(row, dict) and _known(row.get('id'))}
    valid, acceptance = {}, {}
    for bucket in ('satisfied', 'waived', 'failed'):
        for row in report[bucket]:
            acceptance[row['id']] = (row['status'], tuple(row['evidence_ids']))
            for identifier in row['evidence_ids']:
                valid[identifier] = index[identifier]
    # Research can establish a new fact without promoting functional acceptance.
    # A read needs an observed result digest as well as current input and scope.
    scope = _paths(task.get('scope'))
    for identifier, row in index.items():
        paths = _paths(row.get('paths'))
        if (identifier not in duplicates and _known(identifier) and row.get('kind') == 'read'
                and row.get('source') == 'runtime' and row.get('status') == 'passed'
                and type(row.get('task_revision')) is int and row['task_revision'] == task['revision']
                and _identity_matches(row, task)
                and _known(row.get('input_fingerprint'))
                and row['input_fingerprint'] == task.get('current_input_fingerprint')
                and row.get('coverage') in ('partial', 'complete')
                and paths is not None and scope is not None and _applicable(paths, scope)
                and _known(row.get('result_digest')) and _known(row.get('summary'))):
            valid[identifier] = row
    facts = {_fact(row) for row in valid.values()}
    uncertain = bool(duplicates) or len(ids) != len(evidence) or any(
        identifier not in valid and (not _identity_matches(row, task) or _fact(row) not in facts)
        for identifier, row in index.items())
    return valid, acceptance, uncertain


def _fact(row):
    """IDs, rerun receipts and paraphrased summaries are not new observations."""
    paths = _paths(row.get('paths'))
    if paths is None or not all(isinstance(row.get(key), str)
            for key in ('kind', 'source', 'status', 'coverage', 'input_fingerprint')):
        return None
    return (row.get('kind'), row.get('source'), row.get('status'), row.get('coverage'),
        row.get('task_revision'), row.get('input_fingerprint'),
        tuple(sorted(paths)), row.get('result_digest') if _known(row.get('result_digest')) else None)


def assess_goal_progress(before, after) -> dict:
    """Assess an observed delta; unknown never means no effective progress."""
    result = _base(after)
    if not isinstance(before, dict) or not isinstance(after, dict):
        return result
    if _identity(before) != _identity(after):
        result['reason'] = ('task_revision_changed' if before.get('task_id') == after.get('task_id')
            and before.get('project_root') == after.get('project_root') else 'task_identity_changed')
        return result
    if not all(_known(value.get('current_input_fingerprint')) for value in (before, after)):
        result['reason'] = 'current_input_unknown'
        return result
    previous, previous_acceptance, before_unknown = _observed(before)
    current, current_acceptance, after_unknown = _observed(after)
    prior_facts = {_fact(row) for row in previous.values()}
    # Multiple new IDs for a single fact also count once.
    seen = set()
    added = []
    for identifier, row in current.items():
        fact = _fact(row)
        if fact not in prior_facts and fact not in seen:
            added.append(identifier)
            seen.add(fact)
    advanced = [identifier for identifier, (state, _) in current_acceptance.items()
        if state in {'passed', 'waived'} and previous_acceptance.get(identifier, ('pending',))[0] != state]
    if added or advanced:
        added_facts = {_fact(current[identifier]) for identifier in added}
        related = [identifier for identifier, (_, references) in current_acceptance.items()
            if identifier in advanced or any(reference in current and _fact(current[reference]) in added_facts
                for reference in references)]
        result.update(status='progress', reason='new_negative_evidence' if added and not advanced
            and all(current[identifier]['status'] == 'failed' for identifier in added)
            else 'current_evidence_delta', evidence_ids=added, acceptance_ids=related)
    elif before_unknown or after_unknown:
        result['reason'] = 'evidence_unverifiable'
    elif before.get('current_input_fingerprint') != after.get('current_input_fingerprint'):
        result['reason'] = 'input_changed_without_current_evidence'
    else:
        result.update(status='no_change', reason='no_current_evidence_delta')
    return result


def _value(goal, key, default=None):
    return goal.get(key, default) if isinstance(goal, dict) else getattr(goal, key, default)


def decide_goal_turn(goal, task, *, progress=None) -> dict:
    """Project execute/wait/replan/stop before a model stream; never authorize it."""
    def decision(action, reason, resume=''):
        result = {'action': action, 'reason': reason, 'resume_condition': resume}
        if isinstance(task, dict):
            result.update(task_id=task.get('task_id'), task_revision=task.get('revision'))
        return result
    state = _value(goal, 'state')
    if state != 'active':
        return decision('stop', f'目标当前为 {state}，不会开始新的模型调用。', '显式恢复目标或创建新目标。')
    if _value(goal, 'spent_usd', 0) >= _value(goal, 'max_cost_usd', 0):
        return decision('stop', '已达到目标成本上限。', '创建具有新额度的目标。')
    if _value(goal, 'round', 0) >= _value(goal, 'max_rounds', 0):
        return decision('stop', '已达到目标轮数上限。', '创建具有新轮数上限的目标。')
    if not isinstance(task, dict) or task.get('objective') != _value(goal, 'objective'):
        return decision('execute', '当前任务进展未知；在原生权限及预算内执行。')
    waiting = task.get('wait_condition')
    if waiting:
        if isinstance(waiting, dict):
            reason, condition = waiting.get('reason'), waiting.get('condition')
        else:
            reason, condition = '任务有明确的外部等待条件。', waiting
        return decision('wait', str(reason or '任务等待外部条件。'),
            str(condition or '核对任务等待条件并显式清除等待，然后恢复目标。'))
    if task.get('lifecycle') in {'paused', 'blocked'}:
        return decision('wait', '任务已暂停或受阻；先核对已执行操作与阻塞原因。',
            '核对 /task，显式解除任务阻塞并恢复目标。')
    if task.get('lifecycle') == 'completed':
        return decision('stop', '当前任务已结束；请核对交付记录。', '为新的工作创建或修订任务和目标。')
    report = build_delivery_report(task,
        current_input_fingerprint=task.get('current_input_fingerprint'))
    manual = [row for name in ('pending', 'failed', 'stale') for row in report[name]
        if row['required'] and row['kind'] == 'manual']
    # A newly accepted natural brief may require a human check of the result.
    # That check gates delivery after implementation, not its first execution.
    initial_natural=task.get('intake',{}).get('stage')=='executing' and _value(goal,'round',0)==0
    if manual and not initial_natural:
        return decision('wait', '目标等待用户人工验收：'+', '.join(row['id'] for row in manual),
            '用户核对当前输入并确认或明确免除相应验收，再显式恢复目标。')
    assessed = progress if progress is not None else _value(goal, 'last_progress', {})
    if (isinstance(assessed, dict) and assessed.get('status') == 'no_change'
            and _value(goal, 'no_change_rounds', 0) >= REPLAN_AFTER
            and (assessed.get('task_id'), assessed.get('task_revision'), assessed.get('project_root')) == _identity(task)
            and _known(task.get('current_input_fingerprint'))
            and assessed.get('current_input_fingerprint') == task['current_input_fingerprint']):
        return decision('replan', '连续已评估回合没有当前证据增量；下一轮需依据验收和证据调整调查方法。',
            '在当前验收与证据上重新选择下一步；仍受原生权限、轮数和成本限制。')
    return decision('execute', '在原生权限及预算内继续当前任务。')
