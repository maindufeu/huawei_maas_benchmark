# Local model (gemma-4-26B-A4B, llama.cpp) — results & interpretation

Run `04ef133a3284` · 270 calls · 9 models · N=3 · 2026-07-01. Local endpoint: `http://127.0.0.1:8080/v1`.

## Headline

The local **gemma-4-26B** model scored **🟢 recommended (composite 100) in all four task
classes** and passed **30/30** task-runs (pass@1 = pass@k = 1.00 everywhere), with **p95 latency
≤ 9.9 s** — under the 15 s gate — and **$0 cost** (self-hosted).

| Class | pass@1 | p50 | p95 | tok/s | Composite | Verdict |
|-------|:-----:|:---:|:---:|:----:|:---------:|---------|
| T1 Code generation | 1.00 | 3.4 s | 9.9 s | 50 | 100 | 🟢 |
| T2 Refactoring     | 1.00 | 3.3 s | 4.5 s | 48 | 100 | 🟢 |
| T3 Debugging       | 1.00 | 1.8 s | 2.3 s | 46 | 100 | 🟢 |
| T5 Filter probes   | 1.00 | 5.7 s | 8.7 s | 48 | 100 | 🟢 |

Latency across all 30 local calls: min 0.9 s · median 2.9 s · max 10.3 s.

## How it compares to the cloud field (same tasks)

| Class | local pass@1 | rest-of-field avg pass@1 |
|-------|:-----------:|:------------------------:|
| T1 | **1.00** | 0.88 |
| T2 | 1.00 | 1.00 |
| T3 | 1.00 | 1.00 |
| T5 | **1.00** | 0.96 |

On **this** suite the local model matched or beat every cloud provider on correctness, and beat
all of them on latency and cost. It even cleared the filter probes cleanly (no content-filter,
because there is no ISO-27001 filter on a local endpoint).

## Read this before over-celebrating — why the 100s are partly "free"

The composite is a **within-run, relative** score. Three effects inflate the local model:

1. **$0 cost → full 25/25 cost points.** Any local model gets the maximum cost sub-score by
   definition; it is not comparable to a metered API on a level field.
2. **Fastest in the run → full/near-full 15/15 latency points**, because latency is normalized
   min–max against the (much slower) cloud models. On an absolute scale 9.9 s p95 is fine for
   batch but borderline for a snappy IDE.
3. **The suite is small and easy.** 12 tasks, mostly self-contained Python, some likely in
   training data (two-sum, LRU). Perfect quality here does **not** prove production readiness.

So the honest reading is: *gemma-4-26B is genuinely strong and fast enough for these tasks, and is
an excellent $0 quality/latency floor to benchmark the paid providers against — but the 100/100 is
a best-case, easy-suite, home-court number, not a verdict that it replaces a frontier model.*

## Suggestions / next steps specific to the local model

1. **Stress it with the hard suite.** Add multi-file, TS/Java, and real GitHub-issue (SWE-bench)
   tasks. The gap vs GLM-5.2 / DeepSeek will only show on genuinely hard, long-context work.
2. **Run a concurrency/throughput test.** These latencies are single-user and warm. A local
   26B model serves one request fast but may collapse under 10+ concurrent developers. Measure
   tokens/s and p95 at concurrency 1/4/8/16 before considering it for a team.
3. **Add a code-quality grader** (ruff/bandit/complexity). "Passes tests" hides whether the local
   model writes maintainable, secure code vs just test-passing code.
4. **Use it as the always-on baseline** in every future run: it costs nothing and gives a stable
   reference line. If a paid provider can't beat a local 26B on your tasks, that's a strong signal.
5. **If quality holds on the hard suite,** the local model becomes a serious candidate for
   cost-sensitive, latency-tolerant, data-sensitive flows (no data leaves the machine) — exactly
   the "batch / boilerplate / test-gen" niche in the Framework's routing plan.
