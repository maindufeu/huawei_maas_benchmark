"""Pluggable evaluator registry.

Add a new evaluation type by subclassing Evaluator and calling register().
The runner looks up `task.evaluation["type"]` here — nothing else needs to change.
This is the "integrate these and other evaluations" extension point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class EvalResult:
    passed: bool                    # did the task meet its success bar?
    score: float                    # 0..1 (fraction of tests / rubric score / etc.)
    n_checks: int = 0
    n_passed: int = 0
    detail: str = ""
    quality: dict = field(default_factory=dict)  # optional extra metrics (lint, complexity, judge)


class Evaluator:
    """Base class. Implement evaluate()."""

    type: str = "base"

    def evaluate(self, task, completion_text: str, *, judge_call=None) -> EvalResult:  # noqa: ANN001
        raise NotImplementedError


_REGISTRY: dict[str, Evaluator] = {}


def register(evaluator: Evaluator) -> Evaluator:
    _REGISTRY[evaluator.type] = evaluator
    return evaluator


def get_evaluator(type_: str) -> Evaluator:
    if type_ not in _REGISTRY:
        raise KeyError(f"No evaluator registered for type '{type_}'. "
                       f"Available: {sorted(_REGISTRY)}")
    return _REGISTRY[type_]


def available() -> list[str]:
    return sorted(_REGISTRY)


# Import built-ins so they self-register on package import.
from . import unit_test as _unit_test  # noqa: E402,F401
from . import checks as _checks        # noqa: E402,F401
from . import judge as _judge          # noqa: E402,F401
