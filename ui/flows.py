"""UI-neutral plan and bounded-goal workflows shared by both interfaces."""

from __future__ import annotations

import inspect
import asyncio
from ui.arguments import split_arguments
import uuid
from contextlib import aclosing, nullcontext
from pathlib import Path

from agent_service import TurnEvent
from nailong.core.budgets import CostBudgetExceeded, GoalCostBudget
from nailong.core.goal_control import assess_goal_progress, decide_goal_turn


def parse_goal_request(argument: str) -> tuple[str, int, float]:
    tokens = split_arguments(argument)
    max_rounds = 20
    max_cost_usd = 1.0
    while tokens and tokens[0].startswith("--"):
        option, equals, inline_value = tokens.pop(0).partition("=")
        if option not in {"--max-rounds", "--max-cost-usd"}:
            raise ValueError(f"不支持的目标参数：{option}")
        if equals:
            raw = inline_value
        elif tokens:
            raw = tokens.pop(0)
        else:
            raise ValueError(f"{option} 缺少参数值。")
        try:
            if option == "--max-rounds":
                max_rounds = int(raw)
            else:
                max_cost_usd = float(raw)
        except ValueError as error:
            raise ValueError(f"{option} 参数格式无效。") from error
    objective = " ".join(tokens).strip()
    if not objective:
        raise ValueError("用法：/goal [--max-rounds 1-100] [--max-cost-usd 0-100] <目标>")
    if not 1 <= max_rounds <= 100:
        raise ValueError("目标轮数必须在 1 到 100 之间。")
    if not 0 < max_cost_usd <= 100:
        raise ValueError("目标成本上限必须大于 0 且不超过 100 美元。")
    return objective, max_rounds, max_cost_usd


async def _call(callback, *args):
    if callback is None:
        return None
    result = callback(*args)
    return await result if inspect.isawaitable(result) else result


async def _emit(emit, event: TurnEvent) -> None:
    await _call(emit, event)


async def _goal_task_snapshot(service, factory, thread_id):
    """Pair the task owner's snapshot with independently observed input facts."""
    tasks = getattr(service, 'task_store', None) or getattr(factory, 'task_store', None)
    if tasks is None or not callable(getattr(tasks, 'snapshot', None)):
        return None
    before = tasks.snapshot(thread_id)
    if before is None:
        return None
    from nailong.core.verification import input_fingerprint, _owned_worker
    root = getattr(tasks, 'project_root', None)
    current = None
    if root is not None:
        try:
            current = await _owned_worker(input_fingerprint, root,
                before.get('verification_generated_paths', []))
        except (OSError, ValueError):
            pass  # Unknown coverage is not a claim of no effective progress.
    after = tasks.snapshot(thread_id)
    if after is None:
        return None
    if (before['task_id'], before['revision']) != (after['task_id'], after['revision']):
        current = None
    return {**after, 'current_input_fingerprint': current}


async def run_plan_flow(
    service, factory, *, thread_id: str, config: dict, prompt_message: str,
    emit, confirm, edit, approval=None, status=None, allowed_tools=None, history_display=None,
) -> bool:
    """Stage a read-only plan, require approval, then run its approved text."""
    plan_store = getattr(factory, "plan_store", None)
    if plan_store is None:
        raise ValueError("当前运行时没有计划存储。")
    run_config = {
        **config,
        "configurable": {**config.get("configurable", {}), "thread_id": thread_id},
    }
    pending_plan_id = None
    async for event in service.stream_turn(
        prompt_message,
        run_config,
        profile="plan",
        history_display=history_display or "/plan " + prompt_message.removeprefix("为以下目标制定执行计划：\n"),
        allowed_tools=allowed_tools,
        approval_handler=approval,
        status_handler=status,
    ):
        if event.kind == "plan_ready":
            pending_plan_id = event.data.get("plan_id")
        await _emit(emit, event)
    if not pending_plan_id:
        return False
    draft = plan_store.get(pending_plan_id)
    if draft is None:
        await _emit(emit, TurnEvent("notice", {"text": "待审批计划已失效，未执行。"}))
        return False
    await _emit(emit, TurnEvent("plan_draft", {"text": draft}))
    choice = str(await _call(confirm, draft) or "reject").strip().lower()
    approved_markdown = draft
    if choice == "edit":
        try:
            approved_markdown = str(await _call(edit, draft) or "")
        except Exception as error:
            plan_store.discard(pending_plan_id)
            await _emit(emit, TurnEvent("notice", {"text": f"编辑计划失败，计划未执行：{error}"}))
            return False
        choice = str(await _call(confirm, approved_markdown) or "reject").strip().lower()
    if choice != "approve":
        plan_store.discard(pending_plan_id)
        await _emit(emit, TurnEvent("notice", {"text": "已拒绝计划，未执行。"}))
        return False
    approved = plan_store.approve(pending_plan_id, approved_markdown)
    project_root = Path(getattr(plan_store, "project_root", approved.path.parent.parent.parent)).resolve()
    relative_path = approved.path.relative_to(project_root)
    await _emit(emit, TurnEvent("notice", {"text": f"计划已批准并保存：{relative_path}"}))
    execute_message = (
        "请依据下面这份用户已批准的计划，在当前会话中继续实现。"
        "计划中的步骤应逐项完成，并在结束前提供验证证据。\n\n"
        f"计划文件：{relative_path}\n\n{approved.markdown}"
    )
    async for event in service.stream_turn(
        execute_message,
        run_config,
        profile="chat",
        allowed_tools=allowed_tools,
        pin_message=True,
        approval_handler=approval,
        status_handler=status,
    ):
        await _emit(emit, event)
    return True


async def drive_goal(
    service, factory, goal_store, cost_estimator, goal, *, thread_id: str,
    emit, status=None, approval=None, history_display: str | None = None,
):
    lease = goal_store.driver_lease() if hasattr(goal_store, 'driver_lease') else nullcontext()
    with lease:
        if hasattr(goal_store, 'attach_thread'):
            goal = goal_store.attach_thread(goal.id, thread_id) or goal
        return await _drive_goal(service, factory, goal_store, cost_estimator, goal,
            thread_id=thread_id, emit=emit, status=status, approval=approval, history_display=history_display)


async def _drive_goal(service, factory, goal_store, cost_estimator, goal, *, thread_id,
                      emit, status=None, approval=None, history_display=None):
    """Callback compatibility over the shared incremental event engine."""
    events=_goal_events(service,factory,goal_store,cost_estimator,goal,thread_id=thread_id,
        emit=None,status=status,approval=approval,history_display=history_display)
    try:
        async for event in events:
            try:
                await _emit(emit,event)
            except BaseException as error:
                # Preserve the original callback boundary for round settlement.
                try:
                    await events.athrow(error)
                except BaseException as cleanup_error:
                    if cleanup_error is not error and not isinstance(cleanup_error,StopAsyncIteration):
                        error.add_note('Goal event cleanup: '+type(cleanup_error).__name__)
                raise
    finally:
        await events.aclose()
    return goal_store.get(goal.id) or goal


async def stream_goal(service, factory, goal_store, cost_estimator, goal, *, thread_id,
                      status=None, approval=None, history_display=None):
    """Incremental events in the caller's task, without a background lease."""
    lease=goal_store.driver_lease() if hasattr(goal_store,'driver_lease') else nullcontext()
    with lease:
        if hasattr(goal_store,'attach_thread'):
            goal=goal_store.attach_thread(goal.id,thread_id) or goal
        events=_goal_events(service,factory,goal_store,cost_estimator,goal,thread_id=thread_id,
            emit=None,status=status,approval=approval,history_display=history_display)
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()
            current=goal_store.get(goal.id)
            if current is not None and current.state=='active':
                goal_store.update(goal.id,state='paused',summary='目标事件流已结束或取消。',thread_id=thread_id)


async def _goal_events(
    service, factory, goal_store, cost_estimator, goal, *, thread_id: str,
    emit, status=None, approval=None, history_display: str | None = None,
):
    """Advance a persisted goal while GoalStore enforces cost and round limits."""
    first_round = True
    while goal.state == "active":
        goal = goal_store.prepare_round(goal.id)
        if goal is None:
            raise ValueError("目标已不存在。")
        before = await _goal_task_snapshot(service, factory, thread_id) if goal.state == 'active' else None
        goal = goal_store.get(goal.id) or goal
        control = decide_goal_turn(goal, before)
        try:
            for attempt in range(8):
                yield TurnEvent('goal_control', {'goal_id': goal.id, **control})
                # Every publication yields. Admit only after a stable reread,
                # including a pause during publication of a changed decision.
                goal = goal_store.get(goal.id) or goal
                before = await _goal_task_snapshot(service, factory, thread_id) if goal.state == 'active' else before
                goal = goal_store.get(goal.id) or goal
                fresh_control = decide_goal_turn(goal, before)
                if fresh_control == control:
                    break
                control = fresh_control
            else:
                control = {**control, 'action': 'wait', 'reason': '执行前状态持续变化，已暂停。',
                           'resume_condition': '核对任务和目标状态后显式恢复。'}
        except asyncio.CancelledError:
            goal_store.update(goal.id, state='paused', summary='目标运行已取消。', thread_id=thread_id)
            raise
        if control['action'] in {'wait', 'stop'}:
            if goal.state == 'active':
                _, _, saved = goal_store.update(goal.id, state='paused',
                    summary=control['reason']+' '+control['resume_condition'], thread_id=thread_id)
                goal = saved or goal
            yield TurnEvent('goal_status', {'goal': goal, 'text': control['reason']})
            yield TurnEvent('notice', {'text': goal.pause_reason or control['reason']})
            break
        round_id = uuid.uuid4().hex
        round_usage = {"input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0}
        tool_calls = 0
        files_changed = False
        unattended_approval = False
        summary = ""
        goal_prompt = (
            f"正在推进一个有护栏的目标（第 {goal.round + 1}/{goal.max_rounds} 轮，"
            f"已花费约 ${goal.spent_usd:.4f}/${goal.max_cost_usd:.4f}）。\n"
            f"目标：{goal.objective}\n"
            f"上一轮：{goal.last_summary or '尚未开始'}\n"
            "请继续推进。配置的验证流程由核心在本轮回答结束后执行；本轮先完成改动并返回进度。"
            "只有当前任务的用户目标验收与验证证据满足交付要求后，才能调用 update_goal(state='complete')。"
            "未配置验证或缺少目标覆盖时保持未验证并暂停，请提示用户用 /task accept、/task bind 配置验收；单个命令成功不能替代目标验收。"
            "如果受阻，用相同原因调用 update_goal(state='blocked')，至少连续三轮。"
        )
        if control['action'] == 'replan':
            goal_prompt += ('\n本轮先重新核对当前任务版本、验收条件及已有证据引用，'
                '说明哪些假设已排除，再选择能产生新证据的下一步。'
                '重新运行相同检查、工具计数或文件改名不代表有效进展。'
                f"\n任务：{control.get('task_id')}；要求版本：{control.get('task_revision')}。")
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
        budget = GoalCostBudget(cost_estimator, goal.max_cost_usd - goal.spent_usd)
        round_error = None
        stop_reason = ""
        progress = assess_goal_progress(before, None)
        try:
            stream = service.stream_turn(
                goal_prompt,
                config,
                profile="chat",
                cost_budget=budget,
                pin_message="goal",
                history_display=(history_display or f"/goal {goal.objective}") if first_round else None,
                approval_handler=approval,
                status_handler=status,
            )
            async with aclosing(stream):
                async for event in stream:
                    if event.kind == "usage":
                        for key in round_usage:
                            try:
                                round_usage[key] += max(0, int(event.data.get(key, 0) or 0))
                            except (TypeError, ValueError):
                                pass
                    elif event.kind == "tool_start":
                        tool_calls += 1
                    elif event.kind == "tool_end":
                        if event.data.get("name") in {"edit_file", "write_file"} and event.data.get("ok"):
                            files_changed = True
                    elif event.kind == "approval_needed" and approval is None:
                        unattended_approval = True
                    elif event.kind == "final":
                        summary = str(event.data.get("text", ""))[:1_000]
                    elif event.kind in {"error", "hook_blocked", "task_paused"}:
                        stop_reason = str(event.data.get("message") or event.data.get("reason") or "目标轮次被阻止。")
                    yield event
            if not stop_reason and budget.usage_complete:
                from nailong.core.verification import verify_goal_if_configured
                verification_events=[]
                verification=await verify_goal_if_configured(service,factory,goal,thread_id,approval=approval,emit=verification_events.append)
                for verification_event in verification_events:
                    yield verification_event
                if verification:
                    summary+='\n核心项目验证：'+verification['status']
                    tool_calls+=len(verification['steps'])
                    if verification['status']=='unverified':
                        unattended_approval=approval is None
                        stop_reason='项目验证待审批或被拒绝，目标已暂停。'
                    yield TurnEvent('notice',{'text':'项目验证：'+verification['status']})
            if not stop_reason and callable(getattr(service, 'delivery_report', None)):
                delivery = await service.delivery_report(thread_id)
                if delivery is not None:
                    from nailong.core.delivery import render_delivery_report
                    yield TurnEvent('notice', {'text': render_delivery_report(delivery)})
                    latest_goal = goal_store.get(goal.id)
                    if not stop_reason and latest_goal is not None and latest_goal.state == 'active':
                        task_store = getattr(service, 'task_store', None)
                        task = task_store.snapshot(thread_id) if task_store is not None else None
                        goal_criteria = [row for row in (task or {}).get('acceptance', []) if row['required'] and not row['id'].startswith('verify:')]
                        if not goal_criteria:
                            stop_reason = '缺少用户目标验收条件；请用 /task accept 登记可观察条件后恢复目标。'
                        elif any(row['kind'] == 'manual' and row['status'] != 'passed' for row in goal_criteria):
                            stop_reason = '目标等待用户人工验收；请核对 /task，确认后恢复目标。'
        except BaseException as error:
            round_error = error
            stop_reason = "目标运行已取消。" if isinstance(error, (asyncio.CancelledError,GeneratorExit)) else str(error) or type(error).__name__
        finally:
            try:
                round_cost = max(cost_estimator.estimate(round_usage), budget.spent)
            except ValueError:
                round_cost = goal.max_cost_usd
            if not budget.usage_complete and not stop_reason:
                stop_reason = "模型未提供完整用量；已保守计费并暂停目标。"
            try:
                after = await _goal_task_snapshot(service, factory, thread_id)
                progress = assess_goal_progress(before, after)
            except asyncio.CancelledError as error:
                round_error = round_error or error
                stop_reason = '目标运行已取消。'
            except Exception:
                # Progress observation failure must never prevent cost settlement.
                progress['reason'] = 'task_observation_failed'
            goal = goal_store.record_round(
                goal.id, cost_usd=round_cost, files_changed=files_changed,
                tool_calls=tool_calls, summary=summary, unattended_approval=unattended_approval,
                round_id=round_id, stop_reason=stop_reason,
                progress=progress,
            ) or goal
        if isinstance(round_error,(GeneratorExit,asyncio.CancelledError)):
            raise round_error
        try:
            yield TurnEvent('goal_progress', {'goal_id': goal.id,
                'round_id': round_id, **progress})
        except BaseException as error:
            goal_store.update(goal.id, state='paused',
                summary='目标运行已取消。' if isinstance(error, asyncio.CancelledError) else str(error),
                thread_id=thread_id)
            raise
        first_round = False
        if round_error is not None and not isinstance(round_error, CostBudgetExceeded):
            raise round_error
        try:
            yield TurnEvent("goal_status", {
                "goal": goal,
                "text": (
                    f"目标状态：{goal.state} · 第 {goal.round}/{goal.max_rounds} 轮 · "
                    f"累计约 ${goal.spent_usd:.4f}/${goal.max_cost_usd:.4f}"
                ),
            })
            if goal.pause_reason:
                yield TurnEvent("notice", {"text": goal.pause_reason})
        except asyncio.CancelledError:
            # This round is already settled. Pause without charging it again.
            goal_store.update(goal.id, state="paused", summary="目标运行已取消。", thread_id=thread_id)
            raise
    return
