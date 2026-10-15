"""Validated relationships between project work items and execution sessions.

Only relationships live here. Requirements, acceptance and evidence remain in
one TaskStore authority file; permissions and goal budgets are never copied.
Callers hold the relationship lock while reading or changing this index.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from nailong.core.preferences import atomic_json, read_config, safe_config_path


class WorkItemRelationships:
    def __init__(self, sessions):
        self.sessions = sessions
        self.boundary = Path(sessions.root).resolve()
        self.project_root = str(Path(sessions.project_root).resolve())
        self.directory = self.boundary / 'tasks'
        self.path = safe_config_path(self.directory / 'work-items' / 'index.json', self.boundary)
        self.lock_path = safe_config_path(self.directory / 'work-items' / 'index.lock', self.boundary)

    def empty(self):
        return {'schema_version': 1, 'project_root': self.project_root,
            'revision': 0, 'items': {}, 'bindings': {}}

    @staticmethod
    def validate_task_id(task_id):
        if not isinstance(task_id, str) or not task_id or '\0' in task_id:
            raise ValueError('工作项身份无效。')

    def authority_path(self, relative):
        if (not isinstance(relative, str) or not relative or '\\' in relative
                or '\0' in relative or Path(relative).is_absolute()
                or '..' in Path(relative).parts or Path(relative).suffix != '.json'
                or relative == 'work-items/index.json'
                or len(Path(relative).parts) > 2
                or len(Path(relative).parts) == 2 and Path(relative).parts[0] != 'work-items'):
            raise ValueError('工作项权威路径无效。')
        return safe_config_path(self.directory / relative, self.boundary)

    def fresh_authority(self, task_id):
        self.validate_task_id(task_id)
        digest = hashlib.sha256(task_id.encode()).hexdigest()
        return f'work-items/{digest}.json'

    def read(self):
        value = read_config(self.path, self.boundary)
        if not value:
            if self.path.exists():
                raise ValueError('工作项关联为空或损坏，未覆盖原文件。')
            return self.empty()
        self.validate(value)
        return value

    def validate(self, value):
        if (set(value) != {'schema_version', 'project_root', 'revision', 'items', 'bindings'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['project_root'] != self.project_root
                or type(value['revision']) is not int or value['revision'] < 0
                or not isinstance(value['items'], dict) or not isinstance(value['bindings'], dict)):
            raise ValueError('工作项关联身份或版本无效。')
        authorities = set()
        for task_id, item in value['items'].items():
            self.validate_task_id(task_id)
            if not isinstance(item, dict) or set(item) != {'owner_thread_id', 'authority'}:
                raise ValueError('工作项来源无效。')
            self.sessions.session_path(item['owner_thread_id'])
            self.authority_path(item['authority'])
            if item['authority'] in authorities:
                raise ValueError('不同工作项不能共享同一权威文件。')
            authorities.add(item['authority'])
        for thread_id, binding in value['bindings'].items():
            self.sessions.session_path(thread_id)
            if (not isinstance(binding, dict)
                    or set(binding) != {'task_id', 'kind', 'source_thread_id', 'source_revision', 'binding_revision'}
                    or not isinstance(binding['task_id'], str) or binding['task_id'] not in value['items']
                    or not isinstance(binding['kind'], str) or binding['kind'] not in {'owner', 'linked'}
                    or type(binding['source_revision']) is not int or binding['source_revision'] < 1
                    or type(binding['binding_revision']) is not int
                    or not 1 <= binding['binding_revision'] <= value['revision']):
                raise ValueError('会话工作项关联无效。')
            owner = value['items'][binding['task_id']]['owner_thread_id']
            if (binding['source_thread_id'] != owner
                    or (binding['kind'] == 'owner') != (thread_id == owner)):
                raise ValueError('会话工作项来源不一致。')

    def write(self, value):
        self.validate(value)
        if len((json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode()) > 900_000:
            raise ValueError('工作项关联超过持久化预算；保留旧状态。')
        atomic_json(self.path, value, self.boundary)

    def bind(self, value, thread_id, task_id, source_revision):
        """Change one session binding; the caller persists the whole index."""
        owner = value['items'][task_id]['owner_thread_id']
        value['revision'] += 1
        value['bindings'][thread_id] = {'task_id': task_id,
            'kind': 'owner' if thread_id == owner else 'linked',
            'source_thread_id': owner, 'source_revision': source_revision,
            'binding_revision': value['revision']}
