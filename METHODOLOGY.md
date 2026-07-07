# Methodology — what this benchmark measures, why, and how to make it better

This is the critical companion to the [README](README.md). The README tells you how to
*run* the tool; this document explains what the numbers *mean*, where they are trustworthy,
where they are not, and what to build next. It maps to **Huawei MaaS Coding POC Framework
v1.0** but is honest about the gap between that blueprint and what the harness measures today.

---

## 1. Philosophy: a scenario benchmark, not a leaderboard

The goal is **not** "which model is best overall". Public leaderboards (SWE-bench, LiveCodeBench)
already answer that, and they say GLM-5.2 is strong. The goal is a **Softtek-specific, decision-
grade** answer to: *for each kind of coding work Frida ITA actually does, which provider gives
acceptable quality at the lowest cost within a tolerable latency?*

Consequences of that framing, baked into the design:

- **Decision is per task class, not a single ranking.** A model can be 🟢 for batch generation
  and 🔴 for interactive use. The output is a matrix, not a trophy.
- **Latency and cost are first-class, not footnotes.** A model that is 2% more accurate but 5×
  slower and 10× costlier loses for high-volume workloads.
- **Failures are data.** A content-filter block or a timeout is recorded and scored, not retried
  away. That is the whole point of the filter-probe class.

---

## 2. The five dimensions — definition, instrumentation, why, and the catch

Each completion is scored on the dimensions below. Weights are the Framework Table 1 numbers,
rescaled to the four things the harness measures **automatically today** (`maasbench/scoring.py`,
`WEIGHTS`). The other Table 1 dimensions (code-quality static analysis, dual-judge rubric) are
defined in the code but not yet wired into the composite — see §6–7.

| Dimension | Weight | What it is | How it's measured | Why it matters | The catch |
|---|---|---|---|---|---|
| **Functional quality** | 45% | Does the code work? | `pass@1`, `pass@k` from sandboxed `pytest` (`evaluators/unit_test.py`) | The only objective signal; everything else is moot if the code is wrong | Only as good as the test suite; passing ≠ idiomatic/maintainable |
| **Cost** | 25% | $ per *successful* task | `tokens × price / passes` (`config/pricing.yaml`) | Kills the "90% cheaper per token" illusion — retries and failures are priced in | Undefined when a model passes nothing (shown as "—", not 0); prices are a Jun-2026 snapshot |
| **Latency** | 15% | End-to-end + TTFT | client timers, streaming, p50/p95 | The #1 open risk on the CN-HK endpoint (27–40 s observed) | see §4 — the p95 estimate is noisy at N=3 |
| **Reliability** | 15% | Did the call even succeed cleanly? | error rate + content-filter rate over all calls | Enterprise-readiness; a great model behind a flaky endpoint is unusable | Small N → a single blip moves the rate a lot |

**Quality metrics precisely defined:**
- `pass@1` = fraction of tasks whose **first** repetition passed *all* its unit tests. This is the
  honest "one-shot" number a developer experiences.
- `pass@k` = fraction of tasks where **at least one** of the k repetitions passed. Measures
  "can it get there with retries" — relevant for agentic/batch flows, misleading for interactive.
- A task passes only if **every** assertion passes; partial credit (`eval_score`) is reported
  separately but does not count as a pass.

---

## 3. The task suite — what each class probes

Tasks live in `tasks/*.yaml`; each declares an `evaluation.type` that selects a grader from the
registry. Current seed suite is **12 tasks** — deliberately small, representative, and fully
auto-graded so a run is fast and reproducible. Framework v1.0 targets **180**.

| Class | Intent | Grader | Seed count | What a good score tells you |
|---|---|---|---|---|
| **T1 Code generation** | Write new code from a spec | `unit_test` | 3 | Baseline competence: functions, a class, parsing/edge cases |
| **T2 Refactoring** | Change code without changing behavior | `unit_test` (behavior-preserving tests + a "modernized?" assertion) | 2 | Can it improve code and *not* regress? |
| **T3 Debugging** | Fix a seeded bug so hidden tests pass | `unit_test` (buggy file given as context) | 2 | Reasoning over existing code, the most SWE-bench-like signal here |
| **T4 System design** | Open-ended architecture answer | `judge` (LLM rubric) | 2 | Design reasoning where there is no single right answer |
| **Filter probes** | Legitimate code containing "sensitive" keywords (password, token, SQLi) | `unit_test` / `contains` | 3 | The ISO-27001 false-positive rate — the exact failure you hit in the Jupyter run |

Graders available (`maasbench/evaluators/`, extensible): `unit_test` (pytest sandbox),
`contains` (substring/regex), `json_keys` (format compliance), `judge` (1–5 rubric, optional).

---

## 4. Scoring model and its rationale

**Composite (0–100), per (model × task class):**

```
composite = 100 · (0.45·quality + 0.25·cost + 0.15·latency + 0.15·reliability)
```

- `quality` and `reliability` are **absolute** (0–1).
- `cost` and `latency` are **relative**: min–max normalized *within each task class* so the
  cheapest/fastest model in that class scores 1.0 and the worst 0.0 (`_inv_norm`). This makes
  the composite a **within-run comparison**, not a portable absolute score.

**Verdict bands + hard gates** (`_verdict`):
- 🟢 recommended: composite ≥ 75 **and** gates pass.
- 🟡 conditional: composite ≥ 60, or ≥ 75 but a gate is tripped (tag shows which).
- 🔴 disqualified: composite < 60.
- **Gates** (from Framework §1.2/§9): streaming **p95 < 15 s** and content-filter FP **< 5%**.
  A model can be excellent on quality and still be capped at 🟡 by latency — by design.

**Latency percentiles** use the *inclusive* method (`_pct`), which keeps p95 within the observed
range. This was a real fix: at N=3 the default *exclusive* method extrapolated p95 above the
slowest observed call and falsely tripped the 15 s gate.

**Adoption decision matrix** (`decision_matrix`, Framework Table 9): rolls the per-class verdicts
into one recommendation per model — *Adopt broadly* (🟢 in ≥3 classes), *Adopt for niches*,
*Defer* (latency/filter gate), or *Reject*.

---

## 5. Controls that make the comparison fair

- Same system prompt, temperature, and `max_tokens` across models.
- Streaming on, to attribute latency to TTFT vs generation.
- A warm-up call per provider so cold-start (e.g. the 8.5 s first local call) doesn't pollute p50/p95.
- N ≥ 3 repetitions; medians and p95 reported, never a single run.
- Local endpoints auto-priced at $0 so a self-hosted baseline is an honest cost floor.

---

## 6. Critical limitations — read before quoting numbers

These are the reasons a result could be **misleading**, roughly in order of impact:

1. **Tiny, possibly-unrepresentative suite.** 12 tasks, mostly small self-contained Python. It
   under-weights the things Frida developers actually do: multi-file changes, real repos, TS/Java,
   long context. High `pass@1` here ≠ production readiness. *This is the biggest caveat.*
2. **"Passes tests" is a weak proxy for "good code."** Nothing yet measures readability,
   idiomaticity, security, or complexity. A model can hard-code to the tests and score 100.
3. **Composite mixes absolute and relative axes.** Because cost/latency are normalized *within a
   run*, a model benchmarked **alone** gets latency = cost = 1.0 for free (best == worst → 1.0),
   inflating its composite by up to ~40 points. Composites are only comparable **within the same
   multi-model run**, never across runs.
4. **No statistical significance.** No confidence intervals, no McNemar/Wilcoxon. With N=3 a
   pass-rate or latency difference between two models may be noise. The Framework specifies
   bootstrap CIs and paired tests; the harness does not compute them yet.
5. **The "T5 agentic" class is not implemented.** The T5 slot currently holds *filter probes*, and
   they inherit the label "Mixed workflow (agentic)" from `CLASS_LABELS`. There is no real
   read→patch→test→self-repair loop yet. Don't claim agentic-coding coverage.
6. **Single, self-selected judge.** T4 uses one LLM judge with no calibration or second judge;
   it can favor its own model family. Framework calls for dual judges and dropping disagreements.
7. **Benchmark contamination.** Public-style tasks (two-sum, LRU) may be in training data;
   scores can be optimistic. Private, novel tasks are needed to counter this.
8. **Not a hardened sandbox.** Model code runs in a temp dir with a timeout but no container/network
   isolation — fine for a trusted local run, not for untrusted scale.
9. **Prices are a manual snapshot.** `pricing.yaml` is Jun-2026 and partly approximate (DS-R1,
   Gemini flash, DeepSeek-V3 marked as such). Cost rankings inherit that uncertainty.

---

## 7. Next steps to improve the methodology (prioritized)

**Tier 1 — makes results defensible (do before scaling adoption):**
- **Grow and diversify the suite** toward the Framework's 180: add TS/Java, multi-file edits, and
  real GitHub issues (SWE-bench Verified subset) for genuine T3/T5 signal.
- **Add a code-quality grader** (`ruff`/`bandit`/complexity) as a new evaluator and fold it into the
  composite at the Table-1 weight — closes limitation #2.
- **Implement the real T5 agentic loop** (read repo → propose patch → run tests → self-repair) and
  relabel the current filter probes to their own class. Closes #5.
- **Confidence intervals + paired tests** (bootstrap CI, McNemar for pass/fail, Wilcoxon for
  latency/cost). Report "GLM-5.2 vs DeepSeek: Δpass = 4% ± 6% (n.s.)" instead of bare deltas. Closes #4.

**Tier 2 — hardening and rigor:**
- **Dual-judge triangulation** for T4 with disagreement-dropping and a human spot-check. Closes #6.
- **Contamination controls:** add private/novel tasks and a LiveCodeBench-style recent-problems set. #7.
- **Sandbox in Docker/firejail** for safe scale-out and to run untrusted or client-flavored code. #8.
- **Absolute latency/cost scoring option** (fixed reference points instead of within-run min–max) so
  composites are comparable across runs. Closes #3.

**Tier 3 — product/business:**
- **Sensitivity sweeps** the Framework asks for: temperature ∈ {0, 0.2, 0.7}, `reasoning_effort`
  ∈ {high, max} for GLM-5.2 / DeepSeek — separate model quality from decoding luck.
- **Cost-model integration:** wire the per-task cost into the Frida routing simulation (Framework §7)
  to project real monthly savings, not per-token savings.
- **CI regression mode:** run a fixed suite nightly against providers and alert on quality/latency/
  price drift (the Framework's price-index sentinel).
- **Live price feed** to replace the manual `pricing.yaml` snapshot.

---

## 8. How to read a result responsibly (one paragraph)

Trust the **filter rate**, **error rate**, and **latency p50/p95** — they are directly measured over
many calls and are the most decision-relevant, least-contaminated signals. Treat **pass@1** as
directionally useful but suite-limited. Treat the **composite** as a *within-this-run* ranking, not
a portable grade. And never quote a two-model difference as real until it survives a significance
test. Used that way, the tool answers the question that matters for the Jul-3 review — *where can
Huawei MaaS cut cost without breaking the developer experience?* — while being honest about what it
cannot yet see.
