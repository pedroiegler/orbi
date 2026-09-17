"""Router de LLM: primario, fallback e a unica re-tentativa permitida.

Duas regras da secao 6.5 vivem aqui:

- **Uma tool por turno.** Se o modelo responder texto em vez de tool, ha uma
  unica re-tentativa pedindo esclarecimento. Falhou de novo, mensagem
  deterministica de fora de escopo. Nunca um segundo loop.
- **Failover entre fabricantes diferentes** (D-011), sempre respeitando o
  `Deadline`: se nao sobra tempo, nao se tenta o segundo provedor.
"""

from __future__ import annotations

from dataclasses import dataclass

from orbi.core.deadline import Deadline
from orbi.core.errors import ConfigurationError, LLMError, LLMUnavailable
from orbi.core.settings import Settings, get_settings
from orbi.llm.port import LLMPort, LLMRequest, ToolCallEnvelope
from orbi.llm.providers import (
    anthropic_provider,
    gemini_provider,
    openai_provider,
    rule_based,
)

MIN_LLM_BUDGET_MS = 300
"""Abaixo disso nao vale a pena chamar: melhor falhar honestamente."""


@dataclass
class LLMRouter:
    """Encapsula primario + fallback."""

    primary: LLMPort
    fallback: LLMPort | None = None
    contas_distintas: bool = False
    """Dispensa a regra dos dois fabricantes — e so um caso a dispensa (D-041).

    A regra existe contra **queda correlacionada**: dois provedores da mesma
    empresa caem juntos, e o fallback vira enfeite (D-011). Ela nao se aplica
    quando os dois lados sao *contas diferentes do mesmo fabricante*: a chave
    propria de um cliente para por teto de gasto estourado ou por revogacao, e
    nenhum dos dois atinge a nossa conta. Ali o fallback e real.

    Marcar isso a toa reintroduziria exatamente o risco que a regra evita."""

    def __post_init__(self) -> None:
        if (
            self.fallback is not None
            and not self.contas_distintas
            and self.primary.manufacturer == self.fallback.manufacturer
        ):
            raise ConfigurationError(
                "primario e fallback do mesmo fabricante: a queda seria correlacionada"
            )

    def complete(self, request: LLMRequest, deadline: Deadline) -> ToolCallEnvelope:
        """Chama o primario e, se ele falhar, o fallback — dentro do orcamento."""
        with deadline.stage("llm", minimum_ms=MIN_LLM_BUDGET_MS):
            budget = deadline.budget_for("llm", request.timeout_ms, MIN_LLM_BUDGET_MS)
            attempt = request.model_copy(update={"timeout_ms": budget})

            try:
                return self.primary.complete(attempt)
            except LLMError as primary_error:
                if self.fallback is None:
                    raise LLMUnavailable(
                        f"provedor primario falhou e nao ha fallback: {primary_error}"
                    ) from primary_error
                if deadline.remaining_ms() < MIN_LLM_BUDGET_MS:
                    raise LLMUnavailable(
                        f"provedor primario falhou e nao sobrou tempo para o fallback: "
                        f"{primary_error}"
                    ) from primary_error

                retry = request.model_copy(
                    update={
                        "timeout_ms": deadline.budget_for(
                            "llm", request.timeout_ms, MIN_LLM_BUDGET_MS
                        )
                    }
                )
                try:
                    return self.fallback.complete(retry)
                except LLMError as fallback_error:
                    raise LLMUnavailable(
                        f"os dois provedores falharam: {primary_error} / {fallback_error}"
                    ) from fallback_error


def build_provider(name: str, settings: Settings | None = None) -> LLMPort:
    """O provedor da configuracao global — o caminho da maioria dos clientes."""
    resolved = settings or get_settings()
    chaves = {
        "gemini": (resolved.gemini_api_key, resolved.gemini_model),
        "anthropic": (resolved.anthropic_api_key, resolved.anthropic_model),
        "openai": (resolved.openai_api_key, resolved.openai_model),
    }
    if name in chaves:
        segredo, modelo = chaves[name]
        return build_provider_with(
            name, api_key=segredo.get_secret_value(), model=modelo, settings=resolved
        )
    if name == "rule_based":
        if resolved.is_production:
            raise ConfigurationError("provedor rule_based nao e permitido em producao")
        return rule_based.build()
    raise ConfigurationError(f"provedor de LLM desconhecido: {name}")


def build_provider_with(
    name: str, *, api_key: str, model: str, settings: Settings | None = None
) -> LLMPort:
    """Provedor com chave e modelo explicitos.

    Existe para a chave por cliente (D-041): a global vem das settings, a do
    cliente vem cifrada do banco, e as duas passam por aqui — um caminho so
    para construir provedor significa um lugar so para corrigir.
    """
    resolved = settings or get_settings()
    if name == "gemini":
        return gemini_provider.build(
            api_key=api_key, model=model, thinking_budget=resolved.gemini_thinking_budget
        )
    if name == "anthropic":
        return anthropic_provider.build(api_key=api_key, model=model)
    if name == "openai":
        return openai_provider.build(api_key=api_key, model=model)
    raise ConfigurationError(f"provedor de LLM sem suporte a chave propria: {name}")


def build_router(settings: Settings | None = None) -> LLMRouter:
    resolved = settings or get_settings()
    primary = build_provider(resolved.llm_primary, resolved)
    fallback = (
        build_provider(resolved.llm_fallback, resolved)
        if resolved.llm_fallback is not None
        else None
    )
    return LLMRouter(primary=primary, fallback=fallback)
