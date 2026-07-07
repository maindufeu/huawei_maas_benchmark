"""Cheap deterministic evaluators: substring / regex / JSON-format checks.

Useful for format-compliance and quick smoke tests where full unit tests are
overkill (e.g. "does the diff apply", "is the answer valid JSON with keys X").
"""
from __future__ import annotations

import json
import re

from . import EvalResult, Evaluator, register


class ContainsEvaluator(Evaluator):
    """Pass if all `must_include` present and no `must_exclude` present."""

    type = "contains"

    def evaluate(self, task, completion_text, *, judge_call=None) -> EvalResult:
        spec = task.evaluation
        text = completion_text if spec.get("case_sensitive") else completion_text.lower()
        inc = spec.get("must_include", [])
        exc = spec.get("must_exclude", [])
        norm = (lambda s: s) if spec.get("case_sensitive") else (lambda s: s.lower())

        checks = []
        for token in inc:
            checks.append((f"include:{token}", norm(token) in text))
        for token in exc:
            checks.append((f"exclude:{token}", norm(token) not in text))

        n_passed = sum(1 for _, ok in checks if ok)
        n = len(checks)
        detail = "\n".join(f"[{'ok' if ok else 'FAIL'}] {name}" for name, ok in checks)
        return EvalResult(n_passed == n and n > 0, (n_passed / n) if n else 0.0,
                          n, n_passed, detail)


class JsonSchemaEvaluator(Evaluator):
    """Pass if the response contains a JSON object with the required keys."""

    type = "json_keys"

    def evaluate(self, task, completion_text, *, judge_call=None) -> EvalResult:
        required = task.evaluation.get("required_keys", [])
        obj = _first_json_object(completion_text)
        if obj is None:
            return EvalResult(False, 0.0, len(required), 0, "No valid JSON object found.")
        present = [k for k in required if k in obj]
        n = len(required)
        detail = f"found keys: {list(obj)[:20]}"
        return EvalResult(len(present) == n and n > 0, (len(present) / n) if n else 0.0,
                          n, len(present), detail)


def _first_json_object(text: str):
    # try fenced ```json first, then any {...} span
    m = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
    candidates = [m.group(1)] if m else []
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for c in candidates:
        try:
            v = json.loads(c)
            if isinstance(v, dict):
                return v
        except Exception:  # noqa: BLE001
            continue
    return None


register(ContainsEvaluator())
register(JsonSchemaEvaluator())
