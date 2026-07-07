"""FastAPI backend for the MaaS Coding Benchmark React app.

Reuses the maasbench core. Endpoints under /api; serves the built React app
(web/dist) at / when present, so `python -m server.main` is a single command in prod.
Run dev:  uvicorn server.main:app --reload --port 8000
"""
from __future__ import annotations

import csv
import io
import json
import os

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from .report_html import html_report
from .run_manager import (
    ProviderStore, RunManager, _results_from_rows, config_payload, list_runs, load_run,
)


def _saved_results(payload: dict) -> dict:
    """Recompute derived tables from a saved run's raw rows so historical runs
    always reflect the current scoring/breakdown logic (raw rows are the source of truth)."""
    raw = (payload or {}).get("results", {}).get("raw", [])
    if raw:
        return _results_from_rows(raw)
    return (payload or {}).get("results", {})

app = FastAPI(title="MaaS Coding Benchmark API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

STORE = ProviderStore()
RUN = RunManager(STORE)

WEB_DIST = os.path.join(os.path.dirname(__file__), "..", "web", "dist")


# ---- config & providers ----------------------------------------------------
@app.get("/api/config")
def get_config():
    return config_payload()


@app.get("/api/pricing")
def get_pricing():
    """The $/1M-token price table used for cost — so it's inspectable in the UI."""
    from maasbench.pricing import Pricing
    try:
        return {"models": Pricing.load().table}
    except Exception:  # noqa: BLE001
        return {"models": {}}


@app.get("/api/providers")
def list_providers():
    return {"providers": STORE.public(), "judge": bool(STORE.judge)}


@app.post("/api/providers")
def add_provider(spec: dict = Body(...)):
    STORE.add(spec)
    if spec.get("remember"):
        STORE.persist()
    return {"providers": STORE.public()}


@app.delete("/api/providers/{index}")
def del_provider(index: int, remember: bool = False):
    STORE.remove(index)
    if remember:
        STORE.persist()
    return {"providers": STORE.public()}


@app.post("/api/providers/forget")
def forget_providers():
    STORE.clear()
    return {"providers": STORE.public()}


@app.post("/api/providers/save")
def save_providers():
    STORE.persist()
    return {"ok": True}


@app.post("/api/providers/test")
def test_providers():
    return {"results": STORE.test_all()}


@app.post("/api/judge")
def set_judge(spec: dict = Body(...)):
    STORE.set_judge(spec if spec.get("enabled") else None)
    return {"judge": bool(STORE.judge)}


# ---- run --------------------------------------------------------------------
@app.post("/api/run")
def start_run(cfg: dict = Body(...)):
    try:
        run_id = RUN.start(cfg)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"run_id": run_id, **RUN.state()}


@app.get("/api/run/state")
def run_state():
    return RUN.state()


@app.post("/api/run/stop")
def stop_run():
    RUN.stop()
    return {"ok": True}


@app.get("/api/run/events")
def run_events():
    def gen():
        # replay current state first so a late subscriber isn't stuck at 0
        yield f"data: {json.dumps({'type': 'state', **RUN.state()})}\n\n"
        for item in RUN.events():
            yield f"data: {json.dumps(item)}\n\n"
        yield f"data: {json.dumps({'type': 'closed', **RUN.state()})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---- results ----------------------------------------------------------------
@app.get("/api/results")
def get_results(run_id: str | None = None):
    # explicit run_id -> load from disk; else current in-memory run;
    # else fall back to the most recent persisted run (survives a restart).
    if run_id:
        saved = load_run(run_id)
        return _saved_results(saved) if saved else RUN.results()
    live = RUN.results()
    if live.get("raw"):
        return live
    saved = load_run()
    return _saved_results(saved) if saved else live


@app.get("/api/runs")
def get_runs():
    return {"runs": list_runs()}


def _current_results() -> dict:
    live = RUN.results()
    if live.get("raw"):
        return live
    saved = load_run()
    return _saved_results(saved) if saved else live


@app.get("/api/results/export")
def export_results(fmt: str = "csv", table: str = "raw", run_id: str | None = None):
    saved = load_run(run_id) if run_id else None
    if not saved and not RUN.results().get("raw"):
        saved = load_run()  # no live run — report on the most recent persisted one
    res = _saved_results(saved) if saved else _current_results()
    rows = res.get(table, [])
    if fmt == "html":
        meta = {"run_id": (saved or {}).get("run_id") or RUN.run_id,
                "saved_at": (saved or {}).get("saved_at")}
        return HTMLResponse(html_report(res, meta))
    if fmt == "md":
        return PlainTextResponse(_markdown_report(res), media_type="text/markdown")
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


def _markdown_report(res: dict) -> str:
    s = res.get("summary", {})
    out = ["# Huawei MaaS Coding POC — Benchmark Findings", ""]
    if s:
        out.append(f"- Calls: {s.get('calls')} · pass@1: {s.get('pass_at_1', 0)*100:.0f}% "
                   f"· filter: {s.get('filter_rate', 0)*100:.1f}% "
                   f"· error: {s.get('error_rate', 0)*100:.1f}%")
    out += ["", "## Adoption decision", ""]
    out.append(_md_table(res.get("decision", [])))
    out += ["", "## Composite by model × task class", ""]
    cols = ["task_class", "model", "pass@1", "latency_p95", "cost_per_ok_usd",
            "composite", "verdict"]
    out.append(_md_table(res.get("composite", []), cols))
    return "\n".join(out) + "\n"


def _md_table(rows: list[dict], cols: list[str] | None = None) -> str:
    if not rows:
        return "_(no data)_"
    cols = cols or list(rows[0].keys())
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows]
    return "\n".join([header, sep, *body])


# ---- static React app (prod) ------------------------------------------------
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host="127.0.0.1", port=8000, reload=False)
