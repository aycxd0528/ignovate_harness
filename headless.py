"""Single-prompt, non-interactive CLI execution without UI imports."""

from __future__ import annotations

import json
import asyncio
import sys
import uuid
from collections.abc import Mapping
from contextlib import aclosing, ExitStack
from typing import TextIO

from langgraph.errors import GraphRecursionError

from agent import create_agent_runtime
from agent_service import AgentService, MAX_TURN_MODEL_CALLS, TurnRecursionLimitError
from config import ConfigurationError, Settings
from nailong.core.costs import CostEstimator
from nailong.core.budgets import CostBudgetExceeded, GoalCostBudget
import local_tools


def _safe_tree(value, api_key: str):
    if isinstance(value, str):
        return value.replace(api_key, "[密钥已隐藏]") if api_key else value
    if isinstance(value, Mapping):
        return {str(key): _safe_tree(item, api_key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_tree(item, api_key) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_tree(str(value), api_key)


def _write_json_line(stream: TextIO, payload: dict) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


async def run_print(
    settings: Settings,
    prompt: str,
    *,
    output_format: str = "text",
    max_turns: int = MAX_TURN_MODEL_CALLS,
    permission_mode: str = 'default',
    goal_mode: bool = False,
    goal_max_rounds: int = 20,
    goal_max_cost_usd: float = 1.0,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one prompt without starting an interactive interface.

    Text mode writes only the final answer to stdout. JSON modes include safe
    result, usage, tool summaries, and the persistent session ID.
    """
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    if output_format not in {"text", "json", "stream-json"}:
        stderr.write("输出格式必须是 text、json 或 stream-json。\n")
        return 3
    if not isinstance(max_turns, int) or not 1 <= max_turns <= MAX_TURN_MODEL_CALLS:
        stderr.write(f"--max-turns 必须在 1 到 {MAX_TURN_MODEL_CALLS} 之间。\n")
        return 3
    if not isinstance(permission_mode, str) or permission_mode not in {'default', 'bypassPermissions'}:
        stderr.write('无头权限模式必须是 default 或 bypassPermissions。\n')
        return 3

    thread_id = uuid.uuid4().hex
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0, "total_tokens": 0}
    tools: list[dict] = []
    tools_by_call: dict[str, dict] = {}
    approvals: list[dict] = []
    result_text = ""
    status = "success"
    error_text = None
    runtime = None
    factory = None
    service = None
    goal = None
    goal_store = None
    cost_estimator = None
    workflow = None
    delivery = None
    workflow_handled = False
    goal_driver = ExitStack()

    def emit_event(kind: str, data: dict) -> None:
        if output_format == "stream-json":
            _write_json_line(stdout, {"type": kind, "data": _safe_tree(data, settings.api_key)})

    try:
        with local_tools.use_project_root(settings.project_root):
            runtime = create_agent_runtime(settings)
            factory = getattr(runtime, "_nailong_runtime_factory", None)
            settings = getattr(factory,"settings",None) or settings
            service = AgentService(
                factory or (lambda **_kwargs: runtime),
                api_key=settings.api_key,
                permission_mode=permission_mode,
            )
            if goal_mode:
                goal_store = getattr(factory, "goal_store", None)
                if goal_store is None:
                    raise ConfigurationError("当前运行时没有目标存储，无法运行 --goal。")
                goal_driver.enter_context(goal_store.driver_lease())
                cost_estimator = CostEstimator(settings.model, settings.project_root)
                if not cost_estimator.available:
                    raise ConfigurationError(
                        f"模型 {settings.model} 未配置价格；请在 .nailong/settings.json 的 pricing 中配置后再运行 --goal。"
                    )
                goal = goal_store.active()
                if goal is not None:
                    thread_id = goal.thread_id or thread_id
                    goal = goal_store.attach_thread(goal.id, thread_id) or goal
                else:
                    latest_goal = goal_store.latest()
                    if latest_goal is not None and latest_goal.state == "paused":
                        goal = latest_goal
                        status = (
                            "approval_required"
                            if "审批" in goal.pause_reason
                            else "budget_limit"
                        )
                        error_text = (
                            "目标已暂停；请在交互模式恢复目标并处理待审批操作。"
                            if status == "approval_required"
                            else goal.pause_reason or "目标已暂停。"
                        )
                    else:
                        goal = goal_store.create(
                            prompt,
                            max_rounds=goal_max_rounds,
                            max_cost_usd=goal_max_cost_usd,
                            thread_id=thread_id,
                        )

            if not goal_mode and prompt.startswith('/'):
                from ui.controller import CommandController
                from ui.actions import CommandActions
                current_settings=getattr(factory,'settings',None) or settings
                controller=CommandController(current_settings)
                actions=CommandActions(controller)
                request=controller.resolve(prompt)
                if actions.handles(request):
                    async def emit_workflow(event): emit_event(event.kind,event.data)
                    outcome=await actions.execute(request,service,thread_id,emit=emit_workflow)
                    workflow_handled=True; workflow=outcome.data; result_text=outcome.text
                    if workflow.get('status')=='unverified': status='approval_required'
                    elif workflow.get('status') in {'failed','invalidated','cancelled'}: status='error'
                    for model_request in outcome.model_requests:
                        async for event in service.stream_turn(model_request.prompt,{'configurable':{'thread_id':thread_id},'recursion_limit':max_turns},
                            profile=model_request.profile,target_path=model_request.target_path,review_paths=model_request.review_paths,
                            max_model_calls=max_turns,history_display=request.message):
                            emit_event(event.kind,event.data)
                            if event.kind=='final': result_text+='\n'+event.data.get('text','')
                            elif event.kind=='usage':
                                for key in totals: totals[key]+=int(event.data.get(key,0) or 0)
                            elif event.kind=='tool_end': tools.append({'name':event.data.get('name'),'summary':event.data.get('summary'),'status':'completed' if event.data.get('ok') else 'failed'})
                            elif event.kind=='delivery': delivery=event.data
                            elif event.kind in {'error','hook_blocked','task_paused'}: status='error';error_text=event.data.get('message') or event.data.get('reason')
            run_rounds = goal_mode and goal is not None and goal.state == "active"
            first_goal_round = True
            while (run_rounds or not goal_mode) and not workflow_handled:
                if goal_mode:
                    goal = goal_store.prepare_round(goal.id)
                    if goal is None:
                        raise ValueError("目标已不存在。")
                    if goal.state != "active":
                        status = "budget_limit" if goal.state == "paused" else "success" if goal.state == "complete" else "error"
                        error_text = goal.pause_reason or None
                        break
                round_id = uuid.uuid4().hex
                round_usage = {"input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0}
                round_tool_calls = 0
                files_changed = False
                unattended_approval = False
                config = {
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": max_turns,
                }
                turn_prompt = prompt
                if goal_mode:
                    turn_prompt = (
                        f"正在无人值守推进目标（第 {goal.round + 1}/{goal.max_rounds} 轮，"
                        f"约 ${goal.spent_usd:.4f}/${goal.max_cost_usd:.4f}）。\n"
                        f"目标：{goal.objective}\n上一轮：{goal.last_summary or '尚未开始'}\n"
                        "请继续推进。遇到需要用户审批的操作时本轮会暂停。"
                        "配置的验证流程由核心在本轮回答结束后执行。配置流程成功和功能目标验收分别判断；"
                        "只有当前需求和输入版本的真实验证与目标验收均满足后，才能调用 update_goal(state='complete')。"
                        "验收条件缺失或等待人工确认时交付未验证结果并暂停，不能代替用户确认。"
                    )
                budget = GoalCostBudget(cost_estimator, goal.max_cost_usd - goal.spent_usd) if goal_mode else None
                round_stop = None
                cancellation = None
                try:
                    stream = service.stream_turn(
                        turn_prompt,
                        config,
                        max_model_calls=max_turns,
                        cost_budget=budget,
                        pin_message="goal" if goal_mode else False,
                        history_display=f"/goal {goal.objective}" if goal_mode and first_goal_round else None,
                        # No approval handler is supplied in print mode. ASK is
                        # denied for this turn, then a goal is persisted paused.
                        approval_handler=None,
                    )
                    async with aclosing(stream):
                        async for event in stream:
                            data = _safe_tree(event.data, settings.api_key)
                            emit_event(event.kind, data)
                            if event.kind == "final":
                                result_text = str(data.get("text", ""))
                                delivery = data.get('delivery') or delivery
                            elif event.kind == 'delivery':
                                delivery = data
                            elif event.kind == "usage":
                                for key in totals:
                                    try:
                                        amount = max(0, int(data.get(key, 0) or 0))
                                    except (TypeError, ValueError):
                                        amount = 0
                                    totals[key] += amount
                                    if key in round_usage:
                                        round_usage[key] += amount
                            elif event.kind == "tool_start":
                                round_tool_calls += 1
                                call_id = str(data.get("call_id", ""))
                                summary = {
                                    "name": str(data.get("name", "unknown")),
                                    "status": "started",
                                    "summary": str(data.get("summary", "")),
                                }
                                tools.append(summary)
                                if call_id:
                                    tools_by_call[call_id] = summary
                            elif event.kind == "tool_end":
                                call_id = str(data.get("call_id", ""))
                                summary = tools_by_call.get(call_id) if call_id else None
                                if summary is None:
                                    summary = {"name": str(data.get("name", "unknown")), "status": "started"}
                                    tools.append(summary)
                                summary.update({
                                    "status": "failed" if data.get("ok") is False else "completed",
                                    "summary": str(data.get("summary", "")),
                                })
                                if goal_mode and data.get("ok") and data.get("name") in {"edit_file", "write_file"}:
                                    files_changed = True
                                if data.get("ok") is False and status == "success":
                                    status = "error"
                                    error_text = str(data.get("summary") or "工具执行失败。")
                            elif event.kind == "approval_needed":
                                approvals.extend(data.get("actions", []))
                                unattended_approval = True
                            elif event.kind == "approval_decision" and data.get("kind") == "reject":
                                status = "approval_required"
                            elif event.kind in {"error", "hook_blocked", "task_paused"}:
                                status = "error"
                                error_text = str(data.get("message") or data.get("reason") or "运行被阻止。")
                    latest_goal = goal_store.get(goal.id) if goal_mode else None
                    if goal_mode and latest_goal is not None and latest_goal.state == 'active' and status=='success' and budget.usage_complete:
                        from nailong.core.verification import verify_goal_if_configured
                        async def verification_event(event): emit_event(event.kind,event.data)
                        workflow=await verify_goal_if_configured(service,factory,goal,thread_id,emit=verification_event)
                        if workflow:
                            result_text+='\n项目验证：'+workflow['status']
                            delivery=await service.delivery_report(thread_id)
                            if delivery is not None: emit_event('delivery',delivery)
                            if workflow['status']=='unverified': unattended_approval=True;status='approval_required'
                        delivery=await service.delivery_report(thread_id)
                        task=service.task_store.snapshot(thread_id) if service.task_store is not None else None
                        if task is not None:
                            goal_criteria=[row for row in task['acceptance'] if row['required'] and not row['id'].startswith('verify:')]
                            if not goal_criteria or any(row['kind']=='manual' and row['status']!='passed' for row in goal_criteria):
                                round_stop='目标验收条件缺失或等待用户人工确认；请在交互模式核对 /task。'
                                status='approval_required';unattended_approval=True
                    first_goal_round = False
                except asyncio.CancelledError as error:
                    if not goal_mode:
                        raise
                    cancellation = error
                    round_stop = "目标运行已取消。"
                except CostBudgetExceeded as error:
                    round_stop = str(error)
                    status = "budget_limit"
                    error_text = round_stop
                except (TurnRecursionLimitError, GraphRecursionError) as error:
                    if not goal_mode:
                        raise
                    status = "turn_limit"
                    error_text = str(error)
                    round_stop = error_text
                except Exception as error:
                    if not goal_mode:
                        raise
                    status = "error"
                    error_text = f"{type(error).__name__}: {error}"
                    round_stop = error_text

                if not goal_mode:
                    if not result_text and status == "success":
                        status = "error"
                        error_text = "Agent 没有返回最终回答。"
                    break

                if not result_text and status == "success":
                    status = "error"
                    error_text = "Agent 没有返回最终回答。"
                try:
                    round_cost = max(cost_estimator.estimate(round_usage), budget.spent)
                except ValueError:
                    round_cost = goal.max_cost_usd
                goal = goal_store.record_round(
                    goal.id,
                    cost_usd=round_cost,
                    files_changed=files_changed,
                    tool_calls=round_tool_calls,
                    summary=result_text[:1_000],
                    unattended_approval=unattended_approval,
                    round_id=round_id,
                    stop_reason=round_stop or ("模型未提供完整用量；已保守计费并暂停目标。" if not budget.usage_complete else ""),
                ) or goal
                if cancellation is not None:
                    raise cancellation
                if round_stop or not budget.usage_complete:
                    if goal.state == "active":
                        _, _, goal = goal_store.update(
                            goal.id, state="paused", thread_id=thread_id,
                            summary=round_stop or "模型未提供完整用量；已保守计费并暂停目标。",
                        )
                    if round_stop is None:
                        status = "budget_limit"
                    error_text = goal.pause_reason or round_stop
                    break
                if unattended_approval:
                    status = "approval_required"
                    error_text = goal.pause_reason
                    break
                if status == "error":
                    if goal.state == "active":
                        goal_store.update(
                            goal.id,
                            state="paused",
                            summary=error_text or "目标轮次发生错误。",
                            thread_id=thread_id,
                        )
                        goal = goal_store.get(goal.id) or goal
                    break
                if goal.state == "complete":
                    status = "success"
                    break
                if goal.state == "blocked":
                    status = "error"
                    error_text = "目标连续三轮受相同原因阻塞。"
                    break
                if goal.state == "paused":
                    status = "budget_limit"
                    error_text = goal.pause_reason
                    break
                result_text = ""
                run_rounds = goal.state == "active"
    except (TurnRecursionLimitError, GraphRecursionError) as error:
        status = "turn_limit"
        error_text = str(error)
    except ConfigurationError as error:
        status = "configuration_error"
        error_text = str(error)
    except Exception as error:  # Keep machine-readable output free of tracebacks/secrets.
        status = "error"
        error_text = f"{type(error).__name__}: {error}"
    finally:
        goal_driver.close()
        if service is not None and thread_id and (getattr(service,'last_turn_task_id',None)
                or delivery is not None or prompt.split(' ',1)[0] in {'/task','/status','/verify'}):
            try:
                delivery=await service.delivery_report(thread_id)
            except (OSError, ValueError):
                # Never fall back to an earlier positive status on failed refresh.
                delivery={'status':'unverified','reasons':['当前交付状态无法重新核实。']}
        if factory is not None:
            aclose = getattr(factory, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:
                    pass
            close = getattr(factory, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    exit_code = {
        "success": 0,
        "approval_required": 1,
        "error": 1,
        "turn_limit": 2,
        "budget_limit": 2,
        "configuration_error": 3,
    }[status]
    payload = _safe_tree(
        {
            "session_id": thread_id,
            "status": status,
            "result": result_text,
            "usage": totals,
            "tools": tools,
            "approvals": approvals,
            "goal": (
                {
                    "id": goal.id,
                    "objective": goal.objective,
                    "state": goal.state,
                    "round": goal.round,
                    "max_rounds": goal.max_rounds,
                    "spent_usd": goal.spent_usd,
                    "max_cost_usd": goal.max_cost_usd,
                    "pause_reason": goal.pause_reason,
                }
                if goal is not None
                else None
            ),
            "workflow": workflow,
            "delivery": delivery,
            "error": error_text,
        },
        settings.api_key,
    )
    if output_format == "json":
        _write_json_line(stdout, payload)
    elif output_format == "stream-json":
        _write_json_line(stdout, {"type": "headless_result", "data": payload})
    elif result_text:
        stdout.write(result_text + "\n")
        if payload.get('delivery'):
            from nailong.core.delivery import render_delivery_report
            stdout.write(render_delivery_report(payload['delivery']) + '\n')
        stdout.flush()
    if error_text and output_format == "text":
        stderr.write(str(_safe_tree(error_text, settings.api_key)) + "\n")
    return exit_code
