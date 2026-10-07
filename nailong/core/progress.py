"""Conservative repetition detection based on observations, never token count."""
from __future__ import annotations

WINDOW = 12
READ_TOOLS = frozenset({'read_file', 'glob', 'grep', 'search_text', 'list_files',
    'memory_read', 'memory_list', 'read_memory', 'read_history_result', 'read_tool_result'})
_FIELDS = ('tool', 'arguments_digest', 'result_digest', 'input_version', 'ok', 'changed', 'status')


def _row(observation):
    return {key: observation.get(key) for key in _FIELDS}


def _stable(row):
    return (isinstance(row['tool'], str) and row['tool'] in READ_TOOLS
        and row['ok'] is True and row['changed'] is False
        and isinstance(row['arguments_digest'], str) and bool(row['arguments_digest'])
        and isinstance(row['result_digest'], str) and bool(row['result_digest'])
        and row['status'] in (None, 'success', 'passed'))


def _cycle_run(rows, length):
    """Count a stable periodic suffix with distinct requests in each cycle slot."""
    if len(rows) < length * 2:
        return 0
    unit = rows[-length:]
    if not all(_stable(row) for row in unit):
        return 0
    # A single request whose result/version changes is polling, not a read cycle.
    identities = {(row['tool'], row['arguments_digest']) for row in unit}
    if len(identities) != length:
        return 0
    count = 0
    for row in reversed(rows):
        if row != unit[-1 - count % length]:
            break
        count += 1
    return count if count >= length * 2 else 0


def assess_progress(previous, observation, *, phase='investigate'):
    """Repeated successful reads and two/three-request cycles prompt, then stop.

    Arguments include range/cursor in their digest, so paging is distinct.
    Commands/polling, unknown results and declared state changes are not stopped
    by this heuristic. A broader graph/cost budget still applies to them.
    Cycles hint after two complete repetitions, then pause after four in
    investigation or three in other phases; partial repetitions never trigger.
    """
    # Malformed history is a barrier, never silently removed to join two runs.
    rows = [_row(row if isinstance(row, dict) else {}) for row in previous[-WINDOW:]]
    current = _row(observation)
    rows = (rows + [current])[-WINDOW:]
    stable = _stable(current)
    repeats = 0
    if stable:
        for row in reversed(rows[:-1]):
            if row != current:
                break
            repeats += 1
    repeats += 1 if stable else 0
    # Investigation gets extra room; low edit ratio is never a signal.
    pause_at = 6 if phase == 'investigate' else 5
    action = 'pause' if repeats >= pause_at else 'hint' if repeats == 3 else 'none'
    cycle_length = 0
    cycles = 0
    if stable and action == 'none':
        for length in (2, 3):
            run = _cycle_run(rows, length)
            if not run:
                continue
            cycle_length = length
            cycles = run // length
            repeats = run
            if run % length == 0:
                pause_cycles = 4 if phase == 'investigate' else 3
                action = 'pause' if cycles >= pause_cycles else 'hint' if cycles == 2 else 'none'
            break
    reason = ''
    if action != 'none':
        reason = (f'最近 {repeats} 次成功只读操作重复同一个 {cycle_length} 步读取周期，'
            f'共 {cycles} 个完整周期；观察到的参数、结果与版本标识未变化。'
            if cycle_length else f'连续 {repeats} 次相同读取获得相同结果，未观察到新信息。')
        reason += '本轮已暂停；请核对范围或换一种调查方法。' if action == 'pause' else '请缩小问题、检查其他证据或调整下一步。'
    return {'action': action, 'reason': reason, 'repeats': repeats,
        'observations': rows[-WINDOW:]}
