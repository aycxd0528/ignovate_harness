"""Read-only GUI timeline of recorded, visible messages and execution events."""
from __future__ import annotations

from datetime import datetime

from nailong.core.branches import turn_timing

_TOOL_FIELDS = ('name', 'call_id', 'scope', 'preview', 'path', 'ok', 'elapsed_ms', 'exit_code',
                'summary', 'output_preview', 'output_snippet', 'output_truncated', 'error')
_META_FIELDS = ('status', 'model_call', 'model_call_limit', 'model', 'scope', 'kind',
                'input_tokens', 'output_tokens', 'elapsed_ms', 'summary', 'error')


def _time(value):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return float('inf')


def timeline_rows(events, identity):
    rows, tools = [], {}
    tokens = None
    for index, event in enumerate(events):
        kind, data = event.get('kind'), event.get('data', {})
        if not isinstance(data, dict) or data.get('rewound'):
            continue
        base = {'id': f'{identity}:{index}', 'timestamp': event.get('timestamp')}
        if kind in {'token', 'final'}:
            text = str(data.get('text', ''))
            if kind == 'token':
                if tokens is None:
                    tokens = {**base, 'role': 'assistant', 'label': '助手', 'text': ''}
                    rows.append(tokens)
                tokens['text'] += text
            elif tokens is not None and text.startswith(tokens['text']):
                tokens['text'] = text
                tokens = None
            else:
                rows.append({**base, 'role': 'assistant', 'label': '助手', 'text': text})
                tokens = None
            continue
        if kind in {'user', 'tool_start', 'tool_end', 'status', 'model_call', 'approval_needed', 'approval_decision', 'error', 'verification'}:
            tokens = None
        if kind == 'user':
            if not any(row['role'] == 'user' for row in rows):
                base['id'] = identity + ':input'
            rows.append({**base, 'role': 'user', 'label': '用户', 'text': str(data.get('text', ''))})
        elif kind in {'tool_start', 'tool_end'}:
            call_id = data.get('call_id') or data.get('id')
            key = (str(call_id), str(data.get('name')), str(data.get('scope'))) if call_id else None
            candidates = tools.get(key, []) if key else []
            row = candidates.pop() if kind == 'tool_end' and len(candidates) == 1 else None
            if row is None:
                row = {**base, 'role': 'tool', 'label': data.get('name') or '工具', 'data': {}}
                rows.append(row)
                if key and kind == 'tool_start':
                    tools.setdefault(key, []).append(row)
            row['data'].update({key: data[key] for key in _TOOL_FIELDS if key in data})
            row['status'] = ('running' if kind == 'tool_start' else 'failed' if data.get('ok') is False
                             else 'completed' if data.get('ok') is True else 'unknown')
            row['elapsed_ms'] = data.get('elapsed_ms')
        elif kind in {'status', 'model_call', 'approval_needed', 'approval_decision', 'error', 'verification'}:
            label = {'status': {'thinking': '思考中', 'tools': '调用工具', 'responding': '生成回答'}.get(data.get('status'), data.get('status') or '处理中'),
                     'model_call': f"模型调用 {data.get('model_call', '')}",
                     'approval_needed': '等待批准', 'approval_decision': '批准决定',
                     'error': '执行失败', 'verification': '验证'}[kind]
            rows.append({**base, 'role': 'model', 'label': label, 'kind': kind,
                         'data': {key: data[key] for key in _META_FIELDS if key in data},
                         'status': 'failed' if kind == 'error' else data.get('status')})
    return rows


def branch_trace(controller, branch_id, limit=80):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('轨迹轮数必须在 1–500 之间。')
    branch = controller.branches.branch(branch_id)
    nodes = controller.branches.path(branch_id)
    runs = {run['node_id']: run for run in controller.runs.values() if run.get('node_id')}
    turns = []
    for node in nodes:
        run = runs.get(node['id'])
        turns.append({'id': 'run_' + run['id'] if run else node['id'],
                      'node_id': node['id'], 'status': node['status'],
                      'node': node, 'run': run, '_start': (run or {}).get('started_at') or node['timing'].get('started_at') or node.get('created')})
    for run in controller.runs.values():
        if run['branch_id'] == branch_id and not run.get('node_id'):
            start = run.get('started_at') or run.get('queued_at')
            index = next((i for i, turn in enumerate(turns) if _time(turn['_start']) > _time(start)), len(turns))
            turns.insert(index, {'id': 'run_' + run['id'], 'node_id': None, 'status': run['status'],
                                 'node': None, 'run': run, '_start': start})
    for number, turn in enumerate(turns, 1):
        turn['round'] = number
        turn.pop('_start')
    selected = []
    for turn in turns[-limit:]:
        node, run = turn.pop('node'), turn.pop('run')
        if node:
            events = controller.branches.node_events(node)
        elif run['start_index'] is not None and run['status'] in {'running', 'waiting_approval', 'stopping'}:
            events = controller.sessions.read_events(branch['thread_id'])[run['start_index']:]
        else:
            events = []
        turn['rows'] = timeline_rows(events, turn['id'])
        if not any(row['role'] == 'user' for row in turn['rows']) and run:
            turn['rows'].insert(0, {'id': turn['id'] + ':input', 'role': 'user', 'label': '用户',
                                    'text': run['message'], 'timestamp': run.get('queued_at')})
        if not events and run:
            # A terminal run without a committed node owns no journal suffix.
            # Its persisted tool summaries remain observable after later runs.
            turn['rows'].extend({'id': turn['id'] + ':tool:' + str(index), 'role': 'tool',
                                 'label': tool.get('name', '工具'), 'status': tool.get('status', 'unknown'),
                                 'elapsed_ms': tool.get('elapsed_ms'),
                                 'data': {key: tool[key] for key in _TOOL_FIELDS if key in tool}}
                                for index, tool in enumerate(run.get('tools', [])))
        timing = controller._public_run(run) if run else turn_timing(events)
        turn['elapsed_ms'] = timing.get('elapsed_ms')
        turn['started_at'] = timing.get('started_at')
        turn['finished_at'] = timing.get('finished_at')
        turn['model_calls'] = sum(event.get('kind') == 'model_call' and not event.get('data', {}).get('rewound') for event in events)
        for row in turn['rows']:
            if row['role'] == 'tool' and row['status'] == 'running' and turn['status'] not in {'running', 'waiting_approval', 'stopping'}:
                row['status'] = 'unknown' if turn['status'] == 'completed' else 'interrupted'
        if run and run.get('error'):
            turn['rows'].append({'id': turn['id'] + ':error', 'role': 'model', 'label': '执行失败',
                                 'status': 'failed', 'text': run['error'], 'timestamp': run.get('finished_at')})
        selected.append(turn)
    known = [turn['elapsed_ms'] for turn in selected if isinstance(turn['elapsed_ms'], (int, float))]
    return controller.safe({'branch_id': branch_id, 'sequence': controller.sequence,
                            'total_turns': len(turns), 'has_more': len(turns) > limit, 'turns': selected,
                            'elapsed_ms': sum(known) if known else None,
                            'tool_calls': sum(row['role'] == 'tool' for turn in selected for row in turn['rows']),
                            'model_calls': sum(turn['model_calls'] for turn in selected)})
