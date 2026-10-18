"""One call ledger for actual usage; request estimates remain separate evidence."""
from __future__ import annotations

from types import SimpleNamespace


def canonical_usage_events(events):
    """Coalesce identified calls without guessing identities for legacy records."""
    output, positions = [], {}
    sources = {'model_response': 1, 'subagent_model_response': 1, 'model_usage_call': 2,
               'usage': 3, 'usage_missing': 0}

    def observe(row, source, outer, index, nested=None):
        row = dict(row)
        identity = row.get('model_call_id') or outer.get('model_call_id')
        if identity:
            row['model_call_id'] = identity
        key = str(identity) if identity else ('legacy', index, nested)
        known = source != 'usage_missing' and row.get('input_tokens') is not None and row.get('output_tokens') is not None
        # A legacy input-only context sample is useful but not a complete bill.
        # Preserve its fields; the accounting validator keeps completeness false.
        kind = 'usage_missing' if source == 'usage_missing' else 'usage'
        candidate = {'kind': kind, 'data': row,
                     **{key: outer[key] for key in ('run_id', 'thread_id', 'timestamp') if key in outer}}
        rank = (int(known), sources[source])
        if key not in positions:
            positions[key] = (len(output), rank)
            output.append(candidate)
        else:
            position, previous_rank = positions[key]
            previous=output[position]['data']
            if known and previous_rank[0] and any(previous.get(field)!=row.get(field)
                for field in ('input_tokens','output_tokens','cache_hit_tokens')
                if previous.get(field) is not None and row.get(field) is not None):
                row={**row,'estimated':True,'usage_conflict':True}
                candidate['data']=row
                output[position]=candidate
                positions[key]=(position,rank)
                return
            if previous.get('usage_conflict'):
                candidate['data']={**row,'estimated':True,'usage_conflict':True}
            if rank > previous_rank:
                output[position] = candidate
                positions[key] = (position, rank)

    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        kind, data = event.get('kind'), event.get('data') or {}
        if kind not in sources:
            output.append(event)
            continue
        if not isinstance(data,dict):
            output.append({'kind':'usage_missing','data':{'reason':'invalid_usage_record'}})
            continue
        outer = {**event, **{key: data[key] for key in ('model_call_id',) if key in data}}
        if kind in {'model_response', 'subagent_model_response'}:
            # Older prose-only diagnostics are not authoritative call records.
            if not data.get('model_call_id'):
                continue
            observe({**(data.get('usage') or {}), 'scope': 'subagent' if kind.startswith('subagent') else 'main'},
                    kind if data.get('usage') is not None else 'usage_missing', outer, index)
        elif isinstance(data.get('calls'), list) and data['calls']:
            for nested, call in enumerate(data['calls']):
                if isinstance(call, dict):
                    inherited = {key: data[key] for key in ('scope', 'estimated', 'price_snapshot', 'task_id', 'goal_id', 'dispatch_id') if key in data}
                    observe({**inherited, **call}, kind, event, index, nested)
        else:
            observe(data, kind, outer, index)
    return output


def usage_projection(events):
    from nailong.core.diagnostics import usage_snapshot

    def actual(rows):
        return usage_snapshot(SimpleNamespace(read_events=lambda _: rows), None)

    canonical = canonical_usage_events(events)
    turns, current = [], None
    for index, row in enumerate(canonical):
        data = row.get('data') or {}
        if row.get('kind') == 'turn_start':
            if data.get('visible') is not False or current is None:
                current = {'turn_id': data.get('turn_id') or f'legacy-{index}',
                           'estimate': None, '_events': []}
                turns.append(current)
        if current is None:
            current = {'turn_id': 'legacy', 'estimate': None, '_events': []}
            turns.append(current)
        current['_events'].append(row)
        if row.get('kind') == 'context_request' and data.get('profile') != 'subagent':
            current['estimate'] = {key: data[key] for key in (
                'request_id', 'estimated_tokens', 'raw_estimated_tokens', 'categories',
                'tools', 'model', 'provider_host', 'message_count') if key in data}
            current['estimate']['method'] = 'estimated'
    for turn in turns:
        turn['actual'] = actual(turn.pop('_events'))
    return {'cumulative': actual(canonical), 'turns': turns,
            'last_turn': turns[-1] if turns else None,
            'estimates_are_provider_usage': False}
