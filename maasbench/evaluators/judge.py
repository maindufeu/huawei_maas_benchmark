"""LLM-as-judge evaluator (Framework v1.0 §3, Ref 15).

Scores a response 1-5 on a rubric (correctness, idiomaticity, readability,
security by default). Needs a judge provider callable, injected by the runner as
`judge_call(messages) -> text`. If no judge is configured, the task is skipped
gracefully (score None) rather than failing the whole run.

For production rigor the doc recommends TWO independent judges and dropping tasks
where they disagree by >1 point; that triangulation lives in scoring.py when more
than one judge result is present.
"""
from __future__ import annotations

import json
import re

from . import EvalResult, Evaluator, register


DEFAULT_RUBRIC = ["correctness", "idiomaticity", "readability", "security"]

JUDGE_SYSTEM = (
    "You are a strict senior staff engineer grading another model's answer to a "
    "software task. Score each rubric dimension from 1 (poor) to 5 (excellent). "
    "Be calibrated and critical. Respond ONLY with a JSON object of the form "
    '{\"scores\": {\"<dim>\": <1-5>, ...}, \"pass\": true|false, \"comment\": \"<one line>\"}.'
)


class JudgeEvaluator(Evaluator):
    type = "judge"

    def evaluate(self, task, completion_text, *, judge_call=None) -> EvalResult:
        rubric = task.evaluation.get("rubric", DEFAULT_RUBRIC)
        pass_threshold = float(task.evaluation.get("pass_threshold", 3.5))
        if judge_call is None:
            return EvalResult(False, 0.0, detail="No judge configured (skipped).",
                              quality={"judge": "unavailable"})

        user = (
            f"# Task ({task.task_class} — {task.class_label})\n{task.prompt}\n\n"
            f"# Candidate answer\n{completion_text[:12000]}\n\n"
            f"# Rubric dimensions\n{', '.join(rubric)}\n"
            f"Return the JSON now."
        )
        try:
            raw = judge_call([
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": user},
            ])
        except Exception as e:  # noqa: BLE001
            return EvalResult(False, 0.0, detail=f"Judge error: {e}",
                              quality={"judge": "error"})

        parsed = _parse_judge(raw)
        if parsed is None:
            return EvalResult(False, 0.0, detail=f"Unparseable judge output: {raw[:400]}",
                              quality={"judge": "parse_error"})

        scores = parsed.get("scores", {})
        vals = [float(v) for v in scores.values() if _is_num(v)]
        avg = sum(vals) / len(vals) if vals else 0.0
        passed = bool(parsed.get("pass", avg >= pass_threshold))
        return EvalResult(
            passed=passed,
            score=avg / 5.0,               # normalize to 0..1
            n_checks=len(vals),
            n_passed=sum(1 for v in vals if v >= pass_threshold),
            detail=parsed.get("comment", ""),
            quality={"judge_scores": scores, "judge_avg": round(avg, 2)},
        )


def _parse_judge(raw: str):
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except Exception:  # noqa: BLE001
        return False


register(JudgeEvaluator())
