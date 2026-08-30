"""Provedor Google Gemini — o primario enquanto o produto nao tem cliente pagante.

A camada gratuita do Google AI Studio cobre com folga o desenvolvimento e a PoC,
e o Gemini tem function calling nativo — que e a unica coisa que o Orbi pede de
um LLM (D-032).

Decisoes que valem registro:

- `mode=AUTO`, sem `allowed_function_names`: o modelo escolhe uma tool da lista
  ou responde texto. `ANY` obrigaria uma tool sempre, e o "fora de escopo"
  deixaria de existir. A lista enviada ja contem so as tools do papel, entao
  restringir de novo seria redundante — e a API recusa a combinacao.
- `automatic_function_calling.disable=True`: o SDK do Google sabe executar a
  funcao sozinho. Nao aqui. Quem autoriza e executa e a Policy Layer.
- `parameters_json_schema` em vez de `parameters`: passa o JSON Schema do
  `ToolSpec` como esta, com `additionalProperties: false`, sem traduzir.
- `thinking_budget=0`: escolher uma tool entre quatro nao precisa de raciocinio
  longo, e o orcamento do turno e de 800 ms. Configuravel, porque os modelos
  `lite` recusam o campo — nesses, use -1 para nao envia-lo.
- **Prazo do turno vale mesmo assim.** A API recusa deadline abaixo de 10 s
  ("Minimum allowed deadline is 10s"), e o orcamento do Orbi para o LLM e menor
  que isso. Entao o prazo enviado a API e o minimo que ela aceita, e o orcamento
  de verdade e cobrado aqui, com um vigia: se a resposta nao chegar no tempo do
  turno, o Orbi para de esperar. Sem isso o `Deadline` teria um buraco neste
  provedor — e um buraco no `Deadline` e um usuario esperando sem saber ate quando.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from orbi.core.errors import ConfigurationError, LLMError, LLMTimeout
from orbi.llm.port import LLMRequest, ToolCallEnvelope
from orbi.llm.pricing import custo_usd

MIN_API_DEADLINE_MS = 10_000
"""Menor prazo que a API aceita. Abaixo disso ela recusa o pedido com 400."""

MANUFACTURER = "google"
PROVIDER_NAME = "gemini"
DEFAULT_MODEL = "gemini-3.7-flash"



class GeminiProvider:
    """Implementa o `LLMPort` sobre o SDK oficial `google-genai`."""

    manufacturer = MANUFACTURER

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        thinking_budget: int = 0,
        client: Any | None = None,
    ) -> None:
        self.name = PROVIDER_NAME
        self.model = model
        self._api_key = api_key
        self._thinking_budget = thinking_budget
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
                    mode=types.FunctionCallingConfigMode.AUTO
                )
            ),
            # O SDK sabe executar a funcao sozinho. Aqui, nao: quem autoriza e
            # executa e a Policy Layer.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            # Os modelos `lite` recusam `thinking_config`; -1 omite o campo.
            thinking_config=(
                types.ThinkingConfig(thinking_budget=self._thinking_budget)
                if self._thinking_budget >= 0
                else None
            ),
            http_options=types.HttpOptions(
                timeout=max(request.timeout_ms, MIN_API_DEADLINE_MS)
            ),
        )

        contents = [_as_content(message) for message in request.messages]
        try:
            response = self._call_within_budget(client, contents, config, request.timeout_ms)
        except FutureTimeout as exc:
            raise LLMTimeout(
                f"gemini nao respondeu em {request.timeout_ms} ms"
            ) from exc
        except Exception as exc:
            raise _translate(exc) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        return _to_envelope(response, self.name, self.model, latency_ms)

    def _call_within_budget(
        self, client: Any, contents: list[dict[str, Any]], config: Any, budget_ms: int
    ) -> Any:
        """Cobra o orcamento do turno, que a API nao aceita cobrar por conta.

        A requisicao abandonada continua correndo ate o prazo da API; o que o
        Orbi garante e nao deixar o usuario esperando alem do orcamento.
        """
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="orbi-gemini") as pool:
            future = pool.submit(
                client.models.generate_content,
                model=self.model,
                contents=contents,
                config=config,
            )
            try:
                return future.result(timeout=budget_ms / 1000)
            except FutureTimeout:
                future.cancel()
                raise


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

    # `response.text` avisa no log quando ha function call junto; so olhamos o
    # texto quando nao houve escolha de tool.
    text = None if tool_name else _text_of(response)
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
        cost_usd=_custo(model, tokens_in, tokens_out, cached_in),
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




def _translate(exc: Exception) -> Exception:
    name = type(exc).__name__
    message = str(exc)
    if "Timeout" in name or "timeout" in message.lower():
        return LLMTimeout(f"gemini: {name}")
    if "429" in message or "RESOURCE_EXHAUSTED" in message:
        return LLMError(f"gemini limitou as requisicoes: {name}")
    if "400" in message or "INVALID_ARGUMENT" in message:
        # Pedido malformado e bug nosso, nao indisponibilidade do provedor:
        # tentar de novo ou cair no fallback nao resolve. O alerta precisa dizer
        # isso, senao a equipe procura no lugar errado.
        return ConfigurationError(f"gemini recusou o pedido do Orbi: {message[:200]}")
    if "Connection" in name:
        return LLMError(f"gemini indisponivel: {name}")
    return LLMError(f"gemini falhou: {name}")


def build(api_key: str, model: str, **kwargs: Any) -> GeminiProvider:
    return GeminiProvider(api_key=api_key, model=model or DEFAULT_MODEL, **kwargs)


def _custo(model: str, tokens_in: int, tokens_out: int, cached_in: int) -> float:
    """Converte os tokens lidos de cache na fracao que a tabela de precos espera."""
    fracao = cached_in / tokens_in if tokens_in else 0.0
    return custo_usd(model, tokens_in, tokens_out, cache_hit=fracao)
