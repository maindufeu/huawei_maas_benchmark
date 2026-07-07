"""The benchmark runner: fan out (task x model x repetition), grade, collect rows.

One "row" is one completion attempt with everything measured about it. Calls to
DIFFERENT endpoints run in parallel (one worker thread per provider); calls to the
SAME endpoint stay strictly sequential so latency/TTFT numbers are never skewed by
the benchmark competing with itself for that endpoint. The progress callback is
invoked under a lock, so UI consumers (Streamlit / SSE) need no extra locking.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import evaluators
from .pricing import Pricing
from .providers import CallResult, Provider, call_model, is_local
from .tasks import Task


@dataclass
class RunRow:
    task_id: str
    task_class: str
    task_title: str
    language: str
    difficulty: str
    provider: str
    model: str
    vendor: str
    tier: str
    repetition: int
    # call metrics
    status: str
    latency_s: float
    ttft_s: Optional[float]
    tokens_per_second: Optional[float]
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    # evaluation
    passed: bool
    eval_score: float
    n_checks: int
    n_passed: int
    eval_type: str
    # cost
    cost_usd: Optional[float]
    # diagnostics
    error_type: Optional[str] = None
    detail: str = ""
    response_preview: str = ""
    quality: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class RunConfig:
    temperature: float = 0.2
    max_tokens: int = 4096
    repetitions: int = 3
    stream: bool = True
    timeout_s: float = 180.0
    warmup: bool = True          # discard a warm-up call per (provider) before timing
    extra_body: dict = field(default_factory=dict)
    # concurrency: how many DISTINCT endpoints to hit at once.
    # 0 = all providers in parallel (default), 1 = fully sequential (old behavior).
    max_parallel_providers: int = 0
    # focus: task ids that get extra repetitions (e.g. the historically weakest
    # tasks, to tighten their pass-rate estimate without inflating the whole run).
    focus_task_ids: list[str] = field(default_factory=list)
    focus_extra_reps: int = 0


def repetitions_for(task: Task, config: RunConfig) -> int:
    """Repetitions for one task: base + extra when the task is in focus."""
    extra = config.focus_extra_reps if task.id in set(config.focus_task_ids) else 0
    return config.repetitions + max(0, extra)


def planned_total(tasks: list[Task], providers: list[Provider], config: RunConfig) -> int:
    """Total number of graded calls the run will make (excludes warm-ups)."""
    return len(providers) * sum(repetitions_for(t, config) for t in tasks)


def run_benchmark(
    tasks: list[Task],
    providers: list[Provider],
    config: RunConfig,
    pricing: Optional[Pricing] = None,
    judge_provider: Optional[Provider] = None,
    progress: Optional[Callable[[dict], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> list[RunRow]:
    """Execute the full matrix and return one RunRow per completion.

    Providers (distinct endpoints) run concurrently; within one provider the
    task x repetition sequence stays serial. progress(info) is called after
    every attempt with keys: done, total, row. should_stop() cancels mid-run.
    """
    pricing = pricing or _safe_pricing()
    judge_call = _make_judge_call(judge_provider, config) if judge_provider else None

    total = planned_total(tasks, providers, config)
    lock = threading.Lock()
    done = 0
    rows: list[RunRow] = []

    def run_provider(provider: Provider) -> None:
        nonlocal done
        if config.warmup:
            _warmup(provider, config)
        for task in tasks:
            evaluator = _resolve_evaluator(task)
            for rep in range(1, repetitions_for(task, config) + 1):
                if should_stop and should_stop():
                    return
                row = _run_one(task, provider, rep, config, evaluator,
                               pricing, judge_call)
                with lock:
                    rows.append(row)
                    done += 1
                    if progress:
                        progress({"done": done, "total": total, "row": row})

    workers = config.max_parallel_providers or len(providers)
    workers = max(1, min(workers, len(providers)))
    if workers == 1:
        for provider in providers:
            if should_stop and should_stop():
                break
            run_provider(provider)
    else:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="bench") as pool:
            futures = [pool.submit(run_provider, p) for p in providers]
            for f in futures:
                f.result()  # surface unexpected worker crashes

    # deterministic order regardless of thread interleaving
    order = {p.name: i for i, p in enumerate(providers)}
    rows.sort(key=lambda r: (order.get(r.provider, 99), r.task_id, r.repetition))
    return rows


def _run_one(task, provider, rep, config, evaluator, pricing, judge_call) -> RunRow:
    messages = _build_messages(task)
    call: CallResult = call_model(
        provider, messages,
        temperature=config.temperature,
        max_tokens=task.max_tokens or config.max_tokens,
        stream=config.stream,
        timeout=config.timeout_s,
        extra_body=config.extra_body or None,
    )

    passed = False
    score = 0.0
    n_checks = n_passed = 0
    detail = call.error_message or ""
    quality: dict = {}
    eval_type = task.evaluation.get("type", "judge")

    if call.ok:
        try:
            ev = evaluator.evaluate(task, call.text, judge_call=judge_call)
            passed, score = ev.passed, ev.score
            n_checks, n_passed = ev.n_checks, ev.n_passed
            detail, quality = ev.detail, ev.quality
        except Exception as e:  # noqa: BLE001
            detail = f"Evaluator crashed: {e}"

    if is_local(provider.base_url):
        cost = 0.0  # self-hosted (llama.cpp / Ollama): no per-token cost
    else:
        cost = pricing.cost_usd(provider.model, call.prompt_tokens,
                                call.completion_tokens, provider_name=provider.name)

    return RunRow(
        task_id=task.id, task_class=task.task_class, task_title=task.title,
        language=task.language, difficulty=task.difficulty,
        provider=provider.name, model=provider.model, vendor=provider.vendor,
        tier=provider.tier, repetition=rep,
        status=call.status, latency_s=round(call.latency_s, 3),
        ttft_s=round(call.ttft_s, 3) if call.ttft_s is not None else None,
        tokens_per_second=round(call.tokens_per_second, 1) if call.tokens_per_second else None,
        prompt_tokens=call.prompt_tokens, completion_tokens=call.completion_tokens,
        reasoning_tokens=call.reasoning_tokens,
        passed=passed, eval_score=round(score, 4), n_checks=n_checks, n_passed=n_passed,
        eval_type=eval_type,
        cost_usd=round(cost, 6) if cost is not None else None,
        error_type=call.error_type, detail=(detail or "")[:2000],
        response_preview=(call.text or "")[:600],
        quality=quality,
    )


def _build_messages(task: Task) -> list[dict]:
    user = task.prompt
    if task.context_files:
        ctx = "\n\n".join(
            f"# File: {name}\n```\n{content}\n```"
            for name, content in task.context_files.items()
        )
        user = f"{ctx}\n\n---\n\n{task.prompt}"
    return [
        {"role": "system", "content": task.system_prompt},
        {"role": "user", "content": user},
    ]


def _resolve_evaluator(task: Task):
    return evaluators.get_evaluator(task.evaluation.get("type", "judge"))


def _make_judge_call(judge_provider: Provider, config: RunConfig):
    def judge_call(messages: list[dict]) -> str:
        res = call_model(judge_provider, messages, temperature=0.0,
                         max_tokens=1024, stream=False, timeout=config.timeout_s)
        if not res.ok:
            raise RuntimeError(res.error_message or res.status)
        return res.text
    return judge_call


def _warmup(provider: Provider, config: RunConfig) -> None:
    try:
        call_model(provider, [{"role": "user", "content": "ping"}],
                   temperature=0.0, max_tokens=8, stream=False,
                   timeout=min(60.0, config.timeout_s))
    except Exception:  # noqa: BLE001
        pass


def _safe_pricing() -> Pricing:
    try:
        return Pricing.load()
    except Exception:  # noqa: BLE001
        return Pricing({})
