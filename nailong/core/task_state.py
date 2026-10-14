"""Project-scoped task state kept independently of model conversation history."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
import os
from contextlib import contextmanager
from pathlib import Path

from nailong.core.preferences import atomic_json, read_config, safe_config_path
from nailong.core.task_requirements import append_requirement, requirement_records
from nailong.core.file_locks import file_lock
from nailong.core.safe_files import _path, is_link_or_reparse

PHASES = frozenset({'investigate', 'implement', 'verify', 'deliver'})
LIFECYCLES = frozenset({'active', 'paused', 'blocked', 'completed'})
EVIDENCE_KINDS = frozenset({'read', 'edit', 'test', 'build', 'run', 'review', 'manual'})
ACCEPTANCE_KINDS = frozenset({'static', 'test', 'build', 'run', 'review', 'manual'})
MAX_TASK_BYTES = 900_000


class TaskStore:
    """One durable task per session; all mutations come from runtime or user facts.

    Evidence is never inferred from assistant prose. Conversation rewind does
    not rewind this store because file and command effects are not undone.
    """

    def __init__(self, session_store, *, api_key=''):
        self.sessions = session_store
        self.project_root = Path(session_store.project_root).resolve()
        self.boundary = Path(session_store.root).resolve()
        self.directory = self.boundary / 'tasks'
        self.api_key = api_key
        self._lock = threading.RLock()
        self._held_locks = {}

    @contextmanager
    def _locked(self, thread_id):
        """Serialize the same task across factories and separate CLI processes."""
        with self._lock:
            if thread_id in self._held_locks:
                yield
                return
            self.sessions.session_path(thread_id)
            path = safe_config_path(self.directory / f'{thread_id}.lock', self.boundary)
            with file_lock(path) as descriptor:
                try:
                    self._held_locks[thread_id] = descriptor
                    yield
                finally:
                    self._held_locks.pop(thread_id, None)

    def _path(self, thread_id):
        self.sessions.session_path(thread_id)  # Reuse the session ID validator.
        return safe_config_path(self.directory / f'{thread_id}.json', self.boundary)

    def _safe(self, value):
        if isinstance(value, str):
            return value.replace(self.api_key, '[密钥已隐藏]') if self.api_key else value
        if isinstance(value, dict):
            return {key: self._safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._safe(item) for item in value]
        return value

    def _read(self, thread_id):
        value = read_config(self._path(thread_id), self.boundary)
        if not value:
            if self._path(thread_id).exists():
                raise ValueError('任务状态为空或损坏，未覆盖原文件。')
            return None
        if (type(value.get('schema_version')) is not int or value.get('schema_version') != 1 or value.get('thread_id') != thread_id
                or value.get('project_root') != str(self.project_root)
                or not isinstance(value.get('task_id'), str) or not value['task_id']
                or type(value.get('revision')) is not int or value['revision'] < 1
                or value.get('phase') not in PHASES
                or value.get('lifecycle') not in LIFECYCLES):
            raise ValueError('任务状态身份、版本或生命周期无效，未将其作为当前任务。')
        for key in ('scope', 'constraints', 'steps', 'acceptance', 'changed_paths',
                    'pending_verification', 'evidence', 'blockers'):
            if not isinstance(value.get(key), list):
                raise ValueError(f'任务状态 {key} 必须为列表。')
        for key in ('objective', 'latest_request'):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise ValueError(f'任务状态 {key} 必须为非空文本。')
        for key in ('constraints', 'pending_verification', 'blockers', 'request_history'):
            if key in value and (not isinstance(value[key], list)
                    or any(not isinstance(item, str) for item in value[key])):
                raise ValueError(f'任务状态 {key} 包含无效文本。')
        for key in ('scope', 'changed_paths'):
            for path in value[key]:
                self._validate_recorded_path(path)
        for key in ('steps', 'acceptance', 'evidence'):
            rows = value[key]
            if (any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
                    or not row['id'] for row in rows)
                    or len({row['id'] for row in rows}) != len(rows)):
                raise ValueError(f'任务状态 {key} 的 ID 缺失或重复。')
        for row in value['steps']:
            if (row.get('state') not in {'todo', 'doing', 'done', 'blocked'}
                    or not isinstance(row.get('title'), str)
                    or not isinstance(row.get('dependencies'), list)
                    or any(not isinstance(item, str) for item in row['dependencies'])):
                raise ValueError('任务步骤无效。')
        for row in value['acceptance']:
            if (row.get('kind') not in ACCEPTANCE_KINDS or type(row.get('required')) is not bool
                    or row.get('status') not in {'pending', 'passed', 'failed', 'stale', 'waived'}
                    or not isinstance(row.get('description'), str)
                    or not isinstance(row.get('evidence_ids'), list)
                    or any(not isinstance(item, str) for item in row['evidence_ids'])):
                raise ValueError('任务验收无效。')
            if 'verification_binding' in row:
                binding = row['verification_binding']
                if (not isinstance(binding, dict) or set(binding) != {'step', 'paths', 'source'}
                        or binding['source'] != 'user' or row['kind'] not in {'test', 'build', 'run'}
                        or row['id'].startswith('verify:')
                        or not isinstance(binding['step'], str) or not binding['step'].strip()
                        or len(binding['step']) > 80 or not isinstance(binding['paths'], list)
                        or not binding['paths']):
                    raise ValueError('验收验证绑定无效。')
                for path in binding['paths']:
                    self._validate_recorded_path(path)
        for row in value['evidence']:
            if (row.get('kind') not in EVIDENCE_KINDS
                    or row.get('source') not in {'runtime', 'user', 'model'}
                    or row.get('status') not in {'passed', 'failed', 'denied', 'interrupted', 'unknown'}
                    or row.get('coverage') not in {'complete', 'partial', 'unknown'}
                    or type(row.get('task_revision')) is not int
                    or not isinstance(row.get('paths'), list)
                    or any(not isinstance(row.get(key), str) for key in ('input_fingerprint', 'summary', 'artifact_ref'))):
                raise ValueError('任务证据无效。')
            for path in row['paths']:
                self._validate_recorded_path(path)
        if 'requirements' in value:
            requirement_records(value)
        return value

    def _write(self, thread_id, value):
        value = self._safe(value)
        if len((json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)+'\n').encode()) > MAX_TASK_BYTES:
            raise ValueError('任务状态超过持久化预算；保留旧状态，不能静默丢弃要求或证据。')
        atomic_json(self._path(thread_id), value, self.boundary)
        return copy.deepcopy(value)

    def snapshot(self, thread_id):
        with self._locked(thread_id):
            return copy.deepcopy(self._read(thread_id))

    @contextmanager
    def bound_task(self, thread_id, task_id, revision=None):
        """Bind a short synchronous mutation to the same durable task/version.

        Existing mutators are reentrant inside this context. Do not await while
        holding it: collect asynchronous observations first, then recheck here.
        """
        if (not isinstance(task_id, str) or not task_id
                or revision is not None and (type(revision) is not int or revision < 1)):
            raise ValueError('任务绑定身份或版本无效。')
        with self._locked(thread_id):
            task = self._read(thread_id)
            matches = (task is not None and task['task_id'] == task_id
                and (revision is None or task['revision'] == revision))
            yield copy.deepcopy(task) if matches else None

    @staticmethod
    def _validate_recorded_path(raw):
        """Validate history lexically; an external symlink must not erase history."""
        from nailong.tools.files import FileSession
        if (not isinstance(raw, str) or not raw or '\0' in raw or '\\' in raw
                or Path(raw).is_absolute() or '..' in Path(raw).parts
                or any(FileSession._is_protected(part) for part in Path(raw).parts)):
            raise ValueError('任务记录路径必须为安全的项目相对路径。')

    def _relative(self, raw):
        if not isinstance(raw, str) or not raw or '\0' in raw or '\\' in raw:
            raise ValueError('任务范围需要有效的项目内路径。')
        path = Path(raw)
        if '..' in path.parts:
            raise ValueError('任务范围不能穿越项目目录。')
        path = path if path.is_absolute() else self.project_root / path
        if os.name == 'nt':
            _path(path)
            if any(is_link_or_reparse(item) for item in (path, *path.parents)):
                raise ValueError('任务范围不能经过重解析点或目录联接。')
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self.project_root):
            raise ValueError('任务范围必须在当前项目内。')
        from nailong.tools.files import FileSession
        parts = resolved.relative_to(self.project_root).parts
        if any(FileSession._is_protected(part) for part in parts):
            raise ValueError('任务范围包含受保护路径。')
        return resolved.relative_to(self.project_root).as_posix() or '.'

    def begin(self, thread_id, objective, *, profile='chat', scope=None, new=False, latest_request=None):
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError('任务目标不能为空。')
        paths = [self._relative(path) for path in (scope or ['.'])]
        request = objective if latest_request is None else latest_request
        if not isinstance(request, str) or not request.strip():
            raise ValueError('当前要求不能为空。')
        with self._locked(thread_id):
            current = self._read(thread_id)
            if current is not None and not new and current['lifecycle'] != 'completed':
                continuation = request.strip().rstrip('。.!！') in {'继续', '请继续', '开始', '开始修改', '继续修改', 'continue'}
                if request != current.get('latest_request') and not continuation:
                    current['revision'] += 1
                    self._invalidate(current, '收到新的用户要求，请重新核对验收覆盖。')
                    append_requirement(current,request)
                current['latest_request'] = request
                current['profile'] = profile
                return self._write(thread_id, current)
            task = {
                'schema_version': 1, 'task_id': uuid.uuid4().hex,
                'thread_id': thread_id, 'project_root': str(self.project_root),
                'objective': objective.strip(), 'scope': paths, 'constraints': [],
                'revision': 1, 'latest_request': request, 'request_history': list(dict.fromkeys([objective,request])), 'profile': profile,
                'lifecycle': 'active', 'phase': 'investigate', 'steps': [],
                'acceptance': [], 'changed_paths': [], 'pending_verification': [],
                'evidence': [], 'progress': '', 'blockers': [], 'input_fingerprint': '',
                'progress_observations': [], 'progress_repeats': 0,
            }
            task['requirements']=requirement_records(task)
            for row in task['requirements']:
                row['introduced_revision']=1
            return self._write(thread_id, task)

    def replace_requirement(self, thread_id, requirement_id, text):
        """Only the explicit local user command calls this; no model tool."""
        if not isinstance(text,str) or not text.strip():
            raise ValueError('替代要求不能为空。')
        with self._locked(thread_id):
            task=self._read(thread_id)
            if task is None:
                raise ValueError('当前会话没有任务。')
            rows=requirement_records(task)
            previous=next((row for row in rows if row['id']==requirement_id),None)
            if previous is None or previous['status']!='active':
                raise ValueError('只能明确替代当前有效要求；请先 /task requirements 核对 ID。')
            task['requirements']=rows
            task['revision']+=1
            append_requirement(task,text)
            previous=task['requirements'][previous['history_index']]
            previous.update(status='superseded',superseded_by=task['requirements'][-1]['id'])
            if task['objective']==previous['text']:
                task['objective']=text
            task['latest_request']=text
            self._invalidate(task,'用户明确替代了要求，请重新核对验收。')
            task.update(lifecycle='active',phase='investigate',progress_observations=[])
            return self._write(thread_id,task)

    def set_step(self, thread_id, step_id, *, title=None, state='todo', dependencies=None):
        if not isinstance(step_id, str) or not step_id.strip() or state not in {'todo', 'doing', 'done', 'blocked'}:
            raise ValueError('步骤 ID 或状态无效。')
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                raise ValueError('当前会话没有任务。')
            steps = {row['id']: row for row in task['steps']}
            previous = steps.get(step_id)
            if previous is None and (not isinstance(title, str) or not title.strip()):
                raise ValueError('新步骤需要说明。')
            dependencies = list(previous.get('dependencies', []) if previous else []) if dependencies is None else dependencies
            if (not isinstance(dependencies, list) or any(item not in steps or item == step_id for item in dependencies)
                    or (state in {'doing', 'done'} and any(steps[item]['state'] != 'done' for item in dependencies))):
                raise ValueError('步骤依赖不存在、自引用或尚未完成。')
            steps[step_id] = {'id': step_id, 'title': title or previous['title'], 'state': state, 'dependencies': dependencies}
            def visit(current, seen):
                if current in seen:
                    raise ValueError('步骤依赖存在循环。')
                for dependency in steps[current]['dependencies']:
                    visit(dependency, seen | {current})
            for current in steps:
                visit(current, set())
            task['steps'] = list(steps.values())
            return self._write(thread_id, task)

    def amend(self, thread_id, *, objective=None, scope=None, constraints=None):
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                raise ValueError('当前会话没有任务。')
            task['revision'] += 1
            if objective is not None:
                if not isinstance(objective, str) or not objective.strip():
                    raise ValueError('任务目标不能为空。')
                append_requirement(task,objective)
                task['objective'] = objective.strip()
                task['latest_request'] = objective
            if scope is not None:
                if not isinstance(scope, list) or not scope:
                    raise ValueError('范围必须为非空路径列表。')
                task['scope'] = [self._relative(item) for item in scope]
            if constraints is not None:
                if not isinstance(constraints, list) or any(not isinstance(item, str) or not item.strip() for item in constraints):
                    raise ValueError('约束必须为非空文本列表。')
                task['constraints'] = constraints
            self._invalidate(task, '用户更新了任务要求。')
            task['lifecycle'] = 'active'
            task['phase'] = 'investigate'
            task['progress_observations'] = []
            return self._write(thread_id, task)

    def add_acceptance(self, thread_id, criterion_id, description, *, kind='test', required=True):
        if (not isinstance(criterion_id, str) or not criterion_id.strip()
                or not isinstance(description, str) or not description.strip()
                or kind not in ACCEPTANCE_KINDS or type(required) is not bool):
            raise ValueError('验收项 ID、说明、类型或 required 无效。')
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                raise ValueError('当前会话没有任务。')
            if any(row['id'] == criterion_id for row in task['acceptance']):
                raise ValueError('验收项 ID 已存在。')
            task['revision'] += 1
            self._invalidate(task, '验收条件发生变化。')
            task['acceptance'].append({'id': criterion_id, 'description': description,
                'kind': kind, 'required': required, 'status': 'pending', 'evidence_ids': []})
            return self._write(thread_id, task)

    def configure_verification(self, thread_id, steps):
        """Project configuration defines execution criteria, not feature coverage."""
        configured = [{'id': f"verify:{step['name']}",
            'description': f"配置验证步骤 {step['name']} 成功且对应当前项目输入。",
            'kind': step['kind'], 'required': True, 'status': 'pending', 'evidence_ids': []}
            for step in steps]
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                return None
            existing = [row for row in task['acceptance'] if row['id'].startswith('verify:')]
            identity = lambda rows: [(row['id'], row['kind'], row['description']) for row in rows]
            if identity(existing) == identity(configured):
                return copy.deepcopy(task)
            task['revision'] += 1
            self._invalidate(task, '项目配置的验证条件发生变化。')
            task['acceptance'] = [row for row in task['acceptance'] if not row['id'].startswith('verify:')] + configured
            return self._write(thread_id, task)

    def bind_verification(self, thread_id, criterion_id, step_name, paths):
        """User explicitly declares which configured check covers an acceptance."""
        if not isinstance(paths, list) or not paths:
            raise ValueError('绑定需要明确的覆盖路径。')
        coverage = [self._relative(path) for path in paths]
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                raise ValueError('当前会话没有任务。')
            criterion = next((row for row in task['acceptance'] if row['id']==criterion_id), None)
            step = next((row for row in task['acceptance'] if row['id']==f'verify:{step_name}'), None)
            if (criterion is None or step is None or criterion_id.startswith('verify:')
                    or criterion['kind'] not in {'test','build','run'} or criterion['kind'] != step['kind']):
                raise ValueError('用户验收与已配置验证步骤必须存在且类型一致。')
            task['revision'] += 1
            self._invalidate(task, '用户更新了验收与实际验证流程的覆盖绑定。')
            criterion['verification_binding'] = {'step':step_name,'paths':coverage,'source':'user'}
            return self._write(thread_id, task)

    @staticmethod
    def _invalidate(task, reason):
        for criterion in task['acceptance']:
            if criterion.get('status') in {'passed', 'failed', 'waived'}:
                criterion['status'] = 'stale'
        if task['changed_paths'] and reason not in task['pending_verification']:
            task['pending_verification'].append(reason)
        task['input_fingerprint'] = ''

    def record_change(self, thread_id, path):
        relative = self._relative(path)
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                return None
            if relative not in task['changed_paths']:
                task['changed_paths'].append(relative)
            self._invalidate(task, f'{relative} 已修改，旧验证不能证明当前输入。')
            task['phase'] = 'implement'
            task['lifecycle'] = 'active'
            return self._write(thread_id, task)

    def record_evidence(self, thread_id, evidence, *, acceptance_ids=()):
        """Internal API; callers must supply actual execution or explicit user facts."""
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                return None
            self._append_evidence(task, evidence, acceptance_ids=acceptance_ids)
            return self._write(thread_id, task)

    def _append_evidence(self, task, evidence, *, acceptance_ids=()):
        """Stage evidence and acceptance together; the caller commits once."""
        item = copy.deepcopy(evidence)
        if (not isinstance(item, dict) or item.get('kind') not in EVIDENCE_KINDS
                or item.get('source') not in {'runtime', 'user', 'model'}
                or item.get('status') not in {'passed', 'failed', 'denied', 'interrupted', 'unknown'}
                or item.get('coverage') not in {'complete', 'partial', 'unknown'}):
            raise ValueError('证据类型、来源、状态或覆盖无效。')
        item.setdefault('id', uuid.uuid4().hex)
        if not isinstance(item['id'], str) or not item['id']:
            raise ValueError('证据 ID 无效。')
        item.setdefault('task_revision', task['revision'])
        item.setdefault('input_fingerprint', '')
        item.setdefault('artifact_ref', '')
        item.setdefault('summary', '')
        if (type(item['task_revision']) is not int or item['task_revision'] < 1
                or not isinstance(item.get('paths', []), list)
                or any(not isinstance(item[key], str) for key in ('input_fingerprint', 'artifact_ref', 'summary'))):
            raise ValueError('证据版本、路径、摘要或引用无效。')
        item['paths'] = [self._relative(path) for path in item.get('paths', [])]
        # A duplicate result must not charge or promote acceptance twice.
        if any(row['id'] == item['id'] for row in task['evidence']):
            return copy.deepcopy(task)
        task['evidence'].append(item)
        trusted = item['source'] in {'runtime', 'user'} and item['task_revision'] == task['revision']
        for criterion in task['acceptance']:
            if criterion['id'] not in acceptance_ids:
                continue
            matches = (item['kind'] == criterion['kind'] or
                criterion['kind'] == 'static' and item['kind'] in {'review', 'manual'})
            if not trusted or not matches:
                continue
            if (item['status'] == 'passed' and item['coverage'] == 'complete'):
                criterion['status'] = 'passed'
                criterion['evidence_ids'] = [item['id']]
            elif item['status'] in {'failed', 'denied', 'interrupted', 'unknown'}:
                criterion['status'] = 'failed' if item['status'] == 'failed' else 'pending'
                criterion['evidence_ids'] = [item['id']]
        if trusted and item['kind'] in {'test', 'build', 'run'}:
            task['phase'] = 'verify'
        return task

    def record_review(self, thread_id, evidence, *, expected_task_id, expected_revision,
                      criterion_id='review:scope'):
        """Internal runtime receipt; independent input hashes come from the caller."""
        with self.bound_task(thread_id, expected_task_id, expected_revision) as task:
            if task is None:
                return None
            item = copy.deepcopy(evidence)
            if (not isinstance(item, dict) or item.get('source') != 'runtime'
                    or item.get('kind') != 'review' or item.get('status') != 'passed'
                    or item.get('coverage') != 'complete'
                    or type(item.get('task_revision')) is not int
                    or item['task_revision'] != expected_revision
                    or not isinstance(item.get('input_fingerprint'), str)
                    or item['input_fingerprint'].strip().casefold() in {'', 'unknown', 'none', 'null', '?', '未知'}
                    or not isinstance(item.get('summary'), str) or not item['summary'].strip()
                    or not isinstance(item.get('artifact_ref', ''), str)
                    or not isinstance(item.get('paths'), list) or not item['paths']
                    or 'id' in item and (not isinstance(item['id'], str) or not item['id'])
                    or not isinstance(criterion_id, str) or not criterion_id.strip()
                    or criterion_id.startswith('verify:')):
                raise ValueError('静态审查需要当前完整的运行时证据。')
            item['paths'] = [self._relative(path) for path in item['paths']]
            def covers(path, target):
                return path == '.' or Path(target).is_relative_to(Path(path))
            if not all(any(covers(path, target) for path in item['paths'])
                       for target in [*task['scope'], *task['changed_paths']]):
                raise ValueError('静态审查证据没有完整覆盖当前任务范围。')
            criterion = next((row for row in task['acceptance'] if row['id'] == criterion_id), None)
            if criterion is not None and criterion['kind'] not in {'review', 'static'}:
                raise ValueError('静态审查不能替代其他类型的验收。')
            duplicate = next((row for row in task['evidence'] if row['id'] == item.get('id')), None)
            if duplicate is not None:
                for key in ('kind', 'source', 'status', 'coverage', 'task_revision', 'input_fingerprint', 'paths'):
                    if duplicate.get(key) != item.get(key):
                        raise ValueError('审查证据 ID 已用于不同的观察。')
                return task
            if task.get('input_fingerprint') and task['input_fingerprint'] != item['input_fingerprint']:
                self._invalidate(task, '静态审查观察到新的项目输入，旧证据失效。')
            if criterion is None:
                task['acceptance'].append({'id': criterion_id, 'description': '当前范围的静态审查流程完整覆盖。',
                    'kind': 'review', 'required': True, 'status': 'pending', 'evidence_ids': []})
            task['input_fingerprint'] = item['input_fingerprint']
            self._append_evidence(task, item, acceptance_ids=[criterion_id])
            if not task['changed_paths']:
                task['pending_verification'] = []
            return self._write(thread_id, task)

    def record_verification(self, thread_id, record):
        """Map core-configured step evidence onto identically named acceptance."""
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None or not isinstance(record, dict):
                return None
            if record.get('thread_id') != thread_id or record.get('cwd') != str(self.project_root):
                return None
            if record.get('task_id') != task['task_id'] or type(record.get('task_revision')) is not int:
                return None
            current_revision = record['task_revision'] == task['revision']
            same_input = (isinstance(record.get('input_after'), str) and bool(record['input_after'])
                and record.get('input_before') == record['input_after'])
            valid = same_input and record.get('status') == 'passed' and record.get('complete') is True
            if same_input and current_revision:
                task['input_fingerprint'] = record['input_after']
                task['verification_generated_paths'] = list(record.get('generated_paths', []))
            self._write(thread_id, task)
            for index, row in enumerate(record.get('steps', [])):
                kind = row.get('kind')
                if kind not in {'test', 'build', 'run'}:
                    continue
                name = row.get('name')
                started = row.get('started') is True
                status = ('unknown' if row.get('error_code') == 'approval_invalidated' else
                    'denied' if not started else 'interrupted' if row.get('cancelled')
                    else 'passed' if same_input and row.get('ok') is True
                    else 'failed' if row.get('ok') is False else 'unknown')
                self.record_evidence(thread_id, {
                    'id': f"verify-{record.get('run_id', '')}-{index}", 'kind': kind, 'source': 'runtime',
                    'status': status, 'task_revision': record['task_revision'], 'paths': list(record.get('task_scope', task['scope'])),
                    'input_fingerprint': record.get('input_after', ''),
                    # Only the configured process condition is covered here.
                    # No user-goal acceptance is linked by command success.
                    'coverage': 'complete' if same_input and started else 'unknown',
                    'summary': f"配置验证 {name}: {record.get('status', 'unknown')}",
                    'artifact_ref': f"verification:{record.get('run_id', '')}",
                }, acceptance_ids=[f'verify:{name}'])
                if current_revision:
                    for criterion in task['acceptance']:
                        binding = criterion.get('verification_binding')
                        if (not isinstance(binding, dict) or binding.get('source')!='user'
                                or binding.get('step')!=name or criterion['kind']!=kind):
                            continue
                        self.record_evidence(thread_id, {
                            'id': f"verify-{record.get('run_id', '')}-{index}-"+observation_digest(criterion['id'])[:16],
                            'kind':kind,'source':'runtime','status':status,'task_revision':record['task_revision'],
                            'paths':list(binding['paths']),'input_fingerprint':record.get('input_after',''),
                            'coverage':'complete' if same_input and started else 'unknown',
                            'summary':f"按用户声明的覆盖绑定执行配置验证 {name}。",
                            'artifact_ref':f"verification:{record.get('run_id','')}",
                        }, acceptance_ids=[criterion['id']])
            task = self._read(thread_id)
            if record.get('status') in {'cancelled','invalidated'} and current_revision:
                task['lifecycle'] = 'paused'
                task['blockers'] = ['本任务验证中断或输入失效；先核实已执行操作。']
                self._invalidate(task, '验证中断或输入失效。')
            if valid and current_revision:
                task['pending_verification'] = []
            return self._write(thread_id, task)

    def user_decision(self, thread_id, criterion_id, reason, *, current_input_fingerprint, waive=False):
        """Explicit local command only; never invoke from model tool arguments."""
        if not isinstance(reason, str) or not reason.strip() or not current_input_fingerprint:
            raise ValueError('用户确认需要说明和可核实的当前输入。')
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                raise ValueError('当前会话没有任务。')
            criterion = next((row for row in task['acceptance'] if row['id'] == criterion_id), None)
            if criterion is None or criterion_id.startswith('verify:'):
                raise ValueError('请选择用户目标验收；配置流程条件不能由人工声明代替。')
            if not waive and criterion['kind'] != 'manual':
                raise ValueError('confirm 仅用于 manual 验收；测试、构建或审查需要对应的实际证据。')
            if task.get('input_fingerprint') and task['input_fingerprint'] != current_input_fingerprint:
                self._invalidate(task, '用户确认前项目输入变化，旧验证已失效。')
            task['input_fingerprint'] = current_input_fingerprint
            self._write(thread_id, task)
            evidence = {'kind': 'manual', 'source': 'user', 'status': 'passed',
                'coverage': 'complete', 'paths': list(dict.fromkeys(task['scope'] + task['changed_paths'])),
                'input_fingerprint': current_input_fingerprint, 'summary': reason,
                'artifact_ref': f'user-decision:{criterion_id}'}
            updated = self.record_evidence(thread_id, evidence,
                acceptance_ids=[] if waive else [criterion_id])
            if waive:
                target = next(row for row in updated['acceptance'] if row['id'] == criterion_id)
                target.update(status='waived', evidence_ids=[updated['evidence'][-1]['id']])
            from nailong.core.delivery import build_delivery_report
            candidate = dict(updated, pending_verification=[])
            if build_delivery_report(candidate,
                    current_input_fingerprint=current_input_fingerprint)['status'] in {'verified', 'reviewed'}:
                updated['pending_verification'] = []
            return self._write(thread_id, updated)

    def reconcile(self, thread_id, *, current_input_fingerprint=None, interrupted=False):
        """Recheck observed inputs; never restart previously issued operations."""
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                return None
            previous = task.get('input_fingerprint')
            if previous and previous != current_input_fingerprint:
                self._invalidate(task, '项目输入已变化或无法核实，需重新验证。')
                task['lifecycle'] = 'active'
            if interrupted:
                task['lifecycle'] = 'paused'
                task['blockers'] = ['运行已中断；先核实已执行操作，再继续。']
            return self._write(thread_id, task)

    def reset_progress(self, thread_id):
        """A deliberate new user turn is a chance to choose a different strategy."""
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                return None
            task['progress_observations'] = []
            task['progress_repeats'] = 0
            if task['lifecycle'] in {'paused', 'blocked'}:
                task['lifecycle'] = 'active'
                task['blockers'] = []
            return self._write(thread_id, task)

    def set_state(self, thread_id, *, phase=None, lifecycle=None, progress=None, blockers=None,
                  current_input_fingerprint=None):
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                return None
            if phase is not None:
                if phase not in PHASES:
                    raise ValueError('任务阶段无效。')
                task['phase'] = phase
            if blockers is not None:
                if not isinstance(blockers, list) or any(not isinstance(item, str) for item in blockers):
                    raise ValueError('阻塞必须为文本列表。')
                task['blockers'] = blockers
            if lifecycle is not None:
                if lifecycle not in LIFECYCLES:
                    raise ValueError('任务生命周期无效。')
                if lifecycle == 'completed':
                    from nailong.core.delivery import build_delivery_report
                    candidate = dict(task, lifecycle='active')
                    report = build_delivery_report(candidate, current_input_fingerprint=current_input_fingerprint)
                    if report['status'] not in {'verified', 'reviewed'}:
                        raise ValueError('完成任务需要当前有效证据；可交付明确标注的未验证结果。')
                task['lifecycle'] = lifecycle
            if progress is not None:
                task['progress'] = str(progress)[:2000]
            return self._write(thread_id, task)

    def observe_progress(self, thread_id, observation):
        with self._locked(thread_id):
            task = self._read(thread_id)
            if task is None:
                return None
            from nailong.core.progress import assess_progress
            result = assess_progress(task.get('progress_observations', []), observation,
                phase=task['phase'])
            task['progress_observations'] = result['observations']
            task['progress_repeats'] = result['repeats']
            if result['action'] == 'pause':
                task['lifecycle'] = 'paused'
                task['blockers'] = [result['reason']]
            self._write(thread_id, task)
            return result


def observation_digest(value):
    """Fingerprint data without persisting tool arguments or original bodies."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        default=str, separators=(',', ':')).encode()).hexdigest()
