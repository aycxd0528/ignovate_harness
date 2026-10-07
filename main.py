"""Interactive CLI for the local LangChain Agent."""

import argparse
import asyncio
from contextlib import ExitStack
import json
import os
import sys
from pathlib import Path
import uuid
from dataclasses import replace
from typing import Callable

# The offline entry must be usable before optional runtime dependencies import.
if __name__=='__main__' and (sys.argv[1:2]==['doctor'] or '--doctor' in sys.argv[1:]):
    from nailong.cli import doctor_cli
    raise SystemExit(doctor_cli(sys.argv[1:]))

from langgraph.errors import GraphRecursionError

from agent import create_agent_runtime
from agent_service import AgentService, MAX_TURN_GRAPH_STEPS, MAX_TURN_MODEL_CALLS, TurnRecursionLimitError
from config import ConfigurationError, Settings, load_settings, select_project_root
from nailong.core.permissions import PermissionEngine
from ui.controller import CommandController
import local_tools


def run_tui(*args, **kwargs):
    from tui import run_tui as run

    return run(*args, **kwargs)


async def run_inline(*args, **kwargs):
    from ui.app import run_inline as run

    return await run(*args, **kwargs)

def _redact(text: str, api_key: str) -> str:
    if api_key:
        return text.replace(api_key, "[密钥已隐藏]")
    return text


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


def _ask_decision(input_fn: Callable[[str], str]) -> str:
    while True:
        answer = input_fn("允许执行吗？输入 a 批准一次，s 本会话允许，r 拒绝（直接回车为拒绝）：").strip().lower()
        if answer in {"s", "session"}:
            return "approve_session"
        if answer in {"a", "approve", "y", "yes"}:
            return "approve"
        if answer in {"r", "reject", "n", "no", ""}:
            return "reject"


def _display_action(action: dict, index: int, total: int, output_fn: Callable[[str], None]) -> None:
    name = str(action.get("name", "unknown"))
    arguments = action.get("args", {})
    output_fn(f"\n待审批操作 {index}/{total}")
    output_fn(f"工具：{name}")
    if name == "run_command":
        output_fn(f"工作目录：{local_tools.selected_project_root()}")
    approval = action.get("_approval", {})
    if approval.get("reason"):
        output_fn(f"审批原因：{approval['reason']}")
    if approval.get("suggested_rule"):
        output_fn(f"本会话规则建议：{approval['suggested_rule']}")
    preview = approval.get("preview", {})
    if preview:
        if preview.get("path"):
            output_fn(f"文件：{preview['path']}")
        output_fn("变更预览：")
        output_fn(preview.get("diff") or preview.get("error", "无法生成 diff 预览。"))
    output_fn("完整参数：")
    output_fn(json.dumps(arguments, ensure_ascii=False, indent=2, default=str))


def _truncate_error_detail(detail: str, maximum: int = 500) -> str:
    marker = "[密钥已隐藏]"
    marker_start = detail.find(marker)
    if marker_start >= 0 and marker_start < maximum < marker_start + len(marker):
        keep = max(0, maximum - len(marker) - 1)
        return detail[:keep] + marker + "…"
    return detail[:maximum]


def run_turn(
    agent,
    message: str,
    config: dict,
    *,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] = print,
    api_key: str = "",
    runner: asyncio.Runner | None = None,
    service: AgentService | None = None,
    profile: str = "chat",
    target_path: str | None = None,
    allowed_tools=None,
    history_display: str | None = None,
    review_paths=None,
) -> str:
    """Run one user turn and pause for every write/command approval request."""
    input_fn = input_fn or input

    async def approval_handler(action: dict, index: int, total: int) -> str:
        _display_action(action, index, total, output_fn)
        return _ask_decision(input_fn)

    runtime_factory = getattr(agent, "_nailong_runtime_factory", None)
    service = service or AgentService(
        runtime_factory or (lambda **_kwargs: agent),
        api_key=api_key,
    )
    turn = service.run_turn(
        message,
        config,
        profile=profile, target_path=target_path, allowed_tools=allowed_tools,
        history_display=history_display,
        **({"review_paths":review_paths} if review_paths is not None else {}),
        approval_handler=approval_handler,
    )
    return runner.run(turn) if runner is not None else asyncio.run(turn)


async def _run_plain_workflow(request, service, factory, controller, thread_id, input_fn, output_fn):
    from ui.flows import drive_goal, run_plan_flow
    from nailong.core.plan import edit_plan_with_editor

    completed_rounds = 0

    def emit(event):
        nonlocal completed_rounds
        if event.kind == "goal_status":
            completed_rounds += 1
        if event.kind == "final":
            output_fn(event.data.get("text", ""))
            if event.data.get('delivery'):
                from nailong.core.delivery import render_delivery_report
                output_fn(render_delivery_report(event.data['delivery']))
        elif event.kind == "plan_draft":
            output_fn("待审批执行计划：\n" + event.data.get("text", ""))
        elif event.kind in {"notice", "goal_status"}:
            output_fn(event.data.get("text", ""))
        elif event.kind == "tool_end":
            output_fn(f"{event.data.get('name', 'tool')}：{event.data.get('summary', '')}")
        elif event.kind in {"error", "hook_blocked", "hook_feedback"}:
            output_fn(event.data.get("message") or event.data.get("reason") or event.data.get("text") or "请求失败。")

    async def approve(action, index, total):
        _display_action(action, index, total, output_fn)
        return _ask_decision(input_fn)

    async def confirm(_draft):
        answer = input_fn("[a] 批准执行  [e] 编辑  [d] 拒绝（默认 d）：").strip().lower()
        if answer in {"e", "edit"}:
            return "edit"
        return "approve" if answer in {"a", "approve", "y", "yes"} else "reject"

    if request.command == "/goal":
        goal, messages = controller.goal(request.argument, factory, thread_id)
        for message in messages:
            output_fn(message)
        if goal is not None:
            await drive_goal(
                service, factory, factory.goal_store, controller.cost_estimator, goal,
                thread_id=thread_id, emit=emit, approval=approve,
                history_display=request.message,
            )
        return completed_rounds
    await run_plan_flow(
        service, factory, thread_id=thread_id,
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": MAX_TURN_GRAPH_STEPS},
        prompt_message=request.prompt, allowed_tools=request.allowed_tools,
        history_display=request.message, emit=emit, confirm=confirm,
        edit=edit_plan_with_editor, approval=approve,
    )
    return 1


def _history(agent, config: dict, settings: Settings, output_fn: Callable[[str], None], service=None) -> None:
    if service is not None:
        history = service.get_history(config["configurable"]["thread_id"])
        visible = [(role, content) for role, content in history if role in {"user", "assistant"} and content.strip()]
        if not visible:
            output_fn("当前会话暂无历史信息。")
        for role, content in visible:
            output_fn(_redact(f"{role}: {content}", settings.api_key))
        return
    state = agent.get_state(config)
    messages = getattr(state, "values", {}).get("messages", [])
    if not messages:
        output_fn("当前会话暂无历史信息。")
        return

    for message in messages:
        message_type = getattr(message, "type", "message")
        role = {"human": "user", "ai": "assistant", "tool": "tool"}.get(
            message_type, message_type
        )
        name = getattr(message, "name", None)
        if name:
            role = f"{role}({name})"
        content = _message_text(getattr(message, "content", ""))
        if not content and getattr(message, "tool_calls", None):
            content = json.dumps(message.tool_calls, ensure_ascii=False, default=str)
        output_fn(f"{role}: {_redact(content, settings.api_key)}")


def _friendly_error(error: Exception, api_key: str) -> str:
    if isinstance(error, (TurnRecursionLimitError, GraphRecursionError)):
        message = str(error) if isinstance(error, TurnRecursionLimitError) else "本轮内部执行达到上限，已停止并保留会话。"
    elif isinstance(error, ConfigurationError):
        message = str(error)
    else:
        detail = _truncate_error_detail(
            _redact(str(error), api_key).strip().replace("\n", " ")
        )
        message = f"本轮请求失败（{type(error).__name__}）：{detail or '请检查网络和模型配置。'}"
        if "tool" in detail.lower() or "function" in detail.lower():
            message += " 请确认所选 DeepSeek 模型支持工具调用。"
    return _redact(message, api_key)


def run_cli(
    settings: Settings | None = None,
    *,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] = print,
    permission_mode: str = "default",
    continue_session: bool = False,
    resume_session: str | None = None,
) -> None:
    """Start the REPL. Input and output functions can be supplied by callers."""
    if input_fn is None and output_fn is print:
        from ui.plain import run_plain
        try:
            asyncio.run(run_plain(settings or load_settings(),permission_mode=permission_mode,
                continue_session=continue_session,resume_session=resume_session))
        except Exception as error:
            output_fn('启动失败：'+_friendly_error(error,getattr(settings,'api_key','')))
        return
    input_fn = input_fn or input
    try:
        settings = settings or load_settings()
        agent = create_agent_runtime(settings)
    except Exception as error:
        output_fn(f"启动失败：{_friendly_error(error, getattr(settings, 'api_key', ''))}")
        return

    runtime_factory = getattr(agent, "_nailong_runtime_factory", None)
    session_store = getattr(runtime_factory, "session_store", None)
    sessions = session_store.list_sessions() if session_store is not None else []
    thread_id = uuid.uuid4().hex
    try:
        if resume_session is not None:
            if session_store is None:
                raise ValueError("当前运行时不支持恢复持久会话。")
            thread_id = session_store.resolve_session(resume_session)
        elif continue_session and sessions:
            thread_id = sessions[0]["thread_id"]
    except ValueError as error:
        output_fn(str(error))
        if runtime_factory is not None:
            runtime_factory.close()
        return

    # The model's async HTTP client and async SQLite saver share this event loop.
    active_factory = runtime_factory
    try:
        with ExitStack() as project_roots, asyncio.Runner() as runner:
            project_roots.enter_context(local_tools.use_project_root(settings.project_root))
            active_factory = _run_cli_session(
                agent,
                settings,
                input_fn,
                output_fn,
                runner,
                permission_mode,
                thread_id,
                project_roots=project_roots,
            )
            if active_factory is not None:
                runner.run(active_factory.aclose())
    finally:
        if active_factory is not None:
            active_factory.close()


def _run_cli_session(
    agent,
    settings: Settings,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    runner: asyncio.Runner,
    permission_mode: str = "default",
    initial_thread_id: str | None = None,
    *, project_roots: ExitStack | None = None,
):
    thread_id = initial_thread_id or uuid.uuid4().hex
    completed_turns = 0
    runtime_factory = getattr(agent, "_nailong_runtime_factory", None)
    service = AgentService(
        runtime_factory or (lambda **_kwargs: agent),
        api_key=settings.api_key,
        permission_mode=permission_mode,
    )
    controller = CommandController(settings)
    from ui.actions import CommandActions
    actions = CommandActions(controller)
    output_fn("ignovate harness · plain")
    output_fn(f"工作项目：{settings.project_root}")
    from nailong.core.reasoning import PERMISSION_LABELS
    output_fn("输入 /help 查看命令；当前权限："+PERMISSION_LABELS.get(permission_mode,permission_mode)+"。")

    async def model_dialog(preferences, *, add_only=False):
        from ui.model_flow import prompt_model_choice
        async def prompt(label):
            return input_fn(label)
        return await prompt_model_choice(preferences,prompt,
            lambda text: output_fn(_redact(text,settings.api_key)),add_only=add_only)

    async def setting_dialog(title, current, options):
        from ui.settings_flow import prompt_setting_choice
        async def prompt(label): return input_fn(label)
        return await prompt_setting_choice(title, current, options, prompt, output_fn)

    while True:
        try:
            message = input_fn("你 > ").strip()
        except (KeyboardInterrupt, EOFError):
            output_fn("\n再见！")
            break

        if not message:
            continue
        try:
            request = controller.resolve(message)
            information = None if actions.handles(request) else controller.information(request, service, thread_id, completed_turns=completed_turns)
            if information is not None:
                output_fn(_redact(information, settings.api_key))
                continue
        except Exception as error:
            output_fn(_friendly_error(error, settings.api_key))
            continue
        if actions.handles(request):
            async def approve(action,index,total):
                _display_action(action,index,total,output_fn)
                return _ask_decision(input_fn)
            try:
                from nailong.core.plan import edit_plan_with_editor
                result=runner.run(actions.execute(request,service,thread_id,approval=approve,
                    edit=edit_plan_with_editor,model_dialog=model_dialog,setting_dialog=setting_dialog))
                if result.refresh:
                    permission_mode=service.permission_mode
                    settings=getattr(runtime_factory, 'settings', None) or settings
                if result.text: output_fn(_redact(result.text,settings.api_key))
                for model_request in result.model_requests:
                    answer=run_turn(agent,model_request.prompt,{'configurable':{'thread_id':thread_id},'recursion_limit':40},
                        profile=model_request.profile,target_path=model_request.target_path,review_paths=model_request.review_paths,
                        input_fn=input_fn,output_fn=output_fn,api_key=settings.api_key,runner=runner,service=service,
                        history_display=request.message)
                    output_fn(_redact(answer,settings.api_key));completed_turns+=1
                    report=runner.run(service.delivery_report(thread_id)) if service.last_turn_task_id else None
                    if report:
                        from nailong.core.delivery import render_delivery_report
                        output_fn(render_delivery_report(report))
            except Exception as error: output_fn(_friendly_error(error,settings.api_key))
            continue
        message = request.command + (" " + request.argument if request.argument else "") if request.kind == "local" else request.message
        if message == "/exit":
            output_fn("再见！")
            break
        if message == "/history":
            try:
                _history(
                    agent,
                    {"configurable": {"thread_id": thread_id}, "recursion_limit": 40},
                    settings,
                    output_fn,
                    service=service,
                )
            except Exception as error:
                output_fn(f"读取历史失败：{_friendly_error(error, settings.api_key)}")
            continue
        if message == "/sessions":
            store = service.session_store
            if store is None:
                output_fn("当前运行时不支持持久会话。")
                continue
            sessions = store.list_sessions()
            if not sessions:
                output_fn("当前项目还没有已保存的会话。")
                continue
            for index, session in enumerate(sessions, start=1):
                output_fn(
                    f"{index}. {session['thread_id']}  {session['updated_at']}  {session['summary']}"
                )
            continue
        if message == "/rewind":
            output_fn(
                "回退会舍弃当前用户轮次及之后的对话状态；"
                "已执行的文件修改和命令不会撤销。"
            )
            try:
                confirmation = input_fn("确认回退对话状态？[y/N] ")
            except (EOFError, KeyboardInterrupt):
                output_fn("已取消回退。")
                continue
            if str(confirmation).strip().lower() not in {"y", "yes"}:
                output_fn("已取消回退。")
                continue
            try:
                removed = runner.run(service.rewind(thread_id))
                if removed is None:
                    output_fn("当前会话没有可回退的用户轮次。")
                    continue
                output_fn(
                    f"对话状态已回退到上一轮开始前，移除了 {removed} 条消息。"
                    "已执行的文件和命令操作不会撤销。"
                )
            except Exception as error:
                output_fn(f"回退失败：{_friendly_error(error, settings.api_key)}")
            continue
        if request.command == "/resume":
            selector = request.argument
            store = service.session_store
            if not selector or store is None:
                output_fn("用法：/resume <会话 ID 或 /sessions 中的序号>")
                continue
            try:
                thread_id = store.resolve_session(selector,**({"query":actions.session_query} if actions.session_query else {}))
                output_fn(f"已恢复会话 {thread_id}。")
            except ValueError as error:
                output_fn(str(error))
            continue
        if message == "/clear":
            thread_id = uuid.uuid4().hex
            output_fn("已开始新的会话。")
            continue
        if message == "/project" or message.startswith("/project "):
            raw_path = message[len("/project"):].strip()
            if not raw_path:
                output_fn("用法：/project <项目目录>")
                continue
            try:
                new_root = select_project_root(raw_path)
                new_settings = replace(settings, project_root=new_root)
                new_agent = create_agent_runtime(new_settings)
            except Exception as error:
                output_fn(f"切换项目失败：{_friendly_error(error, settings.api_key)}")
                continue
            old_factory = runtime_factory
            if old_factory is not None:
                runner.run(old_factory.aclose())
                old_factory.close()
            settings = new_settings
            agent = new_agent
            runtime_factory = getattr(agent, "_nailong_runtime_factory", None)
            service = AgentService(
                runtime_factory or (lambda **_kwargs: agent),
                api_key=settings.api_key,
                permission_engine=PermissionEngine(new_root),
                permission_mode=permission_mode,
            )
            controller = CommandController(settings)
            actions = CommandActions(controller)
            if project_roots is not None:
                project_roots.enter_context(local_tools.use_project_root(new_root))
            thread_id = uuid.uuid4().hex
            output_fn(f"已切换到项目：{new_root}。已开始新的会话。")
            continue
        if request.command == "/compact":
            try:
                result = runner.run(service.compact_context(thread_id))
                output_fn(
                    f"已压缩较早工具结果：约 {result['before_tokens']} → {result['after_tokens']} tokens。"
                    if result.get("compacted") else f"未压缩上下文：{result.get('reason', '无可压缩内容')}。"
                )
            except Exception as error:
                output_fn(_friendly_error(error, settings.api_key))
            continue
        if request.command == "/goal" or request.profile == "plan":
            try:
                completed = runner.run(_run_plain_workflow(
                    request, service, runtime_factory, controller, thread_id, input_fn,
                    lambda text: output_fn(_redact(str(text), settings.api_key)),
                ))
                completed_turns += int(completed)
            except (KeyboardInterrupt, EOFError):
                output_fn("\n审批被中断，程序已退出。")
                break
            except Exception as error:
                output_fn(_friendly_error(error, settings.api_key))
            continue

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 40,
        }
        try:
            answer = run_turn(
                agent,
                request.prompt,
                config,
                profile=request.profile, target_path=request.target_path,
                allowed_tools=request.allowed_tools, history_display=request.message,
                input_fn=input_fn,
                output_fn=output_fn,
                api_key=settings.api_key,
                runner=runner,
                service=service,
            )
        except KeyboardInterrupt:
            output_fn("\n审批被中断，程序已退出。")
            break
        except (TurnRecursionLimitError, GraphRecursionError) as error:
            output_fn(_friendly_error(error, settings.api_key))
            output_fn("本轮已停止；会话和任务保留，可调整要求后继续。")
            continue
        except Exception as error:
            output_fn(_friendly_error(error, settings.api_key))
            continue

        output_fn(_redact(answer, settings.api_key))
        report=runner.run(service.delivery_report(thread_id)) if service.last_turn_task_id else None
        if report:
            from nailong.core.delivery import render_delivery_report
            output_fn(render_delivery_report(report))
        completed_turns += 1

    return runtime_factory


def _supports_fullscreen_tui() -> bool:
    from nailong.core.diagnostics import terminal_capabilities
    return terminal_capabilities()['fullscreen']


def _supports_inline_ui() -> bool:
    from nailong.core.diagnostics import terminal_capabilities
    return terminal_capabilities()['inline']


def _max_turns(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("必须是 1 到 40 之间的整数。") from error
    if not 1 <= value <= MAX_TURN_MODEL_CALLS:
        raise argparse.ArgumentTypeError(
            f"必须是 1 到 {MAX_TURN_MODEL_CALLS} 之间的整数。"
        )
    return value


def _goal_round_limit(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("必须是 1 到 100 之间的整数。") from error
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError("必须是 1 到 100 之间的整数。")
    return value


def _goal_cost_limit(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError("必须大于 0 且不超过 100 美元。") from error
    if not 0 < value <= 100:
        raise argparse.ArgumentTypeError("必须大于 0 且不超过 100 美元。")
    return value


def _headless_startup_error(output_format: str, error: Exception, api_key: str = "") -> int:
    message = _friendly_error(error, api_key)
    if output_format == "json":
        print(json.dumps({
            "session_id": None,
            "status": "configuration_error" if isinstance(error, ConfigurationError) else "error",
            "result": "",
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0, "total_tokens": 0},
            "tools": [],
            "approvals": [],
            "goal": None,
            "error": message,
        }, ensure_ascii=False))
    elif output_format == "stream-json":
        print(json.dumps({"type": "headless_result", "data": {
            "session_id": None,
            "status": "configuration_error" if isinstance(error, ConfigurationError) else "error",
            "result": "",
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0, "total_tokens": 0},
            "tools": [],
            "approvals": [],
            "goal": None,
            "error": message,
        }}, ensure_ascii=False))
    else:
        print(f"启动失败：{message}", file=sys.stderr)
    return 3 if isinstance(error, ConfigurationError) else 1


def _interactive_terminal() -> bool:
    return bool(callable(getattr(sys.stdin, 'read', None)) and callable(getattr(sys.stdout, 'write', None))
                and not os.getenv('PYCHARM_HOSTED') and getattr(sys.stdin, 'isatty', lambda: False)()
                and getattr(sys.stdout, 'isatty', lambda: False)())


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if "--doctor" in raw_argv or (raw_argv and raw_argv[0]=='doctor'):
        from nailong.cli import doctor_cli
        return doctor_cli(raw_argv)
    parser = argparse.ArgumentParser(description="ignovate harness 本地编码助手")
    parser.add_argument('--setup', action='store_true', help='重新打开欢迎与模型配置指引')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--plain",
        action="store_true",
        help="使用旧版纯文本 REPL",
    )
    mode.add_argument(
        "--tui",
        action="store_true",
        help="使用 Textual 全屏仪表盘",
    )
    mode.add_argument(
        "--ui",
        choices=("auto", "inline", "textual", "plain"),
        help="选择界面：auto 默认自动选择、textual 全屏、inline 终端滚动、plain 纯文本",
    )
    parser.add_argument(
        "--project",
        metavar="目录",
        help="选择本次会话要读取和操作的项目目录",
    )
    permissions = parser.add_mutually_exclusive_group()
    permissions.add_argument(
        "--permission-mode",
        choices=("default", "acceptEdits", "plan"),
        default=None,
        help="选择权限模式：default 请求批准、acceptEdits 帮我批准（修改自动批准，命令仍询问）、plan 只读；完全访问使用 --dangerously-skip-permissions",
    )
    permissions.add_argument(
        '--dangerously-skip-permissions', action='store_true',
        help='仅本次启动允许任意文件路径并跳过权限规则与审批；保留密钥保护和只读模式限制，操作受系统账户权限约束',
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_prompt",
        metavar="PROMPT",
        help="无界面处理单条提示并退出",
    )
    parser.add_argument(
        "--output-format",
        choices=("text", "json", "stream-json"),
        default="text",
        help="无界面输出格式：文本、单个 JSON 对象或逐行 JSON 事件",
    )
    parser.add_argument(
        "--max-turns",
        type=_max_turns,
        default=MAX_TURN_MODEL_CALLS,
        metavar="1-40",
        help="无界面单轮模型调用上限（默认 40，最后一次仅汇总已有证据）",
    )
    parser.add_argument(
        "--goal",
        action="store_true",
        help="无界面运行有护栏的持久目标；待审批操作会暂停目标",
    )
    parser.add_argument(
        "--goal-max-rounds",
        type=_goal_round_limit,
        default=20,
        metavar="1-100",
        help="目标最多迭代轮数（默认 20）",
    )
    parser.add_argument(
        "--goal-max-cost-usd",
        type=_goal_cost_limit,
        default=1.0,
        metavar="USD",
        help="目标费用上限（默认 1 美元）",
    )
    parser.add_argument('--model',help='本次使用的已配置模型名称')
    parser.add_argument('--reasoning-effort', choices=('default','none','low','high','max'),
                        help='本次推理强度：模型默认、关闭、低、高、最高')
    parser.add_argument('--theme',choices=('dark','light','ansi'),help='本次终端主题')
    parser.add_argument('--output-style',choices=('concise','normal','detailed'),help='本次回答详略')
    sessions = parser.add_mutually_exclusive_group()
    sessions.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        help="恢复当前项目最近一次会话",
    )
    sessions.add_argument(
        "--resume",
        dest="resume_session",
        metavar="ID|序号",
        help="恢复指定会话 ID 或 /sessions 中的序号",
    )
    arguments = parser.parse_args(argv)
    explicit_permission = arguments.permission_mode is not None or arguments.dangerously_skip_permissions
    arguments.permission_mode = ('bypassPermissions' if arguments.dangerously_skip_permissions
        else arguments.permission_mode or 'default')

    if arguments.print_prompt is not None:
        if arguments.setup:
            parser.error('--setup 仅适用于交互启动，不能与 -p/--print 同时使用。')
        if arguments.plain or arguments.tui or arguments.ui:
            parser.error("-p/--print 不能与界面选择参数同时使用。")
        if arguments.continue_session or arguments.resume_session:
            parser.error("-p/--print 每次创建新会话；请在交互界面中恢复已有会话。")
        if arguments.permission_mode not in {"default", "bypassPermissions"}:
            parser.error("-p/--print 使用默认审批策略；仅 --dangerously-skip-permissions 可显式跳过审批。")
    elif (
        arguments.output_format != "text"
        or arguments.max_turns != MAX_TURN_MODEL_CALLS
        or arguments.goal
        or arguments.goal_max_rounds != 20
        or arguments.goal_max_cost_usd != 1.0
    ):
        parser.error("无头输出与目标参数只能与 -p/--print 一起使用。")
    if arguments.print_prompt is not None and not arguments.goal and (
        arguments.goal_max_rounds != 20 or arguments.goal_max_cost_usd != 1.0
    ):
        parser.error("--goal-max-rounds 和 --goal-max-cost-usd 需要同时指定 --goal。")

    settings = None
    try:
        selected_ui = arguments.ui or ('plain' if arguments.plain else 'textual' if arguments.tui else 'auto')
        if selected_ui == 'auto':
            selected_ui = ('textual' if _supports_fullscreen_tui()
                           else 'inline' if _supports_inline_ui() else 'plain')
        terminal = _interactive_terminal()
        if arguments.setup and not terminal:
            raise ConfigurationError('模型配置指引需要交互终端；请在终端运行 ignovate --setup。')
        from nailong.core.bootstrap import BootstrapStore
        show_setup = arguments.setup or (arguments.print_prompt is None and terminal and not BootstrapStore().completed())
        try:
            settings = load_settings()
        except ConfigurationError:
            if not show_setup:
                raise
        root = select_project_root(arguments.project) if arguments.project else (
            settings.project_root if settings else Path(__file__).resolve().parent)
        setup_overrides = {}
        if show_setup:
            from ui.setup import run_setup
            from ui.theme import load_theme
            from nailong.core.preferences import PreferenceStore
            setup_theme = PreferenceStore(root).effective(cli={'theme':arguments.theme} if arguments.theme else {})['theme']
            try:
                result = run_setup(settings=settings, project_root=root, permission_mode=arguments.permission_mode,
                                   selected_ui=selected_ui, locked_permission=explicit_permission,
                                   locked_reasoning=arguments.reasoning_effort, theme=load_theme(name=setup_theme))
            except (KeyboardInterrupt, EOFError):
                result = None
            if result is None:
                print('已取消配置。')
                return 0
            settings = result.settings
            arguments.permission_mode = result.permission_mode
            setup_overrides = {'model':'default', 'reasoning_effort':settings.reasoning_effort}
        settings = replace(settings, project_root=root)
        overrides={**setup_overrides, **{key:value for key,value in {'model':arguments.model,'theme':arguments.theme,
            'output_style':arguments.output_style,'reasoning_effort':arguments.reasoning_effort}.items() if value is not None}
        }
        if overrides:
            settings=replace(settings,cli_preferences=overrides)
        with local_tools.use_project_root(settings.project_root):
            if arguments.print_prompt is not None:
                from headless import run_print

                print_options = {
                    "output_format": arguments.output_format,
                    "max_turns": arguments.max_turns,
                }
                if arguments.permission_mode != 'default':
                    print_options['permission_mode'] = arguments.permission_mode
                if arguments.goal:
                    print_options.update({
                        "goal_mode": True,
                        "goal_max_rounds": arguments.goal_max_rounds,
                        "goal_max_cost_usd": arguments.goal_max_cost_usd,
                    })
                return asyncio.run(run_print(settings, arguments.print_prompt, **print_options))
            session_options = {}
            if arguments.permission_mode != "default":
                session_options["permission_mode"] = arguments.permission_mode
            if arguments.continue_session:
                session_options["continue_session"] = True
            if arguments.resume_session:
                session_options["resume_session"] = arguments.resume_session

            if selected_ui == "plain":
                cli_options = dict(session_options)
                run_cli(settings, **cli_options)
            elif selected_ui == "textual":
                tui_options = dict(session_options)
                run_tui(settings, **tui_options)
            else:
                asyncio.run(run_inline(settings, **session_options))
    except Exception as error:
        if arguments.print_prompt is not None:
            return _headless_startup_error(
                arguments.output_format,
                error,
                getattr(settings, "api_key", ""),
            )
        print(
            f"启动失败：{_friendly_error(error, getattr(settings, 'api_key', ''))}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
