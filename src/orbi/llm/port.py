"""LLMPort — a fronteira com o provedor de LLM (ORBI.md secao 6.5).

A resposta de qualquer provedor e normalizada em um `ToolCallEnvelope` proprio.
Isso desacopla o Runtime do formato de cada fabricante e e o que torna o
failover viavel de verdade (D-011).

O LLM so faz uma coisa: escolher **uma** tool de um conjunto fechado e preencher
argumentos em linguagem natural. Ele nao ve dados do ERP, nao decide permissao e
nao redige a resposta.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

FinishReason = Literal["tool_call", "text", "refusal", "max_tokens"]


class ToolCallEnvelope(BaseModel):
    """Resposta normalizada de um turno de function calling."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finish_reason: FinishReason
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    text: str | None = None
    """Texto livre devolvido pelo modelo quando ele nao escolheu tool."""

    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens_in: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    @property
    def has_tool_call(self) -> bool:
        return self.finish_reason == "tool_call" and self.tool_name is not None


class LLMRequest(BaseModel):
    """Pedido neutro de provedor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_prefix: str
    """Prefixo estatico e identico entre requisicoes do mesmo tenant: e o que
    aproveita prompt caching."""
    system_suffix: str = ""
    """Sufixo dinamico: data/hora, papel, slots de contexto."""
    messages: tuple[dict[str, str], ...]
    """Ultimos turnos + pergunta atual, ja com PII mascarada."""
    tools: tuple[dict[str, Any], ...]
    """Somente as tools permitidas ao papel do usuario."""
    timeout_ms: int = 4_000
    max_tokens: int = 1_024
    prompt_version: str = ""


@runtime_checkable
class LLMPort(Protocol):
    """Contrato que todo provedor cumpre."""

    name: str
    manufacturer: str
    """Usado para garantir que primario e fallback nao caiam juntos."""

    model: str

    def complete(self, request: LLMRequest) -> ToolCallEnvelope: ...
