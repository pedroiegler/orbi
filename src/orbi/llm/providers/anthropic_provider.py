"""Provedor Anthropic (Claude) — Messages API com function calling.

Decisoes que valem registro:

- `tool_choice={"type": "auto", "disable_parallel_tool_use": True}`: uma tool por
  turno, sem chamadas paralelas (ORBI.md secao 6.5).
- `strict: True` nas tools: o argumento chega validado, o que soma com o
  `EntityTerm` do lado do backend.
- Prefixo estatico em bloco proprio com `cache_control`: e o que aproveita prompt
  caching e devolve ~metade do custo em conversas seguidas.
- Esforco baixo com thinking adaptativo: escolher uma tool de quatro nao precisa
  de raciocinio longo, e o orcamento do turno e de 800 ms.
"""

from __future__ import annotations

import time
from typing import Any

from orbi.core.errors import LLMError, LLMTimeout
from orbi.llm.port import LLMRequest, ToolCallEnvelope

MANUFACTURER = "anthropic"
PROVIDER_NAME = "anthropic"
DEFAULT_MODEL = "claude-opus-5"

# USD por 1M de tokens. Alimenta `cost_usd` na auditoria e o custo por acerto
# do bake-off (ORBI.md secao 12).
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


class AnthropicProvider:
    """Implementa o `LLMPort` sobre o SDK oficial da Anthropic."""

    manufacturer = MANUFACTURER

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        effort: str = "low",
        client: Any | None = None,
    ) -> None:
        self.name = PROVIDER_NAME
        self.model = model
        self._effort = effort
        self._client = client
        self._api_key = api_key

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depende do extra
                raise LLMError(
                    "SDK da Anthropic ausente: instale com `pip install orbi[llm]`"
                ) from exc
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def complete(self, request: LLMRequest) -> ToolCallEnvelope:
        client = self._ensure_client()
        started = time.monotonic()

        system: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": request.system_prefix,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if request.system_suffix:
            system.append({"type": "text", "text": request.system_suffix})

        try:
            response = client.with_options(
                timeout=request.timeout_ms / 1000, max_retries=0
            ).messages.create(
                model=self.model,
                max_tokens=request.max_tokens,
                system=system,
                messages=[dict(message) for message in request.messages],
                tools=[_as_anthropic_tool(tool) for tool in request.tools],
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                output_config={"effort": self._effort},
            )
        except Exception as exc:
            raise _translate(exc) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        return _to_envelope(response, self.name, self.model, latency_ms)


def _as_anthropic_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": tool["input_schema"],
        "strict": True,
    }


def _to_envelope(response: Any, provider: str, model: str, latency_ms: int) -> ToolCallEnvelope:
    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    cached_in = int(getattr(usage, "cache_read_input_tokens", 0) or 0)

    tool_name: str | None = None
    tool_args: dict[str, Any] = {}
    text_parts: list[str] = []

    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use" and tool_name is None:
            tool_name = str(getattr(block, "name", ""))
            raw_input = getattr(block, "input", {}) or {}
            tool_args = dict(raw_input) if isinstance(raw_input, dict) else {}
        elif block_type == "text":
            text_parts.append(str(getattr(block, "text", "")))

    stop_reason = getattr(response, "stop_reason", None)
    if tool_name is not None:
        finish_reason = "tool_call"
    elif stop_reason == "refusal":
        finish_reason = "refusal"
    elif stop_reason == "max_tokens":
        finish_reason = "max_tokens"
    else:
        finish_reason = "text"

    return ToolCallEnvelope(
        finish_reason=finish_reason,  # type: ignore[arg-type]
        tool_name=tool_name,
        tool_args=tool_args,
        text="\n".join(part for part in text_parts if part) or None,
        provider=provider,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cached_tokens_in=cached_in,
        cost_usd=estimate_cost(model, tokens_in, tokens_out, cached_in),
        latency_ms=latency_ms,
    )


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cached_in: int = 0) -> float:
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    billable_in = max(0, tokens_in - cached_in)
    # Token lido do cache custa cerca de 10% do preco de entrada.
    return round(
        (billable_in * price_in + cached_in * price_in * 0.1 + tokens_out * price_out) / 1_000_000,
        6,
    )


def _translate(exc: Exception) -> Exception:
    name = type(exc).__name__
    if "Timeout" in name:
        return LLMTimeout(f"anthropic: {name}")
    if "Connection" in name:
        return LLMError(f"anthropic indisponivel: {name}")
    if "RateLimit" in name:
        return LLMError(f"anthropic limitou as requisicoes: {name}")
    return LLMError(f"anthropic falhou: {name}")


def build(api_key: str, model: str, **kwargs: Any) -> AnthropicProvider:
    return AnthropicProvider(api_key=api_key, model=model or DEFAULT_MODEL, **kwargs)
