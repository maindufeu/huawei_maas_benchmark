"""Stateful run manager + provider store for the FastAPI backend.

Wraps the UI-agnostic maasbench core: holds the configured providers (reusing the
same local secrets store), runs one benchmark at a time in a background thread, and
exposes a progress stream (consumed as SSE) plus computed results.
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import queue
import threading
import uuid
from dataclasses import asdict
from typing import Optional

import pandas as pd

from maasbench import secrets_store
from maasbench.pricing import Pricing
from maasbench.providers import (
    HUAWEI_MODELS, Provider, call_model, is_local,
)
from maasbench.runner import RunConfig, planned_total, run_benchmark
from maasbench.scoring import (
    P95_LATENCY_GATE_S, aggregate, composite_scores, decision_matrix, rows_to_df,
)
from maasbench.tasks import CLASS_LABELS, load_tasks

TASKS_DIR = "tasks"
RESULTS_DIR = "results"


def _clean_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of dicts with NaN/NaT replaced by None (JSON-safe)."""
    if df is None or df.empty:
        return []
    return df.astype(object).where(pd.notna(df), None).to_dict(orient="records")


class ProviderStore:
    def __init__(self):
        self.providers: list[Provider] = []
        self.judge: Optional[Provider] = None
        loaded, judge = secrets_store.load()
        self.providers = loaded
        self.judge = judge

    def public(self) -> list[dict]:
        out = []
        for i, p in enumerate(self.providers):
            d = p.public()
            d["index"] = i
            d["is_local"] = is_local(p.base_url)
            d["has_key"] = bool(p.api_key and p.api_key != "EMPTY")
            out.append(d)
        return out

    # preset type -> (vendor tag, short label prefix)
    _VENDORS = {
        "Huawei MaaS": "huawei",
        "Azure OpenAI": "azure",
        "Google Gemini": "google",
        "Local (llama.cpp)": "local",
        "Custom (OpenAI-compatible)": "custom",
    }

    def add(self, spec: dict) -> None:
        ptype = spec.get("type", "Custom (OpenAI-compatible)")
        url = spec["base_url"].strip()
        key = (spec.get("api_key") or "").strip()
        tier = spec.get("tier", "frontier")
        effort = spec.get("reasoning_effort")
        extra = {"reasoning_effort": effort} if effort and effort != "(none)" else {}
        models = spec.get("models") or [spec.get("model")]
        vendor = self._VENDORS.get(ptype, "custom")
        thw = {m: t for m, t in HUAWEI_MODELS}
        for m in models:
            if not m:
                continue
            # vendor-tagged name so the SAME model from two vendors stays distinct
            name = f"{vendor}:{m}"
            t = thw.get(m, tier) if ptype == "Huawei MaaS" else tier
            self.providers.append(Provider(name=name, base_url=url, model=m,
                                           api_key=key or "EMPTY", tier=t,
                                           vendor=vendor, extra_body=dict(extra)))

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.providers):
            self.providers.pop(index)

    def clear(self) -> None:
        self.providers = []
        self.judge = None
        secrets_store.clear()

    def persist(self) -> None:
        secrets_store.save(self.providers, self.judge)

    def set_judge(self, spec: Optional[dict]) -> None:
        if not spec or not spec.get("api_key"):
            self.judge = None
            return
        self.judge = Provider(name="judge", base_url=spec["base_url"],
                              model=spec["model"], api_key=spec["api_key"])

    def test_all(self) -> list[dict]:
        results = []
        for p in self.providers:
            r = call_model(p, [{"role": "user", "content": "ping"}],
                           max_tokens=8, stream=False, timeout=30)
            results.append({
                "name": p.name, "model": p.model, "ok": r.ok, "status": r.status,
                "latency_s": round(r.latency_s, 2), "error_type": r.error_type,
                "error_message": (r.error_message or "")[:200],
            })
        return results


class RunManager:
    def __init__(self, store: ProviderStore):
        self.store = store
        self.lock = threading.Lock()
        self.run_id: Optional[str] = None
        self.status = "idle"           # idle | running | done | stopped | error
        self.done = 0
        self.total = 0
        self.rows = []
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[str] = None

    # -- lifecycle --
    def start(self, cfg: dict) -> str:
        with self.lock:
            if self.status == "running":
                raise RuntimeError("A run is already in progress.")
            self.run_id = uuid.uuid4().hex[:12]
            self.status = "running"
            self.done = 0
            self.total = 0
            self.rows = []
            self._error = None
            self._stop = False
            self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, args=(cfg,), daemon=True)
        self._thread.start()
        return self.run_id

    def stop(self) -> None:
        self._stop = True

    def _run(self, cfg: dict) -> None:
        try:
            tasks = load_tasks(TASKS_DIR)
            classes = set(cfg.get("task_classes") or [])
            if classes:
                tasks = [t for t in tasks if t.task_class in classes]
            if cfg.get("only_objective"):
                tasks = [t for t in tasks if t.evaluation.get("type") != "judge"]

            run_cfg = RunConfig(
                temperature=float(cfg.get("temperature", 0.2)),
                max_tokens=int(cfg.get("max_tokens", 4096)),
                repetitions=int(cfg.get("repetitions", 3)),
                stream=bool(cfg.get("stream", True)),
                warmup=bool(cfg.get("warmup", True)),
                timeout_s=float(cfg.get("timeout_s", 180)),
                max_parallel_providers=int(cfg.get("max_parallel", 0)),
                focus_task_ids=list(cfg.get("focus_tasks") or []),
                focus_extra_reps=int(cfg.get("focus_extra_reps", 0)),
            )
            self.total = planned_total(tasks, self.store.providers, run_cfg)
            if not tasks or not self.store.providers:
                raise RuntimeError("No tasks or no providers selected.")

            os.makedirs(RESULTS_DIR, exist_ok=True)
            rows_path = os.path.join(RESULTS_DIR, f"run_{self.run_id}.rows.jsonl")
            open(rows_path, "w").close()  # truncate any stale partial

            def progress(info):
                r = info["row"]
                self.done = info["done"]
                # crash-safe: append each completed row immediately
                try:
                    with open(rows_path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(r.as_dict(), default=str) + "\n")
                except Exception:  # noqa: BLE001
                    pass
                self._queue.put({
                    "type": "progress", "done": info["done"], "total": info["total"],
                    "task_id": r.task_id, "task_class": r.task_class,
                    "model": r.model, "status": r.status, "passed": r.passed,
                    "latency_s": r.latency_s, "eval_score": r.eval_score,
                    "error_type": r.error_type,
                })

            rows = run_benchmark(
                tasks, self.store.providers, run_cfg,
                pricing=self._pricing(),
                judge_provider=self.store.judge if not cfg.get("only_objective") else None,
                progress=progress, should_stop=lambda: self._stop,
            )
            self.rows = rows
            self.status = "stopped" if self._stop else "done"
            self._queue.put({"type": "done", "status": self.status, "count": len(rows)})
        except Exception as e:  # noqa: BLE001
            self._error = str(e)
            self.status = "error"
            self._queue.put({"type": "error", "message": str(e)})
        finally:
            self._persist()
            self._queue.put({"type": "_end"})

    def _persist(self) -> None:
        """Write a self-contained results snapshot for this run to disk."""
        try:
            os.makedirs(RESULTS_DIR, exist_ok=True)
            rows = self.rows
            if not rows and self.run_id:  # crashed mid-run — recover partial rows
                rows = _rows_from_jsonl(
                    os.path.join(RESULTS_DIR, f"run_{self.run_id}.rows.jsonl"))
            payload = {
                "run_id": self.run_id,
                "status": self.status,
                "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "results": _results_from_rows(rows),
            }
            with open(os.path.join(RESULTS_DIR, f"run_{self.run_id}.json"),
                      "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except Exception:  # noqa: BLE001
            pass

    def events(self):
        """Blocking generator of progress dicts until the run ends."""
        while True:
            item = self._queue.get()
            if item.get("type") == "_end":
                break
            yield item

    # -- results --
    def state(self) -> dict:
        return {"run_id": self.run_id, "status": self.status,
                "done": self.done, "total": self.total, "error": self._error}

    def results(self) -> dict:
        return _results_from_rows(self.rows)

    @staticmethod
    def _pricing() -> Pricing:
        try:
            return Pricing.load()
        except Exception:  # noqa: BLE001
            return Pricing({})


def _results_from_rows(rows) -> dict:
    """Build the full results payload (summary + tables) from RunRows or dicts."""
    df = rows_to_df(rows)
    if df.empty:
        return {"summary": {}, "raw": [], "aggregate": [], "composite": [], "decision": []}
    agg = aggregate(df)
    comp = composite_scores(agg)
    dm = decision_matrix(comp)
    summary = {
        "calls": int(len(df)),
        "pass_at_1": float(df[df.repetition == 1].passed.mean()) if len(df) else 0.0,
        "filter_rate": float((df.error_type == "content_filter").mean()),
        "error_rate": float((df.status != "ok").mean()),
        "p95_gate": P95_LATENCY_GATE_S,
    }
    return {
        "summary": summary,
        "raw": _clean_records(df),
        "aggregate": _clean_records(agg),
        "composite": _clean_records(comp),
        "decision": _clean_records(dm),
    }


def _rows_from_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def list_runs() -> list[dict]:
    """Metadata for every persisted run, newest first."""
    out = []
    for f in glob.glob(os.path.join(RESULTS_DIR, "run_*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                p = json.load(fh)
            if not p.get("run_id") or "results" not in p:
                continue  # skip legacy/malformed dumps
            s = p.get("results", {}).get("summary", {})
            out.append({
                "run_id": p.get("run_id"), "status": p.get("status"),
                "saved_at": p.get("saved_at"), "file": os.path.basename(f),
                "calls": s.get("calls", 0), "pass_at_1": s.get("pass_at_1"),
                "mtime": os.path.getmtime(f),
            })
        except Exception:  # noqa: BLE001
            continue
    return sorted(out, key=lambda r: r["mtime"], reverse=True)


def load_run(run_id: Optional[str] = None) -> Optional[dict]:
    """Load a persisted run's results by id, or the most recent if id is None."""
    if run_id:
        path = os.path.join(RESULTS_DIR, f"run_{run_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    runs = list_runs()
    if not runs:
        return None
    with open(os.path.join(RESULTS_DIR, runs[0]["file"]), "r", encoding="utf-8") as fh:
        return json.load(fh)


def config_payload() -> dict:
    tasks = load_tasks(TASKS_DIR)
    return {
        "classes": [{"id": c, "label": CLASS_LABELS.get(c, c)}
                    for c in sorted({t.task_class for t in tasks})],
        "tasks": [{"id": t.id, "task_class": t.task_class, "title": t.title,
                   "language": t.language, "eval_type": t.evaluation.get("type"),
                   "difficulty": t.difficulty} for t in tasks],
        "huawei_models": [{"model": m, "tier": t} for m, t in HUAWEI_MODELS],
        "p95_gate": P95_LATENCY_GATE_S,
    }
