"""Check continuation of a saved runtime without certifying historical forks."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from nailong.core.project_execution import project_execution


def _result(allowed, checkpoint_id=None, reason=''):
    return {'allowed': allowed, 'checkpoint_id': checkpoint_id, 'reason': reason}


def _timestamp(value):
    try:
        timestamp = datetime.fromisoformat(value)
        return timestamp if timestamp.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def _latest_checkpoint(controller, thread_id):
    from langgraph.checkpoint.sqlite import SqliteSaver

    config = {'configurable': {'thread_id': thread_id, 'checkpoint_ns': ''}}
    saver = getattr(controller.factory, 'checkpointer', None)
    if saver is not None:
        return saver.get_tuple(config)
    database = controller.sessions.database_path
    if not database.is_file():
        return None
    # The existing database is initialized by the runtime. A readiness read
    # must not run saver setup/DDL or create a replacement empty database.
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        saver = SqliteSaver(connection)
        saver.is_setup = True
        return saver.get_tuple(config)


def _saved_continuation(controller, branch):
    sessions = controller.sessions
    events = sessions.read_events(branch['thread_id'])
    users = [i for i, event in enumerate(events) if event.get('kind') == 'user']
    if not users:
        return _result(False, reason='旧会话缺少可核对的末轮输入，请基于摘要创建新会话。')
    start = users[-1]
    finals = [i for i in range(start + 1, len(events)) if events[i].get('kind') == 'final']
    if not finals:
        return _result(False, reason='旧会话末轮尚未完成，请先恢复原任务或基于摘要新建。')
    end = finals[-1]
    if any(event.get('kind') in {'turn_start', 'user', 'error', 'task_paused'} for event in events[end + 1:]):
        return _result(False, reason='旧会话在末轮回答后仍有未完成活动，请先恢复原任务。')
    final = events[end].get('data') or {}
    if final.get('output_status') in {'truncated', 'budget_stopped', 'interrupted'}:
        return _result(False, reason='旧会话末轮已中断，请先恢复原任务或基于摘要新建。')
    saved = _latest_checkpoint(controller, branch['thread_id'])
    if saved is None:
        return _result(False, reason='旧会话没有持久化 checkpoint，请基于摘要创建新会话。')
    checkpoint = saved.checkpoint
    values = checkpoint.get('channel_values') or {}
    # A branch channel with value None still schedules a LangGraph step.
    if (saved.pending_writes or checkpoint.get('pending_sends') or values.get('__pregel_tasks')
            or any(key.startswith('branch:') for key in values)):
        return _result(False, reason='checkpoint 仍有未完成步骤或中断，请先恢复原任务。')
    messages = values.get('messages') or []
    calls, results = set(), set()
    for message in messages:
        calls.update(str(call['id']) for call in getattr(message, 'tool_calls', []) or [])
        if getattr(message, 'type', None) == 'tool':
            results.add(str(message.tool_call_id))
    if calls - results:
        return _result(False, reason='checkpoint 的工具交换尚未闭合，请先恢复原任务。')
    human = next((message for message in reversed(messages) if getattr(message, 'type', '') == 'human'), None)
    answer = messages[-1] if messages else None
    if (human is None or getattr(answer, 'type', '') != 'ai'
            or getattr(answer, 'tool_calls', None)):
        return _result(False, reason='checkpoint 不包含完整的末轮问答，请基于摘要创建新会话。')
    # Match exactly what the session logger records, including displayed goal/
    # plan inputs, secret redaction, and its intentional text length limit.
    from agent_service import _final_answer, _message_text

    display = (getattr(human, 'additional_kwargs', {}) or {}).get('nailong_display')
    prompt = display if isinstance(display, str) else _message_text(human.content)
    if (events[start].get('data', {}).get('text') != sessions._sanitize(prompt)
            or final.get('text') != sessions._sanitize(_final_answer({'messages': [answer]}))):
        return _result(False, reason='旧会话末轮问答与 checkpoint 不一致，请基于摘要创建新会话。')
    previous_input_or_final = max((i for i in range(start)
                                   if events[i].get('kind') in {'user', 'final'}), default=-1)
    boundary = next((event.get('data', {}) for event in reversed(events[previous_input_or_final + 1:start + 1])
                     if event.get('kind') == 'turn_start'), {})
    saved_turn = (saved.metadata or {}).get('nailong_turn_id')
    log_turn = boundary.get('turn_id')
    if saved_turn and log_turn and saved_turn != log_turn:
        return _result(False, reason='旧会话末轮与 checkpoint 的回合标识不一致，请基于摘要新建。')
    matching_turn = isinstance(saved_turn, str) and bool(saved_turn) and saved_turn == log_turn
    user_at = _timestamp(events[start].get('timestamp'))
    checkpoint_at = _timestamp(checkpoint.get('ts'))
    final_at = _timestamp(events[end].get('timestamp'))
    if any(timestamp is None for timestamp in (user_at, checkpoint_at, final_at)):
        if not matching_turn:
            return _result(False, reason='旧会话缺少可核对的时间或回合标识，请基于摘要创建新会话。')
    else:
        # Session timestamps are rounded down to seconds. The runtime's full
        # precision timestamp must belong to this input/final time window.
        user_floor = user_at.replace(microsecond=0)
        final_floor = final_at.replace(microsecond=0)
        if checkpoint_at < user_floor or checkpoint_at - final_floor >= timedelta(seconds=1):
            return _result(False, reason='旧会话末轮与 checkpoint 的时间不一致，请基于摘要创建新会话。')
    identity = checkpoint.get('id')
    if not identity:
        return _result(False, reason='旧会话的 checkpoint 标识缺失，请基于摘要创建新会话。')
    return _result(True, identity)


def continuation_state(controller, branch):
    """Return ``allowed``, ``checkpoint_id`` and a user-facing refusal ``reason``.

    Native GUI branches retain their existing continuation behavior. Imported
    logs require a complete, matching *current* runtime. This function never
    writes a checkpoint/task binding to an old node or changes its forkability.
    It acquires the project lease; callers already holding it may nest safely.
    Recheck immediately before execution because another process can append
    history after a snapshot or enqueue.
    """
    try:
        branch = controller.branches.branch(branch['id'])
        if branch['history_conflict']:
            return _result(False, reason='CLI 已回退或改写该会话，请基于摘要创建新会话。')
        # Native branches can enqueue behind another run without reading its
        # live runtime. Requiring the lease here would disable normal queuing.
        if not branch['legacy']:
            return _result(True)
        with project_execution(controller.sessions):
            branch = controller.branches.branch(branch['id'])
            if branch['history_conflict']:
                return _result(False, reason='CLI 已回退或改写该会话，请基于摘要创建新会话。')
            if not branch['legacy']:
                return _result(True)
            return _saved_continuation(controller, branch)
    except BlockingIOError:
        return _result(False, reason='当前项目正被其他运行占用，请稍后继续。')
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return _result(False, reason='无法核对旧会话的持久化 checkpoint，请基于摘要创建新会话。')
