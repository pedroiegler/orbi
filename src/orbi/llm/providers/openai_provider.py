"""Provedor OpenAI — o fallback de fabricante diferente (D-011).

Existe para que a queda de um fornecedor nao derrube o produto. Roda no eval do
CI junto com o primario: failover que nunca foi avaliado degrada a qualidade
silenciosamente no dia do incidente (ORBI.md secao 12).
"""

from __future__ import annotations

import json
import time
from typing import Any

from orbi.core.errors import LLMError, LLMTimeout
from orbi.llm.port import LLMRequest, ToolCallEnvelope
from orbi.llm.pricing import custo_usd

MANUFACTURER = "openai"
PROVIDER_NAME = "openai"
DEFAULT_MODEL = "gpt-4.1"



class OpenAIProvider:
    """Implementa o `LLMPort` sobre a Chat Completions API."""

    manufacturer = MANUFACTURER

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        client: Any | None = None,
    ) -> None:
        self.name = PROVIDER_NAME
        self.model = model
        self._api_key = api_key
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import openai
            except ImportError as exc:  # pragma: no cover - depende do extra
                raise LLMError(
                    "SDK da OpenAI ausente: instale com `pip install orbi[llm]`"
                ) from exc
            self._client = openai.OpenAI(api_key=self._api_key)
        return self._client

    def complete(self, request: LLMRequest) -> ToolCallEnvelope:
        client = self._ensure_client()
        started = time.monotonic()

        system_content = request.system_prefix
        if request.system_suffix:
            system_content = f"{system_content}\n\n{request.system_suffix}"

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
        messages.extend(dict(message) for message in request.messages)

        try:
            response = client.with_options(
                timeout=request.timeout_ms / 1000, max_retries=0
            ).chat.completions.create(
                model=self.model,
                max_completion_tokens=request.max_tokens,
                messages=messages,
                tools=[_as_openai_tool(tool) for tool in request.tools],
                tool_choice="auto",
                parallel_tool_calls=False,
            )
        except Exception as exc:
            raise _translate(exc) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        return _to_envelope(response, self.name, self.model, latency_ms)


def _as_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
            "strict": True,
        },
    }


def _to_envelope(response: Any, provider: str, model: str, latency_ms: int) -> ToolCallEnvelope:
    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)

    choice = (getattr(response, "choices", None) or [None])[0]
    message = getattr(choice, "message", None)
    tool_calls = getattr(message, "tool_calls", None) or []

    tool_name: str | None = None
    tool_args: dict[str, Any] = {}
    if tool_calls:
        function = getattr(tool_calls[0], "function", None)
        tool_name = str(getattr(function, "name", ""))
        raw_arguments = getattr(function, "arguments", "") or "{}"
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise LLMError("openai devolveu argumentos que nao sao JSON") from exc
        tool_args = parsed if isinstance(parsed, dict) else {}

    finish = str(getattr(choice, "finish_reason", "") or "")
    if tool_name:
        finish_reason = "tool_call"
    elif finish == "length":
        finish_reason = "max_tokens"
    elif finish == "content_filter":
        finish_reason = "refusal"
    else:
        finish_reason = "text"

    return ToolCallEnvelope(
        finish_reason=finish_reason,  # type: ignore[arg-type]
        tool_name=tool_name,
        tool_args=tool_args,
        text=getattr(message, "content", None),
        provider=provider,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=custo_usd(model, tokens_in, tokens_out),
        latency_ms=latency_ms,
    )




def _translate(exc: Exception) -> Exception:
    name = type(exc).__name__
    if "Timeout" in name:
        return LLMTimeout(f"openai: {name}")
    if "Connection" in name:
        return LLMError(f"openai indisponivel: {name}")
    if "RateLimit" in name:
        return LLMError(f"openai limitou as requisicoes: {name}")
    return LLMError(f"openai falhou: {name}")


def build(api_key: str, model: str, **kwargs: Any) -> OpenAIProvider:
    return OpenAIProvider(api_key=api_key, model=model or DEFAULT_MODEL, **kwargs)
