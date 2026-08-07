"""OpenAI-compatible chat-completions adapter.

Covers Ollama, vLLM, TGI, LiteLLM proxies, and most self-hosted gateways --
including whatever a TCS endpoint turns out to be, since almost everything
speaks this shape. Written against `httpx` directly rather than the OpenAI SDK
because we must tolerate endpoints that implement the protocol *partially*: no
JSON mode, no streaming, non-standard usage accounting. An SDK that assumes full
compliance turns a degraded endpoint into a crash.

Every outbound call goes through `forge.bootstrap` first, so TLS verification
uses the OS trust store (ADR-0009).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx

from forge.application.ports.llm import (
    Completion,
    LLMRateLimitedError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelCapabilities,
)
from forge.bootstrap import init as bootstrap_init
from forge.bootstrap import verify_for
from forge.domain.provenance import TokenUsage

if TYPE_CHECKING:
    from forge.application.ports.llm import GenerationRequest
    from forge.infrastructure.llm.config import ProviderConfig

# Probe prompts are tiny on purpose: a probe that costs real tokens will be
# disabled by whoever is paying, and then nobody knows what the endpoint can do.
_PROBE_PROMPT = "Reply with the single word: ok"

# 32x32 solid-colour PNGs for the discriminating vision probe. Small enough to
# cost almost nothing, unambiguous enough that a model which can see them cannot
# reasonably answer wrong.
_RED_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAKElEQVR4nO3"
    "NsQ0AAAzCMP5/un0CNkuZ41wybXsHAAAAAAAAAAAAxR4yw/wuPL6QkAAAAABJRU5ErkJggg=="
)
_BLUE_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAJklEQVR4nO3"
    "NsQkAAAjAsP7/tF7hIASyp5pjAoFAIBAIBAKB4EmwOkv8Lm7+zY4AAAAASUVORK5CYII="
)
_PROBE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}


class OpenAICompatAdapter:
    """One configured provider. The chain and fallback live in `service.py`."""

    def __init__(self, config: ProviderConfig, *, timeout_ms: int = 8_000) -> None:
        bootstrap_init()
        self._config = config
        self._timeout = timeout_ms / 1000.0
        base = (config.base_url or "").rstrip("/")
        # Some gateways publish the OpenAI surface at the root rather than
        # under /v1 (TCS GenAI Lab does), so only append /v1 when the caller
        # already asked for it in the base URL.
        self._url = f"{base}/chat/completions"
        # Per-host, never global. See forge.bootstrap.verify_for.
        self._verify = verify_for(self._url)

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def model(self) -> str:
        return self._config.model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _payload(self, request: GenerationRequest, *, json_mode: bool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            if m.images:
                # Multimodal content blocks. Only reached when the probe
                # confirmed vision; otherwise the caller never gets here.
                content: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
                content += [
                    {"type": "image_url", "image_url": {"url": uri}} for uri in m.images
                ]
                messages.append({"role": m.role, "content": content})
            else:
                messages.append({"role": m.role, "content": m.content})

        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": request.temperature,
            "stream": False,
        }
        if request.max_output_tokens:
            payload["max_tokens"] = request.max_output_tokens
        if request.stop:
            payload["stop"] = list(request.stop)
        if json_mode and request.response_schema:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def generate(self, request: GenerationRequest, *, json_mode: bool = True) -> Completion:
        """Single attempt against this provider. Raises; the chain handles it."""
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=self._verify) as client:
                response = await client.post(
                    self._url, headers=self._headers(), json=self._payload(
                        request, json_mode=json_mode
                    )
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"{self.name}: timed out after {self._timeout:.1f}s") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"{self.name}: {type(exc).__name__}: {exc}") from exc

        if response.status_code == 429:
            raise LLMRateLimitedError(f"{self.name}: rate limited")
        if response.status_code >= 400:
            raise LLMUnavailableError(
                f"{self.name}: HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            body = response.json()
            choice = body["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailableError(
                f"{self.name}: unparseable response: {response.text[:200]}"
            ) from exc

        usage = body.get("usage") or {}
        latency_ms = int((time.perf_counter() - started) * 1000)

        return Completion(
            text=text,
            tier_requested=request.tier,
            tier_used=request.tier,
            provider_used=self.name,
            model_used=body.get("model", self._config.model),
            latency_ms=latency_ms,
            tokens=TokenUsage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                cost_usd=0.0,   # priced by the service layer, per provider
            ),
            finish_reason=str(choice.get("finish_reason", "stop")),
        )

    async def probe(self, *, timeout_ms: int = 6_000) -> ModelCapabilities:
        """Measure what this endpoint can actually do.

        Never raises. An unreachable provider is a fact to report on /admin, not
        an exception to crash boot with (`fail_open` in models.yaml).
        """
        timeout = timeout_ms / 1000.0
        base = ModelCapabilities(provider=self.name, model=self._config.model, reachable=False)

        payload = {
            "model": self._config.model,
            "messages": [{"role": "user", "content": _PROBE_PROMPT}],
            "max_tokens": 5,
            "stream": False,
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self._url, headers=self._headers(), json=payload)
                latency_ms = int((time.perf_counter() - started) * 1000)
                if response.status_code >= 400:
                    return ModelCapabilities(
                        provider=self.name, model=self._config.model, reachable=False,
                        measured_p50_latency_ms=latency_ms,
                        error=f"HTTP {response.status_code}: {response.text[:120]}",
                    )

                json_ok = await self._probe_json_mode(client)
                vision_ok = await self._probe_vision(client)

                return ModelCapabilities(
                    provider=self.name,
                    model=self._config.model,
                    reachable=True,
                    supports_vision=vision_ok,
                    # Inferred rather than measured: exercising tool calling
                    # needs a tool round trip, which is too slow for boot. JSON
                    # mode is the practical proxy and is what we actually rely on.
                    supports_function_calling=json_ok,
                    supports_json_mode=json_ok,
                    supports_streaming=True,
                    max_context=0,   # not advertised by this protocol
                    measured_p50_latency_ms=latency_ms,
                )
        except Exception as exc:  # noqa: BLE001 - a probe must never crash boot
            return ModelCapabilities(
                provider=self.name, model=self._config.model, reachable=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        return base

    async def _probe_json_mode(self, client: httpx.AsyncClient) -> bool:
        try:
            response = await client.post(
                self._url,
                headers=self._headers(),
                json={
                    "model": self._config.model,
                    "messages": [
                        {"role": "user", "content": 'Reply with JSON only: {"ok": true}'}
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 20,
                    "stream": False,
                },
            )
            if response.status_code >= 400:
                return False
            content = response.json()["choices"][0]["message"]["content"] or ""
            return "ok" in content.lower()
        except Exception:  # noqa: BLE001
            return False

    async def _probe_vision(self, client: httpx.AsyncClient) -> bool:
        """Decide whether this endpoint can genuinely see an image.

        A status-code check is NOT sufficient and we learned that the hard way:
        Ollama accepts the multimodal message shape for a text-only model,
        silently drops the image block, and returns HTTP 200. `llama-3.2-3b`
        was reported as vision-capable on that basis, which is precisely the
        false capability claim this probe exists to prevent.

        So the test is *discriminating*: send solid-colour images and require
        the model to name them. A text-only model has no information and must
        guess. Two trials with different colours are used because one lucky
        guess is likely enough to matter (a blind guesser names any given common
        colour a fair fraction of the time); two independent correct answers are
        not. False negatives are the safe direction here -- under-claiming vision
        costs us a nice-to-have narration path, while over-claiming it routes the
        pipeline into a step that cannot work.
        """
        for colour, png in (("red", _RED_PNG), ("blue", _BLUE_PNG)):
            try:
                response = await client.post(
                    self._url,
                    headers=self._headers(),
                    json={
                        "model": self._config.model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "Reply with exactly one word: the dominant "
                                            "colour of this image."
                                        ),
                                    },
                                    {"type": "image_url", "image_url": {"url": png}},
                                ],
                            }
                        ],
                        "max_tokens": 10,
                        "temperature": 0.0,
                        "stream": False,
                    },
                )
                if response.status_code >= 400:
                    return False
                answer = (response.json()["choices"][0]["message"]["content"] or "").lower()
            except Exception:  # noqa: BLE001
                return False
            if colour not in answer:
                return False
        return True
