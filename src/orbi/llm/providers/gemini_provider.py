"""Provedor Google Gemini — o primario enquanto o produto nao tem cliente pagante.

A camada gratuita do Google AI Studio cobre com folga o desenvolvimento e a PoC,
e o Gemini tem function calling nativo — que e a unica coisa que o Orbi pede de
um LLM (D-032).

Decisoes que valem registro:

- `mode=ANY` com a lista de tools permitidas: o modelo e obrigado a escolher uma
  tool da lista, ou nenhuma. Combina com a regra de uma tool por turno.
- `automatic_function_calling.disable=True`: o SDK do Google sabe executar a
  funcao sozinho. Nao aqui. Quem autoriza e executa e a Policy Layer.
- `parameters_json_schema` em vez de `parameters`: passa o JSON Schema do
  `ToolSpec` como esta, com `additionalProperties: false`, sem traduzir.
- `thinking_budget=0`: escolher uma tool entre quatro nao precisa de raciocinio
  longo, e o orcamento do turno e de 800 ms.
"""

from __future__ import annotations

import time
from typing import Any

from orbi.core.errors import LLMError, LLMTimeout
from orbi.llm.port import LLMRequest, ToolCallEnvelope

MANUFACTURER = "google"
PROVIDER_NAME = "gemini"
DEFAULT_MODEL = "gemini-2.5-flash"

# USD por 1M de tokens. A camada gratuita nao cobra; os valores existem para o
# custo por acerto do bake-off continuar comparavel entre fabricantes.
PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
}


class GeminiProvider:
    """Implementa o `LLMPort` sobre o SDK oficial `google-genai`."""

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
                from google import genai
            except ImportError as exc:  # pragma: no cover - depende do extra
                raise LLMError(
                    "SDK do Google ausente: instale com `pip install orbi[llm]`"
                ) from exc
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def complete(self, request: LLMRequest) -> ToolCallEnvelope:
        from google.genai import types

        client = self._ensure_client()
        started = time.monotonic()

        system = request.system_prefix
        if request.system_suffix:
            system = f"{system}\n\n{request.system_suffix}"

        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=request.max_tokens,
            temperature=0.0,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool["name"],
                            description=tool["description"],
                            parameters_json_schema=tool["input_schema"],
                        )
                        for tool in request.tools
                    ]
                )
            ],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.AUTO,
                    allowed_function_names=[tool["name"] for tool in request.tools],
                )
            ),
            # O SDK sabe executar a funcao sozinho. Aqui, nao: quem autoriza e
            # executa e a Policy Layer.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            http_options=types.HttpOptions(timeout=request.timeout_ms),
        )

        try:
            response = client.models.generate_content(
                model=self.model,
                contents=[_as_content(message) for message in request.messages],
                config=config,
            )
        except Exception as exc:
            raise _translate(exc) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        return _to_envelope(response, self.name, self.model, latency_ms)


def _as_content(message: dict[str, str]) -> dict[str, Any]:
    """O Gemini chama de `model` o que os outros chamam de `assistant`."""
    role = "model" if message.get("role") == "assistant" else "user"
    return {"role": role, "parts": [{"text": message.get("content", "")}]}


def _to_envelope(response: Any, provider: str, model: str, latency_ms: int) -> ToolCallEnvelope:
    usage = getattr(response, "usage_metadata", None)
    tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
    tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0)
    cached_in = int(getattr(usage, "cached_content_token_count", 0) or 0)

    calls = getattr(response, "function_calls", None) or []
    tool_name: str | None = None
    tool_args: dict[str, Any] = {}
    if calls:
        # Uma tool por turno: se o modelo devolver mais de uma, a primeira vale.
        first = calls[0]
        tool_name = str(getattr(first, "name", ""))
        raw_args = getattr(first, "args", {}) or {}
        tool_args = dict(raw_args) if isinstance(raw_args, dict) else {}

    text = _text_of(response)
    finish = str(_finish_reason(response) or "")

    if tool_name:
        finish_reason = "tool_call"
    elif finish in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
        finish_reason = "refusal"
    elif finish == "MAX_TOKENS":
        finish_reason = "max_tokens"
    else:
        finish_reason = "text"

    return ToolCallEnvelope(
        finish_reason=finish_reason,  # type: ignore[arg-type]
        tool_name=tool_name,
        tool_args=tool_args,
        text=text,
        provider=provider,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cached_tokens_in=cached_in,
        cost_usd=estimate_cost(model, tokens_in, tokens_out, cached_in),
        latency_ms=latency_ms,
    )


def _text_of(response: Any) -> str | None:
    """`response.text` levanta quando a resposta so tem function call."""
    try:
        return str(response.text) if response.text else None
    except Exception:
        return None


def _finish_reason(response: Any) -> str | None:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    return str(getattr(reason, "value", reason)) if reason is not None else None


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cached_in: int = 0) -> float:
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    billable_in = max(0, tokens_in - cached_in)
    return round(
        (billable_in * price_in + cached_in * price_in * 0.25 + tokens_out * price_out)
        / 1_000_000,
        6,
    )


def _translate(exc: Exception) -> Exception:
    name = type(exc).__name__
    message = str(exc)
    if "Timeout" in name or "timeout" in message.lower():
        return LLMTimeout(f"gemini: {name}")
    if "429" in message or "RESOURCE_EXHAUSTED" in message:
        return LLMError(f"gemini limitou as requisicoes: {name}")
    if "Connection" in name:
        return LLMError(f"gemini indisponivel: {name}")
    return LLMError(f"gemini falhou: {name}")


def build(api_key: str, model: str, **kwargs: Any) -> GeminiProvider:
    return GeminiProvider(api_key=api_key, model=model or DEFAULT_MODEL, **kwargs)
