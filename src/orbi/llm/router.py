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
from orbi.llm.providers import anthropic_provider, openai_provider, rule_based

MIN_LLM_BUDGET_MS = 300
"""Abaixo disso nao vale a pena chamar: melhor falhar honestamente."""


@dataclass
class LLMRouter:
    """Encapsula primario + fallback."""

    primary: LLMPort
    fallback: LLMPort | None = None

    def __post_init__(self) -> None:
        if (
            self.fallback is not None
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

    @property
    def providers(self) -> tuple[str, ...]:
        names = [self.primary.name]
        if self.fallback is not None:
            names.append(self.fallback.name)
        return tuple(names)


def build_provider(name: str, settings: Settings | None = None) -> LLMPort:
    resolved = settings or get_settings()
    if name == "anthropic":
        return anthropic_provider.build(
            api_key=resolved.anthropic_api_key.get_secret_value(),
            model=resolved.anthropic_model,
        )
    if name == "openai":
        return openai_provider.build(
            api_key=resolved.openai_api_key.get_secret_value(),
            model=resolved.openai_model,
        )
    if name == "rule_based":
        if resolved.is_production:
            raise ConfigurationError("provedor rule_based nao e permitido em producao")
        return rule_based.build()
    raise ConfigurationError(f"provedor de LLM desconhecido: {name}")


def build_router(settings: Settings | None = None) -> LLMRouter:
    resolved = settings or get_settings()
    primary = build_provider(resolved.llm_primary, resolved)
    fallback = (
        build_provider(resolved.llm_fallback, resolved)
        if resolved.llm_fallback is not None
        else None
    )
    return LLMRouter(primary=primary, fallback=fallback)
