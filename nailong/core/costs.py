"""Conservative token-based cost estimates using peak provider rates."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: float
    cache_hit_per_million: float
    output_per_million: float


_PEAK_RATES = {
    "deepseek-flash": ModelPrice(0.30, 0.006, 1.20),
    "deepseek-v4-flash": ModelPrice(0.30, 0.006, 1.20),
    "deepseek-v4-pro": ModelPrice(1.32, 0.044, 3.96),
}


class CostEstimator:
    """Use current peak rates as an upper-bound estimate; custom models can be configured."""

    def __init__(self, model: str, project_root: str | Path):
        self.model = model.strip()
        self.project_root = Path(project_root).resolve()
        self.source = "unknown"
        self.configuration_error = ""
        configured = self._configured_price()
        self.price = None if self.configuration_error else configured or _PEAK_RATES.get(self.model.casefold())
        if self.price is not None and configured is None:
            self.source = "builtin"

    def _configured_price(self) -> ModelPrice | None:
        path = self.project_root / ".nailong" / "settings.json"
        try:
            from nailong.core.preferences import safe_config_path
            resolved = safe_config_path(path, self.project_root).resolve(strict=True)
            if not resolved.is_relative_to(self.project_root):
                return None
            payload = json.loads(resolved.read_text(encoding="utf-8"))
            pricing = payload.get("pricing", {}) if isinstance(payload, dict) else {}
            values = pricing.get(self.model, {}) if isinstance(pricing, dict) else {}
            if not isinstance(values, dict):
                return None
            if not values:
                return None
            if any(isinstance(values.get(key), bool) for key in ("input_per_million", "cache_hit_per_million", "output_per_million")):
                self.configuration_error = "模型单价不能是布尔值。"
                return None
            rates = [float(values[key]) for key in ("input_per_million", "cache_hit_per_million", "output_per_million")]
            if any(not math.isfinite(value) or value < 0 for value in rates):
                self.configuration_error = "模型单价必须为有限非负数。"
                return None
            self.source = str(path)
            return ModelPrice(
                *rates,
            )
        except (KeyError, TypeError, ValueError):
            self.configuration_error = "模型价格配置无效或缺少字段。"
            return None
        except (OSError, UnicodeError, RuntimeError):
            return None

    @property
    def available(self) -> bool:
        return self.price is not None

    def snapshot(self) -> dict:
        """Keep prices attached to a call instead of re-pricing old usage."""
        return {
            "model": self.model, "source": self.source,
            **{key: getattr(self.price, key) if self.price is not None else None
               for key in ("input_per_million", "cache_hit_per_million", "output_per_million")},
        }

    def estimate(self, usage: dict[str, int | float]) -> float:
        if self.price is None:
            raise ValueError(f"模型 {self.model!r} 没有可用价格；请在 .nailong/settings.json 的 pricing 中配置。")
        input_tokens = max(0, int(usage.get("input_tokens", 0) or 0))
        output_tokens = max(0, int(usage.get("output_tokens", 0) or 0))
        cache_hit = min(input_tokens, max(0, int(usage.get("cache_hit_tokens", 0) or 0)))
        input_cost = (input_tokens - cache_hit) * self.price.input_per_million
        cache_cost = cache_hit * self.price.cache_hit_per_million
        output_cost = output_tokens * self.price.output_per_million
        return (input_cost + cache_cost + output_cost) / 1_000_000
