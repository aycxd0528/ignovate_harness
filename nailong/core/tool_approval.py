"""Review a native tool batch before starting any of its operations."""
from __future__ import annotations

import copy
import json
import uuid
from contextvars import ContextVar

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage
from langchain_core.tools import ToolException
from langgraph.config import get_config
from langgraph.types import interrupt

from nailong.core.permissions import Decision
from nailong.tools.results import failure


active_batch_permit: ContextVar[tuple | None] = ContextVar('active_batch_permit', default=None)
_STATE = 'nailong_approval_batch'
_NONCE = '_nailong_approval_batch'


class ApprovalBatchState(AgentState):
    nailong_approval_batch: str | None


class BatchApprovalMiddleware(AgentMiddleware):
    """Keep opaque, once-only runtime permits outside model-controlled state.

    Checkpoints store only a capability identifier. Lost runtime capabilities
    fail closed; a forged state field cannot create a human approval.
    External approval callbacks retain their conservative per-tool behavior.
    """
    state_schema = ApprovalBatchState

    def __init__(self, execution, tools, profile, thread_id, task_snapshot_provider=None, *, capability_catalog=None):
        super().__init__()
        self.execution = execution
        self._tool_map = {tool.name: tool for tool in tools}
        self.profile = profile
        self.thread_id = thread_id or ''
        self.task_snapshot_provider = task_snapshot_provider
        self.capability_catalog = capability_catalog
        if not hasattr(execution, '_approval_batches'):
            execution._approval_batches = {}
            execution._pending_approval_batches = {}

    def _native(self):
        callback = getattr(self.execution, '_native_approval_handler', None)
        return callback is not None and self.execution.approval_handler is callback

    def _native_file_target(self, tool, arguments):
        """Resolve only proven core file mutations, never a same-named custom tool."""
        from nailong.tools.files import FileSession
        from nailong.tools.registry import TOOL_SPECS
        spec = tool.spec
        session = tool.file_session
        if (spec.name not in {'write_file', 'edit_file'} or spec.read_only
                or type(session) is not FileSession
                or session.project_root != self.execution.project_root
                or self.profile not in spec.profiles):
            return None
        code = getattr(spec.handler, '__code__', None)
        core = next((core for core in TOOL_SPECS if core.name == spec.name
                     and code is getattr(core.handler, '__code__', None)), None)
        if core is None or spec.handler.__globals__ is not core.handler.__globals__:
            return None
        try:
            bound = dict(zip(code.co_freevars,
                             (cell.cell_contents for cell in spec.handler.__closure__)))
        except (TypeError, ValueError):
            return None
        if (bound.get('file_session') is not session
                or ('profile' in bound and bound['profile'] != self.profile)):
            return None
        try:
            return session.resolve(arguments['path'], allow_missing=True)
        except (OSError, ValueError):
            # Normal preparation retains the original path/snapshot error.
            return None

    def _prepare(self, message):
        config = get_config()
        key = (self.thread_id, message.id,
               json.dumps(message.tool_calls, sort_keys=True, ensure_ascii=False))
        pending = self.execution._pending_approval_batches
        if key in pending:
            return key, pending[key]
        plan = {'id': uuid.uuid4().hex, 'thread': self.thread_id,
                'message_id': message.id, 'callback': self.execution.approval_handler,
                'profile': self.profile, 'task_provider': self.task_snapshot_provider,
                'task_binding': _task_binding(self.task_snapshot_provider, self.thread_id),
                'rows': {}, 'actions': []}
        prepared = []
        file_groups = {}
        for call in message.tool_calls:
            if self.capability_catalog is not None and not self.capability_catalog.bound(message, call['name']):
                continue
            marker = message.response_metadata.get('nailong_bound_capabilities')
            if isinstance(marker, dict) and call['name'] not in marker.get('names', []):
                continue
            tool = self._tool_map.get(call['name'])
            if tool is None or not hasattr(tool, 'spec'):
                continue
            args = call['args']
            try:
                args = tool._parse_input(copy.deepcopy(args), call['id'])
            except (TypeError, ValueError, AttributeError, ToolException):
                # The adapter will return its normal schema error, without a handler.
                continue
            prepared.append((call, tool, args))
            with self.execution.file_access_scope():
                target = self._native_file_target(tool, args)
            if target is not None:
                file_groups.setdefault(target, []).append(call['id'])
        conflicts = {identity for members in file_groups.values() if len(members) > 1
                     for identity in members}
        for call, tool, args in prepared:
            call_config = {**config, 'metadata': {**(config.get('metadata') or {}),
                                                'tool_call_id': call['id']}}
            if call['id'] in conflicts:
                permission, action = None, None
                result = failure('batch_file_conflict',
                    '同一工具批次包含对同一真实文件路径的多次修改，未启动任何冲突操作。',
                    started=False, retryable=True,
                    hint='合并为该文件的一次修改，或分批执行并在每次修改后重新读取核对。')
            else:
                with self.execution.file_access_scope():
                    permission, action, result = self.execution._prepare(
                        tool.spec, args, self.profile, tool.file_session, call_config)
            row = {'key': self.execution._approval_key(tool.spec, args, call_config),
                   'permission': permission, 'result': result, 'decision': None,
                   'spec': tool.spec, 'file_session': tool.file_session,
                   'file_version': copy.deepcopy(action.get('file_version')) if action else None,
                   'consumed': False}
            plan['rows'][call['id']] = row
            if result is None and permission.decision == Decision.ASK:
                plan['actions'].append({**action, _NONCE: plan['id'],
                                        '_nailong_tool_call_id': call['id']})
        pending[key] = plan
        return key, plan

    def after_model(self, state, runtime):
        messages = state.get('messages') or []
        message = messages[-1] if messages else None
        if not self._native() or not isinstance(message, AIMessage) or not message.tool_calls:
            return {_STATE: None}
        key, plan = self._prepare(message)
        actions = plan['actions']
        if actions:
            answer = interrupt({'action_requests': actions,
                'review_configs': [{'action_name': a['name'], 'allowed_decisions': ['approve', 'reject']}
                                   for a in actions]})
            decisions = answer.get('decisions') if isinstance(answer, dict) else None
            valid = (isinstance(answer, dict) and answer.get(_NONCE) == plan['id']
                     and isinstance(decisions, list) and len(decisions) == len(actions))
            for i, action in enumerate(actions):
                decision = decisions[i] if valid else None
                plan['rows'][action['_nailong_tool_call_id']]['decision'] = (
                    decision.get('type') if isinstance(decision, dict) else 'reject')
        self.execution._pending_approval_batches.pop(key, None)
        batches = self.execution._approval_batches
        batches[plan['id']] = plan
        while len(batches) > 128:
            batches.pop(next(iter(batches)))
        return {_STATE: plan['id']}

    async def aafter_model(self, state, runtime):
        return self.after_model(state, runtime)

    def _scope(self, request):
        marker = request.state.get(_STATE)
        if marker is None:
            # Native tools must have passed the batch barrier. A lost marker
            # cannot downgrade them into interrupting per-tool approval.
            return (self.execution, None, request.tool_call.get('id')) if self._native() else None
        plan = self.execution._approval_batches.get(marker) if isinstance(marker, str) else None
        messages = request.state.get('messages') or []
        message = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if (plan is None or plan['thread'] != self.thread_id or message is None
                or plan['message_id'] != message.id):
            plan = None
        return self.execution, plan, request.tool_call.get('id')

    def wrap_tool_call(self, request, handler):
        token = active_batch_permit.set(self._scope(request))
        try:
            return handler(request)
        finally:
            active_batch_permit.reset(token)

    async def awrap_tool_call(self, request, handler):
        token = active_batch_permit.set(self._scope(request))
        try:
            return await handler(request)
        finally:
            active_batch_permit.reset(token)


def _task_binding(provider, thread):
    task = provider(thread) if provider is not None else None
    return (task.get('task_id'), task.get('revision'), task.get('lifecycle')) if task else None


def _permit_row(execution, spec, arguments, config, profile, file_session):
    scope = active_batch_permit.get()
    if scope is None:
        return False, None, None
    owner, plan, identity = scope
    row = plan['rows'].get(identity) if plan is not None else None
    if (owner is not execution or row is None
            or plan['callback'] is not execution.approval_handler
            or plan['profile'] != profile or row['spec'] is not spec
            or row['file_session'] is not file_session
            or plan['task_binding'] != _task_binding(plan['task_provider'], plan['thread'])
            or row['key'] != execution._approval_key(spec, arguments, config)):
        return True, None, failure('approval_required', '批次审批已失效或与当前操作不符，未启动操作。', started=False)
    return True, row, None


def batch_precondition(execution, spec, arguments, config, profile, file_session):
    covered, row, error = _permit_row(execution, spec, arguments, config, profile, file_session)
    if not covered or error is not None:
        return error
    if row['result'] is not None:
        return row['result']
    if row['file_version'] is not None:
        try:
            file_session.check_version(arguments['path'], row['file_version'])
        except (OSError, ValueError) as error:
            return failure('approval_precondition', str(error), started=False)
    return None


def batch_decision(execution, spec, arguments, config, permission, profile, file_session, action):
    """Return (covered, failure), consuming a permit before any hook can run."""
    covered, row, error = _permit_row(execution, spec, arguments, config, profile, file_session)
    if not covered or error is not None:
        return covered, error
    if row['result'] is not None:
        return True, row['result']
    if row['decision'] == 'reject':
        return True, failure('approval_rejected', '用户未批准该工具操作。', started=False)
    original = row['permission']
    if permission.decision == Decision.ASK and (
            original.decision != Decision.ASK or row['decision'] != 'approve'
            or permission.matched_rule != original.matched_rule):
        return True, failure('approval_required', '执行前权限规则已变化，需重新审批。', started=False)
    if row['consumed']:
        return True, failure('approval_required', '该批次操作已启动，不自动重放；请先核实已执行效果。', started=False)
    row['consumed'] = True
    if row['file_version'] is not None:
        action['file_version'] = copy.deepcopy(row['file_version'])
    return True, None


def abandon_approval_batches(execution, thread_id):
    """A new real user turn supersedes unfinished reviews, not graph resumes."""
    pending = getattr(execution, '_pending_approval_batches', {})
    batches = getattr(execution, '_approval_batches', {})
    for key in list(pending):
        if key[0] == thread_id:
            pending.pop(key)
    for key, plan in list(batches.items()):
        if plan['thread'] == thread_id:
            batches.pop(key)
    with execution._approval_lock:
        for key in list(execution._approval_versions):
            if key[0] == thread_id:
                execution._approval_versions.pop(key)
