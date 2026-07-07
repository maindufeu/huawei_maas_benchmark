import { useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, ReferenceLine, ResponsiveContainer,
  Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import { Download, Info } from "lucide-react";
import { api } from "../api";
import type { PricingTable, Results, Row } from "../types";

const TABS = ["Scoreboard", "Semaphore", "Charts", "Decision", "Pricing", "Raw", "Export"] as const;
type Tab = typeof TABS[number];

const PALETTE = ["#38bdf8", "#f472b6", "#34d399", "#fbbf24", "#a78bfa", "#fb7185", "#22d3ee", "#facc15", "#4ade80", "#f97316"];

function heat(v: number | null): string {
  if (v == null || isNaN(v)) return "transparent";
  const x = Math.max(0, Math.min(100, v));
  const r = x < 50 ? 214 : Math.round(214 * (100 - x) / 50);
  const g = x < 50 ? Math.round(180 * x / 50) : 180;
  return `rgb(${r},${g},80)`;
}
const fmt = (v: any, d = 2) => (v == null || v === "" ? "—" : typeof v === "number" ? v.toFixed(d) : String(v));
const pct = (v: any) => (v == null ? "—" : `${(v * 100).toFixed(0)}%`);

// model family for chart filtering (deepseek / glm / qwen / gemini / …), derived from model name
function familyOf(r: { model?: string; provider?: string }): string {
  const k = (r.model || r.provider || "").toLowerCase();
  for (const f of ["deepseek", "glm", "qwen", "gemini", "gpt", "claude", "llama", "mistral"]) if (k.includes(f)) return f;
  if (k.includes("local")) return "local";
  return k.split(/[_:\-/. ]/)[0] || "other";
}

// tolerant price lookup (matches backend: provider key wins, then model, then substring)
function rateFor(pricing: PricingTable, model: string, provider?: string) {
  if (provider && pricing[provider.toLowerCase()]) return pricing[provider.toLowerCase()];
  if (!model) return null;
  const k = model.toLowerCase();
  if (pricing[k]) return pricing[k];
  for (const key of Object.keys(pricing)) if (k.includes(key) || key.includes(k)) return pricing[key];
  return null;
}

export default function ResultsView({ results, p95Gate, pricing }: { results: Results; p95Gate: number; pricing: PricingTable }) {
  const [tab, setTab] = useState<Tab>("Scoreboard");
  const s = results.summary as any;
  const comp = results.composite;
  const providers = useMemo(() => [...new Set(comp.map((r) => r.provider))], [comp]);
  const classes = useMemo(() => [...new Set(comp.map((r) => r.task_class))].sort(), [comp]);

  if (!comp.length) return <div className="card text-slate-500 text-sm">No results yet. Run a benchmark.</div>;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-3">
        <Metric label="Calls" value={s.calls ?? 0} />
        <Metric label="pass@1" value={pct(s.pass_at_1)} accent />
        <Metric label="Filter rate" value={pct(s.filter_rate)} warn={s.filter_rate > 0.05} />
        <Metric label="Error rate" value={pct(s.error_rate)} warn={s.error_rate > 0.05} />
      </div>

      <div className="flex gap-1 border-b border-ink-600 flex-wrap">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm rounded-t-lg ${tab === t
              ? "bg-ink-800 text-accent border-b-2 border-accent" : "text-slate-400 hover:text-slate-200"}`}>
            {t}
          </button>
        ))}
      </div>

      {tab === "Scoreboard" && <Scoreboard comp={comp} pricing={pricing} />}
      {tab === "Semaphore" && <Semaphore comp={comp} providers={providers} classes={classes} p95Gate={p95Gate} />}
      {tab === "Charts" && <Charts comp={comp} agg={results.aggregate} providers={providers} classes={classes} p95Gate={p95Gate} />}
      {tab === "Decision" && <Decision decision={results.decision} comp={comp} />}
      {tab === "Pricing" && <Pricing pricing={pricing} comp={comp} />}
      {tab === "Raw" && <Table rows={results.raw} cols={["task_id","task_class","provider","model","vendor","repetition","status","passed","eval_score","latency_s","ttft_s","tokens_per_second","completion_tokens","cost_usd","error_type"]} />}
      {tab === "Export" && <Export />}
    </div>
  );
}

/* ---------- Scoreboard + click-to-explain breakdown ---------- */
function Scoreboard({ comp, pricing }: { comp: Row[]; pricing: PricingTable }) {
  const [sel, setSel] = useState<Row | null>(null);
  const [byProvider, setByProvider] = useState(true);

  const flatCols = ["task_class","provider","vendor","model","tier","pass@1","pass@k","latency_p50","latency_p95","tokens_per_s","cost_per_ok_usd","filter_rate","error_rate","composite","verdict"];
  const groupCols = ["task_class","pass@1","pass@k","latency_p50","latency_p95","tokens_per_s","cost_per_ok_usd","filter_rate","error_rate","composite","verdict"];
  const cols = byProvider ? groupCols : flatCols;

  // one group per provider, best mean composite first, classes in order inside
  const groups = useMemo(() => {
    const m = new Map<string, Row[]>();
    for (const r of comp) {
      if (!m.has(r.provider)) m.set(r.provider, []);
      m.get(r.provider)!.push(r);
    }
    const gs = [...m.entries()].map(([provider, rows]) => ({
      provider,
      rows: [...rows].sort((a, b) => String(a.task_class).localeCompare(String(b.task_class))),
      meanComposite: rows.reduce((s, r) => s + (r.composite ?? 0), 0) / rows.length,
      meanPass1: rows.reduce((s, r) => s + (r["pass@1"] ?? 0), 0) / rows.length,
    }));
    return gs.sort((a, b) => b.meanComposite - a.meanComposite);
  }, [comp]);

  const cell = (r: Row, c: string) => (
    <td key={c} className="td">
      {c === "composite"
        ? <span className="px-2 py-0.5 rounded font-semibold" style={{ background: heat(r[c]), color: "#111" }}>{fmt(r[c], 0)}</span>
        : c.includes("rate") || ["pass@1","pass@k"].includes(c) ? pct(r[c])
        : typeof r[c] === "number" ? fmt(r[c]) : (r[c] ?? "—")}
    </td>
  );

  return (
    <div className="space-y-3">
      <div className="card overflow-auto">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <p className="text-xs text-slate-500 flex items-center gap-1"><Info size={12}/> Click a row to see why it scored what it did (and the cost math).</p>
          <div className="flex items-center gap-1 text-xs">
            <span className="text-slate-500 mr-1">Group by:</span>
            {(["provider", "task class"] as const).map((g) => {
              const on = byProvider === (g === "provider");
              return (
                <button key={g} onClick={() => setByProvider(g === "provider")}
                  className={`chip ${on ? "bg-accent/20 border-accent text-accent" : "border-ink-600 text-slate-400 hover:border-slate-400"}`}>
                  {g}
                </button>
              );
            })}
          </div>
        </div>
        <table className="w-full">
          <thead><tr>{cols.map((c) => <th key={c} className="th">{c}</th>)}</tr></thead>
          <tbody>
            {byProvider
              ? groups.map((g) => {
                  const first = g.rows[0];
                  return [
                    <tr key={`h-${g.provider}`} className="bg-ink-900/70 border-t border-ink-600">
                      <td className="td" colSpan={cols.length}>
                        <span className="font-semibold text-slate-100">{g.provider}</span>
                        <span className="text-slate-500 text-xs"> · {first.vendor} · {first.model} · {first.tier}</span>
                        <span className="ml-3 text-xs text-slate-400">mean pass@1 {pct(g.meanPass1)}</span>
                        <span className="ml-3 px-2 py-0.5 rounded text-xs font-semibold" style={{ background: heat(g.meanComposite), color: "#111" }}>
                          mean {g.meanComposite.toFixed(0)}
                        </span>
                      </td>
                    </tr>,
                    ...g.rows.map((r, i) => (
                      <tr key={`${g.provider}-${i}`} onClick={() => setSel(r)}
                        className={`cursor-pointer hover:bg-ink-700/50 ${sel === r ? "bg-ink-700/60" : ""}`}>
                        {cols.map((c) => cell(r, c))}
                      </tr>
                    )),
                  ];
                })
              : comp.map((r, i) => (
                  <tr key={i} onClick={() => setSel(r)}
                    className={`cursor-pointer hover:bg-ink-700/50 ${sel === r ? "bg-ink-700/60" : ""}`}>
                    {cols.map((c) => cell(r, c))}
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {sel && <Breakdown r={sel} pricing={pricing} />}
    </div>
  );
}

function Breakdown({ r, pricing }: { r: Row; pricing: PricingTable }) {
  const dims = [
    { key: "quality", label: "Quality (pass@1)", pts: r.pts_quality, max: 45, score: r.score_quality, color: "#38bdf8" },
    { key: "cost", label: "Cost / task", pts: r.pts_cost, max: 25, score: r.score_cost, color: "#34d399" },
    { key: "latency", label: "Latency (p95)", pts: r.pts_latency, max: 15, score: r.score_latency, color: "#a78bfa" },
    { key: "reliability", label: "Reliability", pts: r.pts_reliability, max: 15, score: r.score_reliability, color: "#fbbf24" },
  ];
  const rate = rateFor(pricing, r.model, r.provider);
  const isLocal = r.vendor === "local";
  return (
    <div className="card space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">{r.provider} · {r.task_class} — why {Number(r.composite).toFixed(0)}/100</h3>
        <span className="text-sm">{r.verdict}</span>
      </div>
      <p className="text-xs text-slate-300 bg-ink-900/50 rounded p-2 border border-ink-700">{r.why}</p>
      <div className="space-y-2">
        {dims.map((d) => (
          <div key={d.key} className="flex items-center gap-3">
            <div className="w-32 text-xs text-slate-400">{d.label}</div>
            <div className="flex-1 h-4 bg-ink-900 rounded overflow-hidden relative">
              <div className="h-full rounded" style={{ width: `${(d.pts / 45) * 100}%`, background: d.color }} />
              <div className="absolute inset-0 flex items-center px-2 text-[11px] text-slate-200">
                {d.pts} / {d.max} pts &nbsp;<span className="text-slate-500">(sub-score {(d.score * 100).toFixed(0)}%)</span>
              </div>
            </div>
          </div>
        ))}
        <div className="flex items-center gap-3 pt-1 border-t border-ink-700">
          <div className="w-32 text-xs font-semibold text-slate-300">Composite</div>
          <div className="text-sm font-semibold" style={{ color: heat(r.composite) }}>{Number(r.composite).toFixed(1)} / 100</div>
        </div>
      </div>

      {/* cost math */}
      <div className="bg-ink-900/40 rounded p-2 border border-ink-700 text-xs text-slate-300 space-y-1">
        <div className="font-semibold text-slate-200">How the cost was computed</div>
        {isLocal ? (
          <div>Self-hosted (<code>{r.vendor}</code>) → priced at <b>$0</b>.</div>
        ) : (
          <>
            <div>
              Rate for <code>{r.model}</code>:{" "}
              {rate ? <b>${rate.input}/1M in · ${rate.output}/1M out</b> : <span className="text-amber-400">not in price table</span>}
              {rate?.note && <span className="text-slate-500"> ({rate.note})</span>}
            </div>
            <div>
              ~{fmt(r.avg_prompt_tokens, 0)} in + {fmt(r.avg_completion_tokens, 0)} out tokens/call →{" "}
              <b>${fmt(r.cost_per_call_usd, 6)}</b>/call
            </div>
            <div>
              {r.n_pass}/{r.n_calls} passed · total ${fmt(r.total_cost_usd, 5)} ÷ {r.n_pass} passes →{" "}
              <b style={{ color: "#34d399" }}>${fmt(r.cost_per_ok_usd, 5)}</b> per successful task
            </div>
          </>
        )}
      </div>

      {(r.gate_p95_fail || r.gate_filter_fail) && (
        <div className="flex gap-2 text-xs">
          {r.gate_p95_fail && <span className="chip border-rose-600 text-rose-300">⛔ p95 gate: {fmt(r.latency_p95,0)}s &gt; 15s</span>}
          {r.gate_filter_fail && <span className="chip border-fuchsia-600 text-fuchsia-300">⛔ filter gate: {pct(r.filter_rate)}</span>}
        </div>
      )}
      {r.cost_relative && (
        <p className="text-[11px] text-slate-500">Note: cost &amp; latency sub-scores are relative to the other providers in this same task class (best=100%, worst=0%). Quality &amp; reliability are absolute.</p>
      )}
    </div>
  );
}

/* ---------- Semaphore ---------- */
function Semaphore({ comp, providers, classes, p95Gate }: { comp: Row[]; providers: string[]; classes: string[]; p95Gate: number }) {
  const cell = (p: string, c: string) => comp.find((r) => r.provider === p && r.task_class === c);
  return (
    <div className="card space-y-4">
      <p className="text-xs text-slate-500">🟢 ≥75 &amp; gates pass · 🟡 ≥60 / gate warn · 🔴 &lt;60. Gates: p95 &lt; {p95Gate}s, filter &lt; 5%.</p>
      <div className="overflow-auto">
        <table className="w-full">
          <thead><tr><th className="th">provider</th>{classes.map((c) => <th key={c} className="th text-center">{c}</th>)}</tr></thead>
          <tbody>
            {providers.map((p) => (
              <tr key={p}>
                <td className="td font-medium">{p}</td>
                {classes.map((c) => {
                  const r = cell(p, c);
                  return (
                    <td key={c} className="td text-center" title={r?.why}>
                      {r ? (
                        <div className="flex flex-col items-center">
                          <span>{r.verdict?.split(" ")[0]}</span>
                          <span className="text-[11px] font-semibold px-1.5 rounded" style={{ background: heat(r.composite), color: "#111" }}>{fmt(r.composite, 0)}</span>
                        </div>
                      ) : "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------- Charts ---------- */
function Charts({ comp, agg, providers, classes, p95Gate }: { comp: Row[]; agg: Row[]; providers: string[]; classes: string[]; p95Gate: number }) {
  const color = (p: string) => PALETTE[providers.indexOf(p) % PALETTE.length];
  const [latClass, setLatClass] = useState<string>("__worst__");
  const [logScale, setLogScale] = useState(true);
  const [logX, setLogX] = useState(true);
  const [logY, setLogY] = useState(false);

  // model-family filter (applies to every chart in this tab)
  const famOfProvider = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of comp) if (!m.has(r.provider)) m.set(r.provider, familyOf(r));
    return m;
  }, [comp]);
  const families = useMemo(() => [...new Set([...famOfProvider.values()])].sort(), [famOfProvider]);
  const [hiddenFams, setHiddenFams] = useState<string[]>([]);
  const toggleFam = (f: string) =>
    setHiddenFams((h) => (h.includes(f) ? h.filter((x) => x !== f) : [...h, f]));
  const famOk = (p: string) => !hiddenFams.includes(famOfProvider.get(p) ?? "other");
  const fComp = comp.filter((r) => famOk(r.provider));
  const fAgg = agg.filter((r) => famOk(r.provider));
  const fProviders = providers.filter(famOk);

  const scatterRaw = fComp.filter((r) => r.cost_per_ok_usd != null);
  // log X can't plot $0 (local/free) — floor those points just left of the cheapest paid one
  const positive = scatterRaw.map((r) => r.cost_per_ok_usd).filter((v) => v > 0);
  const floor = positive.length ? Math.min(...positive) / 3 : 0.00001;
  const hasFree = scatterRaw.some((r) => r.cost_per_ok_usd === 0);
  const maxX = positive.length ? Math.max(...positive) : 1;
  // recharts generates linear ticks on log axes (they overlap badly) — build decade ticks ourselves
  const xTicks = useMemo(() => {
    const t: number[] = [];
    for (let e = Math.floor(Math.log10(floor)); e <= Math.ceil(Math.log10(maxX)); e++) t.push(10 ** e);
    return t;
  }, [floor, maxX]);
  const yLogTicks = [1, 2, 5, 10, 20, 50, 100];
  const fmtUsd = (v: number) => (v >= 1 ? `$${v.toFixed(0)}` : `$${Number(v.toPrecision(1))}`);
  const scatter: any[] = scatterRaw.map((r) => ({
    ...r,
    x: r.cost_per_ok_usd,
    xPlot: r.cost_per_ok_usd > 0 ? r.cost_per_ok_usd : floor,
    y: (r["pass@1"] ?? 0) * 100,
    yPlot: ((r["pass@1"] ?? 0) * 100) > 0 ? ((r["pass@1"] ?? 0) * 100) : 0.5,
    z: r.tokens_per_s ?? 20,
  }));

  // failure-rate view: log(100 − pass@1) separates the points crowded near 100%
  const failRaw = fComp.filter((r) => r.cost_per_ok_usd != null && r["pass@1"] != null);
  const posFails = failRaw.map((r) => (1 - r["pass@1"]) * 100).filter((v) => v > 0);
  const failFloor = posFails.length ? Math.min(Math.min(...posFails) / 3, 0.5) : 0.1;
  const hasPerfect = failRaw.some((r) => r["pass@1"] === 1);
  const failScatter: any[] = failRaw.map((r) => {
    const fail = (1 - r["pass@1"]) * 100;
    return {
      ...r,
      x: r.cost_per_ok_usd,
      xPlot: r.cost_per_ok_usd > 0 ? r.cost_per_ok_usd : floor,
      fail,
      yPlot: fail > 0 ? fail : failFloor,
      z: r.tokens_per_s ?? 20,
    };
  });
  const failTicks = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100].filter((v) => v >= failFloor * 0.999);

  const latData = useMemo(() => {
    if (latClass === "__worst__") {
      return fProviders.map((p) => {
        const rows = agg.filter((r) => r.provider === p && r.latency_p95 != null);
        if (!rows.length) return null;
        const p95 = Math.max(...rows.map((r) => r.latency_p95));
        const p50s = rows.map((r) => r.latency_p50).filter((v) => v != null).sort((a, b) => a - b);
        const p50 = p50s.length ? p50s[Math.floor(p50s.length / 2)] : null;
        return { name: p, p50, p95 };
      }).filter(Boolean).sort((a: any, b: any) => b.p95 - a.p95);
    }
    return fAgg.filter((r) => r.task_class === latClass && r.latency_p95 != null)
      .map((r) => ({ name: r.provider, p50: r.latency_p50, p95: r.latency_p95 }))
      .sort((a: any, b: any) => b.p95 - a.p95);
  }, [fAgg, fProviders, latClass]);

  const passData = fComp.map((r) => ({ name: `${r.provider} · ${r.task_class}`, pass1: (r["pass@1"] ?? 0) * 100 }));

  return (
    <div className="space-y-4">
      <div className="card !py-2 flex items-center gap-2 flex-wrap">
        <span className="text-xs text-slate-500">Model family:</span>
        {families.map((f) => {
          const on = !hiddenFams.includes(f);
          return (
            <button key={f} onClick={() => toggleFam(f)}
              className={`px-2.5 py-0.5 rounded-full text-xs border transition-colors ${on
                ? "bg-accent/20 border-accent text-accent"
                : "border-ink-500 text-slate-500 hover:text-slate-300"}`}>
              {f}
            </button>
          );
        })}
        {hiddenFams.length > 0 && (
          <button onClick={() => setHiddenFams([])} className="text-xs text-slate-400 underline ml-1">show all</button>
        )}
      </div>

      <div className="card">
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <h3 className="text-sm font-medium text-slate-300">Cost per successful task vs quality</h3>
          <label className="flex items-center gap-1 cursor-pointer text-xs text-slate-400">
            <input type="checkbox" checked={logX} onChange={(e) => setLogX(e.target.checked)} /> log scale (X)
          </label>
          <label className="flex items-center gap-1 cursor-pointer text-xs text-slate-400">
            <input type="checkbox" checked={logY} onChange={(e) => setLogY(e.target.checked)} /> log scale (Y)
          </label>
        </div>
        <p className="text-[11px] text-slate-500 mb-3">
          Each bubble = one provider × task class. X = $/successful task, Y = pass@1, bubble size = tokens/s.
          {logX && hasFree && " Free/local ($0) is shown at the left axis floor (log can't plot 0)."}
          {logY && " 0% scores are shown at the bottom axis floor (log can't plot 0)."}
        </p>
        <ResponsiveContainer width="100%" height={380}>
          <ScatterChart margin={{ left: 24, right: 28, top: 8, bottom: 44 }}>
            <CartesianGrid stroke="#243044" />
            <XAxis
              type="number" name="$/ok task"
              dataKey={logX ? "xPlot" : "x"}
              scale={logX ? "log" : "linear"}
              domain={logX ? [floor, maxX * 1.5] : [0, "auto"]}
              ticks={logX ? xTicks : undefined}
              allowDataOverflow
              stroke="#94a3b8" tick={{ fill: "#94a3b8", fontSize: 11 }}
              tickFormatter={fmtUsd}
              label={{ value: `$ per successful task${logX ? "  ·  log scale" : ""}`,
                       position: "bottom", offset: 8, fill: "#e2e8f0", fontSize: 12 }} />
            <YAxis
              type="number" dataKey={logY ? "yPlot" : "y"} name="pass@1 %"
              scale={logY ? "log" : "linear"}
              domain={logY ? [0.5, 140] : [0, 105]}
              ticks={logY ? yLogTicks : [0, 20, 40, 60, 80, 100]}
              allowDataOverflow
              stroke="#94a3b8" tick={{ fill: "#94a3b8", fontSize: 11 }}
              tickFormatter={logY ? (v: number) => `${v}%` : undefined}
              label={{ value: `pass@1  (%)${logY ? "  ·  log scale" : ""}`, angle: -90, position: "insideLeft",
                       fill: "#e2e8f0", fontSize: 12, dy: 30 }} />
            <ZAxis type="number" dataKey="z" range={[60, 420]} name="tok/s" />
            <Tooltip content={<ScatterTip />} cursor={{ strokeDasharray: "3 3" }} />
            <Legend verticalAlign="top" height={56} wrapperStyle={{ fontSize: 12, paddingBottom: 8 }} />
            {fProviders.map((p) => (
              <Scatter key={p} name={p} data={scatter.filter((d) => d.provider === p)} fill={color(p)} />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      <div className="card">
        <h3 className="text-sm font-medium text-slate-300 mb-1">Failure rate vs cost (log)</h3>
        <p className="text-[11px] text-slate-500 mb-3">
          Y = 100 − pass@1 on a log scale — separates providers crowded near 100% pass@1. Lower is better.
          {hasPerfect && " Perfect scores (0% failure) are shown at the bottom axis floor (log can't plot 0)."}
          {hasFree && " Free/local ($0) is shown at the left axis floor."}
        </p>
        <ResponsiveContainer width="100%" height={380}>
          <ScatterChart margin={{ left: 24, right: 28, top: 8, bottom: 44 }}>
            <CartesianGrid stroke="#243044" />
            <XAxis
              type="number" name="$/ok task" dataKey="xPlot"
              scale="log" domain={[floor, maxX * 1.5]} ticks={xTicks} allowDataOverflow
              stroke="#94a3b8" tick={{ fill: "#94a3b8", fontSize: 11 }}
              tickFormatter={fmtUsd}
              label={{ value: "$ per successful task  ·  log scale",
                       position: "bottom", offset: 8, fill: "#e2e8f0", fontSize: 12 }} />
            <YAxis
              type="number" dataKey="yPlot" name="failure %"
              scale="log" domain={[failFloor, 140]} ticks={failTicks} allowDataOverflow
              stroke="#94a3b8" tick={{ fill: "#94a3b8", fontSize: 11 }}
              tickFormatter={(v: number) => `${v}%`}
              label={{ value: "failure rate (100 − pass@1)  ·  log scale", angle: -90, position: "insideLeft",
                       fill: "#e2e8f0", fontSize: 12, dy: 60 }} />
            <ZAxis type="number" dataKey="z" range={[60, 420]} name="tok/s" />
            <Tooltip content={<ScatterTip />} cursor={{ strokeDasharray: "3 3" }} />
            <Legend verticalAlign="top" height={56} wrapperStyle={{ fontSize: 12, paddingBottom: 8 }} />
            {fProviders.map((p) => (
              <Scatter key={p} name={p} data={failScatter.filter((d) => d.provider === p)} fill={color(p)} />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      <div className="card">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <h3 className="text-sm font-medium text-slate-300">Latency p50 / p95 (s)</h3>
          <div className="flex items-center gap-2 text-xs">
            <select className="input !py-1 !w-auto" value={latClass} onChange={(e) => setLatClass(e.target.value)}>
              <option value="__worst__">Worst p95 per provider</option>
              {classes.map((c) => <option key={c} value={c}>Class {c}</option>)}
            </select>
            <label className="flex items-center gap-1 cursor-pointer text-slate-400">
              <input type="checkbox" checked={logScale} onChange={(e) => setLogScale(e.target.checked)} /> log scale
            </label>
          </div>
        </div>
        <p className="text-[11px] text-slate-500 mb-2">Sorted by p95 (worst first). Log scale recommended — values span ~2s to ~2000s.</p>
        <ResponsiveContainer width="100%" height={340}>
          <BarChart data={latData as any} margin={{ left: 0, right: 20, top: 10, bottom: 80 }}>
            <CartesianGrid stroke="#243044" />
            <XAxis dataKey="name" stroke="#64748b" fontSize={10} angle={-30} textAnchor="end" interval={0} height={100} />
            <YAxis stroke="#64748b" fontSize={11}
                   scale={logScale ? "log" : "auto"} domain={logScale ? [0.1, "auto"] : [0, "auto"]} allowDataOverflow />
            <Tooltip contentStyle={{ background: "#111827", border: "1px solid #334155" }}
                     formatter={(v: any, n: any) => [`${Number(v).toFixed(1)} s`, n]} />
            <ReferenceLine y={p95Gate} stroke="#fb7185" strokeDasharray="4 4"
                           label={{ value: `${p95Gate}s gate`, fill: "#fb7185", fontSize: 11, position: "insideTopRight" }} />
            <Legend />
            <Bar dataKey="p50" fill="#38bdf8" name="p50" />
            <Bar dataKey="p95" fill="#a78bfa" name="p95" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="card">
        <h3 className="text-sm font-medium mb-2 text-slate-300">pass@1 (%) by provider × class</h3>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={passData} margin={{ left: 0, right: 20, top: 10, bottom: 90 }}>
            <CartesianGrid stroke="#243044" />
            <XAxis dataKey="name" stroke="#64748b" fontSize={9} angle={-35} textAnchor="end" interval={0} height={110} />
            <YAxis stroke="#64748b" fontSize={11} domain={[0, 100]} />
            <Tooltip contentStyle={{ background: "#111827", border: "1px solid #334155" }} formatter={(v: any) => [`${Number(v).toFixed(0)}%`, "pass@1"]} />
            <Bar dataKey="pass1">{passData.map((_, i) => <Cell key={i} fill={heat(passData[i].pass1)} />)}</Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function ScatterTip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-ink-800 border border-ink-500 rounded-lg p-2 text-xs space-y-0.5">
      <div className="font-semibold text-accent">{d.provider} · {d.task_class}</div>
      <div className="text-slate-500">{d.vendor} · {d.model}</div>
      <div>pass@1: <b>{(d["pass@1"] * 100).toFixed(0)}%</b></div>
      {d.fail != null && <div>failure rate: <b>{d.fail === 0 ? "0" : Number(d.fail).toFixed(1)}%</b></div>}
      <div>$/successful task: <b>${Number(d.cost_per_ok_usd).toFixed(5)}</b></div>
      <div>latency p95: <b>{fmt(d.latency_p95, 1)} s</b></div>
      <div>tokens/s: <b>{fmt(d.tokens_per_s, 0)}</b></div>
      <div>composite: <b>{fmt(d.composite, 0)}</b> · {d.verdict}</div>
    </div>
  );
}

/* ---------- Decision ---------- */
function Decision({ decision, comp }: { decision: Row[]; comp: Row[] }) {
  const cols = ["provider","vendor","model","tier","classes_tested","green","conditional","mean_composite","recommendation"];
  return (
    <div className="space-y-3">
      <div className="card overflow-auto">
        <table className="w-full">
          <thead><tr>{cols.map((c) => <th key={c} className="th">{c}</th>)}</tr></thead>
          <tbody>
            {decision.map((r, i) => (
              <tr key={i} className="hover:bg-ink-700/40">
                {cols.map((c) => <td key={c} className="td">{typeof r[c] === "number" ? fmt(r[c], 0) : r[c]}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card space-y-2">
        <h3 className="text-sm font-medium text-slate-300">Why each recommendation (per-class verdicts)</h3>
        {decision.map((d) => {
          const rows = comp.filter((r) => r.provider === d.provider);
          return (
            <div key={d.provider} className="text-xs border-t border-ink-700 pt-2">
              <b>{d.provider}</b> → <span className="text-accent">{d.recommendation}</span>
              <div className="flex flex-wrap gap-2 mt-1">
                {rows.map((r) => (
                  <span key={r.task_class} className="chip border-ink-600 text-slate-400" title={r.why}>
                    {r.task_class} {r.verdict?.split(" ")[0]} {fmt(r.composite, 0)}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- Pricing ---------- */
function Pricing({ pricing, comp }: { pricing: PricingTable; comp: Row[] }) {
  const used = useMemo(() => [...new Set(comp.map((r) => r.model.toLowerCase()))], [comp]);
  const keys = Object.keys(pricing).sort();
  const blended = (r: any) => (r.input != null && r.output != null ? (r.input * 0.25 + r.output * 0.75).toFixed(2) : "—");
  return (
    <div className="card space-y-3">
      <p className="text-xs text-slate-400">
        Cost = (prompt_tokens × input + completion_tokens × output) ÷ 1,000,000, from{" "}
        <code>config/pricing.yaml</code>. $/successful task = total cost ÷ passes. Local = $0.
        Blended = input×0.25 + output×0.75 (a rough read-heavy mix). Rows used in this run are highlighted.
      </p>
      <div className="overflow-auto">
        <table className="w-full">
          <thead><tr>{["model","input $/1M","cached in","output $/1M","blended $/1M","note"].map((c) => <th key={c} className="th">{c}</th>)}</tr></thead>
          <tbody>
            {keys.map((k) => {
              const r = pricing[k];
              const isUsed = used.some((m) => m.includes(k) || k.includes(m));
              return (
                <tr key={k} className={isUsed ? "bg-accent/10" : ""}>
                  <td className="td font-medium">{k}{isUsed && <span className="chip ml-2 border-accent text-accent">in run</span>}</td>
                  <td className="td">{r.input ?? "—"}</td>
                  <td className="td">{r.cached_input ?? "—"}</td>
                  <td className="td">{r.output ?? "—"}</td>
                  <td className="td">{blended(r)}</td>
                  <td className="td text-slate-500">{r.note ?? ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Table({ rows, cols }: { rows: Row[]; cols: string[] }) {
  return (
    <div className="card overflow-auto max-h-[560px]">
      <table className="w-full">
        <thead className="sticky top-0 bg-ink-800"><tr>{cols.map((c) => <th key={c} className="th">{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="hover:bg-ink-700/40">
              {cols.map((c) => <td key={c} className="td">{typeof r[c] === "number" ? fmt(r[c]) : String(r[c] ?? "—")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Export() {
  const items: [string, string, string][] = [
    ["Raw results (CSV)", "csv", "raw"],
    ["Composite + verdict + breakdown (CSV)", "csv", "composite"],
    ["Aggregate (CSV)", "csv", "aggregate"],
    ["Decision matrix (CSV)", "csv", "decision"],
    ["One-page report (Markdown)", "md", "raw"],
  ];
  return (
    <div className="card space-y-2">
      <a href={api.exportUrl("html")} target="_blank" rel="noreferrer"
         className="btn-ghost w-full justify-start">
        <Download size={14} /> Printable report (HTML) — exec summary + charts, opens in a new tab (Ctrl+P to print)
      </a>
      {items.map(([label, fmtT, table]) => (
        <a key={label} href={api.exportUrl(fmtT as any, table)} download
           className="btn-ghost w-full justify-start"><Download size={14} /> {label}</a>
      ))}
    </div>
  );
}

function Metric({ label, value, accent, warn }: { label: string; value: any; accent?: boolean; warn?: boolean }) {
  return (
    <div className="card !p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-2xl font-semibold ${warn ? "text-rose-400" : accent ? "text-accent" : "text-slate-100"}`}>{value}</div>
    </div>
  );
}
