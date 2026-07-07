"""OpenAI-compatible provider adapter.

Every provider (Huawei MaaS, Azure OpenAI, Bedrock-via-LiteLLM, Vertex, DeepSeek,
Gemini) speaks the same Chat Completions protocol — only base_url + api_key + model
change. This mirrors Listing 1 in the Framework v1.0 doc but adds:

  * streaming with time-to-first-token (TTFT) capture,
  * robust error/filter classification (ModelArts sensitive-info 403, 429, 5xx, timeout),
  * reasoning-token accounting for GLM / DeepSeek-R1.

The API key is passed in explicitly (the UI collects it from the front-end); we do
NOT read it from the environment unless asked to.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - openai is a hard dep, but keep import lazy-safe
    OpenAI = None  # type: ignore


HUAWEI_BASE_URL = "https://api-ap-southeast-1.modelarts-maas.com/openai/v1"
# Google Gemini's OpenAI-compatibility layer — note the trailing slash matters for some setups.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# Azure AI Foundry / Azure OpenAI expose an OpenAI-compatible v1 path per resource:
#   https://<resource>.services.ai.azure.com/openai/v1   (model = the deployment name)
AZURE_BASE_URL_HINT = "https://<resource>.services.ai.azure.com/openai/v1"
# llama.cpp server (./server) exposes an OpenAI-compatible API at /v1.
LLAMACPP_BASE_URL = "http://127.0.0.1:8080/v1"


def is_local(base_url: str) -> bool:
    """True for localhost endpoints (llama.cpp, Ollama, LM Studio) — treated as $0 cost."""
    return any(h in (base_url or "") for h in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))


@dataclass
class Provider:
    """A callable model endpoint.

    name        : label shown in the UI / reports (e.g. "huawei_glm52")
    base_url    : OpenAI-compatible base URL
    model       : model id sent in the request (e.g. "glm-5.2")
    api_key     : secret, provided from the front-end
    tier        : frontier | balanced | cost | reasoning | small (for grouping)
    extra_body  : provider-specific knobs (e.g. {"reasoning_effort": "high"})
    """

    name: str
    base_url: str
    model: str
    api_key: str = ""
    tier: str = "frontier"
    vendor: str = "custom"          # huawei | azure | google | local | deepseek | custom
    extra_body: dict = field(default_factory=dict)

    def public(self) -> dict:
        d = asdict(self)
        d.pop("api_key", None)
        return d


@dataclass
class CallResult:
    """Everything one model call produced — success or failure."""

    provider: str
    model: str
    status: str  # "ok" | "filtered" | "rate_limited" | "server_error" | "timeout" | "error"
    text: str = ""
    reasoning_text: str = ""
    latency_s: float = 0.0
    ttft_s: Optional[float] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def tokens_per_second(self) -> Optional[float]:
        if self.completion_tokens and self.latency_s > 0:
            return self.completion_tokens / self.latency_s
        return None


def _classify_error(exc: Exception) -> tuple[str, str]:
    """Map an exception to (status, error_type)."""
    msg = str(exc)
    low = msg.lower()
    # Huawei ModelArts content-review filter (ISO 27001 profile)
    if "modelarts.81011" in low or "sensitive information" in low:
        return "filtered", "content_filter"
    # HTTP status hints (openai SDK exposes .status_code on APIStatusError)
    status_code = getattr(exc, "status_code", None)
    if status_code == 429 or "rate limit" in low or "429" in low:
        return "rate_limited", "rate_limit"
    if status_code and 500 <= int(status_code) < 600:
        return "server_error", f"http_{status_code}"
    if "timeout" in low or "timed out" in low:
        return "timeout", "timeout"
    if status_code:
        return "error", f"http_{status_code}"
    return "error", exc.__class__.__name__


def call_model(
    provider: Provider,
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    stream: bool = True,
    timeout: float = 180.0,
    extra_body: Optional[dict] = None,
) -> CallResult:
    """Call one provider once and return a fully-populated CallResult.

    Streaming is on by default so we can measure TTFT (the single biggest lever
    against the observed 27-40s latency). Token usage is requested via
    stream_options.include_usage and falls back gracefully when unsupported.
    """
    if OpenAI is None:
        return CallResult(provider.name, provider.model, "error",
                          error_type="import_error",
                          error_message="openai package not installed")

    client = OpenAI(base_url=provider.base_url, api_key=provider.api_key or "EMPTY",
                    timeout=timeout, max_retries=0)
    body = dict(provider.extra_body)
    if extra_body:
        body.update(extra_body)

    t0 = time.perf_counter()
    try:
        if stream:
            return _call_streaming(client, provider, messages, temperature,
                                   max_tokens, body, t0)
        return _call_blocking(client, provider, messages, temperature,
                              max_tokens, body, t0)
    except Exception as exc:  # noqa: BLE001 - we want to capture every failure mode
        status, etype = _classify_error(exc)
        return CallResult(provider.name, provider.model, status,
                          latency_s=time.perf_counter() - t0,
                          error_type=etype, error_message=str(exc)[:2000])


def _call_streaming(client, provider, messages, temperature, max_tokens, body, t0):
    kwargs: dict[str, Any] = dict(
        model=provider.model, messages=messages, temperature=temperature,
        max_tokens=max_tokens, stream=True,
        stream_options={"include_usage": True},
    )
    if body:
        kwargs["extra_body"] = body

    chunks: list[str] = []
    reasoning: list[str] = []
    ttft: Optional[float] = None
    usage = None
    try:
        stream = client.chat.completions.create(**kwargs)
    except TypeError:
        # some gateways reject stream_options; retry without it
        kwargs.pop("stream_options", None)
        stream = client.chat.completions.create(**kwargs)

    for chunk in stream:
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        piece = getattr(delta, "content", None)
        rpiece = getattr(delta, "reasoning_content", None)
        if piece:
            if ttft is None:
                ttft = time.perf_counter() - t0
            chunks.append(piece)
        if rpiece:
            if ttft is None:
                ttft = time.perf_counter() - t0
            reasoning.append(rpiece)

    latency = time.perf_counter() - t0
    return _finalize(provider, "".join(chunks), "".join(reasoning), latency, ttft, usage)


def _call_blocking(client, provider, messages, temperature, max_tokens, body, t0):
    kwargs: dict[str, Any] = dict(
        model=provider.model, messages=messages, temperature=temperature,
        max_tokens=max_tokens, stream=False,
    )
    if body:
        kwargs["extra_body"] = body
    resp = client.chat.completions.create(**kwargs)
    latency = time.perf_counter() - t0
    msg = resp.choices[0].message
    text = msg.content or ""
    reasoning = getattr(msg, "reasoning_content", "") or ""
    return _finalize(provider, text, reasoning, latency, None, getattr(resp, "usage", None))


def _finalize(provider, text, reasoning, latency, ttft, usage) -> CallResult:
    pt = ct = rt = tt = 0
    if usage is not None:
        pt = getattr(usage, "prompt_tokens", 0) or 0
        ct = getattr(usage, "completion_tokens", 0) or 0
        tt = getattr(usage, "total_tokens", 0) or 0
        details = getattr(usage, "completion_tokens_details", None)
        if details is not None:
            rt = getattr(details, "reasoning_tokens", 0) or 0
    return CallResult(
        provider=provider.name, model=provider.model, status="ok",
        text=text, reasoning_text=reasoning,
        latency_s=latency, ttft_s=ttft,
        prompt_tokens=pt, completion_tokens=ct, reasoning_tokens=rt, total_tokens=tt,
    )


# ---- Convenience presets ---------------------------------------------------

def huawei_provider(name: str, model: str, api_key: str = "", **kw) -> Provider:
    """Build a Huawei MaaS provider on the CN-HK OpenAI-compatible endpoint."""
    return Provider(name=name, base_url=HUAWEI_BASE_URL, model=model,
                    api_key=api_key, **kw)


# Models exposed by the Huawei enterprise account (from the endpoint hand-off).
HUAWEI_MODELS = [
    ("glm-5.2", "frontier"),
    ("glm-5.1", "balanced"),
    ("deepseek-v3.2", "cost"),
    ("deepseek-v3.1-terminus", "cost"),
    ("DeepSeek-V3", "cost"),
    ("qwen3-32b", "small"),
    ("deepseek-r1-250528", "reasoning"),
]
