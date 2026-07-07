# MaaS Coding Benchmark

A small, extensible webapp to benchmark **Huawei ModelArts MaaS** coding models
(GLM-5.2/5.1, DeepSeek-V3.x, Qwen3-32B, DeepSeek-R1) against any OpenAI-compatible
baseline (Gemini, Claude via a proxy, DeepSeek direct, Frida's current provider) for
the Frida ITA Copilot POC. Implements the measurable core of **Huawei MaaS Coding POC
Framework v1.0**: scenario task suite (T1–T5), pass@1/pass@3 via sandboxed pytest,
TTFT + p50/p95 latency, $/successful task, content-filter false-positive rate, a
weighted composite score, semaphore matrix and adoption decision matrix.

The API key is entered **in the front-end** — nothing is written to disk.

> **📖 Read [METHODOLOGY.md](METHODOLOGY.md)** for a critical breakdown of *what* is measured
> and *why*, the limitations to know before quoting numbers, and the prioritized next steps to
> harden the methodology.

## Quick start (React app)

```bash
./run.sh          # venv + pip install, builds the React app, serves app+API on :8000
```
Open **http://127.0.0.1:8000**.

Dev mode with hot-reload (Vite :5173 + FastAPI :8000):
```bash
./run-dev.sh      # open http://127.0.0.1:5173
```

Then in the browser:
1. **Providers → Add provider** → pick a *Type* (presets pre-fill the URL):
   - **Huawei MaaS** — paste key, multi-select models (`glm-5.2`, …), optional `reasoning_effort`.
   - **Google Gemini** — key + a valid model id (`gemini-3.5-flash`, `gemini-3.1-pro-preview`).
     ⚠️ the generic `gemini-3-pro` returns **404**; use a real id. Base URL keeps its trailing slash.
   - **Local (llama.cpp)** — `http://127.0.0.1:8080/v1`, no key needed; auto-priced at **$0**.
   - **Custom** — any OpenAI-compatible URL + key + model.
2. Tick **💾 Recordar credenciales** to persist providers+keys to `.secrets.json` (local, gitignored)
   so you don't re-paste every session. **🔌 Test connection** pings each provider and shows the
   exact error (a 404 tells you the model id is wrong). **🗑 Forget saved** wipes the file.
3. Optionally enable a **judge** (for T4 design tasks).
4. Pick task classes, set repetitions (N≥3), press **Run**.
5. Read **Scoreboard → Semaphore → Charts → Decision**, then **Export** CSV / one-page MD.

## Headless / reproducible runs

```bash
export HUAWEI_MAAS_KEY=sk-...
python cli.py --models glm-5.2 deepseek-v3.2 --reps 3 --out results/jul2
# add a baseline whose key lives in $GEMINI_KEY:
python cli.py --models glm-5.2 \
  --provider gemini=https://generativelanguage.googleapis.com/v1beta/openai::gemini-3-pro::GEMINI_KEY
```

## Layout

```
web/                    React + Vite + TypeScript + Tailwind + Recharts frontend
  src/App.tsx           layout + SSE subscription
  src/api.ts            typed API client
  src/components/       Providers, RunPanel, Results (scoreboard/semaphore/charts/decision)
server/
  main.py               FastAPI: /api/* endpoints + serves web/dist; SSE progress stream
  run_manager.py        provider store + background run + results (wraps maasbench core)
maasbench/              UI-agnostic core (shared by React backend, CLI, and legacy Streamlit)
  providers.py          OpenAI-compatible adapter; TTFT, tokens, error/filter classes
  tasks.py              YAML task loader + code extraction
  pricing.py            $/1M price table -> $/successful task
  runner.py             fan-out (task×model×rep), grade, collect rows
  scoring.py            aggregate, weighted composite, semaphore, decision matrix
  evaluators/           pluggable graders (unit_test, contains, json_keys, judge)
  secrets_store.py      local .secrets.json persistence for provider keys
config/pricing.yaml     editable price snapshot (Framework Table 5)
tasks/*.yaml            the seed suite (T1 gen, T2 refactor, T3 debug, T4 design, filter probes)
cli.py                  headless batch runner (same core)
app.py                  legacy Streamlit UI (still works: `streamlit run app.py`)
```

## Extending it

- **New task**: drop a YAML entry in `tasks/`. Pick an `evaluation.type` from the registry.
- **New evaluator** ("integrate other evaluations"): subclass `Evaluator` in
  `maasbench/evaluators/`, `register()` it — the runner and UI pick it up automatically.
  Natural next ones: static analysis (ruff/bandit/complexity), dual-judge triangulation,
  a real agentic SWE-bench loop, edit-distance-to-reference for the productivity proxy.
- **New provider**: it's just base_url + api_key + model. Bedrock/Vertex work via a
  LiteLLM proxy exposing the OpenAI shim.

## Metrics & gates (from Framework v1.0)

Composite (0–100) = 0.45·quality(pass@1) + 0.25·cost + 0.15·latency + 0.15·reliability,
normalized within each task class. Verdict: 🟢 ≥75 & gates pass · 🟡 ≥60 · 🔴 <60.
Hard gates: streaming **p95 < 15 s**, content-filter false-positive **< 5 %**.

## Security note

Unit-test grading **executes model-generated code** in a temp dir with a timeout — fine
for a trusted run on your machine, not a hardened sandbox. For untrusted scale, wrap the
pytest subprocess in Docker/firejail (see `evaluators/unit_test.py`).
