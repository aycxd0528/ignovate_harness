"""Normalize provider usage and collect all model calls in a child task."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


def message_usage(message) -> dict | None:
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        usage = (getattr(message, "response_metadata", {}) or {}).get("token_usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("input_token_details", {}) or {}
    if not isinstance(details, dict):
        details = {}
    values = {
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
        "cache_hit_tokens": details.get("cache_read", details.get("cache_read_input_tokens", 0))
        or usage.get("cache_hit_tokens", usage.get("prompt_cache_hit_tokens", 0)),
    }
    result = {}
    for key, value in values.items():
        try:
            if key != "cache_hit_tokens" and (
                value is None or isinstance(value, bool)
                or (isinstance(value, float) and not value.is_integer())
                or int(value) < 0
            ):
                return None
            result[key] = max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            if key != "cache_hit_tokens":
                return None
            result[key] = 0
    result["cache_hit_tokens"] = min(result["input_tokens"], result["cache_hit_tokens"])
    result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    metadata=getattr(message,'response_metadata',{}) or {}
    actual=metadata.get('model_name') or metadata.get('model')
    if isinstance(actual,str) and actual: result['model']=actual
    provenance=metadata.get('nailong_call_provenance')
    if isinstance(provenance,dict): result.update(provenance)
    return result


@dataclass
class UsageCollector:
    totals: dict = field(default_factory=lambda: {
        "input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0, "total_tokens": 0,
    })
    complete: bool = True
    attempts: int = 0
    records: list[dict] = field(default_factory=list)

    def observe(self, messages) -> None:
        for message in messages:
            if getattr(message, "type", None) != "ai":
                continue
            usage = message_usage(message)
            if usage is None:
                self.complete = False
                continue
            self.records.append(dict(usage))
            for key in self.totals:
                self.totals[key] += usage[key]


active_usage_collector = ContextVar("nailong_usage_collector", default=None)


@contextmanager
def collect_model_usage(*, reuse: bool = False):
    current = active_usage_collector.get()
    if reuse and current is not None:
        yield current
        return
    collector = UsageCollector()
    token = active_usage_collector.set(collector)
    try:
        yield collector
    finally:
        active_usage_collector.reset(token)
