"""LangChain adapters for the registered, project-scoped tool contracts."""

import json
import inspect
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langchain_core.runnables import RunnableConfig
from pydantic import Field

import local_tools
from nailong.tools.files import FileSession
from nailong.core.skills import SkillRegistry
from nailong.tools.execution import ToolExecutionContext
from nailong.tools.results import failure
from nailong.tools.schemas import parameter_model
from nailong.tools.registry import (
    CONTEXT_FILE,
    REVIEW_FILE_LIMIT,
    ToolProfile,
    build_tool_specs,
)


class ExecutionTool(StructuredTool):
    """Use the same gate for sync and async calls, including read-only tools."""

    execution: Any = Field(exclude=True)
    spec: Any = Field(exclude=True)
    file_session: Any = Field(exclude=True)
    profile: str
    secret: str = Field(default="", exclude=True)

    def _serialize(self, result):
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if self.secret:
            encoded = encoded.replace(self.secret, "[密钥已隐藏]")
        if not result.get("ok"):
            # BaseTool formats handled ToolException as ToolMessage(status="error").
            raise ToolException(encoded)
        return encoded

    def _arguments(self, args, kwargs):
        names = list(inspect.signature(self.spec.handler).parameters)
        return {**dict(zip(names, args)), **kwargs}

    def _run(self, *args, config: RunnableConfig, run_manager=None, **kwargs):
        result = self.execution.invoke_sync(self.spec, self._arguments(args, kwargs),
            profile=self.profile, file_session=self.file_session, config=config)
        return self._serialize(result)

    async def _arun(self, *args, config: RunnableConfig, run_manager=None, **kwargs):
        result = await self.execution.invoke_async(self.spec, self._arguments(args, kwargs),
            profile=self.profile, file_session=self.file_session, config=config)
        return self._serialize(result)

    async def ainvoke(self, input, config=None, **kwargs):
        # StructuredTool checks its optional coroutine field before calling _arun.
        # This adapter implements _arun itself, so enter BaseTool's async path directly.
        return await BaseTool.ainvoke(self, input, config, **kwargs)

    def _call_config(self, kwargs):
        config = dict(kwargs.get("config") or {})
        config["metadata"] = {**(config.get("metadata") or {}),
                              "tool_call_id": kwargs.get("tool_call_id") or (config.get("metadata") or {}).get("tool_call_id") or ""}
        kwargs["config"] = config
        return kwargs

    def run(self, tool_input, *args, **kwargs):
        kwargs = self._call_config(kwargs)
        result = super().run(tool_input, *args, **kwargs)
        failed = self._validation_failure(result)
        if failed is not None:
            bounded = self.execution.observe_validation_sync(self.spec, self._validation_arguments(tool_input), failed,
                file_session=self.file_session, config=kwargs["config"])
            return self._validation_output(result, bounded)
        return result

    async def arun(self, tool_input, *args, **kwargs):
        kwargs = self._call_config(kwargs)
        result = await super().arun(tool_input, *args, **kwargs)
        failed = self._validation_failure(result)
        if failed is not None:
            bounded = await self.execution.observe_validation_async(self.spec, self._validation_arguments(tool_input), failed,
                file_session=self.file_session, config=kwargs["config"])
            return self._validation_output(result, bounded)
        return result

    @staticmethod
    def _validation_arguments(tool_input):
        # A rejected native Python input need not be JSON serializable. Keep the
        # failure observable without letting diagnostic hashing raise a new error.
        try:
            return json.loads(json.dumps(tool_input, ensure_ascii=False, default=str))
        except (TypeError, ValueError, RecursionError):
            return {"unserializable_input_type": type(tool_input).__name__}

    @staticmethod
    def _validation_failure(result):
        try:
            payload = json.loads(getattr(result, "content", result))
        except (TypeError, ValueError):
            return None
        if isinstance(payload, dict) and payload.get("error_code") == "invalid_parameters" and payload.get("started") is False:
            return payload
        return None

    @staticmethod
    def _validation_output(result, bounded):
        encoded = json.dumps(bounded, ensure_ascii=False, default=str)
        if isinstance(result, str):
            return encoded
        result.content = encoded
        return result


def build_tools(
    api_key: str = "",
    *,
    profile: ToolProfile = "chat",
    target_path: str | None = None,
    file_session: FileSession | None = None,
    plan_store=None,
    task_runner=None,
    goal_store=None,
    thread_id: str | None = None,
    allowed_tools: set[str] | frozenset[str] | None = None,
    skill_registry: SkillRegistry | None = None,
    review_paths: frozenset[str] | None = None,
    history_archive=None,
    memory_store=None,
    memory_context=None,
    execution_context: ToolExecutionContext | None = None,
    mcp_manager=None,
    task_history=None,
) -> list[BaseTool]:
    """Build an ordered, profile-scoped set of registered tools."""
    if profile not in {"chat", "init", "review", "plan", "subagent"}:
        raise ValueError(f"未知工具配置：{profile}")
    session = file_session or FileSession()
    execution = execution_context or ToolExecutionContext(session.project_root, api_key=api_key)
    if execution.project_root != session.project_root:
        raise ValueError("工具与执行上下文必须属于同一项目。")
    with execution.file_access_scope():
        specs = build_tool_specs(
            profile=profile,
            session=session,
            target_path=target_path,
            plan_store=plan_store,
            task_runner=task_runner,
            goal_store=goal_store,
            thread_id=thread_id,
            allowed_tools=allowed_tools,
            skill_registry=skill_registry,
            review_paths=review_paths,
            history_archive=history_archive,
            memory_store=memory_store,
            memory_context=memory_context,
            result_store=execution.artifacts,
            task_history=task_history,
        )

    def validation_error(error):
        details = [{"field": ".".join(map(str, item["loc"]))[:100], "type": item["type"]}
                   for item in error.errors(include_input=False, include_url=False)[:8]]
        encoded = json.dumps(failure("invalid_parameters", "工具参数不符合已注册 schema。", started=False, details=details), ensure_ascii=False)
        return encoded.replace(api_key, "[密钥已隐藏]") if api_key else encoded

    tools = [ExecutionTool(
        name=spec.name, description=spec.description + (
            " 本次启动显式启用完全文件访问，path 可为项目内或项目外的绝对/相对路径，包括隐藏文件；运行模式和读后修改约束仍适用。"
            if execution.permission_mode == "bypassPermissions" else ""),
        args_schema=spec.args_schema or parameter_model(spec.name, spec.handler),
        spec=spec, execution=execution, file_session=session, profile=profile,
        secret=api_key, handle_tool_error=True, handle_validation_error=validation_error,
    ) for spec in specs]
    if mcp_manager is not None and profile == 'chat':
        if mcp_manager.store.project_root != session.project_root:
            raise ValueError('MCP 工具与当前项目必须一致。')
        from nailong.mcp.tools import build_mcp_tools
        tools.extend(tool for tool in build_mcp_tools(mcp_manager, execution, session, api_key)
                     if allowed_tools is None or tool.name in allowed_tools)
    return tools


__all__ = [
    "CONTEXT_FILE",
    "REVIEW_FILE_LIMIT",
    "ToolProfile",
    "build_tools",
    "local_tools",
    "ToolExecutionContext",
]
