"""Functional-correctness evaluator: run the model's code against pytest.

The model's response is stripped to code, written to `entry` (default solution.py)
alongside the task's test file and any context files, then pytest runs in an
isolated temp dir with a hard timeout.

SECURITY NOTE: model-generated code is executed. This is a plain subprocess in a
temp directory with a timeout and no explicit network cut-off — adequate for a
trusted benchmarking run on your own machine, NOT a hardened sandbox. For untrusted
scale, wrap the command in Docker / firejail (see runner docstring).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

from . import EvalResult, Evaluator, register
from ..tasks import extract_code


_PYTEST_SUMMARY = re.compile(r"(\d+) (passed|failed|error|errors)")


class UnitTestEvaluator(Evaluator):
    type = "unit_test"

    def evaluate(self, task, completion_text, *, judge_call=None) -> EvalResult:
        spec = task.evaluation
        entry = spec.get("entry", "solution.py")
        test_code = spec.get("tests", "")
        code = extract_code(completion_text, task.language)
        if not code.strip():
            return EvalResult(False, 0.0, detail="No code found in completion.")

        with tempfile.TemporaryDirectory(prefix="maasbench_") as wd:
            # write context files (e.g. buggy module for T3, legacy code for T2)
            for fname, contents in task.context_files.items():
                _write(wd, fname, contents)
            _write(wd, entry, code)
            _write(wd, "test_solution.py", test_code)

            n_total, n_passed, detail = _run_pytest(wd, task.timeout_seconds)

        if n_total == 0:
            # pytest could not collect / crashed on import — treat as fail but keep detail
            return EvalResult(False, 0.0, detail=detail[:4000])
        score = n_passed / n_total
        return EvalResult(
            passed=(n_passed == n_total),
            score=score,
            n_checks=n_total,
            n_passed=n_passed,
            detail=detail[:4000],
        )


def _write(wd: str, relpath: str, contents: str) -> None:
    full = os.path.join(wd, relpath)
    os.makedirs(os.path.dirname(full) or wd, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(contents)


def _run_pytest(wd: str, timeout: int) -> tuple[int, int, str]:
    cmd = [sys.executable, "-m", "pytest", "-q", "--no-header",
           "-p", "no:cacheprovider", "test_solution.py"]
    try:
        proc = subprocess.run(
            cmd, cwd=wd, capture_output=True, text=True,
            timeout=max(5, timeout),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 0, 0, f"TIMEOUT after {timeout}s"
    except FileNotFoundError:
        return 0, 0, "pytest not available in this interpreter"

    passed = failed = errors = 0
    for n, kind in _PYTEST_SUMMARY.findall(out):
        n = int(n)
        if kind == "passed":
            passed = n
        elif kind == "failed":
            failed = n
        elif kind.startswith("error"):
            errors = n
    total = passed + failed + errors
    return total, passed, out


register(UnitTestEvaluator())
