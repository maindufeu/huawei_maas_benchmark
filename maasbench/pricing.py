"""Price table and cost helpers ($ per 1M tokens -> $ per call / per successful task)."""
from __future__ import annotations

import os

import yaml

_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "pricing.yaml")


class Pricing:
    def __init__(self, table: dict[str, dict]):
        # normalize keys to lowercase for tolerant matching
        self.table = {k.lower(): v for k, v in table.items()}

    @classmethod
    def load(cls, path: str = _DEFAULT_PATH) -> "Pricing":
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(data.get("models", {}))

    def rate(self, model: str, provider_name: str | None = None) -> dict | None:
        # vendor-tagged provider key ("azure:deepseek-v3.2") wins over the plain
        # model key, so the same model on two vendors can carry two prices
        if provider_name and provider_name.lower() in self.table:
            return self.table[provider_name.lower()]
        key = model.lower()
        if key in self.table:
            return self.table[key]
        # substring-tolerant match (e.g. "glm-5.2-preview" -> "glm-5.2")
        for k, v in self.table.items():
            if k in key or key in k:
                return v
        return None

    def cost_usd(self, model: str, prompt_tokens: int, completion_tokens: int,
                 provider_name: str | None = None) -> float | None:
        r = self.rate(model, provider_name)
        if not r:
            return None
        return (prompt_tokens * r.get("input", 0.0)
                + completion_tokens * r.get("output", 0.0)) / 1_000_000
