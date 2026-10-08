"""Build profile-scoped LangChain Agent runtimes and approval gates."""

import json
import asyncio
from collections import OrderedDict
from typing import Literal
from dataclasses import replace
from contextvars import ContextVar

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langchain_deepseek import ChatDeepSeek as _DeepSeekBase
from nailong.core.model import HarnessChatDeepSeek as ChatDeepSeek
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph

from config import Settings
from nailong.core.memory import load_project_memory, MemorySnapshot
from nailong.core.memory_context import MemoryReadContext
from nailong.core.goal import GoalStore
from nailong.core.plan import PlanStore
from nailong.core.sessions import ProjectSessionStore
from nailong.core.skills import SkillRegistry
from nailong.tools.agents import (ChildTaskResult, ReadOnlyTaskRunner,
    READONLY_RESEARCH_TOOLS, readonly_prompt_parts, ChildResearchBudgetMiddleware)
from nailong.core.budgets import (
    active_cost_budget, active_token_budget, CostBudgetExceeded, TokenBudgetExceeded,
    active_turn_budget, TurnModelLimitExceeded,
)
from nailong.core.usage import active_usage_collector, collect_model_usage
from nailong.tools.files import FileSession
from tools import CONTEXT_FILE, build_tools


from nailong.core.prompts import (
    AgentProfile, SYSTEM_PROMPT, OUTPUT_POLICY, FINAL_CHECK_POLICY, REVIEW_POLICY,
    EXPLORATION_POLICY, EDIT_POLICY, COMMAND_POLICY, DELEGATION_POLICY,
    GOAL_POLICY, build_prompt_parts,
)


active_review_coverage: ContextVar = ContextVar("active_review_coverage", default=None)


class TaskExecutionStopped(RuntimeError):
    def __init__(self, reason, *, task_replaced=False):
        super().__init__(reason)
        self.task_replaced = task_replaced


class TaskLifecycleMiddleware(AgentMiddleware):
    """Do not fund another model request after a durable task has stopped."""

    def __init__(self, task_store, snapshot):
        super().__init__()
        self.task_store = task_store
        self.snapshot = snapshot

    def before_model(self, state, runtime):
        if self.snapshot is None:
            return None
        expected = self.snapshot
        with self.task_store.bound_task(expected['thread_id'], expected['task_id'], expected['revision']) as task:
            if task is None:
                raise TaskExecutionStopped('本轮任务已更新；旧执行已停止，请按当前要求继续。', task_replaced=True)
            if task['lifecycle'] in {'paused', 'blocked'}:
                raise TaskExecutionStopped('; '.join(task['blockers']) or '当前任务已暂停。')
        return None

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)


class ReviewInputMiddleware(AgentMiddleware):
    """Count only prepared file pages consumed by a successful model request."""

    def wrap_model_call(self, request, handler):
        response = handler(request)
        collector = active_review_coverage.get()
        if collector is not None:
            collector.consume_request(request.messages)
        return response

    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        collector = active_review_coverage.get()
        if collector is not None:
            collector.consume_request(request.messages)
        return response


def _redact_nested(value, secret: str):
    if isinstance(value, str):
        return value.replace(secret, "[密钥已隐藏]")
    if isinstance(value, list):
        return [_redact_nested(item, secret) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_nested(item, secret) for item in value)
    if isinstance(value, dict):
        return {key: _redact_nested(item, secret) for key, item in value.items()}
    return value


class ModelAccountingMiddleware(AgentMiddleware):
    """Reserve each actual request and collect even a child's partial usage."""

    def __init__(self, settings=None, *, main_agent=False):
        super().__init__()
        self.settings=settings
        self.main_agent = main_agent

    def _turn_budget(self):
        return active_turn_budget.get() if self.main_agent else None

    def _check_response(self, response, budget):
        if budget is not None and budget.summary_only:
            if any(getattr(message, 'tool_calls', None) for message in response.result):
                raise TurnModelLimitExceeded("本轮最后一次模型调用仍请求工具，已停止执行并保留会话。",
                                             completed_response=response)

    def _provenance(self):
        if self.settings is None: return {}
        from urllib.parse import urlsplit
        from nailong.core.costs import CostEstimator
        return {'requested_model':self.settings.model,'provider_host':urlsplit(self.settings.api_base).hostname,
                'price_snapshot':CostEstimator(self.settings.model,self.settings.project_root).snapshot()}

    @staticmethod
    def _attach(response,provenance):
        if provenance:
            for message in response.result:
                if getattr(message,'type',None)=='ai':
                    message.response_metadata={**(message.response_metadata or {}),'nailong_call_provenance':provenance}

    def wrap_model_call(self, request, handler):
        if active_cost_budget.get() is not None or active_token_budget.get() is not None:
            raise RuntimeError("预算控制的模型调用必须使用异步执行。")
        provenance=self._provenance()
        collector = active_usage_collector.get()
        if collector is not None:
            collector.attempts += 1
        try:
            budget = self._turn_budget()
            if budget is not None:
                budget.admit()
            response = handler(request)
        except BaseException:
            if collector is not None:
                collector.complete = False
            raise
        self._attach(response,provenance)
        if collector is not None:
            collector.observe(response.result)
        self._check_response(response, budget)
        return response

    async def awrap_model_call(self, request, handler):
        token_budget = active_token_budget.get()
        cost_budget = active_cost_budget.get()
        token_hold = cost_hold = None
        try:
            if token_budget is not None:
                token_hold, cap = await token_budget.reserve(request)
                request = request.override(model_settings={**request.model_settings, "max_tokens": cap})
            if cost_budget is not None:
                cost_hold, cap = await cost_budget.reserve(request)
                request = request.override(model_settings={**request.model_settings, "max_tokens": cap})
        except BaseException:
            if token_hold is not None:
                await token_budget.release(token_hold)
            raise
        provenance=self._provenance()
        collector = active_usage_collector.get()
        if collector is not None:
            collector.attempts += 1
        response = None
        try:
            budget = self._turn_budget()
            if budget is not None:
                budget.admit()
            response = await handler(request)
            self._attach(response,provenance)
            if collector is not None:
                collector.observe(response.result)
            self._check_response(response, budget)
            return response
        except BaseException:
            if collector is not None:
                collector.complete = False
            raise
        finally:
            messages = response.result if response is not None else None
            settlements = []
            if token_hold is not None:
                settlements.append(token_budget.settle(token_hold, messages))
            if cost_hold is not None:
                settlements.append(cost_budget.settle(cost_hold, messages))
            if settlements:
                settling=asyncio.gather(*settlements)
                interrupted=False
                while not settling.done():
                    try: await asyncio.shield(settling)
                    except asyncio.CancelledError: interrupted=True
                settling.result()
                if interrupted: raise asyncio.CancelledError()


class SecretRedactionMiddleware(AgentMiddleware):
    """Remove the configured API key before a model response is checkpointed."""

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key

    def after_model(self, state, runtime):
        if not self.api_key:
            return None
        messages = state.get("messages", [])
        for message in reversed(messages):
            if getattr(message, "type", None) != "ai":
                continue
            updates = {
                "content": _redact_nested(message.content, self.api_key),
                "additional_kwargs": _redact_nested(message.additional_kwargs, self.api_key),
                "response_metadata": _redact_nested(message.response_metadata, self.api_key),
                "tool_calls": _redact_nested(message.tool_calls, self.api_key),
                "invalid_tool_calls": _redact_nested(message.invalid_tool_calls, self.api_key),
            }
            return {"messages": [message.model_copy(update=updates)]}
        return None


class MemoryContextMiddleware(AgentMiddleware):
    """Apply the same budget to current and historical memory tool results."""

    def __init__(self, context):
        super().__init__()
        self.context = context

    def wrap_model_call(self, request, handler):
        return handler(request.override(messages=self.context.filter_messages(request.messages)))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(messages=self.context.filter_messages(request.messages)))


class AgentRuntimeFactory:
    """Share one model and checkpointer while compiling least-privilege agents."""

    def __init__(
        self,
        settings: Settings,
        *,
        session_store: ProjectSessionStore | None = None,
    ):
        from nailong.core.preferences import PreferenceStore
        self.preferences = PreferenceStore(settings.project_root)
        environment_model=settings.environment_model or settings.model
        self.preferences.default_model = environment_model
        self.preferences.default_reasoning = settings.reasoning_effort
        preferences = self.preferences.effective(default_model=environment_model,cli=settings.cli_preferences)
        settings = replace(settings, model=preferences['model'],environment_model=environment_model,
                           reasoning_effort=preferences['reasoning_effort'])
        self.settings = settings
        self.output_style = preferences['output_style']
        self.session_store = session_store or ProjectSessionStore(
            settings.project_root,
            api_key=settings.api_key,
        )
        self.session_store.api_key = settings.api_key
        self.session_store.ensure_directories()
        self.model = ChatDeepSeek(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.api_base,
            timeout=30,
            max_retries=2,
            **self._reasoning_kwargs(settings.model, settings.reasoning_effort),
        )
        self._checkpointer_context = SqliteSaver.from_conn_string(
            str(self.session_store.database_path)
        )
        self.checkpointer = self._checkpointer_context.__enter__()
        self.checkpointer.setup()
        self._async_checkpointer_context = None
        self.async_checkpointer = None
        self._tool_sessions: OrderedDict[tuple[str, str], FileSession] = OrderedDict()
        self.plan_store = PlanStore(settings.project_root, api_key=settings.api_key)
        self.goal_store = GoalStore(
            self.session_store.root / "goals.json",
            api_key=settings.api_key, project_root=settings.project_root,
        )
        from nailong.core.task_state import TaskStore
        self.task_store = TaskStore(self.session_store, api_key=settings.api_key)
        self.goal_store.task_store = self.task_store
        self.task_runner = ReadOnlyTaskRunner(self._run_readonly_subagent,
            record=lambda thread_id, row: self.session_store.append_event(thread_id, 'subagent_budget', row))
        self.skill_registry = SkillRegistry(settings.project_root, api_key=settings.api_key)
        from nailong.core.permissions import PermissionEngine, Decision
        self._memory_permissions = PermissionEngine(settings.project_root)
        self._memory_task_scope = None
        def authorize_memory_source(relative):
            execution = getattr(self, 'tool_execution_context', None)
            engine = execution.permission_engine if execution is not None else self._memory_permissions
            mode = execution.permission_mode if execution is not None else 'default'
            return engine.decide_action('read_file', {'path': relative}, mode=mode).decision == Decision.ALLOW
        self._memory_source_authorizer = authorize_memory_source
        self._memory_snapshot = load_project_memory(settings.project_root, api_key=settings.api_key,
            source_authorizer=self._memory_source_authorizer, task_scope=self._memory_task_scope)
        self._project_memory = tuple(self._memory_snapshot)
        self._skill_catalog = self.skill_registry.catalog()
        from nailong.core.memory import MemoryStore
        from nailong.core.memory_selection import select_core_memory
        from nailong.core.history_archive import HistoryArchive
        from nailong.core.context import UsageCalibration
        self.memory_store=getattr(self._memory_snapshot,'store',None) or MemoryStore(settings.project_root,api_key=settings.api_key)
        core = select_core_memory(self.memory_store)
        self._memory_snapshot = MemorySnapshot(core, store=self.memory_store, reports=core.reports,
                                               budget_tokens=self._memory_snapshot.budget_tokens)
        self._project_memory=tuple(self._memory_snapshot)
        self.history_archive=HistoryArchive(self.session_store.root/'history-results',api_key=settings.api_key)
        self.context_reports={}
        self.context_calibration=UsageCalibration()
        from nailong.tools.execution import ToolExecutionContext
        from nailong.core.permissions import PermissionEngine
        from langgraph.types import interrupt
        def approve_tool(action, permission):
            resumed = interrupt({'action_requests': [action],
                'review_configs': [{'action_name': action['name'], 'allowed_decisions': ['approve', 'reject']}]})
            return (resumed.get('decisions') or [{}])[0] if isinstance(resumed, dict) else {'type': 'reject'}
        self.tool_execution_context = ToolExecutionContext(settings.project_root,
            permission_engine=self._memory_permissions, approval_handler=approve_tool,
            api_key=settings.api_key, artifact_directory=self.session_store.root/'tool-artifacts')
        from nailong.mcp.config import MCPConfigStore
        from nailong.mcp.client import MCPManager
        self.mcp_manager = MCPManager(MCPConfigStore(settings.project_root), api_key=settings.api_key)

    def reload_skills(self) -> dict:
        result = self.skill_registry.reload()
        self._skill_catalog = self.skill_registry.catalog()
        return result

    def reload_memory(self) -> tuple:
        self._memory_snapshot = load_project_memory(self.settings.project_root,
            user_file=self._memory_snapshot.store.user_file, api_key=self.settings.api_key,
            source_authorizer=self._memory_source_authorizer, task_scope=self._memory_task_scope)
        from nailong.core.memory_selection import select_core_memory
        self.memory_store=getattr(self._memory_snapshot,'store',self.memory_store)
        core = select_core_memory(self.memory_store)
        self._memory_snapshot = MemorySnapshot(core, store=self.memory_store, reports=core.reports,
                                               budget_tokens=self._memory_snapshot.budget_tokens)
        self._project_memory = tuple(self._memory_snapshot)
        return self._project_memory

    def set_model(self,model_id: str) -> None:
        """Change the model, keeping the project and existing checkpointers."""
        self.model = ChatDeepSeek(model=model_id, api_key=self.settings.api_key,
                                 base_url=self.settings.api_base,timeout=30,max_retries=2,
                                 **self._reasoning_kwargs(model_id,self.settings.reasoning_effort))
        self.settings = replace(self.settings,model=model_id)

    @staticmethod
    def _reasoning_kwargs(model_id, effort):
        from nailong.core.reasoning import model_reasoning_kwargs
        return model_reasoning_kwargs(model_id, effort)

    def set_reasoning(self, effort: str) -> None:
        options = self._reasoning_kwargs(self.settings.model, effort)
        model = ChatDeepSeek(model=self.settings.model, api_key=self.settings.api_key,
                            base_url=self.settings.api_base,timeout=30,max_retries=2,**options)
        self.model = model
        self.settings = replace(self.settings,reasoning_effort=effort)

    async def _run_readonly_subagent(self, prompt: str) -> ChildTaskResult:
        root = self.settings.project_root
        from nailong.tools.execution import current_tool_execution
        parent_execution = current_tool_execution.get() or {}
        parent_thread_id = parent_execution.get('thread_id')
        child_tools = build_tools(
            api_key=self.settings.api_key,
            profile="subagent",
            file_session=FileSession(root),
            execution_context=self.tool_execution_context,
            allowed_tools=READONLY_RESEARCH_TOOLS,
        )
        from nailong.core.context_runtime import ContextManagerMiddleware
        child_names={tool.name for tool in child_tools}
        child_parts=readonly_prompt_parts(root, child_names, self.tool_execution_context.permission_mode)
        child_prompt=''.join(child_parts.values())
        token_budget=active_token_budget.get()
        output_cap=token_budget.output_tokens if token_budget is not None else self.task_runner.child_output_tokens
        output_cap=min(output_cap,getattr(self.model,'max_tokens',None) or output_cap)
        # A failed provider attempt may have been billed without returning usage.
        # Children make one provider attempt per reservation and fail closed.
        child_model=(ChatDeepSeek(model=self.settings.model,api_key=self.settings.api_key,
            base_url=self.settings.api_base,timeout=30,max_retries=0,max_tokens=output_cap,
            **self._reasoning_kwargs(self.settings.model,self.settings.reasoning_effort))
            if isinstance(self.model,_DeepSeekBase) else self.model)
        finish_budget=ChildResearchBudgetMiddleware()
        child = create_agent(
            model=child_model,
            tools=child_tools,
            system_prompt=child_prompt,
            middleware=[ContextManagerMiddleware(self.settings,child_tools,child_prompt,'subagent',None,None,None,{},self.context_calibration,
                    parts=child_parts,requested_output=output_cap),
                finish_budget, ModelAccountingMiddleware(self.settings),
                SecretRedactionMiddleware(self.settings.api_key)],
        )
        with collect_model_usage(reuse=True) as collector:
            budget_failure=None
            try:
                result = await child.ainvoke(
                    {"messages": [HumanMessage(content=prompt)]},
                    {"recursion_limit": 16, **({'configurable': {'thread_id': parent_thread_id}} if parent_thread_id else {})},
                )
                messages = result.get("messages", []) if isinstance(result, dict) else []
                answer = str(getattr(messages[-1], "content", "")) if messages else "子代理没有返回文本。"
            except (CostBudgetExceeded, TokenBudgetExceeded) as error:
                answer = str(error)
                budget_failure=getattr(error,'diagnostics',{'reason':'cost_budget'})
            finally:
                await finish_budget.close()
            answer = answer.replace(self.settings.api_key, "[密钥已隐藏]") if self.settings.api_key else answer
            return ChildTaskResult(answer, dict(collector.totals), collector.complete,list(collector.records),budget_failure)

    def _tool_session(self, thread_id: str | None) -> FileSession:
        root = str(self.settings.project_root.resolve())
        if not thread_id:
            return FileSession(root)
        key = (thread_id, root)
        session = self._tool_sessions.pop(key, None)
        if session is None:
            session = FileSession(root)
        self._tool_sessions[key] = session
        while len(self._tool_sessions) > 128:
            self._tool_sessions.popitem(last=False)
        return session

    def _task_for_profile(self, thread_id, profile, target_path=None):
        if not thread_id or profile == 'subagent':
            return None
        if (profile == 'review' and target_path and
                self.tool_execution_context.permission_mode == 'bypassPermissions'):
            try:
                self.task_store._relative(target_path)
            except ValueError:
                return None
        return self.task_store.snapshot(thread_id)

    def _prompt_parts(self, profile, target_path, tool_names, thread_id, memory_snapshot=None):
        snapshot = memory_snapshot if memory_snapshot is not None else self._memory_snapshot
        task = self._task_for_profile(thread_id, profile, target_path)
        goal = self.goal_store.active() if profile == 'chat' and 'update_goal' in tool_names else None
        return build_prompt_parts(
            project_root=self.settings.project_root, profile=profile,
            target_path=target_path, tool_names=tool_names,
            output_style=getattr(self, 'output_style', 'normal'), thread_id=thread_id,
            has_task_context=bool(task and task['lifecycle'] != 'completed'),
            active_goal_thread_id=goal.thread_id if goal is not None else None,
            fixed_memory=snapshot.rendered_context, skill_catalog=self._skill_catalog,
            context_file=CONTEXT_FILE,
            permission_mode=(self.tool_execution_context.permission_mode
                if self.tool_execution_context.permission_mode == 'bypassPermissions'
                else 'plan' if profile == 'plan' else self.tool_execution_context.permission_mode),
        )

    def system_prompt(self, profile="chat", target_path=None, *, tool_names=None, thread_id=None, memory_snapshot=None):
        if tool_names is None:
            tools = build_tools(
                profile=profile, target_path=target_path,
                file_session=self._tool_session(thread_id), skill_registry=self.skill_registry,
                history_archive=self.history_archive, memory_store=self.memory_store,
                memory_context=MemoryReadContext(self._memory_snapshot),
            )
            tool_names = {tool.name for tool in tools}
        parts = self._prompt_parts(profile, target_path, set(tool_names), thread_id, memory_snapshot)
        return parts["base_system"] + parts["fixed_memory"] + parts["skill_catalog"]

    def context_parts(self, thread_id=None):
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from nailong.core.task_history import TaskContextHistory
        task_history=TaskContextHistory(self.history_archive,thread_id,self.settings.project_root) if thread_id else None
        tools=build_tools(api_key=self.settings.api_key,profile='chat',file_session=self._tool_session(thread_id),
                          plan_store=self.plan_store,task_runner=self.task_runner,goal_store=self.goal_store,
                          thread_id=thread_id,skill_registry=self.skill_registry,
                          history_archive=self.history_archive, memory_store=self.memory_store,
                          memory_context=MemoryReadContext(self._memory_snapshot), mcp_manager=self.mcp_manager,
                          task_history=task_history)
        parts = self._prompt_parts("chat", None, {tool.name for tool in tools}, thread_id)
        return {**parts, 'tool_definitions':json.dumps([convert_to_openai_tool(tool) for tool in tools],ensure_ascii=False,default=str),
                'memory_report': self._memory_snapshot.report()}

    def _build_runtime(
        self,
        profile: AgentProfile = "chat",
        target_path: str | None = None,
        thread_id: str | None = None,
        *,
        checkpointer=None,
        allowed_tools: set[str] | frozenset[str] | None = None,
        review_paths: frozenset[str] | None = None,
    ) -> CompiledStateGraph:
        if profile != 'subagent':
            task = self._task_for_profile(thread_id, profile, target_path)
            self._memory_task_scope = task['scope'] if task and task['lifecycle'] != 'completed' else None
            self.reload_memory()
        snapshot = self._memory_snapshot
        def current_task(identity):
            task = self._task_for_profile(identity, profile, target_path)
            return task if task and task['lifecycle'] != 'completed' else None
        def current_memory_scope():
            task = current_task(thread_id)
            return task['scope'] if task else None
        memory_context = MemoryReadContext(snapshot,
            task_scope_provider=current_memory_scope if thread_id else None)
        from nailong.core.task_history import TaskContextHistory
        task_history=TaskContextHistory(self.history_archive,thread_id,self.settings.project_root) if thread_id and profile!='subagent' else None
        tools = build_tools(
            api_key=self.settings.api_key,
            profile=profile,
            target_path=target_path,
            file_session=self._tool_session(thread_id),
            plan_store=self.plan_store,
            task_runner=self.task_runner,
            goal_store=self.goal_store,
            thread_id=thread_id,
            allowed_tools=allowed_tools,
            skill_registry=self.skill_registry,
            review_paths=review_paths,
            history_archive=self.history_archive,
            memory_store=self.memory_store,
            memory_context=memory_context,
            execution_context=self.tool_execution_context,
            mcp_manager=self.mcp_manager,
            task_history=task_history,
        )
        tool_names = {tool.name for tool in tools}
        from nailong.core.costs import CostEstimator
        self.active_price_snapshot = CostEstimator(self.settings.model,self.settings.project_root).snapshot()
        system_prompt = self.system_prompt(
            profile, target_path, tool_names=tool_names, thread_id=thread_id, memory_snapshot=snapshot,
        )
        from nailong.core.context_runtime import ContextManagerMiddleware
        context = ContextManagerMiddleware(self.settings,tools,system_prompt,profile,thread_id,
            self.session_store,self.history_archive,self.context_reports,self.context_calibration,
            parts=self._prompt_parts(profile,target_path,tool_names,thread_id,snapshot),goal_store=self.goal_store,
            request_filter=memory_context.filter_messages,requested_output=getattr(self.model,'max_tokens',None),
            memory_budget=snapshot.report(), task_snapshot_provider=current_task if thread_id else None,
            memory_request_context=memory_context,task_history=task_history)
        # Budget accounting sees the complete request, including the task tail.
        # Execution-time permissions own the sole approval gate.
        middleware = [TaskLifecycleMiddleware(self.task_store, current_task(thread_id) if thread_id else None),
            MemoryContextMiddleware(memory_context), context, ReviewInputMiddleware(),
            ModelAccountingMiddleware(self.settings, main_agent=True), SecretRedactionMiddleware(self.settings.api_key)]

        runtime = create_agent(
            model=self.model,
            tools=tools,
            system_prompt=system_prompt,
            middleware=middleware,
            checkpointer=checkpointer or self.checkpointer,
        )
        # Compiled graphs may outlive a temporary factory expression. Keep the
        # SQLite context alive with the graph that uses it.
        try:
            runtime._nailong_runtime_factory = self
            runtime._nailong_tool_specs = {tool.name: getattr(tool, 'spec', None) for tool in tools}
        except (AttributeError, TypeError):
            pass
        return runtime

    def __call__(
        self,
        profile: AgentProfile = "chat",
        target_path: str | None = None,
        thread_id: str | None = None,
        allowed_tools: set[str] | frozenset[str] | None = None,
        review_paths: frozenset[str] | None = None,
    ) -> CompiledStateGraph:
        """Build a runtime for synchronous compatibility callers."""
        return self._build_runtime(profile, target_path, thread_id, allowed_tools=allowed_tools,review_paths=review_paths)

    async def async_runtime(
        self,
        profile: AgentProfile = "chat",
        target_path: str | None = None,
        thread_id: str | None = None,
        allowed_tools: set[str] | frozenset[str] | None = None,
        review_paths: frozenset[str] | None = None,
    ) -> CompiledStateGraph:
        """Build a runtime backed by the async SQLite checkpointer."""
        if self.async_checkpointer is None:
            context = AsyncSqliteSaver.from_conn_string(
                str(self.session_store.database_path)
            )
            self.async_checkpointer = await context.__aenter__()
            self._async_checkpointer_context = context
            await self.async_checkpointer.setup()
        return self._build_runtime(
            profile,
            target_path,
            thread_id,
            checkpointer=self.async_checkpointer,
            allowed_tools=allowed_tools,
            review_paths=review_paths,
        )

    def close(self) -> None:
        context = getattr(self, "_checkpointer_context", None)
        if context is not None:
            self._checkpointer_context = None
            context.__exit__(None, None, None)

    async def aclose(self) -> None:
        try:
            await self.mcp_manager.aclose()
        finally:
            try:
                context = getattr(self, "_async_checkpointer_context", None)
                if context is not None:
                    self._async_checkpointer_context = None
                    self.async_checkpointer = None
                    await context.__aexit__(None, None, None)
            finally:
                self.close()


def create_agent_runtime(settings: Settings) -> CompiledStateGraph:
    """Keep the original factory helper for callers that need a chat runtime."""
    factory = AgentRuntimeFactory(settings)
    runtime = factory()
    # Keep the saver context alive for as long as its compiled graph is used.
    runtime._nailong_runtime_factory = factory
    return runtime
