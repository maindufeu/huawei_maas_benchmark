# Incident summary — GLM-5.1 runaway reasoning on Huawei MaaS endpoint

**Date observed:** July 1, 2026, during a 270-call coding benchmark (9 models × 10 tasks × 3 repetitions)
**Affected call:** model `glm-5.1`, task `t1_parse_iso_duration`, repetition 1 of 3
**Impact:** one API call ran for **51.9 minutes**, produced **98,304 reasoning tokens and zero answer tokens**, and was billed **$0.43** — for a 148-token prompt whose correct answer fits in ~250 tokens.

---

## The exact request

Standard OpenAI-compatible chat completion, identical to the other 269 calls in the run:

| Parameter | Value |
|---|---|
| Prompt | "Write `iso_duration_to_seconds(s: str) -> int` parsing ISO-8601 durations like `P1DT2H3M4S`; raise `ValueError` on malformed input" (148 prompt tokens) |
| `temperature` | 0.2 |
| `max_tokens` | **4096** |
| `stream` | true, with `stream_options: {"include_usage": true}` |
| Client timeout | 180 s (read timeout; never triggers while chunks keep arriving) |
| Retries | 0 |

## Timeline

| Time | Event |
|---|---|
| t = 0 | Request sent |
| t = 3.1 s | First token arrives (TTFT). Server accepted immediately — no queueing, no 429 |
| 3.1 s → 51.9 min | Model streams `reasoning_content` continuously at a steady **31.6 tokens/s**, never exits the reasoning channel, never starts the answer |
| t = 3,114 s | Stream ends at **98,304 reasoning tokens** (= 96 × 1024 — a server-side hard limit, not the model finishing). `content` field is **empty** |
| — | HTTP status: success (`200`/`ok`). Usage bills 98,304 completion tokens → **$0.4327** |

Sanity check: `3.1 s + 98,304 tokens ÷ 31.6 tok/s = 3,114 s` — matches the measured latency exactly. **100% of the elapsed time is token generation at normal speed; there is no unexplained network or queueing time.**

## Why this is not rate limiting

1. **No 429 or error was returned.** Our harness explicitly classifies HTTP 429 / "rate limit" responses; this call returned a clean success status.
2. **TTFT was normal (3.1 s).** Rate limiting shows up *before* the first token. Repetitions 2 and 3 of the same task had TTFT of 5.3 s and 2.2 s.
3. **Throughput was constant and identical to healthy calls:** 31.6 tok/s during the incident vs 30.2 / 31.7 tok/s on the two successful repetitions of the same task. Throttling would degrade or stall the stream; it never did.
4. **The other 29 GLM-5.1 calls in the same run window were unaffected.** An account- or endpoint-level limit would have hit several calls, not exactly one.

The root cause is model behavior, not infrastructure: the model entered a self-verification loop while reasoning about malformed-input edge cases ("runaway / overthinking loop", a known failure mode of reasoning models). Its two *successful* attempts at the same task already used ~3,900 reasoning tokens each; attempt 1 simply never converged and generated reasoning until the server cut it off at 98,304 tokens.

## The main problem: `max_tokens` did not bound the reasoning channel

We sent `max_tokens: 4096`. The call generated **98,304 tokens** — 24× the requested cap. For comparison, `qwen3-32b` on the same endpoint stopped at *exactly* 4,096 tokens in five separate calls, so the cap is enforced for models that think in the visible channel, but **`reasoning_content` for GLM-5.1 appears not to be counted against `max_tokens`**. This means a client currently has **no effective way to bound worst-case latency or cost** of a GLM-5.1 call: the only backstop is the server's own 98,304-token ceiling, i.e. ~52 minutes and ~$0.43 per runaway at observed throughput.

Note the client's 180 s timeout also cannot help: it is a read timeout, and the stream never pauses long enough to trigger it.

## Questions for the MaaS team

1. Is `max_tokens` supposed to include `reasoning_content` tokens for GLM-5.1? If not, is that documented?
2. Is there a server-side parameter to cap thinking (e.g. `thinking_budget`, `reasoning_effort`, or equivalent in `extra_body`)?
3. What exactly is the 98,304-token ceiling we hit (per-request output limit? context limit?), and is it configurable per account?
4. Are runaway-reasoning generations billable in full? (This one billed $0.43 for an empty answer.)

## Reproducibility

The event is stochastic (observed on 1 of 3 repetitions of this specific task; 0 of 27 on other tasks). We can re-run the same task N times with identical parameters to estimate the runaway rate; healthy attempts use ~4k reasoning tokens, so any call exceeding ~10k reasoning tokens is already in the runaway regime and can be aborted early for confirmation. We did not persist the reasoning transcript of the original event (only server-reported usage and timing), but a rerun can capture the full reasoning text if useful for your investigation.

*Raw evidence available: per-call record with server-reported usage (`prompt_tokens=148, completion_tokens=98304, reasoning_tokens=98304`), TTFT, latency, and steady-state throughput, plus the two healthy repetitions of the same task for baseline.*
