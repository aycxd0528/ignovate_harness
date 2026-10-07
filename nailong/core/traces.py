"""Project-scoped projections of observable session events, never private reasoning.

No producer changes are required. Sequence numbers count valid journal records;
derived run IDs are stable while that journal prefix is unchanged. Chronological
grouping does not establish model/tool causality or missing child-call identities.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from nailong.core.permissions import ApprovalDecision, Decision, PermissionEngine
from nailong.core.preferences import safe_config_path
from nailong.tools.files import FileSession


_TEXT_FIELDS = ('name', 'call_id', 'tool_call_id', 'message_id', 'model', 'requested_model',
                'provider_host', 'scope', 'profile', 'status', 'coverage', 'path',
                'kind', 'error_type', 'plan_id', 'task_id')
_NUMBER_FIELDS = ('model_call', 'model_call_limit', 'elapsed_ms', 'exit_code', 'revision')
_BOOL_FIELDS = ('ok', 'timed_out', 'visible', 'local', 'summary_only', 'rewound',
                'output_truncated', 'usage_complete')
_USAGE_FIELDS = ('input_tokens', 'output_tokens', 'total_tokens', 'cache_hit_tokens')
_STAT_FIELDS = ('model_calls', 'model_call_limit', 'tool_calls', 'graph_steps', 'graph_step_limit')
_BUDGET_NUMBER_FIELDS = ('limit_tokens', 'spent_tokens', 'reserved_tokens', 'remaining_tokens',
                         'final_reserved_tokens',
                         'input_tokens_upper_bound', 'output_tokens_requested', 'output_tokens_reserved',
                         'admission_tokens_estimate', 'reservation_tokens', 'charged_tokens')
_BUDGET_ENUM_FIELDS = {
    'event': {'refused', 'waiting', 'reserved', 'settled', 'final_reserved', 'final_released'},
    'input_method': {'utf8_upper_bound', 'prompt_only_estimate'},
    'reason': {'input_does_not_fit', 'admission_does_not_fit', 'concurrent_reservations',
               'usage_unknown', 'token_limit', 'final_answer_does_not_fit', 'final_answer_reserved'},
}
_BODY_FIELDS = ('text', 'content', 'reasoning', 'thinking', 'args', 'preview', 'summary',
                'error', 'output', 'output_preview', 'output_snippet', 'diff', 'raw')


def _text(value, secret=''):
    if not isinstance(value, str):
        return None
    value = value.replace(secret, '[密钥已隐藏]') if secret else value
    value = ''.join(char for char in value if char.isprintable())
    # Removing controls can join fragments into a previously unmatched secret.
    value = value.replace(secret, '[密钥已隐藏]') if secret else value
    return value[:512]


def _number(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) else None
    except OverflowError:
        return None


def _references(data, secret):
    result = []
    containers = [data]
    evidence = data.get('evidence')
    if isinstance(evidence, dict):
        containers.append(evidence)
    elif isinstance(evidence, list):
        containers.extend(row for row in evidence if isinstance(row, dict))
    for container in containers:
        for key in ('artifact_ref', 'result_ref', 'reference'):
            value = _text(container.get(key), secret)
            if value and value not in result:
                result.append(value)
    return result


def _facts(kind, data, secret):
    """Whitelist metadata instead of recopying sanitized-but-sensitive bodies."""
    result = {}
    for key in _TEXT_FIELDS:
        value = _text(data.get(key), secret)
        if value is not None:
            result[key] = value
    for key in _NUMBER_FIELDS:
        if key in data:
            result[key] = _number(data[key])
    for key in _BOOL_FIELDS:
        if type(data.get(key)) is bool:
            result[key] = data[key]
    if any(key in data for key in _BODY_FIELDS):
        result['content_omitted'] = True
    if kind in {'usage', 'model_usage_call'}:
        for key in _USAGE_FIELDS:
            value = _number(data.get(key))
            result[key] = value if value is not None and value >= 0 else None
    if kind == 'usage_missing':
        result['usage_complete'] = False
        if data.get('reason') in {'request_failed', 'cancelled_or_timed_out'}:
            result['reason'] = data['reason']
    if kind == 'subagent_budget':
        # Preserve the producer's diagnostic fields, not an error-body summary.
        for key in _BUDGET_NUMBER_FIELDS:
            value = data.get(key)
            result[key] = _number(value) if type(value) is int and value >= 0 else None
        for key, allowed in _BUDGET_ENUM_FIELDS.items():
            value = data.get(key)
            result[key] = value if isinstance(value, str) and value in allowed else None
        task_key = data.get('task_key')
        result['task_key'] = (_text(task_key, secret) if isinstance(task_key, str)
                              and len(task_key) == 16 and all(char in '0123456789abcdef' for char in task_key)
                              else None)
        result['usage_complete'] = data.get('usage_complete') if type(data.get('usage_complete')) is bool else None
    if data.get('ok') is False or data.get('error') or kind == 'error':
        result['has_error'] = True
    if isinstance(data.get('stats'), dict):
        result['stats'] = {key: _number(data['stats'][key]) for key in _STAT_FIELDS if key in data['stats']}
    delivery = data.get('delivery')
    if isinstance(delivery, dict):
        result['delivery_status'] = _text(delivery.get('status'), secret)
    if kind == 'approval_needed' and isinstance(data.get('actions'), list):
        result['actions'] = [
            {key: _text(row[key], secret) for key in ('name', 'call_id') if key in row}
            for row in data['actions'] if isinstance(row, dict)
        ]
    if kind == 'approval_decision':
        result['decision'] = _text(data.get('kind'), secret)
    references = _references(data, secret)
    if references:
        result['artifact_refs'] = references
    return result


def build_trace(events, thread_id, *, api_key=''):
    """Project one snapshot of the latest turn; never mutate source records."""
    records = [row for row in events if isinstance(row, dict) and isinstance(row.get('data', {}), dict)]
    start = next((index for index in range(len(records) - 1, -1, -1)
                  if records[index].get('kind') == 'turn_start'), None)
    run_id = None
    run_id_source = 'unknown'
    if start is not None:
        recorded = records[start].get('data', {}).get('run_id')
        if isinstance(recorded, str) and recorded.strip() and len(recorded) <= 256:
            run_id = _text(recorded, api_key)
            run_id_source = 'recorded'
        else:
            identity = json.dumps([thread_id, start + 1, records[start]],
                                  ensure_ascii=False, sort_keys=True, default=str)
            run_id = 'derived-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
            run_id_source = 'derived'
    rows = []
    calls = {}
    offset = start if start is not None else 0
    start_counts = {}
    for source in records[offset:]:
        data = source.get('data', {})
        call_id = data.get('call_id') or data.get('tool_call_id')
        if (source.get('kind') == 'tool_start' and data.get('rewound') is not True
                and isinstance(call_id, str) and call_id):
            start_counts[call_id] = start_counts.get(call_id, 0) + 1
    for index in range(offset, len(records)):
        source = records[index]
        data = source.get('data', {})
        kind = _text(source.get('kind'), api_key) or 'unknown'
        sequence = index + 1
        # Rewind retains old usage after the new boundary; it is not this run's usage.
        row_run = None if data.get('rewound') is True else run_id
        row = {'record_type': 'event', 'sequence': sequence,
               'event_id': f"{run_id or 'unbound'}:{sequence}", 'run_id': row_run,
               'kind': kind, 'timestamp': _text(source.get('timestamp'), api_key),
               'parent_event_id': None, 'data': _facts(kind, data, api_key)}
        call_id = data.get('call_id') or data.get('tool_call_id')
        if row_run and isinstance(call_id, str) and call_id:
            if kind == 'tool_start' and start_counts.get(call_id) == 1:
                calls[call_id] = (row['event_id'], data.get('name'), data.get('scope'))
            elif kind == 'tool_end':
                parent = calls.get(call_id)
                if (parent is not None and parent[1] and parent[1] == data.get('name')
                        and parent[2] == data.get('scope')):
                    row['parent_event_id'] = parent[0]
        rows.append(row)
    return {
        'schema_version': 1, 'thread_id': _text(thread_id, api_key), 'run_id': run_id,
        'run_id_source': run_id_source, 'sequence_basis': 'valid_event_order',
        'boundary': 'turn_start' if start is not None else 'unknown',
        'state': ('complete' if any(row['kind'] == 'final' and row['run_id'] == run_id for row in rows)
                  else 'incomplete') if start is not None else 'unknown',
        'state_basis': 'final_event_only',
        'events': rows,
        'limitations': [
            '仅包含已记录的可观察事件，不包含模型私有思考；正文和原始工具内容省略。',
            '按最近 turn_start 之后的记录分组；时间顺序不证明因果，未证明的父子关联为 null。',
            'sequence 为有效日志记录顺序；derived run_id 在日志前缀不变时稳定，回退或重写后可能改变。',
            '用量事件可能包含子代理汇总与逐调用记录，不能直接重复相加；缺失信息保留为未知。',
            'complete 仅表示已记录 final 事件，不表示用户目标已验证或验收。',
            '轨迹导出仅支持项目内路径，包括使用 --dangerously-skip-permissions 时。',
        ],
    }


class TraceActions:
    def __init__(self, project_root, store, *, api_key=''):
        self.root = Path(project_root).resolve()
        self.store = store
        self.api_key = api_key

    def latest(self, thread_id):
        self.store.session_path(thread_id)
        return build_trace(self.store.read_events(thread_id), thread_id, api_key=self.api_key)

    @staticmethod
    def render(report):
        rows = report['events']
        state = {'complete': '已记录最终事件', 'incomplete': '尚未记录最终事件', 'unknown': '未知'}[report['state']]
        lines = ['运行轨迹（可观察事件，正文省略；不包含模型私有思考）',
                 f"轮次：{report['run_id'] or '未知'} · {report['run_id_source']} · {state}",
                 f"共 {len(rows)} 个事件；显示最近 {min(len(rows), 50)} 个。"]
        if not rows:
            lines.append('当前会话没有已保存的运行事件。')
        for row in rows[-50:]:
            detail = json.dumps(row['data'], ensure_ascii=False, separators=(',', ':'))
            lines.append(f"{row['sequence']}. {row['timestamp'] or '时间未知'} · {row['kind']} · {detail}")
        lines.extend(report['limitations'])
        return '\n'.join(lines)

    async def export(self, thread_id, path=None, *, approval=None,
                     permission_engine=None, permission_mode='default'):
        self.store.session_path(thread_id)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        destination = Path(path) if path else Path('.nailong/exports') / f'{thread_id}-trace-{stamp}.jsonl'
        if not destination.is_absolute():
            destination = self.root / destination
        if self.api_key and self.api_key in str(destination):
            raise ValueError('导出路径包含配置密钥，已拒绝。')
        safe_config_path(destination, self.root)
        destination = FileSession(self.root).resolve(str(destination), allow_missing=True)
        safe_config_path(destination, self.root)
        engine = permission_engine or PermissionEngine(self.root)
        arguments = {'path': str(destination)}
        decision = engine.decide_action('write_file', arguments, mode=permission_mode)
        if decision.decision == Decision.DENY:
            return {'written': False, 'path': str(destination), 'reason': decision.reason}
        report = self.latest(thread_id)
        manifest = {'record_type': 'trace_manifest', **{key: value for key, value in report.items() if key != 'events'}}
        content = ''.join(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n'
                          for row in [manifest, *report['events']])
        digest = hashlib.sha256(destination.read_bytes()).digest() if destination.exists() else None
        approved = False
        if decision.decision == Decision.ASK:
            preview = '将写入的 JSONL（原文件内容不展开）：\n' + content[:12000]
            if len(content) > 12000:
                preview += '\n…新内容预览已截断；导出包含本快照的全部事件。'
            action = {'name': 'write_file', 'args': arguments,
                      'preview': {'path': str(destination), 'overwrite': digest is not None,
                                  'event_count': len(report['events']),
                                  'summary': '导出最近一轮的安全 JSONL 元数据；正文省略。',
                                  'diff': preview}}
            value = approval(action, 1, 1) if approval else 'reject'
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, ApprovalDecision):
                value = value.kind
            elif isinstance(value, dict):
                value = value.get('type') or value.get('kind')
            approved = isinstance(value, str) and value in {'approve', 'approve_once', 'approve_session'}
            if not approved:
                return {'written': False, 'path': str(destination), 'reason': '导出写入未批准。'}
        # Recheck both redirection and policy after awaiting approval.
        safe_config_path(destination, self.root)
        FileSession(self.root).resolve(str(destination), allow_missing=True)
        current = hashlib.sha256(destination.read_bytes()).digest() if destination.exists() else None
        if current != digest:
            raise ValueError('导出目标被其他进程修改。')
        decision = engine.decide_action('write_file', arguments, mode=permission_mode)
        if decision.decision == Decision.DENY or (decision.decision == Decision.ASK and not approved):
            return {'written': False, 'path': str(destination), 'reason': decision.reason}
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=destination.parent, prefix='.trace-')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            safe_config_path(destination, self.root)
            current = hashlib.sha256(destination.read_bytes()).digest() if destination.exists() else None
            if current != digest:
                raise ValueError('导出目标被其他进程修改。')
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {'written': True, 'path': str(destination), 'run_id': report['run_id'],
                'event_count': len(report['events'])}
