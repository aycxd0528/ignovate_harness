"""Bounded recovery receipts. Execution, evidence and billing keep their owners."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

from nailong.core.file_locks import file_lock
from nailong.core.preferences import atomic_json, read_config, safe_config_path

STAGES = ('started', 'effects_observed', 'evidence_validated', 'task_committed', 'cost_settled', 'closed')
DETAIL_KEYS = frozenset({'status', 'task_id', 'task_revision', 'evidence_ids', 'usage_ref',
    'calls', 'usage_complete', 'input_fingerprint', 'tool_calls', 'effect_state', 'delivery_status'})


def bounded_text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError(f'{label} 无效或超过长度限制。')
    return value.strip()


class ControlLedger:
    def __init__(self, sessions):
        self.sessions = sessions
        self.boundary = sessions.root
        self.directory = self.boundary / 'control' / 'receipts'

    def _path(self, identity):
        self.sessions.session_path(identity)
        return safe_config_path(self.directory / f'{identity}.json', self.boundary)

    def _read(self, identity):
        path = self._path(identity)
        row = read_config(path, self.boundary)
        if not row:
            if path.exists():
                raise ValueError('恢复回执为空或损坏。')
            return None
        if (row.get('schema_version') != 1 or row.get('run_id') != identity
                or row.get('project_id') != self.boundary.name or not isinstance(row.get('stages'), list)
                or len(row['stages']) > len(STAGES)):
            raise ValueError('恢复回执身份或格式无效。')
        self.sessions.session_path(row.get('thread_id'))
        seen = set()
        for stage in row['stages']:
            if (not isinstance(stage, dict) or stage.get('stage') not in STAGES
                    or stage['stage'] in seen or not isinstance(stage.get('details'), dict)
                    or set(stage['details']) - DETAIL_KEYS):
                raise ValueError('恢复回执阶段无效。')
            seen.add(stage['stage'])
        return row

    @staticmethod
    def _project(row):
        if row is None:
            return None
        result = copy.deepcopy(row)
        stages = {item['stage']: item['details'] for item in row['stages']}
        if stages.get('effects_observed', {}).get('effect_state') == 'unknown' or stages.get('effects_observed', {}).get('status') == 'unknown':
            recovery = 'readback_required'
        elif 'closed' not in stages:
            recovery = 'closeout_required'
        elif 'cost_settled' not in stages:
            recovery = 'usage_unknown'
        else:
            recovery = 'closed'
        result['recovery'] = recovery
        return result

    def begin(self, run_id, *, thread_id, request_id):
        self.sessions.session_path(thread_id)
        request_id = bounded_text(request_id, '请求 ID', 128)
        path = self._path(run_id)
        with file_lock(safe_config_path(path.with_suffix('.lock'), self.boundary)):
            existing = self._read(run_id)
            if existing:
                if existing['thread_id'] != thread_id or existing['request_id'] != request_id:
                    raise ValueError('恢复回执身份冲突。')
                return self._project(existing)
            row = {'schema_version': 1, 'project_id': self.boundary.name, 'run_id': run_id,
                'thread_id': thread_id, 'request_id': request_id, 'stages': [
                    {'stage': 'started', 'recorded_at': datetime.now(timezone.utc).isoformat(), 'details': {}}]}
            atomic_json(path, row, self.boundary)
            return self._project(row)

    def record(self, run_id, stage, *, details):
        if stage not in STAGES or stage == 'started' or not isinstance(details, dict) or set(details) - DETAIL_KEYS:
            raise ValueError('恢复阶段或元数据无效。')
        details = copy.deepcopy(details)
        secret = self.sessions.api_key
        for key, value in list(details.items()):
            if isinstance(value, str) and secret:
                details[key] = value.replace(secret, '[密钥已隐藏]')
        if len(json.dumps(details, ensure_ascii=False, allow_nan=False).encode()) > 16000:
            raise ValueError('恢复回执元数据超过限制。')
        if stage == 'cost_settled' and (not isinstance(details.get('usage_ref'), str)
                or not details['usage_ref'] or type(details.get('calls')) is not int or details['calls'] < 1):
            raise ValueError('费用回执需要原生用量来源，不能推断费用。')
        path = self._path(run_id)
        with file_lock(safe_config_path(path.with_suffix('.lock'), self.boundary)):
            row = self._read(run_id)
            if row is None:
                raise ValueError('尚未记录运行身份。')
            previous = next((item for item in row['stages'] if item['stage'] == stage), None)
            if previous:
                if previous['details'] != details:
                    raise ValueError('同一恢复阶段出现冲突结果。')
                return self._project(row)
            row['stages'].append({'stage': stage, 'recorded_at': datetime.now(timezone.utc).isoformat(), 'details': details})
            atomic_json(path, row, self.boundary)
            return self._project(row)

    def get(self, run_id):
        return self._project(self._read(run_id))

    def list(self, limit=128):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('回执数量限制无效。')
        directory = safe_config_path(self.directory, self.boundary)
        if not directory.exists():
            return []
        paths = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        return [self.get(path.stem) for path in paths[:limit]]
