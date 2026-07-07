"""Aggregation + weighted composite scoring + decision matrix.

Turns raw RunRows into the artifacts the Jul-3 review needs:
  * per (model x task_class) aggregates: pass@1, pass@3, latency p50/p95, tokens/s,
    $/successful task, filter/error rate,
  * a composite score in [0,100] using the Framework v1.0 Table 1 weights
    (rescaled to the dimensions actually measured),
  * a semaphore verdict and a per-model adoption recommendation (Table 9).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

import pandas as pd


# Table 1 weights, restricted to dimensions this harness measures automatically.
# (code-quality and dual-judge dims can be folded in once those evaluators run.)
WEIGHTS = {
    "quality": 0.45,     # functional pass rate (Table 1: 30% functional + partial judge)
    "latency": 0.15,
    "cost": 0.25,
    "reliability": 0.15,
}

# Thresholds from Framework v1.0 (Table 1 bands + §1.2 / §9 gates).
COMPOSITE_RECOMMEND = 75
COMPOSITE_CONDITIONAL = 60
P95_LATENCY_GATE_S = 15.0     # streaming p95 target
FILTER_FP_GATE = 0.05         # <5% filter false-positive rate


def rows_to_df(rows) -> pd.DataFrame:
    return pd.DataFrame([r.as_dict() if hasattr(r, "as_dict") else r for r in rows])


def _pct(series, q):
    vals = sorted(v for v in series if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    # 'inclusive' keeps the estimate within [min, max] — the 'exclusive' default
    # extrapolates beyond the observed max for small N (e.g. N=3), which would
    # unfairly trip the p95<15s gate. Report a bounded, honest percentile.
    if q >= 100:
        return float(vals[-1])
    return float(statistics.quantiles(vals, n=100, method="inclusive")[q - 1])


def _ensure_provider_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Back-compat: older runs may lack 'provider'/'vendor'. Derive them so the
    same model from two vendors is never merged into one row."""
    df = df.copy()
    if "provider" not in df.columns:
        df["provider"] = df["model"]
    if "vendor" not in df.columns:
        # derive from a "vendor:model" or "vendor_model" provider name, else "custom"
        def _v(p):
            s = str(p)
            for sep in (":", "_"):
                if sep in s:
                    return s.split(sep)[0]
            return "custom"
        df["vendor"] = df["provider"].map(_v)
    return df


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (provider, task_class) with the headline metrics.

    Grouped by PROVIDER (unique, vendor-tagged) — NOT by model id — so the same
    model served by two vendors (e.g. DeepSeek-V3 on Huawei vs Azure) stays distinct.
    """
    if df.empty:
        return pd.DataFrame()
    df = _ensure_provider_cols(df)
    out = []
    for (provider, tclass), g in df.groupby(["provider", "task_class"]):
        ok = g[g.status == "ok"]
        n_calls = len(g)
        n_ok = len(ok)
        pass_at_1 = _pass_at_1(g)
        pass_at_k = _pass_at_k(g)
        lat = [v for v in ok.latency_s if v is not None]
        ttft = [v for v in ok.ttft_s if v is not None]
        tps = [v for v in ok.tokens_per_second if v is not None]
        cost_per_ok = _cost_per_successful(g)
        filter_rate = (g.error_type == "content_filter").mean() if n_calls else 0.0
        err_rate = (g.status != "ok").mean() if n_calls else 0.0
        # cost transparency: average tokens + per-call cost, so $/ok is explainable
        costs = [c for c in g.cost_usd if c is not None]
        pin = [v for v in ok.prompt_tokens if v is not None]
        pout = [v for v in ok.completion_tokens if v is not None]
        n_pass = int(g.passed.sum())
        out.append({
            "provider": provider, "vendor": g.vendor.iloc[0], "model": g.model.iloc[0],
            "tier": g.tier.iloc[0], "task_class": tclass,
            "n_calls": n_calls, "n_ok": n_ok, "n_pass": n_pass,
            "pass@1": round(pass_at_1, 3), "pass@k": round(pass_at_k, 3),
            "mean_score": round(ok.eval_score.mean(), 3) if n_ok else 0.0,
            "latency_p50": round(statistics.median(lat), 2) if lat else None,
            "latency_p95": round(_pct(lat, 95), 2) if lat else None,
            "ttft_p50": round(statistics.median(ttft), 2) if ttft else None,
            "tokens_per_s": round(statistics.mean(tps), 1) if tps else None,
            "avg_prompt_tokens": round(statistics.mean(pin)) if pin else None,
            "avg_completion_tokens": round(statistics.mean(pout)) if pout else None,
            "cost_per_call_usd": round(statistics.mean(costs), 6) if costs else None,
            "total_cost_usd": round(sum(costs), 6) if costs else None,
            "cost_per_ok_usd": round(cost_per_ok, 5) if cost_per_ok is not None else None,
            "filter_rate": round(filter_rate, 3),
            "error_rate": round(err_rate, 3),
        })
    return pd.DataFrame(out).sort_values(["task_class", "pass@1"], ascending=[True, False])


def _pass_at_1(g: pd.DataFrame) -> float:
    first = g[g.repetition == 1]
    ref = first if len(first) else g
    return ref.passed.mean() if len(ref) else 0.0


def _pass_at_k(g: pd.DataFrame) -> float:
    # per task: did ANY repetition pass? then average across tasks
    per_task = g.groupby("task_id").passed.any()
    return per_task.mean() if len(per_task) else 0.0


def _cost_per_successful(g: pd.DataFrame) -> float | None:
    costs = [c for c in g.cost_usd if c is not None]
    if not costs:
        return None
    n_pass = int(g.passed.sum())
    total_cost = sum(costs)
    if n_pass == 0:
        return None  # undefined — surfaced as "no successes" in the UI
    return total_cost / n_pass


def composite_scores(agg: pd.DataFrame) -> pd.DataFrame:
    """Blend the aggregate into a 0-100 composite per (model, task_class).

    Latency and cost are normalized relative to the best performer in each
    task_class (min-max), so the composite is a within-suite comparison.
    """
    if agg.empty:
        return agg
    rows = []
    for tclass, g in agg.groupby("task_class"):
        best_lat = g.latency_p95.dropna().min()
        worst_lat = g.latency_p95.dropna().max()
        costs = g.cost_per_ok_usd.dropna()
        best_cost, worst_cost = (costs.min(), costs.max()) if len(costs) else (None, None)
        n_models = len(g)
        for _, r in g.iterrows():
            quality = r["pass@1"]  # already 0..1
            latency = _inv_norm(r.latency_p95, best_lat, worst_lat)
            cost = _inv_norm(r.cost_per_ok_usd, best_cost, worst_cost)
            reliability = 1.0 - min(1.0, r.error_rate + r.filter_rate)
            scores = {"quality": quality, "cost": cost, "latency": latency,
                      "reliability": reliability}
            pts = {k: round(WEIGHTS[k] * v * 100, 1) for k, v in scores.items()}
            comp = round(sum(pts.values()), 1)
            gates = _gate_flags(r)
            rows.append({
                **r.to_dict(),
                "composite": comp,
                "verdict": _verdict(comp, gates),
                # per-component breakdown (0..1 normalized sub-scores)
                "score_quality": round(quality, 3),
                "score_cost": round(cost, 3),
                "score_latency": round(latency, 3),
                "score_reliability": round(reliability, 3),
                # weighted contributions to the 0..100 composite
                "pts_quality": pts["quality"],
                "pts_cost": pts["cost"],
                "pts_latency": pts["latency"],
                "pts_reliability": pts["reliability"],
                "gate_p95_fail": gates["p95"],
                "gate_filter_fail": gates["filter"],
                "cost_relative": n_models > 1,  # cost/latency are relative only vs peers
                "why": _reason(r, scores, pts, gates, n_models),
            })
    return pd.DataFrame(rows).sort_values(["task_class", "composite"], ascending=[True, False])


def _gate_flags(r) -> dict:
    return {
        "p95": r.latency_p95 is not None and r.latency_p95 > P95_LATENCY_GATE_S,
        "filter": r.filter_rate > FILTER_FP_GATE,
    }


# max points each dimension can contribute (weight × 100)
_MAX_PTS = {"quality": 45.0, "cost": 25.0, "latency": 15.0, "reliability": 15.0}
_DIM_LABEL = {"quality": "quality (pass@1)", "cost": "cost/task",
              "latency": "latency p95", "reliability": "reliability"}


def _reason(r, scores: dict, pts: dict, gates: dict, n_models: int) -> str:
    """One-line, human explanation of what drove (and capped) the score."""
    parts: list[str] = []
    # what gate (if any) caps the verdict regardless of composite
    caps = []
    if gates["p95"]:
        caps.append(f"p95={r.latency_p95:.0f}s > {P95_LATENCY_GATE_S:.0f}s gate")
    if gates["filter"]:
        caps.append(f"filter={r.filter_rate*100:.0f}% > {int(FILTER_FP_GATE*100)}% gate")
    if caps:
        parts.append("Capped at 🟡 by " + " & ".join(caps) + ".")
    # biggest point losses vs the maximum each dimension could give
    lost = sorted(((k, round(_MAX_PTS[k] - pts[k], 1)) for k in pts),
                  key=lambda x: x[1], reverse=True)
    top = [f"{_DIM_LABEL[k]} (−{v} of {_MAX_PTS[k]:.0f})" for k, v in lost if v >= 3][:2]
    if top:
        parts.append("Main drag: " + ", ".join(top) + ".")
    else:
        parts.append("Strong across all dimensions.")
    # honesty flags about relative normalization
    if n_models > 1 and (scores["cost"] in (0.0, 1.0) or scores["latency"] in (0.0, 1.0)):
        parts.append("(cost/latency scored relative to peers in this class.)")
    elif n_models == 1:
        parts.append("(only model in class → cost & latency default to full marks; not comparative.)")
    return " ".join(parts)


def _inv_norm(val, best, worst) -> float:
    """Lower-is-better -> 1.0 for best, 0.0 for worst. Missing -> neutral 0.5."""
    if val is None or best is None or worst is None:
        return 0.5
    if worst == best:
        return 1.0
    return max(0.0, min(1.0, (worst - val) / (worst - best)))


def _verdict(comp: float, gates: dict) -> str:
    gate_fail = []
    if gates["p95"]:
        gate_fail.append(f"p95>{P95_LATENCY_GATE_S}s")
    if gates["filter"]:
        gate_fail.append(f"filter>{int(FILTER_FP_GATE*100)}%")
    if comp >= COMPOSITE_RECOMMEND and not gate_fail:
        return "🟢 recommended"
    if comp >= COMPOSITE_CONDITIONAL:
        tag = " (" + ", ".join(gate_fail) + ")" if gate_fail else ""
        return f"🟡 conditional{tag}"
    return "🔴 disqualified"


def decision_matrix(comp: pd.DataFrame, baseline_model: str | None = None) -> pd.DataFrame:
    """Per-model adoption call across task classes (Framework v1.0 Table 9)."""
    if comp.empty:
        return comp
    rows = []
    for provider, g in comp.groupby("provider"):
        wins = int((g.verdict.str.startswith("🟢")).sum())
        conditional = int((g.verdict.str.startswith("🟡")).sum())
        classes = len(g)
        mean_comp = round(g.composite.mean(), 1)
        if wins >= 3:
            rec = "Adopt broadly"
        elif wins + conditional >= 1 and wins >= 1:
            rec = "Adopt for niches"
        elif (g.latency_p95.dropna() > P95_LATENCY_GATE_S).any() or (g.filter_rate > FILTER_FP_GATE).any():
            rec = "Defer (latency/filter gate)"
        else:
            rec = "Reject / keep as low-cost alt"
        rows.append({"provider": provider, "vendor": g.vendor.iloc[0],
                     "model": g.model.iloc[0], "tier": g.tier.iloc[0],
                     "classes_tested": classes, "green": wins,
                     "conditional": conditional, "mean_composite": mean_comp,
                     "recommendation": rec})
    return pd.DataFrame(rows).sort_values("mean_composite", ascending=False)
