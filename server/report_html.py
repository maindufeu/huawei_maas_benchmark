"""Self-contained, print-ready HTML report (A4) with inline SVG charts.

Consumes the same results payload the API serves (summary / raw / aggregate /
composite / decision) and returns one HTML string with zero external assets,
so it can be saved, mailed, or printed straight from the browser (Ctrl+P).
"""
from __future__ import annotations

import datetime
import html
import math
from collections import defaultdict

PALETTE = ["#2563eb", "#db2777", "#059669", "#d97706", "#7c3aed",
           "#e11d48", "#0891b2", "#ca8a04", "#16a34a", "#ea580c"]


def esc(v) -> str:
    return html.escape(str(v if v is not None else "—"))


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _num(v, d=2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{d}f}"
    except (TypeError, ValueError):
        return str(v)


def _heat(v) -> str:
    """0..100 -> red..green (print-friendly)."""
    if v is None:
        return "#e5e7eb"
    x = max(0.0, min(100.0, float(v)))
    r = 214 if x < 50 else round(214 * (100 - x) / 50)
    g = round(170 * x / 50) if x < 50 else 170
    return f"rgb({r},{g},70)"


# ---------------------------------------------------------------- SVG charts

def _bar_chart(items: list[tuple[str, float | None]], fmt, title_unit: str,
               color="#2563eb", width=760, log=False, gate: float | None = None) -> str:
    """Horizontal bar chart. items = [(label, value)]; None values render as '—'."""
    vals = [v for _, v in items if v is not None and v > 0]
    if not vals:
        return "<p class='muted'>no data</p>"
    vmax = max(vals)
    lo = min(vals) if log else 0.0
    floor = min(lo, gate) / 3 if (log and gate) else (lo / 3 if log else 0.0)
    label_w, bar_w = 240, width - 240 - 90
    row_h, gap = 22, 5

    def w_of(v: float) -> float:
        if v is None or v <= 0:
            return 0.0
        if log:
            return bar_w * (math.log10(v / floor) / math.log10(vmax * 1.2 / floor))
        return bar_w * (v / (vmax * 1.05))

    rows, y = [], 4
    for label, v in items:
        bw = w_of(v)
        rows.append(
            f"<text x='{label_w - 8}' y='{y + row_h - 8}' text-anchor='end' class='cl'>{esc(label)}</text>"
            f"<rect x='{label_w}' y='{y + 3}' width='{max(bw, 1.5):.1f}' height='{row_h - 9}' rx='2' fill='{color}'/>"
            f"<text x='{label_w + max(bw, 1.5) + 6:.1f}' y='{y + row_h - 8}' class='cv'>{fmt(v)}</text>")
        y += row_h + gap
    gate_line = ""
    if gate and gate > 0:
        gx = label_w + w_of(gate)
        gate_line = (f"<line x1='{gx:.1f}' y1='0' x2='{gx:.1f}' y2='{y}' stroke='#dc2626' "
                     f"stroke-dasharray='4 3'/><text x='{gx + 4:.1f}' y='12' class='cg'>{fmt(gate)} gate</text>")
    return (f"<svg viewBox='0 0 {width} {y + 4}' xmlns='http://www.w3.org/2000/svg' "
            f"role='img' style='width:100%;height:auto'>{''.join(rows)}{gate_line}"
            f"<text x='{width - 4}' y='{y}' text-anchor='end' class='cg'>{esc(title_unit)}"
            f"{' · log scale' if log else ''}</text></svg>")


def _grouped_latency_chart(items: list[tuple[str, float | None, float | None]],
                           gate: float, width=760) -> str:
    """Two bars (p50/p95) per provider, log X — latencies span ~1s..2000s."""
    vals = [v for _, a, b in items for v in (a, b) if v]
    if not vals:
        return "<p class='muted'>no data</p>"
    vmax, vmin = max(vals), min(vals)
    floor = min(vmin, gate) / 3
    label_w, bar_w = 240, width - 240 - 90
    row_h, bar_h, gap = 34, 11, 8

    def w_of(v):
        if not v:
            return 0.0
        return bar_w * (math.log10(v / floor) / math.log10(vmax * 1.2 / floor))

    rows, y = [], 16
    for label, p50, p95 in items:
        w50, w95 = w_of(p50), w_of(p95)
        rows.append(
            f"<text x='{label_w - 8}' y='{y + row_h / 2 + 4}' text-anchor='end' class='cl'>{esc(label)}</text>"
            f"<rect x='{label_w}' y='{y}' width='{max(w50, 1.5):.1f}' height='{bar_h}' rx='2' fill='#0891b2'/>"
            f"<text x='{label_w + max(w50, 1.5) + 5:.1f}' y='{y + bar_h - 2}' class='cv'>{_num(p50, 1)}s</text>"
            f"<rect x='{label_w}' y='{y + bar_h + 2}' width='{max(w95, 1.5):.1f}' height='{bar_h}' rx='2' fill='#7c3aed'/>"
            f"<text x='{label_w + max(w95, 1.5) + 5:.1f}' y='{y + 2 * bar_h}' class='cv'>{_num(p95, 1)}s</text>")
        y += row_h + gap
    gx = label_w + w_of(gate)
    return (f"<svg viewBox='0 0 {width} {y + 6}' xmlns='http://www.w3.org/2000/svg' "
            f"role='img' style='width:100%;height:auto'>"
            f"<rect x='{label_w}' y='2' width='10' height='8' fill='#0891b2'/><text x='{label_w + 14}' y='10' class='cg'>p50</text>"
            f"<rect x='{label_w + 52}' y='2' width='10' height='8' fill='#7c3aed'/><text x='{label_w + 66}' y='10' class='cg'>p95</text>"
            f"<text x='{width - 4}' y='10' text-anchor='end' class='cg'>seconds · log scale</text>"
            f"{''.join(rows)}"
            f"<line x1='{gx:.1f}' y1='14' x2='{gx:.1f}' y2='{y}' stroke='#dc2626' stroke-dasharray='4 3'/>"
            f"<text x='{gx + 4:.1f}' y='24' class='cg' fill='#dc2626'>{gate:.0f}s gate</text></svg>")


def _scatter_chart(comp: list[dict], providers: list[str], width=760, height=380) -> str:
    """Cost per successful task (log X) vs pass@1 (Y). One dot per provider×class."""
    pts = [r for r in comp if r.get("cost_per_ok_usd") is not None and r.get("pass@1") is not None]
    if not pts:
        return "<p class='muted'>no cost data</p>"
    pos = [r["cost_per_ok_usd"] for r in pts if r["cost_per_ok_usd"] > 0]
    if not pos:
        return "<p class='muted'>all runs were free — cost scatter omitted</p>"
    xmin, xmax = min(pos) / 3, max(pos) * 1.6
    ml, mr, mt, mb = 56, 16, 14, 74
    pw, ph = width - ml - mr, height - mt - mb

    def X(v):
        v = max(v, xmin) if v and v > 0 else xmin
        return ml + pw * (math.log10(v / xmin) / math.log10(xmax / xmin))

    def Y(p):
        return mt + ph * (1 - min(p, 1.0) / 1.05)

    grid, e = [], math.floor(math.log10(xmin))
    while 10 ** e <= xmax:
        x = X(10 ** e)
        lbl = f"${10 ** e:g}"
        grid.append(f"<line x1='{x:.1f}' y1='{mt}' x2='{x:.1f}' y2='{mt + ph}' stroke='#e5e7eb'/>"
                    f"<text x='{x:.1f}' y='{mt + ph + 16}' text-anchor='middle' class='cg'>{lbl}</text>")
        e += 1
    for p in (0, 25, 50, 75, 100):
        y = Y(p / 100)
        grid.append(f"<line x1='{ml}' y1='{y:.1f}' x2='{ml + pw}' y2='{y:.1f}' stroke='#e5e7eb'/>"
                    f"<text x='{ml - 6}' y='{y + 4:.1f}' text-anchor='end' class='cg'>{p}%</text>")

    color = {p: PALETTE[i % len(PALETTE)] for i, p in enumerate(providers)}
    dots = []
    for r in pts:
        free = r["cost_per_ok_usd"] == 0
        dots.append(
            f"<circle cx='{X(r['cost_per_ok_usd']):.1f}' cy='{Y(r['pass@1']):.1f}' r='6' "
            f"fill='{'none' if free else color.get(r['provider'], '#64748b')}' "
            f"stroke='{color.get(r['provider'], '#64748b')}' stroke-width='2' fill-opacity='0.75'>"
            f"<title>{esc(r['provider'])} · {esc(r['task_class'])} — pass@1 {_pct(r['pass@1'])}, "
            f"${r['cost_per_ok_usd']:.5f}/ok</title></circle>")
    lx, ly, leg = ml, mt + ph + 52, []
    for p in providers:
        leg.append(f"<circle cx='{lx + 5}' cy='{ly - 4}' r='5' fill='{color[p]}'/>"
                   f"<text x='{lx + 14}' y='{ly}' class='cg'>{esc(p)}</text>")
        lx += 14 + 7 * len(p) + 18
        if lx > width - 130:
            lx, ly = ml, ly + 16
    return (f"<svg viewBox='0 0 {width} {max(height, ly + 12)}' xmlns='http://www.w3.org/2000/svg' "
            f"role='img' style='width:100%;height:auto'>{''.join(grid)}{''.join(dots)}"
            f"<text x='{ml + pw / 2:.0f}' y='{mt + ph + 32}' text-anchor='middle' class='cl'>"
            f"$ per successful task · log scale{' · $0 (local) drawn hollow at left edge' if any(r['cost_per_ok_usd'] == 0 for r in pts) else ''}</text>"
            f"{''.join(leg)}</svg>")


# ------------------------------------------------------------------- tables

def _table(rows: list[dict], cols: list[str], heat_col: str | None = None) -> str:
    if not rows:
        return "<p class='muted'>no data</p>"
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = []
    for r in rows:
        tds = []
        for c in cols:
            v = r.get(c)
            if c == heat_col:
                tds.append(f"<td><span class='pill' style='background:{_heat(v)}'>{_num(v, 0)}</span></td>")
            elif c.endswith("rate") or c in ("pass@1", "pass@k"):
                tds.append(f"<td>{_pct(v)}</td>")
            elif isinstance(v, float):
                tds.append(f"<td>{_num(v)}</td>")
            else:
                tds.append(f"<td>{esc(v)}</td>")
        body.append(f"<tr>{''.join(tds)}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _provider_groups(comp: list[dict]) -> list[dict]:
    by = defaultdict(list)
    for r in comp:
        by[r["provider"]].append(r)
    groups = []
    for provider, rows in by.items():
        groups.append({
            "provider": provider,
            "rows": sorted(rows, key=lambda r: str(r.get("task_class"))),
            "mean_composite": sum(r.get("composite") or 0 for r in rows) / len(rows),
            "mean_pass1": sum(r.get("pass@1") or 0 for r in rows) / len(rows),
        })
    return sorted(groups, key=lambda g: -g["mean_composite"])


def _task_breakdown(raw: list[dict]) -> list[dict]:
    """Per-task pass rate + the provider that struggled most — worst tasks first."""
    by = defaultdict(list)
    for r in raw:
        by[(r["task_id"], r.get("task_class", ""))].append(r)
    out = []
    for (task, tclass), rows in by.items():
        rate = sum(1 for r in rows if r.get("passed")) / len(rows)
        pv = defaultdict(list)
        for r in rows:
            pv[r["provider"]].append(bool(r.get("passed")))
        worst_p, worst_r = min(((p, sum(v) / len(v)) for p, v in pv.items()),
                               key=lambda x: x[1])
        out.append({"task": task, "class": tclass, "n": len(rows), "pass_rate": rate,
                    "worst_provider": worst_p, "worst_rate": worst_r})
    return sorted(out, key=lambda r: r["pass_rate"])


# ------------------------------------------------------------------ report

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font: 13px/1.5 -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
       color: #111827; margin: 0 auto; max-width: 900px; padding: 32px 28px; background: #fff; }
h1 { font-size: 22px; margin: 0 0 2px; }
h2 { font-size: 15px; margin: 26px 0 8px; padding-bottom: 4px; border-bottom: 2px solid #111827; }
h3 { font-size: 13px; margin: 14px 0 6px; color: #374151; }
.sub { color: #6b7280; font-size: 12px; margin-bottom: 18px; }
.muted { color: #9ca3af; }
.cards { display: flex; gap: 10px; margin: 12px 0; }
.kpi { flex: 1; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px; }
.kpi b { display: block; font-size: 20px; }
.kpi span { color: #6b7280; font-size: 11px; }
.kpi.warn b { color: #dc2626; }
table { width: 100%; border-collapse: collapse; font-size: 11.5px; margin: 6px 0 14px; }
th { text-align: left; padding: 5px 7px; background: #f3f4f6; border-bottom: 1.5px solid #d1d5db;
     font-weight: 600; white-space: nowrap; }
td { padding: 4px 7px; border-bottom: 1px solid #f3f4f6; }
tr.gh td { background: #eef2ff; font-weight: 600; border-top: 1.5px solid #c7d2fe; }
tr.gh td small { font-weight: 400; color: #6b7280; }
.pill { padding: 1px 8px; border-radius: 9px; font-weight: 600; color: #111; }
.exec { border: 1px solid #e5e7eb; border-left: 4px solid #2563eb; border-radius: 6px;
        padding: 10px 14px; margin: 10px 0; }
.exec li { margin: 3px 0; }
.cl { font: 11.5px sans-serif; fill: #111827; }
.cv { font: 10.5px sans-serif; fill: #374151; }
.cg { font: 10px sans-serif; fill: #6b7280; }
figure { margin: 8px 0 18px; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; }
figcaption { font-size: 11px; color: #6b7280; margin-top: 6px; }
.section { break-inside: avoid; }
@media print {
  body { padding: 0; max-width: none; font-size: 11.5px; }
  h2 { break-after: avoid; }
  figure, table, .exec { break-inside: avoid; }
  .pagebreak { break-before: page; }
  @page { size: A4; margin: 16mm 14mm; }
}
"""


def html_report(res: dict, run_meta: dict | None = None) -> str:
    s = res.get("summary", {}) or {}
    raw = res.get("raw", []) or []
    agg = res.get("aggregate", []) or []
    comp = res.get("composite", []) or []
    decision = res.get("decision", []) or []
    gate = float(s.get("p95_gate") or 15.0)

    groups = _provider_groups(comp)
    providers = [g["provider"] for g in groups]
    tasks = _task_breakdown(raw)
    weak = [t for t in tasks if t["pass_rate"] < 1.0][:5]

    # per-provider rollups for charts
    pass_items = [(g["provider"], g["mean_pass1"]) for g in groups]
    lat_by_p: dict[str, tuple] = {}
    for p in providers:
        rows = [r for r in agg if r["provider"] == p and r.get("latency_p95") is not None]
        if rows:
            p95 = max(r["latency_p95"] for r in rows)
            p50s = sorted(r["latency_p50"] for r in rows if r.get("latency_p50") is not None)
            lat_by_p[p] = (p50s[len(p50s) // 2] if p50s else None, p95)
    lat_items = sorted(([p, *lat_by_p[p]] for p in lat_by_p),
                       key=lambda x: -(x[2] or 0))
    cost_items = []
    for p in providers:
        cs = [r["cost_per_ok_usd"] for r in agg
              if r["provider"] == p and r.get("cost_per_ok_usd") is not None]
        cost_items.append((p, sum(cs) / len(cs) if cs else None))
    cost_items.sort(key=lambda x: (x[1] is None, x[1] or 0))

    # executive bullets
    top = decision[0] if decision else None
    bullets = []
    if top:
        bullets.append(f"<b>{esc(top['provider'])}</b> leads the suite "
                       f"(mean composite {_num(top.get('mean_composite'), 0)}/100 — "
                       f"“{esc(top.get('recommendation'))}”).")
    gated = [d for d in decision if "Defer" in str(d.get("recommendation", ""))]
    if gated:
        bullets.append("Latency/filter gate holds back: "
                       + ", ".join(f"<b>{esc(d['provider'])}</b>" for d in gated) + ".")
    if weak:
        bullets.append("Weakest tasks: " + ", ".join(
            f"<b>{esc(t['task'])}</b> ({_pct(t['pass_rate'])})" for t in weak[:3])
            + " — candidates for focus repetitions.")
    if s:
        bullets.append(f"{s.get('calls', 0)} calls · pass@1 {_pct(s.get('pass_at_1'))} · "
                       f"content-filter rate {_pct(s.get('filter_rate'))} · "
                       f"error rate {_pct(s.get('error_rate'))}.")

    # grouped scoreboard
    sb_cols = ["task_class", "pass@1", "pass@k", "latency_p50", "latency_p95",
               "tokens_per_s", "cost_per_ok_usd", "filter_rate", "error_rate",
               "composite", "verdict"]
    sb_rows = []
    for g in groups:
        first = g["rows"][0]
        sb_rows.append(
            f"<tr class='gh'><td colspan='{len(sb_cols)}'>{esc(g['provider'])} "
            f"<small>· {esc(first.get('vendor'))} · {esc(first.get('model'))} · {esc(first.get('tier'))}"
            f" · mean pass@1 {_pct(g['mean_pass1'])}</small> "
            f"<span class='pill' style='background:{_heat(g['mean_composite'])}'>"
            f"mean {g['mean_composite']:.0f}</span></td></tr>")
        for r in g["rows"]:
            tds = []
            for c in sb_cols:
                v = r.get(c)
                if c == "composite":
                    tds.append(f"<td><span class='pill' style='background:{_heat(v)}'>{_num(v, 0)}</span></td>")
                elif c in ("pass@1", "pass@k") or c.endswith("rate"):
                    tds.append(f"<td>{_pct(v)}</td>")
                elif isinstance(v, float):
                    tds.append(f"<td>{_num(v)}</td>")
                else:
                    tds.append(f"<td>{esc(v)}</td>")
            sb_rows.append(f"<tr>{''.join(tds)}</tr>")
    scoreboard = (f"<table><thead><tr>{''.join(f'<th>{esc(c)}</th>' for c in sb_cols)}</tr></thead>"
                  f"<tbody>{''.join(sb_rows)}</tbody></table>")

    task_rows = [{"task": t["task"], "class": t["class"], "attempts": t["n"],
                  "pass_rate": t["pass_rate"],
                  "hardest for": f"{t['worst_provider']} ({_pct(t['worst_rate'])})"}
                 for t in tasks]

    meta = run_meta or {}
    when = meta.get("saved_at") or datetime.datetime.now().isoformat(timespec="seconds")
    run_id = meta.get("run_id") or "current"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>MaaS Coding Benchmark — {esc(run_id)}</title>
<style>{_CSS}</style></head><body>

<h1>MaaS Coding Benchmark — Findings</h1>
<p class="sub">Huawei ModelArts MaaS vs baselines · Framework v1.0 · run <code>{esc(run_id)}</code> · {esc(when)}</p>

<div class="cards">
  <div class="kpi"><span>Calls</span><b>{s.get('calls', 0)}</b></div>
  <div class="kpi"><span>pass@1</span><b>{_pct(s.get('pass_at_1'))}</b></div>
  <div class="kpi{' warn' if (s.get('filter_rate') or 0) > 0.05 else ''}"><span>Filter rate</span><b>{_pct(s.get('filter_rate'))}</b></div>
  <div class="kpi{' warn' if (s.get('error_rate') or 0) > 0.05 else ''}"><span>Error rate</span><b>{_pct(s.get('error_rate'))}</b></div>
</div>

<div class="section"><h2>Executive summary</h2>
<div class="exec"><ul>{''.join(f'<li>{b}</li>' for b in bullets)}</ul></div></div>

<div class="section"><h2>Adoption decision</h2>
{_table(decision, ["provider", "vendor", "model", "tier", "classes_tested", "green",
                   "conditional", "mean_composite", "recommendation"], heat_col="mean_composite")}</div>

<div class="section"><h2>Quality vs cost</h2>
<figure>{_scatter_chart(comp, providers)}
<figcaption>Each dot is one provider × task class. Up and to the left is better.</figcaption></figure></div>

<div class="section"><h2>pass@1 by provider (mean across classes)</h2>
<figure>{_bar_chart(pass_items, lambda v: _pct(v), "", color="#059669")}</figure></div>

<div class="section"><h2>Latency (worst class per provider)</h2>
<figure>{_grouped_latency_chart([tuple(i) for i in lat_items], gate)}
<figcaption>p50 = median across classes; p95 = worst class. Gate: p95 &lt; {gate:.0f}s (streaming).</figcaption></figure></div>

<div class="section"><h2>Cost per successful task (mean across classes)</h2>
<figure>{_bar_chart(cost_items, lambda v: '—' if v is None else ('$0 (local)' if v == 0 else f'${v:.5f}'),
                    "USD / successful task", color="#d97706")}</figure></div>

<div class="pagebreak section"><h2>Scoreboard — grouped by provider</h2>
{scoreboard}</div>

<div class="section"><h2>Per-task difficulty (worst first)</h2>
{_table(task_rows, ["task", "class", "attempts", "pass_rate", "hardest for"])}</div>

<p class="sub">Generated by the MaaS Coding Benchmark · composite = 45% quality + 25% cost + 15% latency
+ 15% reliability (Framework v1.0 Table 1) · cost &amp; latency normalized within each task class.</p>
</body></html>"""
