"""Per-call goal cost reservations shared with concurrently running children."""

import asyncio
import json
from contextvars import ContextVar
from dataclasses import dataclass

from nailong.core.usage import message_usage


class CostBudgetExceeded(RuntimeError):
    """The remaining goal budget cannot fund another model call."""


active_cost_budget = ContextVar("nailong_cost_budget", default=None)
active_token_budget = ContextVar("nailong_token_budget", default=None)
active_token_admission = ContextVar("nailong_token_admission", default=None)
active_final_reservation = ContextVar("nailong_child_final_reservation", default=None)
active_turn_budget = ContextVar("nailong_turn_budget", default=None)


class TurnModelLimitExceeded(RuntimeError):
    """The main agent has consumed this turn's model request allowance."""

    def __init__(self, message: str, *, completed_response=None):
        super().__init__(message)
        self.completed_response = completed_response


class TurnModelBudget:
    """Count main model requests across approval resumes, independently of graph nodes."""

    def __init__(self, limit: int, record=None):
        self.limit = limit
        self.calls = 0
        self.record = record

    @property
    def summary_only(self):
        return self.calls >= self.limit

    def prepare(self, request):
        if self.calls >= self.limit:
            raise TurnModelLimitExceeded(f"本轮模型调用已达到上限（{self.limit} 次），已停止本轮。")
        if self.calls + 1 < self.limit:
            return request
        # This instruction belongs to the prepared request, not checkpoint history.
        # Tools are removed so the last request delivers evidence already obtained.
        from langchain_core.messages import SystemMessage
        note = (
            "\n\n本轮已进入模型调用预算的最后一次请求。工具现已禁用，停止继续探索或执行。"
            "请根据已有证据直接回答用户，简述已完成的工作、已确认结论和未完成部分。"
            "没有执行的验证或尚未满足的目标不能宣称完成；缺少信息时明确说明限制。"
        )
        system = request.system_message
        if system is None:
            system = SystemMessage(content=note.strip())
        elif isinstance(system.content, str):
            system = system.model_copy(update={"content": system.content + note})
        else:
            system = system.model_copy(update={"content": [*system.content, {"type": "text", "text": note}]})
        return request.override(system_message=system, tools=[], tool_choice="none")

    def admit(self):
        if self.calls >= self.limit:
            raise TurnModelLimitExceeded(f"本轮模型调用已达到上限（{self.limit} 次），已停止本轮。")
        self.calls += 1
        if self.record is not None:
            self.record({"model_call": self.calls, "model_call_limit": self.limit,
                         "summary_only": self.summary_only})


class TokenBudgetExceeded(RuntimeError):
    """A child call cannot fit within its shared token allowance."""

    def __init__(self, message: str, *, diagnostics=None):
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


def _input_bound(request) -> int:
    """Conservative UTF-8 bound including the system prompt and tool schemas."""
    messages = list(request.messages)
    if request.system_message is not None:
        messages.insert(0, request.system_message)
    serialized = [message.model_dump(mode="json") for message in messages]
    tools = [tool if isinstance(tool, dict) else {
        "name": tool.name, "description": tool.description,
        "parameters": tool.get_input_schema().model_json_schema(),
    } for tool in request.tools]
    return len(json.dumps({"messages": serialized, "tools": tools}, ensure_ascii=False, default=str).encode("utf-8")) + 1_024


def _configured_output(request) -> int:
    return max(1, int(request.model_settings.get("max_tokens") or getattr(request.model, "max_tokens", None) or 4_096))


@dataclass
class TokenAdmission:
    budget: "SharedTokenBudget"
    amount: int
    task_key: str | None = None


@dataclass
class FinalAnswerReservation:
    budget: "SharedTokenBudget"
    amount: int


class SharedTokenBudget:
    """Reserve every child model call against one parent-turn allowance."""

    def __init__(self, tokens: int, *, output_tokens: int = 4_000,
                 wait_for_capacity: bool = False, record=None):
        self.limit = max(0, int(tokens))
        self.output_tokens = max(1, int(output_tokens))
        self.spent = 0
        self.reserved = 0
        self.final_reserved = 0
        self.stopped = False
        self.stop_reason = None
        self.wait_for_capacity = wait_for_capacity
        self.record = record
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition(self._lock)

    def diagnostics(self, *, inputs=0, output=0, cap=0, reason=None):
        return {
            "limit_tokens": self.limit, "spent_tokens": self.spent,
            "reserved_tokens": self.reserved, "remaining_tokens": self.remaining,
            "final_reserved_tokens": self.final_reserved,
            "input_tokens_upper_bound": inputs, "output_tokens_requested": output,
            "output_tokens_reserved": cap, "reason": reason or self.stop_reason,
            "input_method": "utf8_upper_bound",
        }

    def _record(self, event, *, details=None, **values):
        if self.record is not None:
            try:
                admission = active_token_admission.get()
                task_key = admission.task_key if admission is not None and admission.budget is self else None
                self.record({"event": event, "task_key": task_key,
                             **self.diagnostics(**values), **(details or {})})
            except Exception:
                # Observability must never strand a reservation or bypass settlement.
                pass

    def _refuse(self, *, inputs=0, output=0, admission=False, reason=None):
        reason = self.stop_reason or reason or "input_does_not_fit"
        info = self.diagnostics(inputs=inputs, output=output, reason=reason)
        admission_info = ({"input_tokens_upper_bound": None, "input_method": "prompt_only_estimate",
                           "admission_tokens_estimate": inputs} if admission else {})
        info.update(admission_info)
        self._record("refused", inputs=inputs, output=output, reason=reason, details=admission_info)
        prefix = "子代理预算不足，未启动新的子任务。" if admission else "子代理剩余 token 预算不足以容纳下一次模型调用，已停止调研。"
        detail = "提供商用量未知，已按预留上界扣除并停止本轮子代理。" if reason == "usage_unknown" else "本轮不重试相同子任务；主代理可使用已有证据继续。"
        input_label = "任务准入暂估" if admission else "输入上界"
        raise TokenBudgetExceeded(
            f"{prefix} {input_label} {inputs}，输出预留目标 {output}；"
            f"共享额度 {self.limit}，已用 {self.spent}，在途预留 {self.reserved}，余额 {self.remaining}。{detail}",
            diagnostics=info,
        )

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent - self.reserved)

    async def admit(self, amount: int, *, output: int = 0) -> TokenAdmission:
        """Hold an admission estimate until the first real model request arrives."""
        async with self._changed:
            while True:
                if self.stopped or amount > self.limit - self.spent:
                    self._refuse(inputs=amount, output=output, admission=True, reason="admission_does_not_fit")
                if amount <= self.remaining:
                    break
                if not self.wait_for_capacity:
                    self._refuse(inputs=amount, output=output, admission=True, reason="concurrent_reservations")
                await self._changed.wait()
            self.reserved += amount
            return TokenAdmission(self, amount)

    async def reserve_final(self, amount: int, *, output=0) -> FinalAnswerReservation:
        """Protect a bounded, tools-disabled final request before funding research."""
        async with self._changed:
            admission = active_token_admission.get()
            if admission is not None and admission.budget is self:
                self.reserved -= admission.amount
                admission.amount = 0
                self._changed.notify_all()
            while True:
                if self.stopped or amount > self.limit - self.spent:
                    self._refuse(inputs=amount-output, output=output, reason="final_answer_does_not_fit")
                if amount <= self.remaining:
                    break
                await self._changed.wait()
            self.reserved += amount
            self.final_reserved += amount
            self._record("final_reserved", inputs=amount-output, output=output, cap=output)
            return FinalAnswerReservation(self, amount)

    async def release_final(self, reservation: FinalAnswerReservation | None):
        async with self._changed:
            if reservation is None or reservation.budget is not self or not reservation.amount:
                return
            self.reserved -= reservation.amount
            self.final_reserved -= reservation.amount
            reservation.amount = 0
            self._record("final_released")
            self._changed.notify_all()

    async def reserve(self, request) -> tuple[int, int]:
        inputs = _input_bound(request)
        maximum_output = min(self.output_tokens, _configured_output(request))
        async with self._changed:
            final = active_final_reservation.get()
            if final is not None and final.budget is self:
                self.reserved -= final.amount
                self.final_reserved -= final.amount
                final.amount = 0
            admission = active_token_admission.get()
            if admission is not None and admission.budget is self:
                self.reserved -= admission.amount
                admission.amount = 0
                self._changed.notify_all()
            waited = False
            while True:
                available_without_holds = self.limit - self.spent - self.final_reserved - inputs
                if self.stopped or available_without_holds < min(64, maximum_output):
                    self._refuse(inputs=inputs, output=maximum_output,
                                 reason="final_answer_reserved" if self.final_reserved else None)
                target = min(maximum_output, available_without_holds)
                cap = min(maximum_output, self.remaining - inputs)
                if cap >= target or (not self.wait_for_capacity and cap >= min(64, maximum_output)):
                    break
                if not self.wait_for_capacity:
                    self._refuse(inputs=inputs, output=maximum_output, reason="concurrent_reservations")
                if not waited:
                    self._record("waiting", inputs=inputs, output=maximum_output, reason="concurrent_reservations")
                    waited = True
                await self._changed.wait()
            reservation = inputs + cap
            self.reserved += reservation
            self._record("reserved", inputs=inputs, output=maximum_output, cap=cap)
            return reservation, cap

    async def release(self, reservation: int) -> None:
        """Release a hold only when the provider was never called."""
        async with self._changed:
            self.reserved = max(0, self.reserved - reservation)
            self._changed.notify_all()

    async def settle(self, reservation: int, messages=None) -> None:
        usages = [message_usage(message) for message in (messages or []) if getattr(message, "type", None) == "ai"]
        known = bool(usages) and all(usage is not None for usage in usages)
        charged = sum(usage["total_tokens"] for usage in usages) if known else reservation
        async with self._changed:
            self.reserved = max(0, self.reserved - reservation)
            self.spent += charged
            if not known or self.spent >= self.limit:
                self.stopped = True
                self.stop_reason = "usage_unknown" if not known else "token_limit"
            self._record("settled", reason=self.stop_reason, details={
                "reservation_tokens": reservation, "charged_tokens": charged, "usage_complete": known,
            })
            self._changed.notify_all()

    async def settle_admission(self, admission: TokenAdmission, usage: dict, *, complete: bool) -> None:
        """Compatibility for workers that do not invoke model middleware."""
        async with self._changed:
            if not admission.amount:
                return
            amount = admission.amount
            admission.amount = 0
            self.reserved = max(0, self.reserved - amount)
            charged = usage["total_tokens"] if complete else max(amount, usage["total_tokens"])
            self.spent += charged
            if self.spent >= self.limit:
                self.stopped = True
                self.stop_reason = "token_limit"
            self._changed.notify_all()


class GoalCostBudget:
    def __init__(self, estimator, remaining_usd: float):
        if not estimator.available:
            raise ValueError("模型未配置价格，不能启动目标成本控制。")
        self.estimator = estimator
        self.limit = max(0.0, remaining_usd)
        self.spent = 0.0
        self.reserved = 0.0
        self.usage_complete = True
        self.stopped = False
        self._lock = asyncio.Lock()

    async def reserve(self, request) -> tuple[float, int]:
        input_cost = self.estimator.estimate({"input_tokens": _input_bound(request)})
        configured = _configured_output(request)
        output_cap = min(4_096, configured)
        output_price = self.estimator.price.output_per_million / 1_000_000
        async with self._lock:
            available = self.limit - self.spent - self.reserved - input_cost
            if output_price > 0:
                output_cap = min(output_cap, max(0, int(available / output_price)))
            if self.stopped or available <= 0 or output_cap < min(64, int(configured)):
                self.stopped = True
                raise CostBudgetExceeded("剩余目标预算不足以预留下一次模型调用，目标已暂停。")
            reservation = input_cost + output_cap * output_price
            self.reserved += reservation
            return reservation, output_cap

    async def release(self, reservation: float) -> None:
        async with self._lock:
            self.reserved = max(0.0, self.reserved - reservation)

    async def settle(self, reservation: float, messages=None) -> None:
        usages = [message_usage(message) for message in (messages or []) if getattr(message, "type", None) == "ai"]
        known = bool(usages) and all(usage is not None for usage in usages)
        cost = sum(self.estimator.estimate(usage) for usage in usages) if known else reservation
        async with self._lock:
            self.reserved = max(0.0, self.reserved - reservation)
            self.spent += cost
            self.usage_complete = self.usage_complete and known
            if not known or self.spent >= self.limit:
                self.stopped = True
