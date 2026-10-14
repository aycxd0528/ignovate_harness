"""Inline scrollback application consuming the shared AgentService event stream."""

from __future__ import annotations

import json
import asyncio
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from agent import AgentRuntimeFactory
from agent_service import AgentService, TurnRecursionLimitError
from config import Settings, select_project_root
from langgraph.errors import GraphRecursionError
from rich.table import Table
import local_tools
from nailong.core.plan import edit_plan_with_editor
from nailong.core.permissions import PermissionEngine
from ui.approval import prompt_approval
from ui.banner import banner_renderable, configured_banner_enabled
from ui.console import Console
from ui.commands import COMMANDS as COMMAND_SPECS
from ui.controller import CommandController
from ui.actions import CommandActions, IMMEDIATE_COMMANDS, IDENTITY_COMMANDS, pause_session_goal
from nailong.core.runner import SessionRunner
from ui.input_broker import InputBroker, ApprovalInterrupted, InputInterrupted, InteractionCancelled
from ui.theme import load_theme
from ui.flows import drive_goal as drive_goal_flow, run_plan_flow, parse_goal_request as _parse_goal_request
from ui.prompt import build_session
from ui.presentation import SessionMetrics, configured_context_window, context_percent, render_run_header, render_role_header
from ui.render import _build_toolbar, render_user_message, render_diff_text
from ui.token_weather import render_token_weather


COMMANDS = {spec.name: spec.description for spec in COMMAND_SPECS}


def _is_tool_call_payload(content: str) -> bool:
    """Recognize serialized AI tool calls that have no user-facing prose."""
    if not content.lstrip().startswith("["):
        return False
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return False
    return bool(payload) and isinstance(payload, list) and all(
        isinstance(item, dict)
        and item.get("type") == "tool_call"
        and isinstance(item.get("name"), str)
        for item in payload
    )


def _format_history(service: AgentService, thread_id: str, console: Console) -> None:
    rows = service.get_history(thread_id)
    if not rows:
        console.print("当前会话暂无历史信息。")
        return
    hidden_tools = 0
    for role, content in rows:
        if role == "tool" or role.startswith("tool(") or (
            role == "assistant" and _is_tool_call_payload(content)
        ):
            hidden_tools += 1
            continue
        if not content.strip():
            continue
        if role == "assistant":
            console.print(render_role_header("assistant", theme=console.theme))
            console.print_markdown(content)
        elif role == "user":
            console.print(render_user_message(content, theme=console.theme))
        else:
            console.print(f"{role}: {content}")
    if hidden_tools:
        console.print(f"（已省略 {hidden_tools} 条工具调用与结果记录）")


def _session_stats(store, thread_id: str) -> tuple[int, int]:
    """Aggregate turn count and token usage from a session's event log."""
    turns = 0
    tokens = 0
    for record in store.read_events(thread_id):
        kind = record.get("kind")
        if kind == "turn_start":
            data = record.get("data") or {}
            if not isinstance(data, dict) or data.get("visible") is not False:
                turns += 1
        elif kind == "usage":
            data = record.get("data") or {}
            tokens += int(data.get("input_tokens", 0) or 0)
            tokens += int(data.get("output_tokens", 0) or 0)
    return turns, tokens


def _print_sessions(store, console: Console) -> None:
    sessions = store.list_sessions()
    if not sessions:
        console.print("当前项目还没有已保存的会话。")
        return
    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("#", justify="right")
    table.add_column("会话 ID")
    table.add_column("更新时间")
    table.add_column("轮数", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("摘要")
    for index, item in enumerate(sessions, start=1):
        turns, tokens = _session_stats(store, item["thread_id"])
        summary = str(item.get("summary") or "").strip().replace("\n", " ")
        if len(summary) > 32:
            summary = summary[:32] + "…"
        table.add_row(
            str(index),
            item["thread_id"][:8],
            item.get("updated_at") or "-",
            str(turns),
            str(tokens),
            summary or "-",
        )
    console.print(table)


def _safe_error(error: Exception, api_key: str) -> str:
    message = str(error)
    return message.replace(api_key, "[密钥已隐藏]") if api_key else message


async def run_inline(
    settings: Settings,
    *,
    permission_mode: str = "default",
    continue_session: bool = False,
    resume_session: str | None = None,
    prompt_session=None,
    console: Console | None = None,
    plain: bool = False,
) -> None:
    """Run the default inline UI; prompt input and Rich output never overlap."""
    console = console or Console()
    factories: list[AgentRuntimeFactory] = []
    factory = AgentRuntimeFactory(settings)
    settings = getattr(factory,"settings",None) or settings
    factories.append(factory)
    service = AgentService(
        factory,
        api_key=settings.api_key,
        permission_engine=PermissionEngine(settings.project_root),
        permission_mode=permission_mode,
    )
    store = factory.session_store
    sessions = store.list_sessions()
    thread_id = uuid.uuid4().hex
    restored = False
    current_settings = settings
    controller = CommandController(current_settings)
    completion_commands = {spec.name: spec.description for spec in controller.specs()}
    cost_estimator = controller.cost_estimator
    try:
        if resume_session is not None:
            thread_id = store.resolve_session(resume_session)
            restored = True
        elif continue_session and sessions:
            thread_id = sessions[0]["thread_id"]
            restored = True
        session = prompt_session or build_session(
            settings.project_root,
            completion_commands,
            api_key=settings.api_key,
        )
    except Exception:
        await factory.aclose()
        factory.close()
        raise
    if not restored and resume_session is None and not continue_session and sessions:
        latest = sessions[0]
        summary = str(latest.get("summary") or "").strip().replace("\n", " ")
        if len(summary) > 40:
            summary = summary[:40] + "…"
        console.print(f"上次会话：{latest.get('updated_at') or '-'} · {summary or '无摘要'}")
        answer = (await session.prompt_async("继续上次会话？[y/N] ", default="n")).strip().lower()
        if answer in {"y", "yes"}:
            thread_id = latest["thread_id"]
            restored = True
    if getattr(service,"session_store",None) is None: service.session_store=store
    service.ui_name='plain' if plain else 'inline'
    session = InputBroker(session)
    actions = CommandActions(controller)
    controller.skill_registry=getattr(factory,'skill_registry',None)
    completion_commands.update({spec.name:spec.description for spec in controller.specs()})
    session_runner = SessionRunner(current_settings.project_root,thread_id)
    from prompt_toolkit.patch_stdout import patch_stdout
    patcher = patch_stdout(raw=True) if prompt_session is None else None
    if patcher: patcher.__enter__()
    interrupt_cleanup = None
    if plain:
        from ui.plain import install_plain_interrupt
        def interrupt():
            if session_runner.busy:
                asyncio.create_task(session_runner.stop())
                pause_session_goal(service,thread_id)
                console.print('当前任务已停止，会话保留。用 /queue resume 继续或 /exit 退出。')
            else:
                session.session.buffer=b''
                console.print('输入已清空。用 /exit 退出。')
    if hasattr(factory,'settings'): current_settings=factory.settings
    if getattr(factory,'preferences',None) is not None: actions.preferences=factory.preferences
    initial_preferences=actions.preferences.effective()
    console.apply_theme(load_theme(name=initial_preferences["theme"]))
    console.output_style=initial_preferences["output_style"]
    completed_turns = 0
    retained_input=None
    usage_total = {"input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0}
    latest_context_input_tokens: int | None = None

    def toolbar():
        from nailong.core.diagnostics import usage_snapshot
        snapshot=usage_snapshot(store,thread_id)
        records=store.read_events(thread_id) if store else []
        current_metrics=SessionMetrics.from_events(records)
        last_input=current_metrics.last_input_tokens if records else latest_context_input_tokens
        name=next((row.get('name','') for row in getattr(store,'list_sessions',lambda:[])() if row['thread_id']==thread_id),'')
        project = Path(current_settings.project_root).name or str(current_settings.project_root)
        total = snapshot['total_tokens'] if store else usage_total['input_tokens']+usage_total['output_tokens']
        goal_store = getattr(factory, "goal_store", None)
        goal = (goal_store.active() or goal_store.latest()) if goal_store is not None else None
        goal_status = (
            f"目标 {goal.state} {goal.round}/{goal.max_rounds} 轮"
            if goal is not None
            else ""
        )
        cost_status = ""
        if store:
            cost_status=f"约 ${snapshot['cost_usd']:.6f}" if snapshot['cost_complete'] else '费用不完整/未知'
            if not snapshot['usage_complete']: cost_status+=' · 用量不完整'
        elif cost_estimator.available:
            try:
                estimated = cost_estimator.estimate(usage_total)
                cost_status = f"约 ${estimated:.6f}"
            except ValueError:
                pass
        status = _build_toolbar(
            model=current_settings.model,
            permission_mode=permission_mode,
            project=project,
            tokens=total,
            cost_status=f" · {cost_status}" if cost_status else "",
            goal_status=goal_status,
            session_id=thread_id,
            session_name=_safe_error(name,current_settings.api_key),
            width=console.console.width,
            cache_hit_tokens=snapshot['cache_hit_tokens'] if store else usage_total['cache_hit_tokens'],
            context_usage_percent=(
                context_percent(
                    {"input_tokens": last_input},
                    configured_context_window(current_settings.model, current_settings.project_root),
                )
                if last_input is not None else None
            ),
        )
        weather = render_token_weather(
            current_metrics,
            context_window=configured_context_window(current_settings.model, current_settings.project_root),
            width=console.console.width,
            theme=console.theme,
        )
        return status + '\n' + weather.plain

    async def ask_approval(action, index, total):
        return await prompt_approval(
            session,
            console,
            action,
            api_key=current_settings.api_key,
        )

    async def model_dialog(preferences, *, add_only=False):
        from ui.model_flow import prompt_model_choice
        async with session.interaction() as prompt:
            return await prompt_model_choice(preferences, prompt,
                lambda text: console.print(_safe_error(text,current_settings.api_key)),add_only=add_only)

    async def setting_dialog(title, current, options):
        from ui.settings_flow import prompt_setting_choice
        async with session.interaction() as prompt:
            return await prompt_setting_choice(title, current, options, prompt,
                lambda text: console.print(_safe_error(text, current_settings.api_key)))

    async def drive_goal(goal, display_message: str):
        nonlocal completed_turns, latest_context_input_tokens
        def emit(event):
            nonlocal completed_turns, latest_context_input_tokens
            if event.kind == "usage":
                for key in usage_total:
                    usage_total[key] += int(event.data.get(key, 0) or 0)
                if event.data.get("scope") != "subagent":
                    latest_context_input_tokens = int(event.data.get("input_tokens", 0) or 0)
            if event.kind == "goal_status":
                completed_turns += 1
                console.print(_safe_error(event.data["text"], current_settings.api_key))
            elif event.kind == "notice":
                console.print(_safe_error(event.data["text"], current_settings.api_key))
            else:
                console.print_event(event)

        console.print(render_run_header(
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            goal.objective,
            theme=console.theme,
            api_key=current_settings.api_key,
        ))
        with console.working(
            context_window=configured_context_window(current_settings.model, current_settings.project_root),
            api_key=current_settings.api_key,
        ):
            return await drive_goal_flow(
                service, factory, factory.goal_store, cost_estimator, goal,
                thread_id=thread_id, emit=emit,
                status=console.set_status, approval=ask_approval,
                history_display=display_message,
            )

    def report_queued_error(future):
        if not future.cancelled() and future.exception():
            console.error(_safe_error(future.exception(),current_settings.api_key))

    async def execute_request(request):
        try:
            return await perform_request(request)
        except asyncio.CancelledError:
            pause_session_goal(service,thread_id)
            raise

    async def perform_request(request):
        nonlocal completed_turns,current_settings,latest_context_input_tokens,cost_estimator,permission_mode
        message=request.message
        if actions.handles(request):
            result=await actions.execute(request,service,thread_id,approval=ask_approval,
                edit=session.edit,runner=session_runner,model_dialog=model_dialog,setting_dialog=setting_dialog,
                emit=lambda event:console.print(f"{event.data.get('name','验证')} · {'通过' if event.data.get('ok') else '未通过'}"))
            if result.text:
                console.print(render_diff_text(result.text,theme=console.theme,api_key=current_settings.api_key) if request.command=="/diff" else _safe_error(result.text,current_settings.api_key))
            if result.refresh:
                permission_mode=service.permission_mode
                current_settings=getattr(factory,'settings',None) or current_settings
                prefs=actions.preferences.effective()
                console.apply_theme(load_theme(name=prefs['theme']))
                console.output_style=prefs['output_style']
                cost_estimator=controller.cost_estimator
                completion_commands.clear();completion_commands.update({spec.name:spec.description for spec in controller.specs()})
            for model_request in result.model_requests: await execute_request(model_request)
            return
        if request.command=='/compact':
            result=await service.compact_context(thread_id)
            console.print(str(result))
            return
        if request.command=='/goal':
            goal,messages=controller.goal(request.argument,factory,thread_id)
            for text in messages: console.print(text)
            if goal is not None: await drive_goal(goal,request.message)
            return
        profile = request.profile
        target_path = request.target_path
        allowed_tools = request.allowed_tools
        prompt_message = request.prompt

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 40,
        }
        console.print(
            render_user_message(message, api_key=current_settings.api_key,theme=console.theme)
        )
        if console.output_style == "detailed":
            console.print(render_run_header(
                datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
                message, theme=console.theme, api_key=current_settings.api_key,
            ))

        async def approve(action, index, total):
            return await prompt_approval(
                session,
                console,
                action,
                api_key=current_settings.api_key,
            )

        try:
            def emit(event):
                nonlocal latest_context_input_tokens
                if event.kind == "usage":
                    for key in usage_total:
                        usage_total[key] += int(event.data.get(key, 0) or 0)
                    if event.data.get("scope") != "subagent":
                        latest_context_input_tokens = int(event.data.get("input_tokens", 0) or 0)
                if event.kind == "plan_draft":
                    console.pause_live()
                    console.print("\n待审批执行计划：")
                    console.print_markdown(event.data["text"])
                elif event.kind == 'task_paused':
                    session_runner.state = 'paused'
                    console.print('本轮已暂停；待执行输入保留，用 /queue resume 恢复。')
                elif event.kind == "notice":
                    console.print(_safe_error(event.data["text"], current_settings.api_key))
                    if "计划已批准" in event.data["text"]:
                        console.resume_live()
                else:
                    console.print_event(event)

            if profile == "plan":
                confirmation_count = 0

                async def confirm_plan(_draft):
                    nonlocal confirmation_count
                    confirmation_count += 1
                    if confirmation_count == 1:
                        answer = (await session.prompt_async(
                            "  [a] 批准  [e] 用 $EDITOR 编辑  [d] 拒绝（默认 d）: ",
                            default="d",
                        )).strip().lower()
                        if answer in {"e", "edit"}:
                            return "edit"
                        return "approve" if answer in {"a", "approve", "y", "yes"} else "reject"
                    answer = (await session.prompt_async(
                        "  执行编辑后的计划吗？[y/n] ", default="n"
                    )).strip().lower()
                    return "approve" if answer in {"a", "approve", "y", "yes"} else "reject"

                with console.working(
                    context_window=configured_context_window(current_settings.model, current_settings.project_root),
                    api_key=current_settings.api_key,
                ):
                    await run_plan_flow(
                        service, factory,
                        thread_id=thread_id, config=config,
                        prompt_message=prompt_message,
                        allowed_tools=allowed_tools, history_display=message,
                        emit=emit, confirm=confirm_plan,
                        edit=session.edit,
                        approval=approve, status=console.set_status,
                    )
            else:
                with console.working(
                    context_window=configured_context_window(current_settings.model, current_settings.project_root),
                    api_key=current_settings.api_key,
                ):
                    async for event in service.stream_turn(
                        prompt_message,
                        config,
                        profile=profile,
                        target_path=target_path,
                        allowed_tools=allowed_tools,
                        **({"review_paths":request.review_paths} if request.review_paths is not None else {}),
                        history_display=message,
                        approval_handler=approve,
                        status_handler=console.set_status,
                    ):
                        emit(event)
            completed_turns += 1
        except (TurnRecursionLimitError, GraphRecursionError) as error:
            console.error(f"本轮已停止：{_safe_error(error, current_settings.api_key)}")
        except KeyboardInterrupt:
            console.print("本轮已中断，会话保留。")
            raise asyncio.CancelledError('本轮已中断。') from None
        except Exception as error:
            console.error(f"本轮请求失败：{_safe_error(error, current_settings.api_key)}")

    with local_tools.use_project_root(settings.project_root):
        banner = banner_renderable(
            width=console.console.width,
            height=console.console.height,
            theme=console.theme,
            enabled=configured_banner_enabled(settings.project_root),
        )
        if banner is not None:
            console.print(banner)
        else:
            console.print("ignovate harness · inline")
        console.print(f"工作项目：{settings.project_root}")
        console.print("终端滚动与复制由终端原生处理；输入 /help 查看命令。")
        if restored:
            console.print(f"已恢复会话 {thread_id}。")
            _format_history(service, thread_id, console)
        try:
            if plain:
                interrupt_cleanup = install_plain_interrupt(interrupt)
            while True:
                try:
                    prompt_options={'bottom_toolbar':toolbar}
                    if retained_input is not None: prompt_options['default']=retained_input;retained_input=None
                    message = (await session.prompt_async("> ", **prompt_options)).strip()
                except ApprovalInterrupted:
                    await session.ready.wait()
                    continue
                except (KeyboardInterrupt,InputInterrupted):
                    if session_runner.busy:
                        await session_runner.stop()
                        pause_session_goal(service,thread_id)
                        console.print('当前任务已停止，会话保留；输入队列已暂停。')
                    else: console.print('输入已清空。用 /exit 退出。')
                    continue
                except EOFError:
                    await session_runner.wait_idle()
                    console.print('再见！')
                    break
                if not message:
                    continue
                try:
                    request = controller.resolve(message)
                    information = None if actions.handles(request) else controller.information(request, service, thread_id, completed_turns=completed_turns)
                    if information is not None:
                        console.print(information)
                        continue
                except Exception as error:
                    console.error(_safe_error(error, current_settings.api_key))
                    continue
                command, argument = request.command, request.argument

                if command in IMMEDIATE_COMMANDS and actions.handles(request):
                    try: await execute_request(request)
                    except Exception as error:
                        retained_input=message
                        console.error(_safe_error(error,current_settings.api_key))
                    continue
                if actions.handles(request) or command in {'/goal','/compact'}:
                    try:
                        future=session_runner.submit(request.message,lambda request=request:execute_request(request))
                        future.add_done_callback(report_queued_error)
                        await asyncio.sleep(0)
                        await asyncio.sleep(0)
                    except Exception as error:
                        retained_input=message
                        console.error(_safe_error(error,current_settings.api_key))
                    continue
                if command in IDENTITY_COMMANDS and command!='/exit' and session_runner.busy:
                    console.error('请先 /stop 并 /queue clear，再切换项目或会话。')
                    continue
                if command == "/exit":
                    await session_runner.wait_idle()
                    console.print("再见！")
                    break
                if command == "/history":
                    try:
                        _format_history(service, thread_id, console)
                    except Exception as error:
                        console.error(f"读取历史失败：{_safe_error(error, current_settings.api_key)}")
                    continue
                if command == "/sessions":
                    _print_sessions(store, console)
                    continue
                if command == "/resume":
                    try:
                        saved = store.list_sessions()
                        if not saved:
                            console.print("当前项目还没有已保存的会话。")
                            continue
                        selector = argument
                        if not selector:
                            _print_sessions(store, console)
                            selector = (
                                await session.prompt_async("选择会话序号（默认 1）：", default="1")
                            ).strip() or "1"
                        thread_id = store.resolve_session(selector,**({"query":actions.session_query} if actions.session_query else {}))
                        session_runner.rebind(current_settings.project_root,thread_id)
                        console.print(f"已恢复会话 {thread_id}。")
                        _format_history(service, thread_id, console)
                    except ValueError as error:
                        console.error(str(error))
                    continue
                if command == "/rewind":
                    if argument:
                        console.error("用法：/rewind")
                        continue
                    console.print(
                        "回退会舍弃当前用户轮次及之后的对话状态；"
                        "已执行的文件修改和命令不会撤销。"
                    )
                    try:
                        confirmation = await session.prompt_async("确认回退对话状态？[y/N] ")
                    except (EOFError, KeyboardInterrupt, InteractionCancelled):
                        console.print("已取消回退。")
                        continue
                    if str(confirmation).strip().lower() not in {"y", "yes"}:
                        console.print("已取消回退。")
                        continue
                    try:
                        removed = await service.rewind(thread_id)
                        if removed is None:
                            console.print("当前会话没有可回退的用户轮次。")
                        else:
                            console.print(
                                f"对话状态已回退到上一轮开始前，移除了 {removed} 条消息。"
                                "已执行的文件和命令操作不会撤销。"
                            )
                    except Exception as error:
                        console.error(f"回退失败：{_safe_error(error, current_settings.api_key)}")
                    continue
                if command == "/compact":
                    try:
                        result = await service.compact_context(thread_id)
                        if result.get("compacted"):
                            console.print(
                                f"已压缩较早工具结果：约 {result['before_tokens']} → "
                                f"{result['after_tokens']} tokens。"
                            )
                        else:
                            console.print(f"未压缩上下文：{result.get('reason', '无可压缩内容')}。")
                    except Exception as error:
                        console.error(f"压缩失败：{_safe_error(error, current_settings.api_key)}")
                    continue
                if command == "/goal":
                    try:
                        goal, messages = controller.goal(argument, factory, thread_id)
                        for text in messages:
                            console.print(text)
                        if goal is not None:
                            await drive_goal(goal, message)
                    except Exception as error:
                        console.error(f"目标运行失败：{_safe_error(error, current_settings.api_key)}")
                    continue
                if command == "/clear":
                    thread_id = uuid.uuid4().hex
                    session_runner.rebind(current_settings.project_root,thread_id)
                    latest_context_input_tokens = None
                    usage_total={"input_tokens":0,"output_tokens":0,"cache_hit_tokens":0}
                    console.print("已开始新的会话。")
                    continue
                if command == "/project":
                    try:
                        if not argument:
                            raise ValueError("用法：/project <项目目录>")
                        new_root = select_project_root(argument)
                        candidate = AgentRuntimeFactory(replace(current_settings, project_root=new_root))
                        factories.append(candidate)
                        new_settings = candidate.settings
                        new_service = AgentService(
                            candidate,
                            api_key=new_settings.api_key,
                            permission_engine=PermissionEngine(new_root),
                            permission_mode=permission_mode,
                        )
                        new_service.ui_name='plain' if plain else 'inline'
                        new_controller = CommandController(new_settings)
                        new_actions = CommandActions(new_controller)
                        new_actions.preferences=candidate.preferences
                        new_controller.skill_registry=candidate.skill_registry
                        new_commands = {spec.name: spec.description for spec in new_controller.specs()}
                        new_session = (session if plain else InputBroker(build_session(
                            new_root, new_commands, api_key=new_settings.api_key)))
                        new_thread_id = uuid.uuid4().hex
                        session_runner.rebind(new_root,new_thread_id)
                        old_factory = factory
                        factory, current_settings, service = candidate, new_settings, new_service
                        store, controller, actions = candidate.session_store, new_controller, new_actions
                        completion_commands, session, thread_id = new_commands, new_session, new_thread_id
                        usage_total = {"input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0}
                        latest_context_input_tokens = None
                        cost_estimator = controller.cost_estimator
                        try:
                            await old_factory.aclose()
                            old_factory.close()
                        except Exception as error:
                            console.error(f'已切换项目；清理旧会话失败：{_safe_error(error, current_settings.api_key)}')
                        console.print(f"已切换到项目：{new_root}。已开始新的会话。")
                    except Exception as error:
                        console.error(f"切换项目失败：{_safe_error(error, current_settings.api_key)}")
                    continue
                try:
                    pending=session_runner.busy or session_runner.state=='paused'
                    future=session_runner.submit(request.message,lambda request=request:execute_request(request))
                    future.add_done_callback(report_queued_error)
                    if pending: console.print(f'已加入输入队列 · {len(session_runner.queue)} 项')
                    # Let model-side approval claim the prompt before requesting the next line.
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                except Exception as error:
                    retained_input=message
                    console.error(_safe_error(error,current_settings.api_key))
        finally:
            try:
                await session_runner.close()
            finally:
                if patcher: patcher.__exit__(None,None,None)
                if interrupt_cleanup: interrupt_cleanup()
                for active_factory in reversed(factories):
                    await active_factory.aclose()
                    active_factory.close()
