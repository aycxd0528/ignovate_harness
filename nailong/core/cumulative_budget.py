"""Cumulative model token accounting, independent of turn and child allowances.

One factory-owned instance must be reused for every call in a session. Set
``active_session_token_budget`` around the session so children inherit it. A
persistent owner holds its ledger lease until ``close()``; a new owner cannot
reset a live session or silently change its ceiling.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from nailong.core.budgets import TokenBudgetExceeded, _input_bound
from nailong.core.file_locks import file_lock
from nailong.core.preferences import safe_config_path
from nailong.core.safe_files import atomic_write_bytes, open_regular_file
from nailong.core.usage import message_usage


active_session_token_budget = ContextVar("nailong_session_token_budget", default=None)


class SessionTokenBudgetExceeded(TokenBudgetExceeded):
    """The cumulative session allowance cannot safely fund another model call."""


@dataclass(frozen=True)
class SessionTokenReservation:
    """An opaque, single-use hold returned by ``SessionTokenBudget.reserve``."""

    amount: int
    key: str
    owner: str


def _positive_integer(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class SessionTokenBudget:
    """Reserve complete requests against a ceiling that persists across turns.

    ``output_tokens`` is an optional per-call allowance, separate from ``tokens``.
    It supplies an absent model output default and rejects a larger configured
    output; it never silently cuts the requested cap. Ordinarily leave it unset
    and reserve the cap already prepared by context middleware.

    ``release`` is only for requests never sent to a provider. Once a provider
    attempt starts, settle its hold even after cancellation or an exception;
    absent complete usage is charged conservatively and stops further calls.
    The caller owns cancellation-safe settlement and instance lifecycle.
    """

    def __init__(self, tokens: int, *, output_tokens: int | None = None,
                 ledger_path: str | Path | None = None,
                 ledger_boundary: str | Path | None = None, record=None):
        self.limit = _positive_integer(tokens, "session token limit")
        self.output_tokens = (_positive_integer(output_tokens, "output allowance")
                              if output_tokens is not None else None)
        self.spent = 0
        self.reserved = 0
        self.stopped = False
        self.stop_reason = None
        self.record = record
        self._lock = asyncio.Lock()
        self._holds: dict[str, SessionTokenReservation] = {}
        self._owner = secrets.token_hex(16)
        self._closed = False
        self._lease = None
        self.ledger_path = None
        self.ledger_boundary = None
        if ledger_path is None:
            if ledger_boundary is not None:
                raise ValueError("ledger boundary requires a ledger path")
            return
        original = Path(ledger_path).expanduser().absolute()
        boundary = Path(ledger_boundary).expanduser().absolute() if ledger_boundary is not None else original.parent
        self.ledger_path = safe_config_path(original, boundary)
        self.ledger_boundary = boundary
        lease = file_lock(self.ledger_path.with_name(self.ledger_path.name + ".lock"), blocking=False)
        try:
            lease.__enter__()
        except BlockingIOError as error:
            raise ValueError("the session token ledger is already owned by another runtime") from error
        self._lease = lease
        try:
            self._restore()
            self._write_ledger()
        except BaseException:
            self.close()
            raise

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent - self.reserved)

    def diagnostics(self, *, inputs=0, output=0, cap=0, reason=None) -> dict:
        return {
            "scope": "session", "limit_tokens": self.limit,
            "spent_tokens": self.spent, "reserved_tokens": self.reserved,
            "remaining_tokens": self.remaining, "input_tokens_upper_bound": inputs,
            "output_tokens_requested": output, "output_tokens_reserved": cap,
            "reason": reason or self.stop_reason, "input_method": "utf8_upper_bound",
        }

    def _record(self, event: str, *, details=None, **values) -> None:
        if self.record is not None:
            try:
                self.record({"event": event, **self.diagnostics(**values), **(details or {})})
            except Exception:
                # Event logging cannot bypass accounting or strand a valid hold.
                pass

    def _refuse(self, *, inputs=0, output=0, reason=None) -> None:
        reason = "budget_closed" if self._closed else self.stop_reason or reason or "input_does_not_fit"
        self._record("refused", inputs=inputs, output=output, reason=reason)
        detail = "提供商用量未知，已保守扣除并停止后续模型调用。" if reason == "usage_unknown" else "未发起新的模型调用。"
        raise SessionTokenBudgetExceeded(
            "会话累计 token 预算不足或无法安全继续。"
            f"输入上界 {inputs}，完整输出预留 {output}；"
            f"会话额度 {self.limit}，已用 {self.spent}，在途预留 {self.reserved}，余额 {self.remaining}。"
            f"{detail}", diagnostics=self.diagnostics(inputs=inputs, output=output, reason=reason),
        )

    def _configured_output(self, request) -> int:
        if "max_tokens" in request.model_settings:
            configured = request.model_settings["max_tokens"]
        else:
            configured = getattr(request.model, "max_tokens", None)
            if configured is None:
                configured = self.output_tokens if self.output_tokens is not None else 4096
        return _positive_integer(configured, "configured output allowance")

    async def reserve(self, request) -> tuple[SessionTokenReservation, int]:
        inputs = _input_bound(request)
        output = self._configured_output(request)
        async with self._lock:
            if self._closed or self.stopped:
                self._refuse(inputs=inputs, output=output)
            if self.output_tokens is not None and output > self.output_tokens:
                self._refuse(inputs=inputs, output=output, reason="output_allowance_exceeded")
            amount = inputs + output
            if amount > self.remaining:
                reason = "concurrent_reservations" if amount <= self.limit - self.spent else "input_does_not_fit"
                self._refuse(inputs=inputs, output=output, reason=reason)
            hold = SessionTokenReservation(amount, secrets.token_hex(16), self._owner)
            self._holds[hold.key] = hold
            self.reserved += amount
            self._persist()
            self._record("reserved", inputs=inputs, output=output, cap=output)
            return hold, output

    def _current_hold(self, hold: SessionTokenReservation) -> bool:
        if not isinstance(hold, SessionTokenReservation) or hold.owner != self._owner:
            raise ValueError("reservation belongs to another session token budget")
        return self._holds.get(hold.key) is hold

    async def release(self, hold: SessionTokenReservation) -> None:
        """Release a never-sent request, without refunding any settled usage."""
        async with self._lock:
            if self._closed:
                self._refuse()
            if not self._current_hold(hold):
                return
            del self._holds[hold.key]
            self.reserved -= hold.amount
            self._persist()
            self._record("released", details={"reservation_tokens": hold.amount})

    async def settle(self, hold: SessionTokenReservation, messages=None) -> None:
        usages = [message_usage(message) for message in (messages or [])
                  if getattr(message, "type", None) == "ai"]
        complete = bool(usages) and all(usage is not None for usage in usages)
        known_total = sum(usage["total_tokens"] for usage in usages if usage is not None)
        async with self._lock:
            if self._closed:
                self._refuse()
            if not self._current_hold(hold):
                return
            charged = known_total if complete else max(hold.amount, known_total)
            del self._holds[hold.key]
            self.reserved -= hold.amount
            self.spent += charged
            reason = ("usage_unknown" if not complete else
                      "usage_exceeds_reservation" if charged > hold.amount else
                      "token_limit" if self.spent >= self.limit else None)
            if reason is not None:
                self.stopped = True
                self.stop_reason = self.stop_reason or reason
            self._persist()
            self._record("settled", details={"reservation_tokens": hold.amount,
                "charged_tokens": charged, "usage_complete": complete})

    def _restore(self) -> None:
        try:
            with open_regular_file(self.ledger_path) as stream:
                raw = stream.read(1024 * 1024 + 1)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            raise ValueError("session token ledger cannot be safely read") from error
        if len(raw) > 1024 * 1024:
            raise ValueError("session token ledger exceeds 1 MiB")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("session token ledger is corrupt") from error
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("unsupported session token ledger schema")
        persisted_limit = _positive_integer(data.get("limit_tokens"), "persisted token limit")
        if persisted_limit != self.limit:
            raise ValueError("configured session token limit differs from its existing ledger")
        spent, pending = data.get("spent_tokens"), data.get("pending")
        stopped, reason = data.get("stopped"), data.get("stop_reason")
        if isinstance(spent, bool) or not isinstance(spent, int) or spent < 0 or not isinstance(pending, dict):
            raise ValueError("invalid session token ledger accounting")
        if (type(stopped) is not bool or (reason is not None and not isinstance(reason, str)) or
                (not stopped and reason is not None) or
                (stopped and reason not in {"usage_unknown", "usage_exceeds_reservation", "token_limit", "ledger_write_failed"}) or
                (spent >= self.limit and not stopped)):
            raise ValueError("invalid session token ledger stop state")
        pending_total = 0
        for key, amount in pending.items():
            if not isinstance(key, str) or not key:
                raise ValueError("invalid persisted reservation key")
            pending_total += _positive_integer(amount, "persisted reservation")
        self.spent = spent + pending_total
        self.stopped = stopped or bool(pending)
        self.stop_reason = "usage_unknown" if pending else reason

    def _write_ledger(self) -> None:
        if self.ledger_path is None:
            return
        safe_config_path(self.ledger_path, self.ledger_boundary)
        data = {"schema_version": 1, "limit_tokens": self.limit,
                "spent_tokens": self.spent,
                "pending": {key: hold.amount for key, hold in self._holds.items()},
                "stopped": self.stopped, "stop_reason": self.stop_reason}
        encoded = (json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        atomic_write_bytes(self.ledger_path, encoded)

    def _persist(self) -> None:
        try:
            self._write_ledger()
        except (OSError, ValueError) as error:
            self.stopped = True
            self.stop_reason = "ledger_write_failed"
            try:
                self._refuse(reason="ledger_write_failed")
            except SessionTokenBudgetExceeded as failure:
                raise failure from error

    def close(self) -> None:
        """Release ledger ownership; outstanding holds remain durable and uncertain."""
        if self._closed:
            return
        self._closed = True
        lease, self._lease = self._lease, None
        if lease is not None:
            lease.__exit__(None, None, None)
