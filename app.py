"""MaaS Coding Benchmark — Streamlit webapp.

Configure providers (API key entered here in the front-end), pick a task suite,
run the matrix, and read the scoreboard / semaphore matrix / charts / decision
matrix — the artifacts for the Jul-3 Huawei review.

Run:  streamlit run app.py
"""
from __future__ import annotations

import io
import os

import pandas as pd
import plotly.express as px
import streamlit as st

from maasbench import evaluators, secrets_store
from maasbench.pricing import Pricing
from maasbench.providers import (
    GEMINI_BASE_URL, HUAWEI_BASE_URL, HUAWEI_MODELS, LLAMACPP_BASE_URL,
    Provider, call_model, is_local,
)
from maasbench.runner import RunConfig, run_benchmark
from maasbench.scoring import (
    P95_LATENCY_GATE_S, aggregate, composite_scores, decision_matrix, rows_to_df,
)
from maasbench.tasks import CLASS_LABELS, load_tasks

st.set_page_config(page_title="MaaS Coding Benchmark", page_icon="🧪", layout="wide")

TASKS_DIR = os.path.join(os.path.dirname(__file__), "tasks")

# ---- session state ---------------------------------------------------------
ss = st.session_state
ss.setdefault("providers", [])       # list[Provider]
ss.setdefault("rows", [])            # list[RunRow]
ss.setdefault("judge", None)         # Provider | None
ss.setdefault("stop", False)


def _pricing() -> Pricing:
    try:
        return Pricing.load()
    except Exception:
        return Pricing({})


def _heat_color(v):
    """Red→yellow→green background for a 0-100 score, no matplotlib needed."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    v = max(0.0, min(100.0, float(v)))
    if v < 50:                      # red -> yellow
        r, g = 214, int(180 * v / 50)
    else:                           # yellow -> green
        r, g = int(214 * (100 - v) / 50), 180
    return f"background-color: rgb({r},{g},80); color: #111;"


def _report_md(df, comp, dm) -> str:  # noqa: ANN001
    buf = io.StringIO()
    buf.write("# Huawei MaaS Coding POC — Benchmark Findings\n\n")
    buf.write(f"- Calls: {len(df)} · pass@1 overall: "
              f"{df[df.repetition==1].passed.mean()*100:.0f}% · "
              f"filter rate: {(df.error_type=='content_filter').mean()*100:.1f}% · "
              f"error rate: {(df.status!='ok').mean()*100:.1f}%\n\n")
    buf.write("## Adoption decision\n\n")
    buf.write(dm.to_markdown(index=False))
    buf.write("\n\n## Composite by model × task class\n\n")
    buf.write(comp[["task_class", "model", "pass@1", "latency_p95",
                    "cost_per_ok_usd", "composite", "verdict"]].to_markdown(index=False))
    buf.write("\n")
    return buf.getvalue()


# ============================================================ SIDEBAR ========
with st.sidebar:
    st.title("🧪 MaaS Coding Benchmark")
    st.caption("Huawei ModelArts MaaS vs baselines · Framework v1.0")

    # bootstrap: load saved credentials once per session
    if not ss.get("_bootstrapped"):
        saved, _ = secrets_store.load()
        if saved:
            ss.providers = saved
        ss._bootstrapped = True

    st.header("1 · Providers")
    remember = st.checkbox(
        "💾 Recordar credenciales en esta máquina",
        value=secrets_store.exists(),
        help="Guarda proveedores + API keys en .secrets.json (texto plano, local, gitignored). "
             "No lo uses en una máquina compartida.")

    PRESETS = {
        "Huawei MaaS": (HUAWEI_BASE_URL, ""),
        "Google Gemini": (GEMINI_BASE_URL, "gemini-3.5-flash"),
        "Local (llama.cpp)": (LLAMACPP_BASE_URL, "local-model"),
        "Custom (OpenAI-compatible)": ("", ""),
    }
    with st.expander("➕ Add provider", expanded=not ss.providers):
        ptype = st.selectbox("Type", list(PRESETS), key="ptype")
        def_url, def_model = PRESETS[ptype]
        url = st.text_input("Base URL", def_url, key=f"url_{ptype}")
        is_local_type = ptype == "Local (llama.cpp)"
        if ptype == "Huawei MaaS":
            add_models = st.multiselect("Models", [m for m, _ in HUAWEI_MODELS],
                                        default=["glm-5.2"], key="hw_models")
            effort = st.selectbox("reasoning_effort (GLM-5.2 / DeepSeek)",
                                  ["(none)", "high", "max"], index=0, key="hw_effort")
        else:
            add_models = [st.text_input("Model id", def_model, key=f"model_{ptype}")]
            effort = "(none)"
            if ptype == "Google Gemini":
                st.caption("Modelos válidos p. ej.: `gemini-3.5-flash`, `gemini-3.1-pro-preview`. "
                           "El `gemini-3-pro` genérico da 404.")
        key = st.text_input("API key" + (" (opcional para local)" if is_local_type else ""),
                            type="password", key=f"key_{ptype}")
        tier = st.selectbox("Tier", ["frontier", "balanced", "cost", "reasoning", "small"],
                            key=f"tier_{ptype}", index=0 if ptype != "Local (llama.cpp)" else 4)
        if st.button("Add", width='stretch'):
            if not url:
                st.warning("Enter the base URL.")
            elif not key and not is_local_type:
                st.warning("Enter the API key first.")
            else:
                thw = {m: t for m, t in HUAWEI_MODELS}
                extra = {} if effort == "(none)" else {"reasoning_effort": effort}
                for m in add_models:
                    if not m:
                        continue
                    if ptype == "Huawei MaaS":
                        nm, t = f"huawei_{m}", thw.get(m, "frontier")
                    elif is_local_type:
                        nm, t = f"local_{m}", tier
                    else:
                        nm, t = m, tier
                    ss.providers.append(Provider(name=nm, base_url=url, model=m,
                                                 api_key=key or "EMPTY", tier=t,
                                                 extra_body=dict(extra)))
                if remember:
                    secrets_store.save(ss.providers)
                st.rerun()

    if ss.providers:
        st.write("**Configured:**")
        for i, p in enumerate(ss.providers):
            c1, c2 = st.columns([5, 1])
            tag = " · 🖥local" if is_local(p.base_url) else ""
            c1.write(f"`{p.name}` · {p.model} · _{p.tier}_{tag}")
            if c2.button("✕", key=f"del_{i}"):
                ss.providers.pop(i)
                if remember:
                    secrets_store.save(ss.providers)
                st.rerun()
        cc1, cc2 = st.columns(2)
        if cc1.button("🔌 Test connection", width='stretch'):
            for p in ss.providers:
                r = call_model(p, [{"role": "user", "content": "ping"}],
                               max_tokens=8, stream=False, timeout=30)
                if r.ok:
                    st.success(f"{p.name}: ok · {r.latency_s:.1f}s")
                else:
                    st.error(f"{p.name}: {r.status} · {r.error_type} — "
                             f"{(r.error_message or '')[:140]}")
        if cc2.button("🗑 Forget saved", width='stretch'):
            secrets_store.clear()
            ss.providers = []
            st.rerun()

    st.header("2 · Judge (for T4)")
    st.caption("LLM-as-judge for design tasks with no unit tests. Optional.")
    use_judge = st.checkbox("Enable judge", value=False)
    if use_judge:
        j_url = st.text_input("Judge base URL", HUAWEI_BASE_URL, key="j_url")
        j_model = st.text_input("Judge model", "glm-5.2", key="j_model")
        j_key = st.text_input("Judge API key", type="password", key="j_key")
        ss.judge = (Provider(name="judge", base_url=j_url, model=j_model, api_key=j_key)
                    if j_key else None)
    else:
        ss.judge = None

    st.header("3 · Run config")
    reps = st.slider("Repetitions (N per task)", 1, 5, 3)
    temperature = st.slider("Temperature", 0.0, 1.0, 0.2, 0.1)
    max_tokens = st.number_input("Max tokens", 256, 16384, 4096, 256)
    stream = st.checkbox("Streaming (measure TTFT)", value=True)
    warmup = st.checkbox("Warm-up call per provider", value=True)
    timeout = st.number_input("Per-call timeout (s)", 15, 600, 180, 15)


# ============================================================ MAIN ===========
st.title("Coding-scenario benchmark")

# ---- task selection --------------------------------------------------------
all_tasks = load_tasks(TASKS_DIR)
classes = sorted({t.task_class for t in all_tasks})
left, right = st.columns([2, 1])
with left:
    chosen_classes = st.multiselect(
        "Task classes", classes,
        default=classes,
        format_func=lambda c: f"{c} — {CLASS_LABELS.get(c, c)}")
with right:
    only_objective = st.checkbox("Only auto-graded (skip judge tasks)", value=ss.judge is None)

tasks = [t for t in all_tasks if t.task_class in chosen_classes]
if only_objective:
    tasks = [t for t in tasks if t.evaluation.get("type") != "judge"]

n_calls = len(tasks) * len(ss.providers) * reps
st.caption(f"**{len(tasks)}** tasks × **{len(ss.providers)}** providers × **{reps}** reps "
           f"= **{n_calls}** calls · evaluators available: {', '.join(evaluators.available())}")

with st.expander("Preview selected tasks"):
    st.dataframe(pd.DataFrame([{
        "id": t.id, "class": t.task_class, "title": t.title,
        "lang": t.language, "eval": t.evaluation.get("type"),
        "difficulty": t.difficulty} for t in tasks]),
        width='stretch', hide_index=True)

# ---- run -------------------------------------------------------------------
run_col, stop_col, _ = st.columns([1, 1, 4])
start = run_col.button("▶ Run benchmark", type="primary", disabled=not (tasks and ss.providers))
if stop_col.button("■ Stop"):
    ss.stop = True

if start:
    ss.stop = False
    ss.rows = []
    # sequential: st.progress/st.write are not safe from the runner's worker threads
    cfg = RunConfig(temperature=temperature, max_tokens=int(max_tokens), repetitions=reps,
                    stream=stream, timeout_s=float(timeout), warmup=warmup,
                    max_parallel_providers=1)
    bar = st.progress(0.0, text="Starting…")
    live = st.empty()

    def _progress(info):
        done, total = info["done"], info["total"]
        r = info["row"]
        bar.progress(done / total,
                     text=f"{done}/{total} · {r.model} · {r.task_id} · {r.status}")
        flag = "✅" if r.passed else ("🛡️filter" if r.error_type == "content_filter"
                                      else ("⚠️" + (r.error_type or r.status) if not r.status == "ok" else "❌"))
        live.write(f"{flag} `{r.model}` · {r.task_id} · {r.latency_s}s · score={r.eval_score}")

    with st.spinner("Running…"):
        rows = run_benchmark(tasks, ss.providers, cfg, pricing=_pricing(),
                             judge_provider=ss.judge, progress=_progress,
                             should_stop=lambda: ss.stop)
    ss.rows = rows
    bar.progress(1.0, text="Done.")
    st.success(f"Completed {len(rows)} calls.")

# ============================================================ RESULTS ========
if ss.rows:
    df = rows_to_df(ss.rows)
    agg = aggregate(df)
    comp = composite_scores(agg)

    tabs = st.tabs(["🏁 Scoreboard", "🚦 Semaphore", "📊 Charts",
                    "🧭 Decision", "🔎 Raw", "⬇ Export"])

    # -- Scoreboard --
    with tabs[0]:
        c = st.columns(4)
        ok = df[df.status == "ok"]
        c[0].metric("Calls", len(df))
        c[1].metric("Overall pass@1", f"{df[df.repetition == 1].passed.mean()*100:.0f}%")
        c[2].metric("Filter rate", f"{(df.error_type == 'content_filter').mean()*100:.1f}%")
        c[3].metric("Error rate", f"{(df.status != 'ok').mean()*100:.1f}%")
        st.subheader("Composite by model × task class")
        show = comp[["task_class", "model", "tier", "pass@1", "pass@k",
                     "latency_p50", "latency_p95", "ttft_p50", "tokens_per_s",
                     "cost_per_ok_usd", "filter_rate", "error_rate",
                     "composite", "verdict"]]
        st.dataframe(show, width='stretch', hide_index=True)

    # -- Semaphore matrix --
    with tabs[1]:
        st.subheader("Verdict matrix (model × task class)")
        st.caption(f"🟢 composite ≥75 & gates pass · 🟡 ≥60 or gate warn · 🔴 <60. "
                   f"Latency gate p95 < {P95_LATENCY_GATE_S}s, filter FP < 5%.")
        pivot = comp.pivot_table(index="model", columns="task_class",
                                 values="verdict", aggfunc="first")
        st.dataframe(pivot, width='stretch')
        st.subheader("Composite heat (numbers)")
        heat = comp.pivot_table(index="model", columns="task_class",
                                values="composite", aggfunc="first")
        st.dataframe(heat.style.map(_heat_color).format("{:.0f}", na_rep="—"),
                     width='stretch')

    # -- Charts --
    with tabs[2]:
        okc = comp.copy()
        if okc.cost_per_ok_usd.notna().any():
            st.subheader("Cost per successful task vs quality (per class)")
            fig = px.scatter(okc.dropna(subset=["cost_per_ok_usd"]),
                             x="cost_per_ok_usd", y="pass@1", color="model",
                             size="tokens_per_s", facet_col="task_class",
                             facet_col_wrap=3, hover_data=["latency_p95", "composite"],
                             labels={"cost_per_ok_usd": "$/successful task", "pass@1": "pass@1"})
            st.plotly_chart(fig, width='stretch')
        st.subheader("Latency profile (p50 / p95)")
        lat = agg.dropna(subset=["latency_p95"])
        if not lat.empty:
            fig2 = px.bar(lat, x="model", y=["latency_p50", "latency_p95"],
                          barmode="group", facet_col="task_class", facet_col_wrap=3)
            fig2.add_hline(y=P95_LATENCY_GATE_S, line_dash="dash", line_color="red",
                           annotation_text="15s gate")
            st.plotly_chart(fig2, width='stretch')
        st.subheader("pass@1 by model × class")
        fig3 = px.bar(comp, x="model", y="pass@1", color="task_class", barmode="group")
        st.plotly_chart(fig3, width='stretch')

    # -- Decision --
    with tabs[3]:
        st.subheader("Adoption decision matrix (Framework v1.0 Table 9)")
        dm = decision_matrix(comp)
        st.dataframe(dm, width='stretch', hide_index=True)
        for _, r in dm.iterrows():
            st.write(f"**{r.model}** ({r.tier}) → _{r.recommendation}_ · "
                     f"🟢{r.green} 🟡{r.conditional} · mean composite {r.mean_composite}")

    # -- Raw --
    with tabs[4]:
        st.subheader("Every call")
        cols = ["task_id", "task_class", "model", "repetition", "status", "passed",
                "eval_score", "latency_s", "ttft_s", "tokens_per_second",
                "prompt_tokens", "completion_tokens", "cost_usd", "error_type", "detail"]
        st.dataframe(df[cols], width='stretch', hide_index=True)
        with st.expander("Inspect a single response"):
            tid = st.selectbox("task_id", sorted(df.task_id.unique()))
            mdl = st.selectbox("model", sorted(df.model.unique()))
            sub = df[(df.task_id == tid) & (df.model == mdl)]
            for _, r in sub.iterrows():
                st.markdown(f"**rep {r.repetition}** · {r.status} · score {r.eval_score}")
                st.code(r.response_preview or "(empty)")
                if r.detail:
                    st.caption(r.detail[:1500])

    # -- Export --
    with tabs[5]:
        st.download_button("⬇ raw results (CSV)", df.to_csv(index=False),
                           "maasbench_raw.csv", "text/csv")
        st.download_button("⬇ aggregate (CSV)", agg.to_csv(index=False),
                           "maasbench_aggregate.csv", "text/csv")
        st.download_button("⬇ composite + verdict (CSV)", comp.to_csv(index=False),
                           "maasbench_composite.csv", "text/csv")
        st.download_button("⬇ one-page report (Markdown)",
                           _report_md(df, comp, decision_matrix(comp)),
                           "maasbench_report.md", "text/markdown")
else:
    st.info("Add at least one provider (with API key) and press **Run benchmark**.")
