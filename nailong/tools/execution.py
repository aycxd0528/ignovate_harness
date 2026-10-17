"""Execution-time permissions, approval preconditions and bounded observations."""

from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
import threading
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path

from langgraph.errors import GraphBubbleUp, GraphInterrupt
import local_tools

from nailong.core.permissions import ApprovalDecision, Decision, PermissionEngine
from nailong.tools.coordination import active_file_version, active_read_permission
from nailong.tools.results import ResultArchive, failure, normalize_result, redact

current_tool_execution: ContextVar[dict | None] = ContextVar("current_tool_execution", default=None)
active_approval_cancellation: ContextVar[object | None] = ContextVar("active_approval_cancellation", default=None)
_active_pre_tool_invocation: ContextVar[object | None] = ContextVar("active_pre_tool_invocation", default=None)


class _PreToolInvocation:
    def __init__(self, callback, native_callback):
        self.callback = callback
        self.native_callback = native_callback
        self.no_effects = False


def attest_native_pre_tool_no_effects(callback):
    """Only this invocation's registered native callback may attest no hook work."""
    invocation = _active_pre_tool_invocation.get()
    if (invocation is not None and callback is invocation.callback
            and callback is invocation.native_callback):
        invocation.no_effects = True


class ApprovalCancellationScope:
    """Runtime proof of a sibling approval interrupt within one graph advance.

    A new scope is created for every invoke/stream, including approval resumes.
    Model arguments and tool-returned labels cannot create this proof.
    """

    def __init__(self):
        self.owner = asyncio.current_task()
        self.batches = set()

    @staticmethod
    def _batch(config):
        metadata = (config or {}).get('metadata') or {}
        configurable = (config or {}).get('configurable') or {}
        thread = configurable.get('thread_id')
        step = metadata.get('langgraph_step')
        namespace = configurable.get('checkpoint_ns')
        if (not isinstance(thread, str) or not thread or type(step) is not int
                or metadata.get('langgraph_node') != 'tools'
                or not configurable.get('__pregel_task_id')
                or not isinstance(namespace, str)):
            return None
        parent, _, leaf = namespace.rpartition('|')
        if not leaf.startswith('tools:'):
            return None
        return thread, step, parent

    def record(self, config, action, interruption):
        batch = self._batch(config)
        interrupts = interruption.args[0] if interruption.args else ()
        if batch is not None and any(
                isinstance(getattr(row, 'value', None), dict)
                and row.value.get('action_requests') == [action] for row in interrupts):
            self.batches.add(batch)

    def allows_read(self, config, spec, *, file_session, profile):
        # A real cancellation of the turn always wins, even if an approval
        # interrupt happened concurrently in a child task.
        if self.owner is None or self.owner.cancelling() or self._batch(config) not in self.batches:
            return False
        # Review reads execute additional captured helpers. Their provenance is
        # not attested here, so keep the existing conservative pause behavior.
        if type(profile) is not str or profile not in {'chat', 'init', 'plan', 'subagent'}:
            return False
        from nailong.tools.registry import TOOL_SPECS
        from nailong.tools.files import FileSession
        safe_names = {'read_file', 'list_files', 'glob', 'grep', 'search_text'}
        if (spec.name not in safe_names or not spec.read_only or not spec.concurrency_safe
                or spec.async_handler is not None or type(file_session) is not FileSession):
            return False
        # Code identity alone is insufficient: cloned core code can delegate to
        # an external closure. Bind the handler to this exact native session and
        # profile, with the original registry globals rather than replacement IO.
        code = getattr(spec.handler, '__code__', None)
        core = next((core for core in TOOL_SPECS if core.name == spec.name
                     and code is getattr(core.handler, '__code__', None)), None)
        if core is None or spec.handler.__globals__ is not core.handler.__globals__:
            return False
        try:
            bound = dict(zip(code.co_freevars, (cell.cell_contents for cell in spec.handler.__closure__)))
        except (TypeError, ValueError):
            return False
        return (bound.get('file_session') is file_session
                and ('profile' not in bound or
                     type(bound['profile']) is str and bound['profile'] == profile))

_TRANSPORT_FIELDS = frozenset({
    'tool_result_id', 'tool_result_turn_id',
    "elapsed_ms", "duration_ms", "duration", "timestamp", "started_at", "finished_at",
    "call_id", "tool_call_id", "artifact_ref", "result_ref", "reference",
})


def _json_digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_result(value):
    """Remove only transport metadata; business outcome and page/version data remain."""
    if isinstance(value, dict):
        return {key: _stable_result(item) for key, item in value.items() if key not in _TRANSPORT_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_stable_result(item) for item in value]
    return value


def _approved(answer) -> bool:
    if isinstance(answer, ApprovalDecision):
        return answer.kind in {"approve", "approve_once", "approve_session"}
    if isinstance(answer, dict):
        return answer.get("type") in {"approve", "approve_once", "approve_session"}
    return isinstance(answer, str) and answer in {"approve", "approve_once", "approve_session"}


def _blocked(answer) -> tuple[bool, str]:
    if isinstance(answer, dict):
        return bool(answer.get("blocked")), str(answer.get("reason") or "前置钩子拒绝了操作。")
    return bool(getattr(answer, "blocked", False)), str(getattr(answer, "reason", "") or "前置钩子拒绝了操作。")


async def _join_owned(task):
    """Join a synchronous worker after cancellation; started writes are not cancellable guesses."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass
    return task.result()


class ToolExecutionContext:
    def __init__(self, project_root, *, permission_engine=None, permission_mode="default",
                 approval_handler=None, pre_tool_hook=None, post_tool_hook=None,
                 observer=None, api_key="", artifact_directory=None, task_snapshot_provider=None):
        self.project_root = Path(project_root).resolve()
        self.permission_engine = permission_engine or PermissionEngine(self.project_root)
        if Path(self.permission_engine.project_root).resolve() != self.project_root:
            raise ValueError("工具执行与权限引擎必须属于同一项目。")
        self.permission_mode = permission_mode
        self.approval_handler = approval_handler
        self.pre_tool_hook = pre_tool_hook
        self._native_pre_tool_hook = None
        self.post_tool_hook = post_tool_hook
        self.observer = observer
        self.api_key = api_key
        self.task_snapshot_provider = task_snapshot_provider
        self.artifacts = ResultArchive(artifact_directory, api_key=api_key)
        self._approval_versions = {}
        self._approval_lock = threading.RLock()

    @contextmanager
    def file_access_scope(self):
        """Use for execution and explicit build/preview resolution; never persist the grant."""
        with local_tools.use_project_root(self.project_root), local_tools.use_file_access(
            self.project_root, unrestricted=self.permission_mode == "bypassPermissions"
        ):
            yield

    def _permission_mode(self, profile):
        if self.permission_mode == "bypassPermissions":
            return self.permission_mode
        return "plan" if profile == "plan" else self.permission_mode

    def _approval_key(self, spec, arguments, config):
        metadata = (config or {}).get("metadata") or {}
        configurable = (config or {}).get("configurable") or {}
        call_id = metadata.get("tool_call_id") or configurable.get("tool_call_id")
        if not call_id:
            return None
        digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return (str(configurable.get("thread_id") or ""), str(call_id), spec.name, digest)

    def _prepare(self, spec, arguments, profile, file_session, config):
        if file_session.project_root != self.project_root or profile not in spec.profiles:
            return None, None, failure("execution_scope", "工具不属于当前项目或运行模式。")
        if profile == "subagent" and not spec.read_only:
            return None, None, failure("permission_denied", "只读子代理不能执行有副作用的工具。")
        stage_failure = self._stage_restriction(spec, arguments, config)
        if stage_failure is not None:
            return None, None, stage_failure
        permission = self._decide(spec, arguments, profile)
        if permission.decision == Decision.DENY:
            return None, None, failure("permission_denied", permission.reason, started=False)
        version = None
        if spec.name in {"write_file", "edit_file"}:
            try:
                key = self._approval_key(spec, arguments, config)
                with self._approval_lock:
                    version = self._approval_versions.get(key)
                if version is None:
                    version = file_session.mutation_version(arguments["path"], require_complete=spec.name == "write_file")
                    if key is not None and permission.decision == Decision.ASK:
                        with self._approval_lock:
                            if len(self._approval_versions) >= 128:
                                return None, None, failure("approval_budget", "待审批文件版本数量已达上限。", started=False)
                            self._approval_versions[key] = version
                else:
                    file_session.check_version(arguments["path"], version)
            except (OSError, ValueError) as error:
                return None, None, normalize_result(spec.name, {"ok": False, "error": str(error), "started": False})
        # Profile comes from the registered adapter, never from model arguments.
        action = {"name": spec.name, "args": dict(arguments), "profile": profile}
        if version is not None:
            action["file_version"] = version
        return permission, action, None

    def _decide(self, spec, arguments, profile):
        options = {"profile": profile, "mode": self._permission_mode(profile)}
        # Total control may supply the current registered spec to the permission engine,
        # including deferred read tools absent from its compatibility snapshot.
        if "tool_spec" in inspect.signature(self.permission_engine.decide_action).parameters:
            options["tool_spec"] = spec
        with local_tools.use_project_root(self.project_root):
            return self.permission_engine.decide_action(spec.name, arguments, **options)

    def _stage_restriction(self, spec, arguments, config):
        if self.task_snapshot_provider is None:
            return None
        thread = str(((config or {}).get('configurable') or {}).get('thread_id') or '')
        if not thread:
            return None
        try:
            task = self.task_snapshot_provider(thread)
            if task is not None and (task.get('thread_id') != thread
                    or Path(task['project_root']).resolve() != self.project_root):
                raise ValueError('任务阶段策略不属于调用项目或会话。')
            intake=(task or {}).get('intake',{})
            if intake and (intake.get('stage') in {'clarifying','ready'} or intake.get('pending_start')) and not spec.read_only and spec.name not in {'prepare_task','start_task','update_goal'}:
                return failure('task_not_started','需求澄清或启动交接中，尚不能执行文件或命令操作。',started=False)
            from nailong.core.stage_constraints import compile_stage_policy
            return compile_stage_policy(task).check(spec.name, arguments)
        except (ValueError, OSError, RuntimeError, KeyError, TypeError):
            return failure('stage_policy_unavailable', '无法核对真实用户阶段约束，未启动操作。', started=False)

    def _recheck(self, spec, arguments, profile, original, config=None):
        stage_failure = self._stage_restriction(spec, arguments, config)
        if stage_failure is not None:
            return stage_failure
        current = self._decide(spec, arguments, profile)
        if current.decision == Decision.DENY:
            return failure("permission_denied", current.reason, started=False)
        if current.decision == Decision.ASK and (original.decision != Decision.ASK
                                                or current.matched_rule != original.matched_rule):
            return failure("approval_required", "执行前权限规则已变化，需重新审批。", started=False)
        return None

    def _active_context(self, config, profile):
        return {"context": self, "profile": profile,
                "thread_id": str(((config or {}).get("configurable") or {}).get("thread_id") or "")}

    def _path_permission(self, profile, action, permission):
        def evaluate(path):
            decision = self.permission_engine.decide_action("read_file", {"path": path},
                profile=profile, mode=self._permission_mode(profile)).decision.value
            # Once-only approval covers the exact file requested, never a broader search's files.
            requested = (action.get("args") or {}).get("path") if action else None
            if decision == "ask" and permission.decision == Decision.ASK and requested:
                if (self.project_root / requested).resolve() == (self.project_root / path).resolve():
                    return "allow"
            return decision
        return evaluate

    def _finish(self, spec, arguments, result, config, file_session, *, started=False):
        key = self._approval_key(spec, arguments, config)
        if key is not None:
            with self._approval_lock:
                self._approval_versions.pop(key, None)
        result = normalize_result(spec.name, result)
        # Execution state belongs to the gate, never to a handler's returned payload.
        result["started"] = started
        # Runtime identity is not supplied by the handler or stdout, and does
        # not itself attest handler/process/test success.
        result['tool_result_id']=uuid.uuid4().hex
        origin=((config or {}).get('metadata') or {}).get('nailong_turn_id')
        result['tool_result_turn_id']=origin if isinstance(origin,str) and len(origin)<=128 else None
        bounded = self.artifacts.bound(result)
        if spec.name == "read_file" and bounded.get("result_truncated"):
            file_session.invalidate_read(arguments["path"])
            bounded["read_complete"] = False
        metadata = (config or {}).get("metadata") or {}
        configurable = (config or {}).get("configurable") or {}
        paths = [str(result["path"])] if result.get("path") else []
        input_version = bounded.get("version") or bounded.get("digest") or ""
        observation = {
            "name": spec.name, "call_id": str(metadata.get("tool_call_id") or configurable.get("tool_call_id") or ""),
            "thread_id": str(configurable.get("thread_id") or ""),
            "status": "denied" if result.get("error_code") in {"permission_denied", "approval_required", "approval_rejected", "hook_blocked"}
                      else "interrupted" if result.get("cancelled") else "passed" if result["ok"] else "failed",
            "paths": paths,
            "coverage": bounded.get("coverage", "partial" if bounded.get("truncated") or bounded.get("output_truncated") else "unknown"),
            "summary": str(result.get("error") or (f"{spec.name} 执行成功" if result["ok"] else f"{spec.name} 执行失败"))[:500],
            "artifact_ref": bounded.get("reference"),
            "input_fingerprint": result.get("input_fingerprint"),
            "arguments_digest": _json_digest(arguments),
            "result_digest": _json_digest(_stable_result(bounded)),
            "input_version": input_version[:256] if isinstance(input_version, str) else "",
            "ok": result["ok"],
            "changed": result["ok"] and spec.name in {"edit_file", "write_file"},
            "started": started,
            "tool_result_id": result['tool_result_id'],
        }
        return bounded, redact(observation, self.api_key)

    @staticmethod
    def _sync_callback(callback, *args):
        answer = callback(*args)
        if inspect.isawaitable(answer):
            if inspect.iscoroutine(answer):
                answer.close()
            raise ValueError("同步工具不能使用异步回调；请使用异步执行入口。")
        return answer

    def observe_validation_sync(self, spec, arguments, result, *, file_session, config=None):
        """Schema validation happens before handlers; report its failure before returning."""
        with self.file_access_scope():
            bounded, observation = self._finish(spec, arguments, result, config, file_session, started=False)
            if self.observer is not None:
                try:
                    self._sync_callback(self.observer, observation)
                except Exception:
                    bounded["observation_incomplete"] = True
            return self.artifacts.bound(bounded)

    async def observe_validation_async(self, spec, arguments, result, *, file_session, config=None):
        with self.file_access_scope():
            bounded, observation = self._finish(spec, arguments, result, config, file_session, started=False)
            if self.observer is not None:
                try:
                    answer = self.observer(observation)
                    if inspect.isawaitable(answer):
                        await answer
                except Exception:
                    bounded["observation_incomplete"] = True
            return self.artifacts.bound(bounded)

    def invoke_sync(self, spec, arguments, *, profile, file_session, config=None):
        with self.file_access_scope():
            return self._invoke_sync_scoped(spec, arguments, profile=profile,
                file_session=file_session, config=config)

    def _invoke_sync_scoped(self, spec, arguments, *, profile, file_session, config=None):
        started = False
        action = None
        try:
            from nailong.core.tool_approval import batch_precondition
            result = batch_precondition(self, spec, arguments, config, profile, file_session)
            if result is None:
                permission, action, result = self._prepare(spec, arguments, profile, file_session, config)
            if result is None:
                from nailong.core.tool_approval import batch_decision
                covered, batch_failure = batch_decision(self, spec, arguments, config, permission,
                                                      profile, file_session, action)
                if batch_failure is not None:
                    result = batch_failure
                if result is None and permission.decision == Decision.ASK and not covered:
                    if self.approval_handler is None or profile == "subagent":
                        result = failure("approval_required", "操作需要审批，当前执行入口未提供可用的审批桥接。", started=False)
                    elif not _approved(self._sync_callback(self.approval_handler, action, permission)):
                        result = failure("approval_rejected", "用户未批准该工具操作。", started=False)
                if result is None and self.pre_tool_hook is not None:
                    blocked, reason = _blocked(self._sync_callback(self.pre_tool_hook, action))
                    if blocked:
                        result = failure("hook_blocked", reason, started=False)
                if result is None:
                    result = self._recheck(spec, arguments, profile, permission, config)
                if result is None:
                    if inspect.iscoroutinefunction(spec.handler):
                        result = failure("async_required", "该工具必须使用异步执行入口。", started=False)
                    else:
                        token = active_file_version.set(action.get("file_version"))
                        read_token = active_read_permission.set(self._path_permission(profile, action, permission))
                        execution_token = current_tool_execution.set(self._active_context(config, profile))
                        try:
                            started = True
                            result = spec.handler(**arguments)
                        finally:
                            active_file_version.reset(token)
                            active_read_permission.reset(read_token)
                            current_tool_execution.reset(execution_token)
        except GraphBubbleUp:
            raise
        except Exception as error:
            result = failure("execution_error", f"工具执行异常：{type(error).__name__}: {str(error)[:500]}")
        bounded, observation = self._finish(spec, arguments, result, config, file_session, started=started)
        post_hook = self.post_tool_hook if observation["started"] else None
        for callback, args in ((post_hook, (action, bounded)), (self.observer, (observation,))):
            if callback is not None:
                try:
                    self._sync_callback(callback, *args)
                except Exception:
                    bounded["observation_incomplete"] = True
        return self.artifacts.bound(bounded)

    async def invoke_async(self, spec, arguments, *, profile, file_session, config=None):
        with self.file_access_scope():
            return await self._invoke_async_scoped(spec, arguments, profile=profile,
                file_session=file_session, config=config)

    async def _invoke_async_scoped(self, spec, arguments, *, profile, file_session, config=None):
        started = False
        pre_tool_safe = self.pre_tool_hook is None
        action = None
        try:
            from nailong.core.tool_approval import batch_precondition
            result = batch_precondition(self, spec, arguments, config, profile, file_session)
            if result is None:
                permission, action, result = self._prepare(spec, arguments, profile, file_session, config)
            if result is None:
                from nailong.core.tool_approval import batch_decision
                covered, batch_failure = batch_decision(self, spec, arguments, config, permission,
                                                      profile, file_session, action)
                if batch_failure is not None:
                    result = batch_failure
                if result is None and permission.decision == Decision.ASK and not covered:
                    if self.approval_handler is None or profile == "subagent":
                        result = failure("approval_required", "操作需要审批，当前执行入口未提供可用的审批桥接。", started=False)
                    else:
                        try:
                            answer = self.approval_handler(action, permission)
                            if inspect.isawaitable(answer):
                                answer = await answer
                        except GraphInterrupt as interruption:
                            scope = active_approval_cancellation.get()
                            if scope is not None:
                                scope.record(config, action, interruption)
                            raise
                        if not _approved(answer):
                            result = failure("approval_rejected", "用户未批准该工具操作。", started=False)
                if result is None and self.pre_tool_hook is not None:
                    callback = self.pre_tool_hook
                    invocation = _PreToolInvocation(callback, self._native_pre_tool_hook)
                    token = _active_pre_tool_invocation.set(invocation)
                    try:
                        answer = callback(action)
                        if inspect.isawaitable(answer):
                            answer = await answer
                        pre_tool_safe = invocation.no_effects
                    finally:
                        _active_pre_tool_invocation.reset(token)
                    blocked, reason = _blocked(answer)
                    if blocked:
                        result = failure("hook_blocked", reason, started=False)
                if result is None:
                    result = self._recheck(spec, arguments, profile, permission, config)
                if result is None:
                    token = active_file_version.set(action.get("file_version"))
                    read_token = active_read_permission.set(self._path_permission(profile, action, permission))
                    execution_token = current_tool_execution.set(self._active_context(config, profile))
                    try:
                        started = True
                        handler = spec.async_handler or spec.handler
                        if inspect.iscoroutinefunction(handler):
                            result = await handler(**arguments)
                        else:
                            worker = asyncio.create_task(asyncio.to_thread(handler, **arguments))
                            try:
                                result = await asyncio.shield(worker)
                            except asyncio.CancelledError:
                                try:
                                    await _join_owned(worker)
                                except Exception:
                                    pass
                                raise
                    finally:
                        active_file_version.reset(token)
                        active_read_permission.reset(read_token)
                        current_tool_execution.reset(execution_token)
        except GraphBubbleUp:
            raise
        except asyncio.CancelledError:
            bounded, observation = self._finish(spec, arguments,
                failure("interrupted", "操作已中断；已启动的操作可能产生副作用，恢复前需核对。", cancelled=True, started=started),
                config, file_session, started=started)
            scope = active_approval_cancellation.get()
            if (started and pre_tool_safe and scope is not None
                    and scope.allows_read(config, spec, file_session=file_session, profile=profile)):
                observation['interruption_source'] = 'approval_sibling_read'
            if self.observer is not None:
                try:
                    answer = self.observer(observation)
                    if inspect.isawaitable(answer):
                        task = asyncio.ensure_future(answer)
                        await _join_owned(task)
                except Exception:
                    pass
            raise
        except Exception as error:
            result = failure("execution_error", f"工具执行异常：{type(error).__name__}: {str(error)[:500]}", started=started)
        bounded, observation = self._finish(spec, arguments, result, config, file_session, started=started)
        post_hook = self.post_tool_hook if observation["started"] else None
        for callback, args in ((post_hook, (action, bounded)), (self.observer, (observation,))):
            if callback is not None:
                try:
                    answer = callback(*args)
                    if inspect.isawaitable(answer):
                        await answer
                except Exception:
                    bounded["observation_incomplete"] = True
        return self.artifacts.bound(bounded)
