"""Agent-declared acceptance for ordinary work; execution proof stays core-owned."""
from __future__ import annotations

import copy
import hashlib
import json

from nailong.core.task_requirements import requirement_records


def active_requirement_ids(task):
    return [row['id'] for row in requirement_records(task) if row['status'] == 'active']


def current_plan(task):
    plan = (task or {}).get('acceptance_plan')
    if plan and plan['requirement_ids'] == active_requirement_ids(task):
        return plan
    return None


def configuration_fingerprint(steps):
    return hashlib.sha256(json.dumps(steps, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def merge_verification_steps(*groups):
    merged = {}
    for group in groups:
        for row in group:
            if row['name'] in merged and merged[row['name']] != row:
                raise ValueError('已有验证步骤不能被同名替换；请为新检查使用新名称并保留旧条件。')
            merged[row['name']] = copy.deepcopy(row)
    if len(merged) > 30:
        raise ValueError('任务验证步骤不能超过 30 项。')
    return list(merged.values())


def binding_transition(task, expected, request):
    plan = (task or {}).get('acceptance_plan', {})
    return bool(task and request and task['task_id'] == expected['task_id']
        and request['thread_id'] == expected['thread_id']
        and plan.get('source_turn') == request['turn_id']
        and plan.get('previous_revision') == expected['revision']
        and plan.get('revision') == task['revision'] == expected['revision'] + 1)


def validate_plan(plan, task, relative):
    from nailong.core.task_intake import validate_brief
    if (not isinstance(plan, dict) or set(plan) != {'source', 'source_turn', 'requirement_ids', 'verification', 'previous_revision', 'revision'}
            or plan['source'] != 'agent' or not isinstance(plan['source_turn'], str) or not plan['source_turn']
            or type(plan['previous_revision']) is not int or plan['previous_revision'] < 1
            or type(plan['revision']) is not int or plan['revision'] != plan['previous_revision'] + 1
            or plan['revision'] > task['revision']
            or not isinstance(plan['requirement_ids'], list) or not plan['requirement_ids']
            or any(not isinstance(key, str) for key in plan['requirement_ids'])
            or len(set(plan['requirement_ids'])) != len(plan['requirement_ids'])
            or not set(plan['requirement_ids']) <= {r['id'] for r in requirement_records(task)}):
        raise ValueError('普通任务验收计划来源或要求关联无效。')
    validate_brief({'objective': task['objective'], 'verification': plan['verification']})


def bind_acceptance(intake, acceptance, verification, expected_revision):
    from nailong.core.task_intake import bound_goal, validate_brief
    request = intake._request()
    store, thread = intake.store, intake.thread_id
    from nailong.core.task_requests import task_request_kind, automatic_execution_prohibited
    if automatic_execution_prohibited(request['text']) or task_request_kind(request['text']) not in {'work', 'resume', 'report'}:
        raise ValueError('当前用户未授权实施；不能绑定自动执行验收。')
    if not acceptance or any(not isinstance(row, dict) for row in acceptance):
        raise ValueError('自动验收需要非空的 description/step/paths 条件。')
    with store._locked(thread):
        task = store._read(thread)
        if task is None or type(expected_revision) is not int or expected_revision != task['revision']:
            raise ValueError('任务版本已变化；请读取当前任务后绑定。')
        active_goal = intake.goals.active() if intake.goals is not None else None
        if (task.get('intake') or bound_goal(store, thread, task) or task['lifecycle'] != 'active'
                or active_goal is not None and active_goal.thread_id == thread):
            raise ValueError('持续任务或未激活任务请使用原需求草案与验收流程。')
        requirement_ids = active_requirement_ids(task)
        declared = set()
        for criterion in acceptance:
            if set(criterion) != {'description', 'step', 'paths', 'requirement_ids'}:
                raise ValueError('验收需明确 description/step/paths/requirement_ids。')
            coverage = criterion['requirement_ids']
            if (not isinstance(coverage, list) or not coverage
                    or any(not isinstance(key, str) or key not in requirement_ids for key in coverage)):
                raise ValueError('验收必须关联当前有效的用户要求 ID。')
            declared.update(coverage)
        if declared != set(requirement_ids):
            raise ValueError('自动验收未覆盖全部有效用户要求；不能遗漏历史约束或新增要求。')
        brief = validate_brief({'objective': task['objective'], 'acceptance': [
            {key: row[key] for key in ('description', 'step', 'paths')} for row in acceptance], 'verification': verification})
        from nailong.core.verification import VerificationService
        steps = VerificationService.validate_steps(brief['verification'], root=store.project_root)
        previous = task.get('acceptance_plan', {})
        configured = VerificationService(store.project_root, store.sessions).list_steps()
        merged = {row['name']: row for row in merge_verification_steps(previous.get('verification', []), configured, steps)}
        additions = []
        for criterion, original in zip(brief['acceptance'], acceptance):
            step = merged.get(criterion['step'])
            if step is None:
                raise ValueError('验收步骤不存在。')
            paths = list(dict.fromkeys(store._relative(path) for path in criterion['paths']))
            covered = [key for key in requirement_ids if key in original['requirement_ids']]
            declaration = {'description': criterion['description'], 'step': criterion['step'], 'paths': paths, 'requirement_ids': covered}
            identity = hashlib.sha256(json.dumps(declaration, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
            additions.append({'id': 'auto:' + identity, 'description': criterion['description'],
                'kind': step['kind'], 'required': True, 'status': 'pending', 'evidence_ids': [], 'requirement_ids': covered,
                'verification_binding': {'step': step['name'], 'paths': paths, 'source': 'agent'}})
        ids = {row['id'] for row in task['acceptance']}
        additions = [row for row in additions if row['id'] not in ids]
        changed = bool(additions or list(merged.values()) != previous.get('verification')
            or requirement_ids != previous.get('requirement_ids'))
        if changed:
            task['revision'] += 1
            store._invalidate(task, '已声明本轮自动验收；需要当前输入的原生验证证据。')
            task['acceptance'].extend(additions)
            configured = {row['id'] for row in task['acceptance']}
            for step in merged.values():
                key = 'verify:' + step['name']
                if key not in configured:
                    task['acceptance'].append({'id': key, 'description': f"配置验证步骤 {step['name']} 成功且对应当前项目输入。",
                        'kind': step['kind'], 'required': True, 'status': 'pending', 'evidence_ids': []})
            task['acceptance_plan'] = {'source': 'agent', 'source_turn': request['turn_id'],
                'previous_revision': expected_revision, 'revision': task['revision'],
                'requirement_ids': requirement_ids, 'verification': list(merged.values())}
            store._write(thread, task)
        return {'ok': True, 'task_id': task['task_id'], 'task_revision': task['revision'],
            'previous_revision': expected_revision, 'source': 'agent',
            'plan': copy.deepcopy(task['acceptance_plan']),
            'message': '已绑定代理声明的覆盖条件，尚未通过；交付前由原生验证器按权限执行。'}


def is_native_acceptance(spec, root):
    from nailong.core.task_intake import TaskIntake
    owner = getattr(getattr(spec, 'handler', None), '__self__', None)
    return (type(owner) is TaskIntake and owner.store.project_root == root
            and getattr(spec.handler, '__func__', None) is TaskIntake.bind_acceptance)
