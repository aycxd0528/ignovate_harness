"""Reviewed feedback candidates publish to the existing project memory store."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nailong.core.control_ledger import bounded_text
from nailong.core.file_locks import file_lock
from nailong.core.memory import MemoryStore
from nailong.core.memory_knowledge import project_identity
from nailong.core.preferences import atomic_json, read_config, safe_config_path
from nailong.core.verification import current_evidence

SOURCE = re.compile(r'session:[A-Za-z0-9_-]{1,80}#(?:event|node):[A-Za-z0-9_-]{1,80}\Z')


@dataclass(frozen=True)
class _FeedbackInput:
    """Native scan context; never accepted from model or HTTP payload fields."""
    project: Path
    boundary: Path
    operation: str
    identity: str
    task: dict
    document: str
    generated: tuple
    candidate: dict | None

    @property
    def needs_scan(self):
        return self.candidate is None or self.operation == 'confirm' and self.candidate['state'] == 'candidate'

    def observed(self, fingerprint):
        return _FeedbackObservation(self, fingerprint)


@dataclass(frozen=True)
class _FeedbackObservation:
    context: _FeedbackInput
    fingerprint: str | None


class FeedbackStore:
    def __init__(self, sessions, *, memory_store=None):
        self.sessions = sessions
        self.boundary = sessions.root
        self.project = sessions.project_root
        self.memories = memory_store or MemoryStore(self.project, api_key=sessions.api_key)
        self.directory = self.boundary / 'control' / 'feedback'

    def _path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{64}', identity):
            raise ValueError('反馈 ID 无效。')
        return safe_config_path(self.directory / f'{identity}.json', self.boundary)

    def _task(self, task):
        if (not isinstance(task, dict) or task.get('project_root') != str(self.project)
                or not isinstance(task.get('task_id'), str) or type(task.get('revision')) is not int):
            raise ValueError('反馈不属于当前项目任务。')

    def _safe(self, text):
        return text.replace(self.sessions.api_key, '[密钥已隐藏]') if self.sessions.api_key else text

    def _generated(self, task, document):
        # Publication changes these two owned outputs. Excluding only this
        # candidate's topic and lock permits readback recovery without masking
        # changes to other project inputs or independently edited experiences.
        path = self.memories.document_path(document)
        return (str(path.relative_to(self.project)), str(path.with_suffix('.lock').relative_to(self.project)),
            *(str(parent.relative_to(self.project)) for parent in path.parents
              if parent != self.project and parent.is_relative_to(self.project)))

    def _fingerprint(self, task, document):
        return current_evidence(self.project, task, self.boundary, self._generated(task, document))

    def _request_identity(self, request_id):
        request_id = bounded_text(request_id, '请求 ID', 128)
        return hashlib.sha256((str(self.boundary) + ':' + request_id).encode()).hexdigest()

    def _input_context(self, operation, task, identity, document, row):
        self._task(task)
        return _FeedbackInput(self.project, self.boundary, operation, identity, copy.deepcopy(task),
            document, self._generated(task, document), copy.deepcopy(row))

    def _prepare_proposal(self, *, task, request_id):
        identity = self._request_identity(request_id)
        row = self.get(identity) if self._path(identity).exists() else None
        document = row['document'] if row else f'project/feedback-{identity[:24]}.md'
        return self._input_context('propose', task, identity, document, row)

    def _prepare_confirmation(self, identity, *, task):
        row = self.get(identity)
        return self._input_context('confirm', task, identity, row['document'], row)

    def _observed_fingerprint(self, observation, operation, task, identity, document, row):
        # Recheck inside the candidate lock, including the complete candidate
        # and exact output exclusions. A hash for another topic is not usable.
        if (not isinstance(observation, _FeedbackObservation)
                or observation.context != self._input_context(operation, task, identity, document, row)):
            raise ValueError('反馈任务、候选内容或目标文档已变化，请重新核对。')
        fingerprint = observation.fingerprint
        if fingerprint is not None and (not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', fingerprint)):
            raise ValueError('反馈输入观察无效。')
        return fingerprint

    def get(self, identity):
        path = self._path(identity)
        row = read_config(path, self.boundary)
        if (not row or row.get('schema_version') != 1 or row.get('id') != identity
                or row.get('project_id') != project_identity(self.project)
                or row.get('state') not in {'candidate', 'confirmed', 'revoked'}
                or type(row.get('revision')) is not int or row['revision'] < 1):
            raise ValueError('反馈记录不存在或身份无效。')
        return copy.deepcopy(row)

    def propose(self, *, task, source_ref, context, outcome, behavior, boundary, request_id,
                source_input_fingerprint=None, _observation=None):
        self._task(task)
        if not isinstance(source_ref, str) or not SOURCE.fullmatch(source_ref):
            raise ValueError('反馈需要准确的会话回合来源。')
        identity = self._request_identity(request_id)
        data = {key: self._safe(bounded_text(value, key)) for key, value in {
            'context': context, 'outcome': outcome, 'behavior': behavior, 'boundary': boundary}.items()}
        if source_input_fingerprint is not None and (not isinstance(source_input_fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', source_input_fingerprint)):
            raise ValueError('反馈来源输入版本无效。')
        data.update(task_id=task['task_id'], task_revision=task['revision'], source_ref=source_ref,
                    source_input_fingerprint=source_input_fingerprint,
                    project_id=project_identity(self.project))
        signature = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        path = self._path(identity)
        with file_lock(safe_config_path(path.with_suffix('.lock'), self.boundary)):
            row = self.get(identity) if path.exists() else None
            document = row['document'] if row else f'project/feedback-{identity[:24]}.md'
            if _observation is not None:
                fingerprint = self._observed_fingerprint(_observation, 'propose', task, identity, document, row)
            if row is not None:
                if row['signature'] != signature:
                    raise ValueError('反馈请求 ID 已用于不同内容。')
                return row
            if _observation is None:
                try:
                    fingerprint = self._fingerprint(task, document)
                except (OSError, ValueError):
                    fingerprint = None
            row = {**data, 'schema_version': 1, 'id': identity, 'signature': signature,
                'state': 'candidate', 'revision': 1, 'input_fingerprint': fingerprint,
                'document': document, 'memory_version': None,
                'created_at': datetime.now(timezone.utc).isoformat(), 'utility': 'unknown'}
            atomic_json(path, row, self.boundary)
            return copy.deepcopy(row)

    def _content(self, row, *, revoked=False):
        knowledge = {'type': 'experience', 'state': 'revoked' if revoked else 'confirmed',
            'origin': 'user', 'applicability': {'scope': 'project', 'project_id': row['project_id'], 'paths': []},
            'sources': [{'kind': 'reference', 'ref': row['source_ref']}]}
        if not revoked:
            knowledge['confirmed_by'] = 'user'
        frontmatter = json.dumps({'description': row['context'][:180], 'knowledge': knowledge}, ensure_ascii=False)
        if revoked:
            body = '# 已撤销的反馈经验\n\n此记录保留撤销身份，不再用于建议或行动。\n'
        else:
            body = (f"# 经用户确认的项目经验\n\n## 适用情境\n{row['context']}\n\n"
                f"## 已观察结果与反馈\n{row['outcome']}\n\n## 建议的未来行为\n{row['behavior']}\n\n"
                f"## 失效边界\n{row['boundary']}\n\n"
                '用户确认是经验声明，不能代替测试或授予动作权限。效用归因：未知。\n')
        return '---\n' + frontmatter + '\n---\n\n' + body

    def confirm(self, identity, *, task, expected_revision, _observation=None):
        self._task(task)
        path = self._path(identity)
        with file_lock(safe_config_path(path.with_suffix('.lock'), self.boundary)):
            row = self.get(identity)
            if _observation is not None:
                fingerprint = self._observed_fingerprint(_observation, 'confirm', task, identity, row['document'], row)
            if (task['task_id'], task['revision']) != (row['task_id'], row['task_revision']):
                raise ValueError('反馈来源任务或要求版本已变化。')
            if row['state'] == 'confirmed' and expected_revision in {row['revision'], row['revision'] - 1}:
                memory = self.memories.read_document(row['document'])
                if memory.get('version') != row['memory_version']:
                    raise ValueError('已确认经验被修改，请重新核对。')
                return row
            if row['state'] != 'candidate' or type(expected_revision) is not int or expected_revision != row['revision']:
                raise ValueError('反馈状态或版本发生冲突。')
            if _observation is None:
                fingerprint = self._fingerprint(task, row['document'])
            if not fingerprint or fingerprint != row['input_fingerprint']:
                raise ValueError('反馈来源输入版本已变化，请重新整理候选。')
            content = self._content(row)
            edit = self.memories.stage_document(row['document'], content)
            if edit.before_content and edit.before_content != content:
                raise ValueError('经验目标已有不同内容，未覆盖。')
            if edit.before_content != content:
                self.memories.commit_document(edit)
            memory = self.memories.read_document(row['document'], limit=16000)
            if not memory.get('ok') or memory.get('truncated') or memory.get('content') != content:
                raise ValueError('经验写入读回无法确认。')
            row.update(state='confirmed', revision=row['revision'] + 1, memory_version=memory['version'])
            atomic_json(path, row, self.boundary)
            return copy.deepcopy(row)

    def revoke(self, identity, *, expected_revision):
        path = self._path(identity)
        with file_lock(safe_config_path(path.with_suffix('.lock'), self.boundary)):
            row = self.get(identity)
            if row['state'] == 'revoked' and expected_revision in {row['revision'], row['revision'] - 1}:
                return row
            if type(expected_revision) is not int or expected_revision != row['revision']:
                raise ValueError('撤销经验版本发生冲突。')
            content = self._content(row, revoked=True)
            edit = self.memories.stage_document(row['document'], content)
            if edit.before_digest is not None:
                already_retired = edit.before_content == content
                unsettled_publication = row['state'] == 'candidate' and edit.before_content == self._content(row)
                if not (already_retired or unsettled_publication or
                        row['state'] == 'confirmed' and edit.before_digest == row['memory_version']):
                    raise ValueError('经验已被独立修改，请先核对版本。')
                if not already_retired:
                    self.memories.commit_document(edit)
                recalled = self.memories.read_document(row['document'])
                if recalled.get('status') != 'revoked' or recalled.get('version') != hashlib.sha256(content.encode()).hexdigest():
                    raise ValueError('经验撤销读回无法确认。')
                row['memory_version'] = recalled['version']
            row.update(state='revoked', revision=row['revision'] + 1)
            atomic_json(path, row, self.boundary)
            return copy.deepcopy(row)

    def list(self, limit=128):
        directory = safe_config_path(self.directory, self.boundary)
        if not directory.exists():
            return []
        return [self.get(path.stem) for path in sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]]
