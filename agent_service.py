"""UI-independent asynchronous turn orchestration for the local Agent."""

import inspect
import asyncio
import json
import time
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from contextvars import ContextVar
from contextlib import nullcontext
from typing import Literal

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, RemoveMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from langgraph.graph.message import REMOVE_ALL_MESSAGES

import local_tools
from nailong.core.permissions import ApprovalDecision, Decision, PermissionEngine
from nailong.core.sessions import ProjectSessionStore, StreamingRedactor
from nailong.core.compact import compact_messages
from nailong.core.usage import message_usage
from nailong.core.budgets import active_cost_budget, GoalCostBudget, CostBudgetExceeded
from nailong.core.budgets import active_turn_budget, TurnModelBudget, TurnModelLimitExceeded
from nailong.tools.files import FileSession
from nailong.core.hooks import HookRunner
from nailong.core.task_requests import task_request_kind, render_task_status, is_simple_project_question
from nailong.tools.previews import preview_mutation


MAX_TURN_MODEL_CALLS = 40
# Internal graph nodes include middleware and tools; this is a separate fail-safe.
MAX_TURN_GRAPH_STEPS = 256
DEFAULT_COMPACT_THRESHOLD_TOKENS = 150_000
TOKEN_LOG_BATCH_CHARS = 512
TOKEN_LOG_FLUSH_SECONDS = 1.0
AgentProfile = Literal["chat", "init", "review", "plan"]
ApprovalHandler = Callable[[dict, int, int], str | ApprovalDecision | Awaitable[str | ApprovalDecision]]
StatusHandler = Callable[[str], None]


@dataclass(frozen=True)
class TurnEvent:
    kind: str
    data: dict


class TurnRecursionLimitError(RuntimeError):
    """Raised when a turn consumes its model or internal graph allowance."""

    def __init__(self, message: str, *, stats: dict | None = None):
        super().__init__(message)
        self.stats = stats or {}


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
            else:
                parts.append(json.dumps(block, ensure_ascii=False, default=str))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _output_value(result):
    return getattr(result, "value", result)


def _graph_step(agent, config: dict) -> int | None:
    get_state = getattr(agent, "get_state", None)
    if not callable(get_state):
        return None
    state = get_state(config)
    metadata = getattr(state, "metadata", {}) or {}
    try:
        return int(metadata.get("step", 0))
    except (TypeError, ValueError):
        return 0


def _interrupt_actions(result) -> list[dict]:
    actions = []
    for interrupt in getattr(result, "interrupts", ()):
        value = getattr(interrupt, "value", {})
        if isinstance(value, dict):
            actions.extend({**action, **({'_nailong_interrupt_id': interrupt.id} if getattr(interrupt, 'id', None) else {})}
                for action in value.get('action_requests', []))
    return actions


def _interrupt_actions_from_update(update: dict) -> list[dict]:
    actions = []
    for interrupt in update.get("__interrupt__", ()):
        value = getattr(interrupt, "value", {})
        if isinstance(value, dict):
            actions.extend({**action, **({'_nailong_interrupt_id': interrupt.id} if getattr(interrupt, 'id', None) else {})}
                for action in value.get('action_requests', []))
    return actions


def _resume_approvals(actions, decisions):
    """Keep each decision bound to its originating graph interrupt."""
    if actions and all(action.get('_nailong_interrupt_id') for action in actions):
        grouped = {}
        for action, decision in zip(actions, decisions, strict=True):
            grouped.setdefault(action['_nailong_interrupt_id'], {'decisions': []})['decisions'].append(decision)
        return Command(resume=grouped)
    return Command(resume={'decisions': decisions})


def _contains_secret(action: dict, api_key: str) -> bool:
    return bool(api_key) and api_key in json.dumps(action, ensure_ascii=False, default=str)


def _final_answer(value) -> str:
    messages = value.get("messages", []) if isinstance(value, dict) else []
    for message in reversed(messages):
        if not (
            isinstance(message, AIMessage)
            or getattr(message, "type", None) == "ai"
        ):
            continue
        content = _message_text(getattr(message, "content", "")).strip()
        return content or "模型返回了空回答。"
    return "模型没有返回文本回答。"


def _usage_from_message(message) -> dict | None:
    return message_usage(message)


async def _budgeted_events(stream, budget):
    """Apply the budget only while advancing the graph, never across a UI yield."""
    iterator = stream.__aiter__()
    try:
        while True:
            token = active_cost_budget.set(budget)
            try:
                item = await anext(iterator)
            except StopAsyncIteration:
                return
            finally:
                active_cost_budget.reset(token)
            yield item
    finally:
        close = getattr(stream, "aclose", None)
        if callable(close):
            await close()


async def _budgeted_invoke(agent, inputs, config, budget):
    token = active_cost_budget.set(budget)
    try:
        return await agent.ainvoke(inputs, config, version="v2")
    finally:
        active_cost_budget.reset(token)


class AgentService:
    """Run chat, initialization, and review turns with scoped tools and HITL."""

    def __init__(
        self,
        runtime_factory,
        api_key: str = "",
        *,
        permission_engine: PermissionEngine | None = None,
        permission_mode: str = "default",
        session_store: ProjectSessionStore | None = None,
    ):
        self.runtime_factory = runtime_factory
        self.api_key = api_key
        configured_root = getattr(getattr(runtime_factory, "settings", None), "project_root", local_tools.PROJECT_ROOT)
        self.permission_engine = permission_engine or PermissionEngine(configured_root)
        self.permission_mode = permission_mode
        self.session_store = session_store or getattr(runtime_factory, "session_store", None)
        self.task_store = getattr(runtime_factory, 'task_store', None)
        self._pending_token_events: dict[str, tuple[str, float]] = {}
        self.compact_threshold_tokens = DEFAULT_COMPACT_THRESHOLD_TOKENS
        self.hook_runner = HookRunner(configured_root, api_key=api_key)
        self._hook_approval = ContextVar(f'nailong_hook_approval_{id(self)}', default=None)
        self._turn_task = ContextVar(f'nailong_turn_task_{id(self)}', default=None)
        execution = getattr(runtime_factory, 'tool_execution_context', None)
        self._uses_tool_executor = execution is not None
        self.last_turn_task_id = None
        if execution is not None:
            execution.permission_engine = self.permission_engine
            execution.permission_mode = permission_mode
            def pre_tool(action):
                expected = self._turn_task.get()
                if expected is not None:
                    with self._bound_turn_task(expected['thread_id']) as task:
                        if task is None or task['lifecycle'] in {'paused', 'blocked'}:
                            return {'blocked': True, 'reason': '当前任务已更新或暂停，未启动该操作。'}
                if action.get('profile') in {'init', 'plan', 'review', 'subagent'} or self.permission_mode == 'plan':
                    return None
                value = self._run_hook('PreToolUse', name=str(action.get('name', '')),
                    args=action.get('args', {}) or {}, approval_handler=self._hook_approval.get())
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    return asyncio.run(value)
                return value
            execution.pre_tool_hook = pre_tool
            def observe_interrupted(observation):
                if self.task_store is None:
                    return
                identity = observation.get('thread_id')
                if not identity:
                    return
                with self._bound_turn_task(identity) as task:
                    if task is None:
                        return
                    if observation.get('status') != 'interrupted':
                        name = observation.get('name')
                        call_id = observation.get('call_id')
                        if not call_id:
                            return
                        seen = self._turn_task.get().setdefault('_observed_progress_calls', set())
                        if call_id in seen:
                            return
                        progress = self.task_store.observe_progress(identity, {
                            'tool': name, 'arguments_digest': observation.get('arguments_digest'),
                            'result_digest': observation.get('result_digest'),
                            'input_version': observation.get('input_version', ''),
                            'ok': observation.get('ok'), 'changed': observation.get('changed'),
                            'status': observation.get('status'),
                        })
                        if progress and progress['action'] == 'hint':
                            self.task_store.set_state(identity, progress=progress['reason'])
                        seen.add(call_id)
                        return
                    self.task_store.reconcile(identity, interrupted=True)
                    name = observation.get('name', '')
                    paths = []
                    for path in observation.get('paths', []):
                        try:
                            paths.append(self.task_store._relative(path))
                        except ValueError:
                            self._event(identity, 'external_operation_interrupted', {
                                'path': path, 'name': name, 'status': 'interrupted',
                                'coverage': 'unknown', 'task_id': task['task_id'],
                                'revision': task['revision'],
                            })
                    self.task_store.record_evidence(identity, {
                        'id': 'interrupted-' + str(observation.get('call_id') or uuid.uuid4().hex),
                        'kind': 'edit' if name in {'write_file', 'edit_file'} else 'run' if name == 'run_command' else 'read',
                        'task_revision': task['revision'],
                        'source': 'runtime', 'status': 'interrupted', 'coverage': 'unknown',
                        'paths': paths, 'input_fingerprint': '',
                        'summary': f'{name} 已中断；已启动操作的效果需要核实。',
                        'artifact_ref': observation.get('artifact_ref') or '',
                    })
            execution.observer = observe_interrupted
        if self.session_store is not None:
            self.session_store.api_key = api_key

    async def _begin_task(self, thread_id, message, profile, target_path=None, *, goal_round=False, review_paths=None):
        if self.task_store is None or not thread_id:
            return None
        if profile == 'review' and self.permission_mode == 'bypassPermissions':
            # Project task evidence cannot represent external or protected paths.
            # The review still runs and is journaled, without mutating an older task.
            try:
                for path in review_paths or ([target_path] if target_path else ['.']):
                    self.task_store._relative(path)
            except ValueError:
                return None
        task = self.task_store.snapshot(thread_id)
        intent = task_request_kind(message)
        if profile == 'chat' and not goal_round:
            if intent == 'status':
                return task
            if is_simple_project_question(message):
                return None
            if task is not None and task['lifecycle'] in {'paused', 'blocked'} and intent not in {'work', 'resume'}:
                return task
        goal_store = getattr(self.runtime_factory, 'goal_store', None)
        goal = goal_store.active() if goal_store is not None else None
        goal = goal if goal is not None and goal.thread_id == thread_id else None
        engineering = profile != 'chat' or goal is not None or bool(re.search(
            r'修复|修改|实现|审查|重构|优化|排查|测试|验证|创建|新增|编辑|删除|重命名|构建|'
            r'\b(?:review|debug|implement|refactor|fix|create|edit|rename|build|test)\b',
            message, re.IGNORECASE))
        if task is None and not engineering:
            return None
        if task is not None and task['lifecycle'] == 'completed' and not engineering:
            return None
        if task is not None and task.get('input_fingerprint'):
            current = await self._task_input_fingerprint(task)
            task = self.task_store.reconcile(thread_id, current_input_fingerprint=current)
        objective = goal.objective if goal is not None else message
        scope = list(review_paths) if review_paths else [target_path] if target_path else None
        new_review = bool(profile == 'review' and task is not None and
            (task.get('profile') != 'review' or task.get('scope') != [self.task_store._relative(path) for path in (scope or ['.'])]))
        resuming = (profile == 'chat' and not goal_round and task is not None
            and task['lifecycle'] != 'completed' and intent == 'resume')
        if resuming:
            # Keep requirements while still reconciling files and verification configuration.
            pass
        elif task is None or engineering:
            effective_request = (task.get('latest_request', objective) if goal_round and task is not None else
                objective if goal_round else message)
            task = self.task_store.begin(thread_id, objective, profile=profile, scope=scope,
                new=new_review or bool(goal is not None and task is not None and task['objective'] != goal.objective),
                latest_request=effective_request)
        elif task is not None:
            # Keep the latest user input visible even if it is not a code request.
            task = self.task_store.begin(thread_id, message, profile=profile)
        if not goal_round:
            self.task_store.reset_progress(thread_id)
        if profile in {'chat', 'init'}:
            from nailong.core.verification import VerificationService
            steps = VerificationService(self.permission_engine.project_root, self.session_store).list_steps()
            self.task_store.configure_verification(thread_id, steps)
        if profile == 'review':
            current = self.task_store.snapshot(thread_id)
            if not any(row['id'] == 'review:scope' for row in current['acceptance']):
                self.task_store.add_acceptance(thread_id, 'review:scope',
                    '本轮选定范围的当前文件内容实际进入成功的静态审查模型流程。', kind='review')
        return self.task_store.snapshot(thread_id)

    async def _prepare_review(self, agent, config, task):
        from nailong.core.review_evidence import ReviewCoverageCollector
        from nailong.core.review_runtime import select_review_files
        # The baseline must precede enumeration: a file added during selection
        # cannot be covered by consumption of the older selected file set.
        fingerprint_before = await self._task_input_fingerprint(task)
        selection = await asyncio.to_thread(select_review_files,
            self.permission_engine.project_root, task['scope'], self.permission_engine,
            mode=self.permission_mode)
        previous_reads = []
        try:
            get_state = getattr(agent, 'aget_state', None)
            state = await get_state(config) if callable(get_state) else agent.get_state(config)
            messages = (getattr(state, 'values', {}) or {}).get('messages', [])
            previous_reads = [str(row.tool_call_id) for row in messages
                if isinstance(row, ToolMessage) and row.name == 'read_file' and row.tool_call_id]
        except Exception:
            # Unknown history cannot establish that a consumed page is fresh.
            selection['selection_complete'] = False
            selection['skipped_paths'].append({'reason': 'history_boundary_unknown'})
        return ReviewCoverageCollector(selection['paths'],
            fingerprint_before=fingerprint_before,
            selection_complete=selection['selection_complete'],
            skipped_paths=selection['skipped_paths'], excluded_call_ids=previous_reads)

    async def _finish_review(self, thread_id, task, collector):
        result = collector.finish(fingerprint_after=await self._task_input_fingerprint(task),
            completed=True, task_revision=task['revision'],
            evidence_id='review-' + uuid.uuid4().hex,
            artifact_ref=f'events:{thread_id}:review_coverage')
        self._event(thread_id, 'review_coverage', result)
        if result['evidence'] is not None:
            # A directory path represents complete, bounded enumeration followed
            # by actual consumption of every file; gaps never reach this branch.
            evidence = {**result['evidence'],
                'paths': list(dict.fromkeys([*result['paths'], *task['scope']]))}
            self.task_store.record_review(thread_id, evidence,
                expected_task_id=task['task_id'], expected_revision=task['revision'])
        return result

    async def _task_input_fingerprint(self, task):
        from nailong.core.verification import input_fingerprint
        try:
            return await asyncio.to_thread(input_fingerprint, self.permission_engine.project_root,
                task.get('verification_generated_paths', []))
        except (OSError, ValueError):
            return None

    def _bound_turn_task(self, thread_id, *, require_revision=True):
        task = self._turn_task.get()
        if self.task_store is None or task is None or task['thread_id'] != thread_id:
            return nullcontext(None)
        return self.task_store.bound_task(thread_id, task['task_id'],
            revision=task['revision'] if require_revision else None)

    def _pause_turn_task(self, thread_id, reason=None):
        with self._bound_turn_task(thread_id) as task:
            if task is None:
                return
            if reason:
                self.task_store.set_state(thread_id, lifecycle='paused', blockers=[reason])
            else:
                self.task_store.reconcile(thread_id, interrupted=True)

    def _turn_pause_reason(self, thread_id):
        with self._bound_turn_task(thread_id) as task:
            if task is not None and task['lifecycle'] in {'paused', 'blocked'}:
                return '; '.join(task['blockers']) or '当前任务已暂停。'
        return None

    async def _observe_task_tool(self, thread_id, message, args):
        turn_task = self._turn_task.get()
        if self.task_store is None or not thread_id or turn_task is None:
            return None
        from nailong.core.task_state import observation_digest
        name = str(getattr(message, 'name', '') or '')
        try:
            payload = json.loads(_message_text(getattr(message, 'content', '')))
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        body = payload.get('data') if isinstance(payload.get('data'), dict) else payload
        ok = payload.get('ok') if type(payload.get('ok')) is bool else None
        before = self.task_store.snapshot(thread_id)
        if before is None or before['task_id'] != turn_task['task_id']:
            return None
        current_input = (await self._task_input_fingerprint(before)
            if name == 'run_command' and before.get('input_fingerprint') else None)
        with self._bound_turn_task(thread_id, require_revision=False) as task:
            if task is None:
                return None
            evidence_id = f"tool-{getattr(message, 'tool_call_id', '') or observation_digest(payload)[:24]}"
            if any(row.get('id') == evidence_id for row in task['evidence']):
                return None
            path = body.get('path') or args.get('path')
            paths = []
            if isinstance(path, str):
                try:
                    paths = [self.task_store._relative(path)]
                except ValueError:
                    pass
            changed = name in {'edit_file', 'write_file'} and ok is True
            if changed and paths:
                task = self.task_store.record_change(thread_id, paths[0])
            elif changed and isinstance(path, str):
                # Full access does not make a project check cover an external edit.
                task = self.task_store.reconcile(thread_id)
                reason = '完全访问修改了项目验证范围外的文件；项目内验证不能证明该文件。'
                self.task_store.set_state(thread_id, blockers=list(dict.fromkeys([*task['blockers'], reason])))
                self._event(thread_id, 'external_file_change', {
                    'path': path, 'task_id': task['task_id'], 'revision': task['revision'],
                    'coverage': 'unknown', 'ok': True,
                })
            elif (name == 'run_command' and task.get('input_fingerprint')
                    and task['revision'] == before['revision']):
                task = self.task_store.reconcile(thread_id, current_input_fingerprint=current_input)
            error = payload.get('error')
            error_code = payload.get('error_code') or (error.get('code', '') if isinstance(error, dict) else error)
            status = ('denied' if error_code in {'permission_denied', 'approval_required', 'approval_rejected', 'hook_blocked'}
                else 'interrupted' if body.get('cancelled') is True else
                'passed' if ok is True else 'failed' if ok is False else 'unknown')
            if name not in {'update_goal', 'exit_plan_mode'}:
                self.task_store.record_evidence(thread_id, {
                    'id': evidence_id,
                    'kind': 'edit' if name in {'edit_file', 'write_file'} else 'run' if name == 'run_command' else 'read',
                    'source': 'runtime', 'status': status, 'task_revision': turn_task['revision'],
                    'paths': paths, 'input_fingerprint': '', 'coverage': 'unknown',
                    **({'outside_project_scope': True} if changed and isinstance(path, str) and not paths else {}),
                    'summary': f"{name}: {'成功' if ok is True else '失败' if ok is False else '结果未知'}"
                        + (f'；项目验证范围外路径：{path}' if isinstance(path, str) and not paths else ''),
                    'artifact_ref': str(body.get('artifact_ref') or body.get('reference') or payload.get('reference') or '')[:200],
                })
            # A changed requirement may retain old facts, but old repeated reads
            # cannot pause its new investigation strategy.
            if task['revision'] != turn_task['revision']:
                return None
            if str(getattr(message, 'tool_call_id', '') or '') in turn_task.get('_observed_progress_calls', set()):
                return None
            def stable(value):
                if isinstance(value, dict):
                    return {key: stable(item) for key, item in value.items() if key not in {
                        'elapsed_ms', 'duration_ms', 'timestamp', 'started_at', 'finished_at',
                        'call_id', 'artifact_ref', 'result_ref', 'reference'}}
                if isinstance(value, list):
                    return [stable(item) for item in value]
                return value
            result = self.task_store.observe_progress(thread_id, {
                'tool': name, 'arguments_digest': observation_digest(args),
                'result_digest': observation_digest(stable(payload)),
                'input_version': body.get('version') or body.get('digest') or '',
                'ok': ok, 'changed': changed,
                'status': body.get('status'),
            })
            if result and result['action'] == 'hint':
                self.task_store.set_state(thread_id, progress=result['reason'])
            return result

    async def delivery_report(self, thread_id, *, expected_task_id=None, expected_revision=None):
        if self.task_store is None or not thread_id:
            return None
        task = self.task_store.snapshot(thread_id)
        if task is None:
            return None
        if expected_task_id is not None and task['task_id'] != expected_task_id:
            return None
        if expected_revision is not None and task['revision'] != expected_revision:
            return None
        current = await self._task_input_fingerprint(task) if task.get('input_fingerprint') else None
        from nailong.core.delivery import build_delivery_report
        with self.task_store.bound_task(thread_id, task['task_id'], revision=task['revision']) as bound:
            if bound is None:
                latest = self.task_store.snapshot(thread_id)
                return None if expected_task_id is not None else build_delivery_report(latest)
            if bound.get('input_fingerprint'):
                bound = self.task_store.reconcile(thread_id, current_input_fingerprint=current)
            return build_delivery_report(bound, current_input_fingerprint=current)

    async def _notify(self, status_handler: StatusHandler | None, status: str) -> None:
        if status_handler is None:
            return
        result = status_handler(status)
        if inspect.isawaitable(result):
            await result

    def _resolve_review_target(self, target_path):
        if not target_path:
            raise ValueError('请为 /review 指定文件或目录。')
        from nailong.tools.execution import ToolExecutionContext
        execution = getattr(self.runtime_factory, 'tool_execution_context', None)
        if execution is None:
            execution = ToolExecutionContext(self.permission_engine.project_root,
                permission_engine=self.permission_engine, permission_mode=self.permission_mode)
        with execution.file_access_scope():
            target = FileSession(self.permission_engine.project_root).resolve(target_path)
        if not target.is_file() and not target.is_dir():
            raise ValueError('审查目标必须是文件或目录。')
        return target

    def set_permission_mode(self, mode: str) -> None:
        if mode not in {'default', 'acceptEdits', 'bypassPermissions', 'plan'}:
            raise ValueError('未知权限模式。')
        self.permission_mode = mode
        execution = getattr(self.runtime_factory, 'tool_execution_context', None)
        if execution is not None:
            execution.permission_mode = mode

    def _permission_mode(self, profile):
        if self.permission_mode == 'bypassPermissions':
            return self.permission_mode
        return 'plan' if profile == 'plan' else self.permission_mode

    def _runtime(
        self,
        profile: AgentProfile,
        target_path: str | None,
        thread_id: str | None = None,
        allowed_tools: set[str] | frozenset[str] | None = None,
        review_paths: frozenset[str] | None = None,
    ):
        scope = {"review_paths": review_paths} if review_paths is not None else {}
        if profile == "review":
            target = self._resolve_review_target(target_path)
            return self.runtime_factory(
                profile=profile, target_path=str(target), thread_id=thread_id, allowed_tools=allowed_tools, **scope
            )
        if profile == "init":
            return self.runtime_factory(
                profile=profile, target_path=None, thread_id=thread_id, allowed_tools=allowed_tools
            )
        if profile in {"chat", "plan"}:
            return self.runtime_factory(
                profile=profile, target_path=None, thread_id=thread_id, allowed_tools=allowed_tools
            )
        raise ValueError(f"未知 Agent 模式：{profile}")

    async def _runtime_async(
        self,
        profile: AgentProfile,
        target_path: str | None,
        thread_id: str | None,
        allowed_tools: set[str] | frozenset[str] | None = None,
        review_paths: frozenset[str] | None = None,
    ):
        factory = getattr(self.runtime_factory, "async_runtime", None)
        if not callable(factory):
            agent = self._runtime(profile, target_path, thread_id, allowed_tools, review_paths)
            owner = getattr(agent, "_nailong_runtime_factory", None)
            factory = getattr(owner, "async_runtime", None)
            if not callable(factory):
                return agent
        if profile == "review":
            target = self._resolve_review_target(target_path)
            target_path = str(target)
        elif profile not in {"init", "chat", "plan"}:
            raise ValueError(f"未知 Agent 模式：{profile}")
        result = factory(
            profile=profile,
            target_path=target_path if profile == "review" else None,
            thread_id=thread_id,
            allowed_tools=allowed_tools,
            **({"review_paths": review_paths} if review_paths is not None else {}),
        )
        return await result if inspect.isawaitable(result) else result

    async def _graph_step_async(self, agent, config: dict) -> int | None:
        get_state = getattr(agent, "aget_state", None)
        if callable(get_state):
            try:
                state = await get_state(config)
            except NotImplementedError:
                return _graph_step(agent, config)
            metadata = getattr(state, "metadata", {}) or {}
            try:
                return int(metadata.get("step", 0))
            except (TypeError, ValueError):
                return 0
        return _graph_step(agent, config)

    async def _message_ids_at_boundary_async(self, agent, config: dict) -> list[str]:
        get_state = getattr(agent, "aget_state", None)
        if callable(get_state):
            try:
                state = await get_state(config)
                messages = (getattr(state, "values", {}) or {}).get("messages", [])
            except NotImplementedError:
                return self._message_ids_at_boundary(agent, config)
            except Exception:
                return []
            return [str(message.id) for message in messages if getattr(message, "id", None)]
        return self._message_ids_at_boundary(agent, config)

    async def _compact_state(self, agent, config: dict, *, force: bool = False):
        get_state = getattr(agent, "aget_state", None)
        if callable(get_state):
            state = await get_state(config)
        else:
            get_state = getattr(agent, "get_state", None)
            if not callable(get_state):
                return None
            state = get_state(config)
        messages = (getattr(state, "values", {}) or {}).get("messages", [])
        if not messages:
            return None
        current_tokens = sum(len(str(getattr(item, "content", ""))) for item in messages) // 4
        if not force and current_tokens < self.compact_threshold_tokens:
            return None
        result = compact_messages(messages,archive=getattr(self.runtime_factory,'history_archive',None),thread_id=config.get('configurable',{}).get('thread_id'))
        if not result.compacted:
            return result
        update = getattr(agent, "aupdate_state", None)
        # Replacing the whole sequence preserves chronology. Removing old IDs
        # first and then adding the summary would append it after recent turns.
        deltas = [RemoveMessage(id=REMOVE_ALL_MESSAGES), *result.messages]
        if callable(update):
            await update(config, {"messages": deltas})
        else:
            agent.update_state(config, {"messages": deltas})
        return result

    async def _close_interrupted_tool_exchange(self, agent, config: dict) -> None:
        """Close abandoned calls before a new user turn; never replay side effects."""
        get_state = getattr(agent, 'aget_state', None)
        if callable(get_state):
            state = await get_state(config)
        elif callable(getattr(agent, 'get_state', None)):
            state = agent.get_state(config)
        else:
            return
        messages = (getattr(state, 'values', {}) or {}).get('messages', [])
        repaired = []
        pending = {}
        cancelled = 0

        def close_pending():
            nonlocal cancelled
            for identity, name in pending.items():
                repaired.append(ToolMessage(tool_call_id=identity, name=name, status='error',
                    content=json.dumps({'ok':False, 'error_code':'interrupted',
                        'outcome_unknown':True,
                        'error':'上一轮已中断。此调用没有完整结果，副作用需核对；未自动重放。'}, ensure_ascii=False)))
                cancelled += 1
            pending.clear()

        for message in messages:
            if isinstance(message, ToolMessage):
                pending.pop(message.tool_call_id, None)
            else:
                close_pending()
                if isinstance(message, AIMessage):
                    for call in message.tool_calls:
                        if isinstance(call.get('id'), str) and call['id']:
                            pending[call['id']] = call.get('name', '')
            repaired.append(message)
        close_pending()
        if not cancelled:
            return
        update = {'messages':[RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]}
        # Every managed graph has a model node, including commands with no
        # tools. Explicit END clears pending sends/approval interrupts before
        # the next user input starts a fresh turn under its current policy.
        if callable(getattr(agent, 'aupdate_state', None)):
            repaired_config = await agent.aupdate_state(config, update, as_node='model')
            await agent.aupdate_state(repaired_config or config, None, as_node='__end__')
        else:
            repaired_config = agent.update_state(config, update, as_node='model')
            agent.update_state(repaired_config or config, None, as_node='__end__')
        self._event(config.get('configurable', {}).get('thread_id'),
                    'interrupted_tools_closed', {'count':cancelled, 'outcome_unknown':True})

    async def compact_context(self, thread_id: str, *, force: bool = True) -> dict:
        """Compact eligible old tool output in a saved thread."""
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": MAX_TURN_GRAPH_STEPS,
        }
        agent = await self._runtime_async("chat", None, thread_id)
        result = await self._compact_state(agent, config, force=force)
        if result is None:
            return {"compacted": False, "reason": "below_threshold"}
        data = {
            "compacted": result.compacted,
            "before_tokens": result.before_tokens,
            "after_tokens": result.after_tokens,
            "reason": result.reason,
        }
        if result.compacted:
            self._event(thread_id, "compact", {"level": result.level, **data})
        return data

    async def _run_hook(self, event: str, *, name: str = "", args: dict | None = None, prompt: str = "", approval_handler=None, profile='chat'):
        if profile in {'init', 'plan', 'review', 'subagent'} or self.permission_mode == 'plan':
            from nailong.core.hooks import HookRunResult
            return HookRunResult()
        args = args or {}

        async def confirm(action):
            if approval_handler is None:
                return False
            answer = approval_handler(action, 1, 1)
            return await answer if inspect.isawaitable(answer) else answer

        from nailong.tools.coordination import project_coordinator
        async with project_coordinator(self.permission_engine.project_root).async_scope():
            return await self.hook_runner.run_event(
                event, tool_name=name, path=str(args.get("path", "")),
                prompt=prompt, confirm=confirm,
            )

    def get_history(self, thread_id: str) -> list[tuple[str, str]]:
        """Read saved messages, omitting internal plan and goal prompts."""
        agent = self._runtime("chat", None, thread_id)
        state = agent.get_state(
            {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": MAX_TURN_GRAPH_STEPS,
            }
        )
        values = getattr(state, "values", {}) or {}
        history = []
        previous_goal = None
        for message in values.get("messages", []):
            if (getattr(message, "additional_kwargs", {}) or {}).get("nailong_compact_summary"):
                continue
            message_type = getattr(message, "type", "message")
            role = {
                "human": "user",
                "ai": "assistant",
                "tool": "tool",
            }.get(message_type, message_type)
            name = getattr(message, "name", None)
            if name:
                role = f"{role}({name})"
            content = _message_text(getattr(message, "content", ""))
            if message_type == "human":
                metadata = getattr(message, "additional_kwargs", {}) or {}
                pin = metadata.get("nailong_pin")
                display = metadata.get("nailong_display")
                goal_objective = next(
                    (line.removeprefix("目标：") for line in content.splitlines()
                     if line.startswith("目标：")),
                    "",
                ) if pin == "goal" else ""
                if isinstance(display, str):
                    content = display
                elif pin == "goal":
                    # Older checkpoints did not store the visible /goal input.
                    if not goal_objective or goal_objective == previous_goal:
                        continue
                    content = f"/goal {goal_objective}"
                elif pin:
                    continue
                elif content.startswith("为以下目标制定执行计划：\n"):
                    # Restore the visible command from older plan checkpoints.
                    content = "/plan " + content.removeprefix("为以下目标制定执行计划：\n")
                previous_goal = goal_objective if pin == "goal" else None
            if not content and getattr(message, "tool_calls", None):
                content = json.dumps(message.tool_calls, ensure_ascii=False, default=str)
            if self.api_key:
                content = content.replace(self.api_key, "[密钥已隐藏]")
            history.append((role, content))
        return history

    def get_context_summary(self, thread_id: str) -> dict:
        """Return a non-secret estimate of the saved conversation context."""
        reports=getattr(self.runtime_factory,'context_reports',{})
        latest=reports.get(thread_id)
        if latest is None and self.session_store:
            for event in self.session_store.read_events(thread_id):
                data=event.get('data',{})
                if event.get('kind')=='context_request' and data.get('profile')!='subagent': latest=dict(data)
                elif latest is not None and event.get('kind')=='context_result' and data.get('profile')==latest.get('profile'):
                    latest['actual_main_input_tokens']=data.get('actual_main_input_tokens')
        if latest is not None:
            result={**latest,'user_turns':sum(role=='user' for role,_ in self.get_history(thread_id))}
            snapshot=getattr(self.runtime_factory,'_memory_snapshot',None)
            if 'memory_budget' not in result and snapshot is not None:
                result['memory_budget']=snapshot.report()
            return result
        agent = self._runtime("chat", None, thread_id)
        state = agent.get_state(
            {"configurable": {"thread_id": thread_id}, "recursion_limit": MAX_TURN_GRAPH_STEPS}
        )
        messages = (getattr(state, "values", {}) or {}).get("messages", [])
        from nailong.core.context import context_report
        parts_fn=getattr(self.runtime_factory,'context_parts',None)
        parts=parts_fn(thread_id) if callable(parts_fn) else {}
        actual=None
        for event in self.session_store.read_events(thread_id) if self.session_store else []:
            if event.get('kind')=='usage' and event.get('data',{}).get('scope','main')=='main' and not event.get('data',{}).get('estimated'):
                actual=event['data'].get('input_tokens')
            elif event.get('kind')=='usage_missing' and event.get('data',{}).get('scope','main')=='main':
                actual=None
        result=context_report(parts,messages,actual)
        from nailong.core.context import configured_context_window
        settings=getattr(self.runtime_factory,'settings',None)
        result['model']=getattr(settings,'model','未知')
        result['context_window']=configured_context_window(settings.model,settings.project_root) if settings else None
        result['compact_threshold_tokens']=self.compact_threshold_tokens
        result['user_turns']=sum(role=='user' for role,_ in self.get_history(thread_id))
        result['memory_budget']=parts.get('memory_report')
        return result

    def get_permission_summary(self) -> str:
        summary = self.permission_engine.format_rules()
        summary = f"当前权限模式：{self.permission_mode}\n" + summary
        return summary.replace(self.api_key, "[密钥已隐藏]") if self.api_key else summary

    def _action_allowed(
        self,
        action: dict,
        profile: AgentProfile,
    ) -> bool:
        name = str(action.get("name", ""))
        result = self.permission_engine.decide_action(
            name,
            action.get("args", {}) or {},
            profile=profile,
            mode=self.permission_mode,
        )
        return result.decision != Decision.DENY

    def _redact_secret(self, value: str) -> str:
        return value.replace(self.api_key, "[密钥已隐藏]") if self.api_key else value

    def _redact_preview(self, preview: dict) -> dict:
        return self._redact_tree(preview)

    def _event(self, thread_id: str | None, kind: str, data: dict) -> TurnEvent:
        if kind == "usage":
            from urllib.parse import urlsplit
            from nailong.core.costs import CostEstimator
            settings = getattr(self.runtime_factory, "settings", None)
            if settings is not None:
                model = data.get("model") or settings.model
                data = {**data, "model": model,
                        "provider_host": urlsplit(settings.api_base).hostname,
                        "price_snapshot": data.get("price_snapshot") or getattr(self.runtime_factory,"active_price_snapshot",None) or CostEstimator(model, settings.project_root).snapshot()}
        safe_data = self._redact_tree(data)
        if thread_id and self.session_store is not None:
            if kind == "token":
                now = time.monotonic()
                pending, started = self._pending_token_events.get(thread_id, ("", now))
                pending += str(safe_data.get("text", ""))
                while len(pending) >= TOKEN_LOG_BATCH_CHARS:
                    self.session_store.append_event(
                        thread_id, "token", {"text": pending[:TOKEN_LOG_BATCH_CHARS]}
                    )
                    pending = pending[TOKEN_LOG_BATCH_CHARS:]
                    started = now
                if pending and now - started >= TOKEN_LOG_FLUSH_SECONDS:
                    self.session_store.append_event(thread_id, "token", {"text": pending})
                    pending = ""
                if pending:
                    self._pending_token_events[thread_id] = (pending, started)
                else:
                    self._pending_token_events.pop(thread_id, None)
            else:
                self._flush_token_events(thread_id)
                self.session_store.append_event(thread_id, kind, safe_data)
        return TurnEvent(kind, safe_data)

    def _flush_token_events(self, thread_id: str | None) -> None:
        if not thread_id or self.session_store is None:
            return
        pending = self._pending_token_events.pop(thread_id, None)
        if pending and pending[0]:
            self.session_store.append_event(thread_id, "token", {"text": pending[0]})

    def _child_usage_event(self, thread_id: str | None) -> TurnEvent | None:
        runner = getattr(self.runtime_factory, "task_runner", None)
        drain = getattr(runner, "drain_usage", None)
        usage = drain(thread_id) if thread_id and callable(drain) else None
        if usage is not None and usage.get('calls'):
            for call in usage['calls']:
                self._event(thread_id,'model_usage_call',{**call,'scope':'subagent'})
        return self._event(thread_id, "usage", usage) if usage is not None else None

    def _redact_tree(self, value):
        if isinstance(value, str):
            return self._redact_secret(value)
        if isinstance(value, dict):
            return {key: self._redact_tree(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_tree(item) for item in value]
        if isinstance(value, tuple):
            return [self._redact_tree(item) for item in value]
        return value

    def _message_ids_at_boundary(self, agent, config: dict) -> list[str]:
        get_state = getattr(agent, "get_state", None)
        if not callable(get_state):
            return []
        try:
            state = get_state(config)
            messages = (getattr(state, "values", {}) or {}).get("messages", [])
        except Exception:
            return []
        return [str(message.id) for message in messages if getattr(message, "id", None)]

    def _tool_start_data(
        self, name: str, args: dict, call_id: str = "", *, profile: AgentProfile = "chat", tool_spec=None
    ) -> dict:
        options = {}
        if tool_spec is not None and 'tool_spec' in inspect.signature(self.permission_engine.decide_action).parameters:
            options['tool_spec'] = tool_spec
        permission = self.permission_engine.decide_action(
            name,
            args,
            profile=profile,
            mode=self._permission_mode(profile),
            **options,
        )
        preview = preview_mutation(
            {"name": name, "args": args},
            self.permission_engine.project_root,
        )
        if name == "run_command":
            preview = {"command": self._redact_secret(str(args.get("command", "")))[:2_000], "cwd": str(self.permission_engine.project_root)}
        elif not preview and args.get("path"):
            preview = {"path": str(args["path"])}
        return self._redact_preview(
            {
                "name": name,
                "call_id": call_id,
                "reason": permission.reason,
                "preview": preview,
            }
        )

    def _tool_result_summary(self, message: ToolMessage) -> dict:
        content = _message_text(getattr(message, "content", ""))
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            payload = None
        summary = {"name": getattr(message, "name", "tool")}
        if isinstance(payload, dict):
            if "ok" in payload:
                summary["ok"] = bool(payload["ok"])
            if payload.get("path"):
                summary["path"] = str(payload["path"])
            if payload.get("exit_code") is not None:
                summary["exit_code"] = payload["exit_code"]
            if payload.get("timed_out"):
                summary["timed_out"] = True
            if payload.get("error"):
                summary["summary"] = str(payload["error"])[:500]
            elif "files" in payload:
                summary["summary"] = f"返回 {len(payload.get('files') or [])} 个文件"
            elif "matches" in payload:
                summary["summary"] = f"返回 {len(payload.get('matches') or [])} 条匹配"
            elif "diff" in payload:
                summary["summary"] = f"替换 {payload.get('replacements', 0)} 处"
                summary["diff"] = str(payload["diff"])[:3_000]
            elif "content" in payload:
                summary["summary"] = f"读取 {len(str(payload['content']))} 个字符"
            elif "output" in payload:
                summary["summary"] = f"命令输出 {len(str(payload['output']))} 个字符"
            elif "chars_written" in payload:
                summary["summary"] = f"写入 {payload['chars_written']} 个字符"
            if "output" in payload:
                output = self._redact_secret(str(payload["output"]))
                lines = [line for line in output.splitlines() if line.strip()]
                tail = "\n".join(lines[-3:])[-400:]
                if tail:
                    summary["output_snippet"] = ("…\n" if output.strip() != tail else "") + tail
                    summary["output_preview"] = ("…\n" if len(output) > 6_000 else "") + output[-6_000:]
                summary["output_truncated"] = bool(payload.get("output_truncated")) or len(str(payload["output"])) > 6_000
        else:
            summary["summary"] = f"返回 {len(content)} 个字符"
        return self._redact_preview(summary)

    @staticmethod
    def _update_messages(update: dict) -> list:
        messages = []
        for node_update in update.values():
            if not isinstance(node_update, dict):
                continue
            node_messages = node_update.get("messages", [])
            if isinstance(node_messages, list):
                messages.extend(node_messages)
        return messages

    async def stream_turn(
        self,
        message: str,
        config: dict,
        *,
        profile: AgentProfile = "chat",
        target_path: str | None = None,
        allowed_tools: set[str] | frozenset[str] | None = None,
        review_paths: frozenset[str] | None = None,
        pin_message: bool | str = False,
        history_display: str | None = None,
        max_graph_steps: int = MAX_TURN_GRAPH_STEPS,
        max_model_calls: int = MAX_TURN_MODEL_CALLS,
        cost_budget: GoalCostBudget | None = None,
        approval_handler: ApprovalHandler | None = None,
        status_handler: StatusHandler | None = None,
    ):
        """Stream safe turn events, pausing and restarting after each approval."""
        try:
            graph_step_limit = int(max_graph_steps)
        except (TypeError, ValueError) as error:
            raise ValueError("max_graph_steps 必须是正整数。") from error
        if not 1 <= graph_step_limit <= MAX_TURN_GRAPH_STEPS:
            raise ValueError(
                f"max_graph_steps 必须在 1 到 {MAX_TURN_GRAPH_STEPS} 之间。"
            )
        if type(max_model_calls) is not int or not 1 <= max_model_calls <= MAX_TURN_MODEL_CALLS:
            raise ValueError(f"max_model_calls 必须在 1 到 {MAX_TURN_MODEL_CALLS} 之间。")
        original_message = message
        thread_id = config.get("configurable", {}).get("thread_id")
        self.last_turn_task_id = None
        if profile == 'chat' and not pin_message:
            visible_message = history_display if history_display is not None else original_message
            intent = task_request_kind(visible_message)
            task = (self.task_store.snapshot(thread_id)
                if self.task_store is not None and thread_id else None)
            paused_chat = (task is not None and task['lifecycle'] in {'paused', 'blocked'}
                and intent not in {'resume', 'work'})
            if intent == 'status' or paused_chat:
                # A query never enters task mutation, hooks, model inference or tools.
                answer = render_task_status(task)
                if paused_chat:
                    answer += '\n任务保持暂停；输入 /task resume 或“继续执行”恢复，修改要求可用 /task amend。'
                self._event(thread_id, 'turn_start', {'profile': profile, 'visible': True, 'local': True})
                self._event(thread_id, 'user', {'text': visible_message})
                self._event(thread_id, 'task_query', {
                    'intent': intent, 'task_id': task['task_id'] if task else None,
                    'revision': task['revision'] if task else None,
                })
                await self._notify(status_handler, 'ready')
                yield self._event(thread_id, 'status', {'status': 'ready'})
                yield self._event(thread_id, 'final', {
                    'text': answer, 'local': True,
                    'stats': {'model_calls': 0, 'tool_calls': 0, 'graph_steps': 0},
                })
                return
        skill_registry = getattr(self.runtime_factory, "skill_registry", None)
        if profile == "chat" and skill_registry is not None:
            message, explicit_skill = skill_registry.prepare_invocation(message)
            if explicit_skill and history_display is None:
                history_display = original_message
        thread_id = config.get("configurable", {}).get("thread_id")
        participating_task = await self._begin_task(thread_id, history_display or original_message, profile,
            target_path, goal_round=bool(pin_message and history_display is None), review_paths=review_paths)
        self.last_turn_task_id = participating_task['task_id'] if participating_task is not None else None
        visible_message = history_display if history_display is not None else original_message
        simple_project_question = profile == 'chat' and not pin_message and is_simple_project_question(visible_message)
        if simple_project_question:
            from nailong.tools.registry import get_tool_specs
            read_tools = {spec.name for spec in get_tool_specs('chat') if spec.read_only and spec.name != 'task'}
            # The current task archive is registered dynamically when a session
            # exists, so it cannot be discovered by the unbound static registry.
            read_tools.add('read_task_context')
            if allowed_tools is None:
                allowed_tools = read_tools
            else:
                allowed_tools = set(allowed_tools) & read_tools
        agent = await self._runtime_async(profile, target_path, thread_id, allowed_tools, review_paths)
        await self._close_interrupted_tool_exchange(agent, config)
        task_runner = getattr(self.runtime_factory, "task_runner", None)
        if task_runner is not None and thread_id:
            task_runner.begin_turn(thread_id)
        if profile == "chat" and not hasattr(self.runtime_factory,'context_reports'):
            compacted = await self._compact_state(agent, config)
            if compacted is not None and compacted.compacted:
                self._event(
                    thread_id,
                    "compact",
                    {
                        "level": "L1",
                        "before_tokens": compacted.before_tokens,
                        "after_tokens": compacted.after_tokens,
                        "reason": "automatic_threshold",
                    },
                )
        initial_step = await self._graph_step_async(agent, config)
        boundary_ids = await self._message_ids_at_boundary_async(agent, config)
        review_collector = (await self._prepare_review(agent, config, participating_task)
            if profile == 'review' and participating_task is not None else None)
        visible_turn = not pin_message or history_display is not None
        if thread_id and self.session_store is not None:
            self.session_store.append_event(
                thread_id,
                "turn_start",
                {"message_ids": boundary_ids, "profile": profile, "visible": visible_turn},
            )
        if simple_project_question:
            self._event(thread_id, 'tool_policy', {
                'excluded_tools': ['task'], 'reason': '简单项目介绍由主代理直接处理。',
            })

        async def invocation_config() -> dict:
            if initial_step is None:
                return {**config, "recursion_limit": graph_step_limit - 1}
            current_step = await self._graph_step_async(agent, config)
            consumed = max(0, (current_step or 0) - initial_step)
            remaining = graph_step_limit - consumed
            # Resuming an interrupted graph can commit up to three checkpoint
            # steps before LangGraph applies a recursion limit of one.
            if remaining <= 2:
                raise TurnRecursionLimitError(
                    f"本轮内部执行已达到上限（{graph_step_limit} 步），已停止本轮。"
                )
            return {**config, "recursion_limit": remaining - 1}

        safe_message = (
            message.replace(self.api_key, "[密钥已隐藏]") if self.api_key else message
        )
        prompt_hooks = await self._run_hook(
            "UserPromptSubmit",
            prompt=self._redact_secret(original_message),
            approval_handler=approval_handler,
            profile=profile,
        )
        if prompt_hooks.blocked:
            if participating_task is not None:
                with self.task_store.bound_task(thread_id, participating_task['task_id'],
                        revision=participating_task['revision']) as bound:
                    if bound is not None:
                        self.task_store.set_state(thread_id, lifecycle='paused',
                            blockers=[prompt_hooks.reason or '用户输入钩子阻止了本轮。'])
            yield self._event(thread_id, "error", {"message": prompt_hooks.reason or "用户输入钩子已阻止本轮。"})
            return
        if prompt_hooks.feedback:
            safe_message += "\n\n项目钩子补充上下文：\n" + self._redact_secret(prompt_hooks.feedback)
            yield self._event(thread_id, "hook_feedback", {"event": "UserPromptSubmit", "text": prompt_hooks.feedback})
        current_input = {
            "messages": [
                HumanMessage(
                    content=safe_message,
                    id=(f"nailong-fixed-{pin_message if isinstance(pin_message,str) else 'plan'}" if pin_message else None),
                    additional_kwargs={
                        **({"nailong_pin": pin_message if isinstance(pin_message, str) else "plan"} if pin_message else {}),
                        **({"nailong_display": self._redact_secret(history_display)} if history_display is not None else {}),
                    },
                )
            ]
        }
        if visible_turn:
            self._event(thread_id,"user",{"text":self._redact_secret(history_display if history_display is not None else original_message)})
        started_tools: dict[str, float] = {}
        tool_args_by_id: dict[str, dict] = {}
        emitted_tool_calls: set[str] = set()
        emitted_usage_message_ids: set[str] = set(boundary_ids)
        stream = None
        hook_approval_token = self._hook_approval.set(approval_handler)
        from agent import active_review_coverage, TaskExecutionStopped
        review_token = active_review_coverage.set(review_collector)
        task_token = self._turn_task.set(participating_task)
        turn_budget = TurnModelBudget(max_model_calls, lambda data: self._event(thread_id, 'model_call', data))
        budget_token = active_turn_budget.set(turn_budget)
        try:
            await self._notify(status_handler, "thinking")
            yield self._event(thread_id, "status", {"status": "thinking"})
            while True:
                actions: list[dict] = []
                latest_value = None
                streamed_text: list[str] = []
                pending_usage = None
                redactor = StreamingRedactor(self.api_key)
                if callable(getattr(agent, "astream", None)):
                    stream = _budgeted_events(agent.astream(
                        current_input,
                        await invocation_config(),
                        stream_mode=["messages", "updates"],
                    ), cost_budget)
                    async for item in stream:
                        if isinstance(item, tuple) and len(item) == 2:
                            mode, payload = item
                        else:
                            mode, payload = getattr(item, "type", ""), getattr(item, "data", item)
                        if mode == "messages":
                            chunk = payload[0] if isinstance(payload, tuple) else payload
                            if isinstance(chunk, (AIMessage, AIMessageChunk)):
                                pending_usage = _usage_from_message(chunk) or pending_usage
                                token = redactor.feed(_message_text(getattr(chunk, "content", "")))
                                if token:
                                    streamed_text.append(token)
                                    yield self._event(thread_id, "token", {"text": token})
                            continue
                        if mode != "updates" or not isinstance(payload, dict):
                            continue
                        if "__interrupt__" in payload:
                            actions = _interrupt_actions_from_update(payload)
                            # LangGraph commits the interrupted checkpoint when
                            # its async stream closes. Resume must wait for
                            # that close or the same approval can be replayed.
                            close_stream = getattr(stream, "aclose", None)
                            if callable(close_stream):
                                await close_stream()
                            break
                        for updated_message in self._update_messages(payload):
                            if isinstance(updated_message, AIMessage):
                                # Finish this message's redaction buffer before
                                # a tool record or the next model message can
                                # appear in the transcript.
                                remainder = redactor.feed("", final=True)
                                if remainder:
                                    streamed_text.append(remainder)
                                    yield self._event(thread_id, "token", {"text": remainder})
                                redactor = StreamingRedactor(self.api_key)
                                latest_value = {"messages": [updated_message]}
                                usage = _usage_from_message(updated_message) or pending_usage
                                message_id = str(getattr(updated_message, "id", "") or "")
                                if usage is not None and (
                                    not message_id or message_id not in emitted_usage_message_ids
                                ):
                                    yield self._event(thread_id, "usage", usage)
                                    if message_id:
                                        emitted_usage_message_ids.add(message_id)
                                elif usage is None:
                                    self._event(thread_id, "usage_missing", {"scope": "main", "message_id": message_id})
                                pending_usage = None
                                for tool_call in updated_message.tool_calls:
                                    call_id = str(tool_call.get("id", ""))
                                    if call_id and call_id in emitted_tool_calls:
                                        continue
                                    if call_id:
                                        emitted_tool_calls.add(call_id)
                                    name = str(tool_call.get("name", ""))
                                    args = tool_call.get("args", {}) or {}
                                    tool_args_by_id[call_id] = args
                                    event_data = self._tool_start_data(name, args, call_id, profile=profile,
                                        tool_spec=getattr(agent, '_nailong_tool_specs', {}).get(name))
                                    started_tools[call_id] = time.monotonic()
                                    yield self._event(thread_id, "tool_start", event_data)
                            elif isinstance(updated_message, ToolMessage):
                                call_id = str(getattr(updated_message, "tool_call_id", ""))
                                elapsed_ms = int(
                                    max(0.0, time.monotonic() - started_tools.pop(call_id, time.monotonic())) * 1000
                                )
                                tool_summary = self._tool_result_summary(updated_message)
                                tool_summary["elapsed_ms"] = elapsed_ms
                                tool_summary["call_id"] = call_id
                                yield self._event(thread_id, "tool_end", tool_summary)
                                progress = await self._observe_task_tool(thread_id, updated_message, tool_args_by_id.get(call_id, {}))
                                if progress and progress['action'] != 'none':
                                    yield self._event(thread_id, 'notice', {'text': progress['reason']})
                                child_usage = self._child_usage_event(thread_id)
                                if child_usage is not None:
                                    yield child_usage
                                tool_name = str(getattr(updated_message, "name", ""))
                                hook_result = await self._run_hook(
                                    "PostToolUse",
                                    name=tool_name,
                                    args=tool_args_by_id.pop(call_id, {}),
                                    approval_handler=approval_handler,
                                    profile=profile,
                                )
                                if hook_result.feedback:
                                    yield self._event(
                                        thread_id,
                                        "hook_feedback",
                                        {"event": "PostToolUse", "name": tool_name, "text": hook_result.feedback},
                                    )
                                if getattr(updated_message, "name", None) == "exit_plan_mode":
                                    try:
                                        result = json.loads(_message_text(updated_message.content))
                                    except (json.JSONDecodeError, TypeError):
                                        result = {}
                                    if result.get("ok") and result.get("plan_id"):
                                        yield self._event(
                                            thread_id,
                                            "plan_ready",
                                            {"plan_id": str(result["plan_id"])},
                                        )
                                if not tool_args_by_id:
                                    pause_reason = self._turn_pause_reason(thread_id)
                                    if pause_reason:
                                        yield self._event(thread_id, 'task_paused', {'reason': pause_reason})
                                        yield self._event(thread_id, 'final', {'text': pause_reason, 'delivery': {'status': 'blocked'}})
                                        return
                    remainder = redactor.feed("", final=True)
                    if remainder:
                        streamed_text.append(remainder)
                        yield self._event(thread_id, "token", {"text": remainder})
                    if pending_usage is not None:
                        yield self._event(thread_id, "usage", pending_usage)
                else:
                    try:
                        result = await _budgeted_invoke(
                            agent,
                            current_input,
                            await invocation_config(),
                            cost_budget,
                        )
                    except GraphRecursionError as error:
                        raise TurnRecursionLimitError(
                            f"本轮内部执行已达到上限（{graph_step_limit} 步），已停止本轮。"
                        ) from error
                    latest_value = _output_value(result)
                    actions = _interrupt_actions(result)
                    messages = latest_value.get("messages", []) if isinstance(latest_value, dict) else []
                    # The non-streaming adapter must only record new tool results.
                    completed_args = {str(call.get('id', '')): call.get('args', {}) or {}
                        for row in messages for call in getattr(row, 'tool_calls', []) or []}
                    for row in messages:
                        if isinstance(row, ToolMessage) and str(getattr(row, 'id', '') or '') not in boundary_ids:
                            progress = await self._observe_task_tool(thread_id, row,
                                completed_args.get(str(getattr(row, 'tool_call_id', '')), {}))
                            if progress and progress['action'] == 'pause':
                                yield self._event(thread_id, 'task_paused', {'reason': progress['reason']})
                                yield self._event(thread_id, 'final', {'text': progress['reason'], 'delivery': {'status': 'blocked'}})
                                return
                    for completed_message in messages:
                        if getattr(completed_message, "name", None) == "exit_plan_mode":
                            try:
                                plan_result = json.loads(_message_text(completed_message.content))
                            except (json.JSONDecodeError, TypeError):
                                plan_result = {}
                            if plan_result.get("ok") and plan_result.get("plan_id"):
                                yield self._event(
                                    thread_id,
                                    "plan_ready",
                                    {"plan_id": str(plan_result["plan_id"])},
                                )
                    for completed_message in messages:
                        if getattr(completed_message, "type", None) != "ai":
                            continue
                        usage = _usage_from_message(completed_message)
                        message_id = str(getattr(completed_message, "id", "") or "")
                        if usage is not None and (not message_id or message_id not in emitted_usage_message_ids):
                            yield self._event(thread_id, "usage", usage)
                            if message_id:
                                emitted_usage_message_ids.add(message_id)
                        elif usage is None and message_id not in emitted_usage_message_ids:
                            self._event(thread_id, "usage_missing", {"scope": "main", "message_id": message_id})
                    child_usage = self._child_usage_event(thread_id)
                    if child_usage is not None:
                        yield child_usage

                if actions:
                    prepared = []
                    for action in actions:
                        name = str(action.get("name", ""))
                        args = action.get("args", {}) or {}
                        secret_in_action = _contains_secret(action, self.api_key) or "[密钥已隐藏]" in json.dumps(action, ensure_ascii=False)
                        permission = self.permission_engine.decide_action(
                            name,
                            args,
                            profile=profile,
                            mode=self._permission_mode(profile),
                            **({'tool_spec':getattr(agent,'_nailong_tool_specs',{}).get(name)}
                               if 'tool_spec' in inspect.signature(self.permission_engine.decide_action).parameters else {}),
                        )
                        prepared.append((action, permission, secret_in_action))
                    approval_events = []
                    for action, permission, secret_in_action in prepared:
                        if permission.decision != Decision.ASK or secret_in_action:
                            continue
                        approval_events.append(
                            {
                                "name": str(action.get("name", "")),
                                "reason": self._redact_secret(permission.reason),
                                "preview": self._redact_preview(
                                    preview_mutation(action, self.permission_engine.project_root)
                                ),
                            }
                        )
                    if approval_events:
                        yield self._event(thread_id, "approval_needed", {"actions": approval_events})
                    decisions = []
                    for index, (action, permission, secret_in_action) in enumerate(prepared, start=1):
                        name = str(action.get("name", ""))
                        args = action.get("args", {}) or {}
                        if secret_in_action:
                            decisions.append({"type": "reject", "message": "操作参数包含配置密钥，已拒绝。"})
                            continue
                        if permission.decision == Decision.DENY:
                            decisions.append({"type": "reject", "message": permission.reason})
                            continue
                        if permission.decision == Decision.ALLOW:
                            if self._uses_tool_executor:
                                decisions.append({'type': 'approve'})
                                continue
                            pre_hook = await self._run_hook(
                                "PreToolUse", name=name, args=args, approval_handler=approval_handler
                            )
                            if pre_hook.blocked:
                                reason = self._redact_secret(pre_hook.reason or "PreToolUse 钩子阻止了操作。")
                                yield self._event(thread_id, "hook_blocked", {"event": "PreToolUse", "name": name, "reason": reason})
                                decisions.append({"type": "reject", "message": reason})
                                continue
                            decisions.append({"type": "approve"})
                            continue

                        await self._notify(status_handler, "waiting_approval")
                        yield self._event(thread_id, "status", {"status": "waiting_approval"})
                        approval = ApprovalDecision("reject")
                        if approval_handler is not None:
                            display_action = {
                                **action,
                                "_approval": {
                                    "reason": self._redact_secret(permission.reason),
                                    "suggested_rule": self._redact_secret(permission.suggested_rule or ""),
                                    "preview": self._redact_preview(
                                        preview_mutation(action, self.permission_engine.project_root)
                                    ),
                                },
                            }
                            answer = approval_handler(display_action, index, len(actions))
                            if inspect.isawaitable(answer):
                                answer = await answer
                            if isinstance(answer, ApprovalDecision):
                                approval = answer
                            elif str(answer).strip().lower() in {
                                "approve", "approve_once", "a", "yes", "y"
                            }:
                                approval = ApprovalDecision("approve_once")
                            elif str(answer).strip().lower() == "approve_session":
                                approval = ApprovalDecision("approve_session")
                        if approval.kind == "approve_session":
                            rule = approval.rule or permission.suggested_rule
                            if rule and "<" not in rule:
                                try:
                                    self.permission_engine.grant_session(rule)
                                except ValueError:
                                    approval = ApprovalDecision("reject", comment="会话规则无效，已拒绝。")
                            else:
                                approval = ApprovalDecision("reject", comment="无法为该操作生成安全的会话规则。")
                        if approval.kind in {"approve_once", "approve_session"}:
                            pre_hook = None if self._uses_tool_executor else await self._run_hook(
                                "PreToolUse", name=name, args=args, approval_handler=approval_handler)
                            if pre_hook is not None and pre_hook.blocked:
                                reason = self._redact_secret(pre_hook.reason or "PreToolUse 钩子阻止了操作。")
                                yield self._event(thread_id, "hook_blocked", {"event": "PreToolUse", "name": name, "reason": reason})
                                decisions.append({"type": "reject", "message": reason})
                            else:
                                decisions.append({"type": "approve"})
                        else:
                            reject_decision = {"type": "reject"}
                            if approval.comment:
                                reject_decision["message"] = self._redact_secret(approval.comment)
                            decisions.append(reject_decision)
                        yield self._event(
                            thread_id,
                            "approval_decision",
                            {"name": name, "kind": approval.kind, "rule": approval.rule},
                        )
                    current_input = _resume_approvals(actions, decisions)
                    await self._notify(status_handler, "thinking")
                    yield self._event(thread_id, "status", {"status": "thinking"})
                    continue

                answer = _final_answer(latest_value) if latest_value is not None else "".join(streamed_text)
                answer = self._redact_secret(answer) or "模型返回了空回答。"
                stop_hooks = await self._run_hook("Stop", approval_handler=approval_handler, profile=profile)
                if stop_hooks.feedback:
                    yield self._event(thread_id, "hook_feedback", {"event": "Stop", "text": stop_hooks.feedback})
                child_usage = self._child_usage_event(thread_id)
                if child_usage is not None:
                    yield child_usage
                if review_collector is not None:
                    await self._finish_review(thread_id, participating_task, review_collector)
                report = (await self.delivery_report(thread_id, expected_task_id=participating_task['task_id'],
                    expected_revision=participating_task['revision']) if participating_task is not None else None)
                if report is not None:
                    from nailong.core.delivery import render_delivery_report
                    with self._bound_turn_task(thread_id) as bound:
                        if bound is not None:
                            self.task_store.set_state(thread_id, phase='deliver')
                    yield self._event(thread_id, 'delivery', {**report, 'text': render_delivery_report(report)})
                current_step = await self._graph_step_async(agent, config)
                stats = {"model_calls": turn_budget.calls, "model_call_limit": max_model_calls,
                         "summary_only": turn_budget.summary_only,
                         "tool_calls": len(emitted_tool_calls), "graph_step_limit": graph_step_limit,
                         "graph_steps": max(0, current_step - initial_step) if current_step is not None and initial_step is not None else None}
                yield self._event(thread_id, "final", {"text": answer, "stats": stats,
                    **({'delivery': report} if report is not None else {})})
                return
        except TurnModelLimitExceeded as error:
            self._pause_turn_task(thread_id, str(error))
            if error.completed_response is not None:
                for completed_message in error.completed_response.result:
                    if not isinstance(completed_message, AIMessage):
                        continue
                    message_id = str(getattr(completed_message, 'id', '') or '')
                    if message_id and message_id in emitted_usage_message_ids:
                        continue
                    usage = _usage_from_message(completed_message)
                    if usage is not None:
                        yield self._event(thread_id, 'usage', usage)
                        if message_id:
                            emitted_usage_message_ids.add(message_id)
                    else:
                        yield self._event(thread_id, 'usage_missing', {'scope': 'main', 'message_id': message_id})
            stats = {"model_calls": turn_budget.calls, "model_call_limit": max_model_calls,
                     "tool_calls": len(emitted_tool_calls), "graph_step_limit": graph_step_limit,
                     "limit_type": "model"}
            self._event(thread_id, 'turn_limit', {**stats, "message": str(error)})
            raise TurnRecursionLimitError(str(error), stats=stats) from error
        except TaskExecutionStopped as error:
            reason = self._redact_secret(str(error))
            if not error.task_replaced:
                self._pause_turn_task(thread_id, reason)
            yield self._event(thread_id, 'notice' if error.task_replaced else 'task_paused', {'reason': reason, 'text': reason})
            yield self._event(thread_id, 'final', {'text': reason,
                **({'delivery': {'status': 'blocked'}} if not error.task_replaced else {})})
            return
        except (GraphRecursionError, TurnRecursionLimitError) as error:
            detail = (str(error) if isinstance(error, TurnRecursionLimitError) else
                f"本轮内部执行已达到上限（{graph_step_limit} 步；模型调用 {turn_budget.calls}/{max_model_calls} 次），已停止本轮。")
            self._pause_turn_task(thread_id, detail)
            stats = {"model_calls": turn_budget.calls, "model_call_limit": max_model_calls,
                     "tool_calls": len(emitted_tool_calls), "graph_step_limit": graph_step_limit,
                     "limit_type": "graph"}
            self._event(thread_id, 'turn_limit', {**stats, "message": detail})
            raise TurnRecursionLimitError(detail, stats=stats) from error
        except (asyncio.CancelledError, TimeoutError):
            self._pause_turn_task(thread_id)
            self._event(thread_id,'usage_missing',{'scope':'main','reason':'cancelled_or_timed_out'})
            raise
        except CostBudgetExceeded:
            self._pause_turn_task(thread_id, '已达到本次成本预算。')
            raise
        except Exception:
            self._pause_turn_task(thread_id)
            self._event(thread_id,'usage_missing',{'scope':'main','reason':'request_failed'})
            raise
        finally:
            try:
                # Closing the graph cancels and joins its children before their
                # collected usage is drained and the caller settles this round.
                close_stream = getattr(stream, "aclose", None)
                if callable(close_stream):
                    await close_stream()
            finally:
                active_review_coverage.reset(review_token)
                active_turn_budget.reset(budget_token)
                self._turn_task.reset(task_token)
                self._hook_approval.reset(hook_approval_token)
                self._child_usage_event(thread_id)
                self._flush_token_events(thread_id)

    async def rewind(self, thread_id: str) -> int | None:
        """Roll back dialog state to the latest user-turn boundary.

        This only changes LangGraph messages and the safe event log. It does not
        undo file edits or commands that already ran.
        """
        if self.session_store is None:
            raise ValueError("当前会话没有持久化存储。")
        boundary = self.session_store.last_turn_boundary(thread_id)
        if boundary is None:
            return None
        index, marker = boundary
        boundary_data = marker.get("data") or {}
        if boundary_data.get('local') is True:
            removed_events = self.session_store.truncate_events(thread_id, index - 1)
            self.session_store.append_event(thread_id, 'rewind', {
                'messages_removed': 0, 'events_removed': removed_events, 'local': True,
            })
            return 0
        boundary_ids = boundary_data.get("message_ids")
        boundary_count = boundary_data.get("boundary_message_count")
        if (
            not isinstance(boundary_ids, list)
            or any(not isinstance(item, str) for item in boundary_ids)
            or (boundary_count is not None and boundary_count != len(boundary_ids))
            or (boundary_count is None and len(boundary_ids) >= 100)
        ):
            raise ValueError("会话回退边界不完整或属于可能截断的旧日志，未回退；请开始新会话。")
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": MAX_TURN_GRAPH_STEPS,
        }
        agent = await self._runtime_async("chat", None, thread_id)
        get_state = getattr(agent, "aget_state", None)
        state = await get_state(config) if callable(get_state) else agent.get_state(config)
        messages = (getattr(state, "values", {}) or {}).get("messages", [])
        keep_ids = set(boundary_ids)
        removed_ids = [
            str(message.id)
            for message in messages
            if getattr(message, "id", None) and str(message.id) not in keep_ids
        ]
        if removed_ids:
            update = getattr(agent, "aupdate_state", None)
            if callable(update):
                await update(config, {"messages": [RemoveMessage(id=item) for item in removed_ids]})
            else:
                agent.update_state(config, {"messages": [RemoveMessage(id=item) for item in removed_ids]})
        removed_events = self.session_store.truncate_events(thread_id, index - 1)
        self.session_store.append_event(
            thread_id,
            "rewind",
            {"messages_removed": len(removed_ids), "events_removed": removed_events},
        )
        if self.task_store is not None:
            task = self.task_store.snapshot(thread_id)
            if task is not None:
                self.task_store.reconcile(thread_id,
                    current_input_fingerprint=await self._task_input_fingerprint(task))
                self.task_store.set_state(thread_id, lifecycle='paused',
                    blockers=['对话已回退；已执行操作和任务证据保留，继续前请核对。'])
        return len(removed_ids)

    async def run_turn(
        self,
        message: str,
        config: dict,
        *,
        profile: AgentProfile = "chat",
        target_path: str | None = None,
        allowed_tools: set[str] | frozenset[str] | None = None,
        review_paths: frozenset[str] | None = None,
        pin_message: bool | str = False,
        history_display: str | None = None,
        max_graph_steps: int = MAX_TURN_GRAPH_STEPS,
        max_model_calls: int = MAX_TURN_MODEL_CALLS,
        approval_handler: ApprovalHandler | None = None,
        status_handler: StatusHandler | None = None,
    ) -> str:
        """Backward-compatible adapter that folds a stream into its final text."""
        answer = ""
        async for event in self.stream_turn(
            message,
            config,
            profile=profile,
            target_path=target_path,
            allowed_tools=allowed_tools,
            **({"review_paths": review_paths} if review_paths is not None else {}),
            pin_message=pin_message,
            history_display=history_display,
            max_graph_steps=max_graph_steps,
            max_model_calls=max_model_calls,
            approval_handler=approval_handler,
            status_handler=status_handler,
        ):
            if event.kind == "final":
                answer = event.data.get("text", "")
        return answer
