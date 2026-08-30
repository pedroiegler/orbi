"""Bake-off de modelos (ORBI.md secao 12 e pendencia da secao 21).

> O primario e escolhido por **custo por resposta correta**, nao por preco de
> token:
>
>     custo_por_acerto = custo_medio_do_turno / taxa_de_acerto
>
> Um modelo 40% mais barato que erra a tool 8% mais vezes e mais caro na pratica,
> porque cada erro vira desambiguacao, retrabalho e ticket de suporte.

Este modulo mede isso: roda o mesmo conjunto do eval L1/L2 contra um provedor e
devolve acerto, latencia (p50 e p95), tokens e custo — os numeros que decidem no
lugar da opiniao.

Nao toca no ERP e nao usa tenant: e comparacao de modelo, nao de integracao.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from orbi.core.deadline import Deadline
from orbi.core.errors import LLMError
from orbi.db.models import EvalRun
from orbi.db.session import admin_session
from orbi.evals.runner import _args_match, tool_cases
from orbi.llm.pricing import preco_de
from orbi.llm.prompt import PromptBuilder, PromptContext
from orbi.llm.router import LLMRouter
from orbi.observability.metrics import percentil
from orbi.tools.registry import tools_for_role

TIMEOUT_BENCH_MS = 60_000
"""Generoso de proposito: aqui se mede qualidade, nao se cumpre SLA de turno.
Um modelo lento precisa aparecer como lento, nao como erro."""


@dataclass
class ResultadoBench:
    """O que sai de uma rodada contra um modelo."""

    provedor: str
    modelo: str
    total: int = 0
    acertos_tool: int = 0
    acertos_args: int = 0
    falhas: int = 0
    latencias_ms: list[int] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_saida: int = 0
    divergencias: list[str] = field(default_factory=list)

    @property
    def taxa_acerto(self) -> float:
        return self.acertos_tool / self.total if self.total else 0.0

    @property
    def taxa_args(self) -> float:
        return self.acertos_args / self.total if self.total else 0.0

    @property
    def p50_ms(self) -> int:
        return percentil(self.latencias_ms, 50)

    @property
    def p95_ms(self) -> int:
        return percentil(self.latencias_ms, 95)

    @property
    def tokens_por_pergunta(self) -> tuple[int, int]:
        if not self.total:
            return (0, 0)
        return (self.tokens_entrada // self.total, self.tokens_saida // self.total)

    def custo_mil_perguntas(self, cache_hit: float = 0.0) -> float:
        """USD por mil perguntas, com a fracao de cache informada."""
        entrada, saida = self.tokens_por_pergunta
        return preco_de(self.modelo).custo(entrada, saida, cache_hit) * 1000

    def custo_por_acerto(self, cache_hit: float = 0.0) -> float:
        """A metrica que decide: custo dividido pela taxa de acerto.

        Modelo barato que erra muito fica caro aqui, que e o ponto.
        """
        if self.taxa_acerto == 0:
            return float("inf")
        return self.custo_mil_perguntas(cache_hit) / self.taxa_acerto

    def resumo(self) -> str:
        entrada, saida = self.tokens_por_pergunta
        return (
            f"{self.modelo}: acerto {self.taxa_acerto:.0%} · args {self.taxa_args:.0%} · "
            f"p50 {self.p50_ms} ms · p95 {self.p95_ms} ms · "
            f"{entrada}+{saida} tokens · US$ {self.custo_mil_perguntas():.4f}/1k"
        )


def rodar_bench(
    llm: LLMRouter,
    *,
    limite: int | None = None,
    cache_hit: float = 0.0,
    registrar: bool = False,
) -> ResultadoBench:
    """Mede um provedor contra o conjunto L1/L2.

    `limite` existe porque camada gratuita tem cota diaria: dá para medir com 10
    casos hoje e com o conjunto inteiro quando houver billing.
    """
    casos = tool_cases()
    if limite is not None:
        casos = casos[:limite]

    resultado = ResultadoBench(provedor=llm.primary.name, modelo=llm.primary.model)
    construtor = PromptBuilder()

    for caso in casos:
        papel = caso.get("role", "admin")
        pedido = construtor.build(
            tenant_name="Bench",
            question=caso["question"],
            tools=tools_for_role(papel),
            context=PromptContext(role=papel, now=datetime.now(UTC)),
            timeout_ms=TIMEOUT_BENCH_MS,
        )

        resultado.total += 1
        inicio = time.monotonic()
        try:
            envelope = llm.complete(pedido, Deadline(total_ms=TIMEOUT_BENCH_MS + 5_000))
        except LLMError as exc:
            resultado.falhas += 1
            resultado.divergencias.append(f"{caso['question'][:40]!r}: {type(exc).__name__}")
            continue

        resultado.latencias_ms.append(int((time.monotonic() - inicio) * 1000))
        resultado.tokens_entrada += envelope.tokens_in
        resultado.tokens_saida += envelope.tokens_out

        esperada = caso.get("expected_tool")
        obtida = envelope.tool_name if envelope.has_tool_call else None
        if obtida != esperada:
            resultado.divergencias.append(
                f"{caso['question'][:40]!r}: {obtida} (esperada {esperada})"
            )
            continue

        resultado.acertos_tool += 1
        esperados = caso.get("expected_args")
        if not esperados or _args_match(esperados, envelope.tool_args):
            resultado.acertos_args += 1
        else:
            resultado.divergencias.append(
                f"{caso['question'][:40]!r}: args {envelope.tool_args} != {esperados}"
            )

    if registrar:
        _registrar(resultado, cache_hit)
    return resultado


def _registrar(resultado: ResultadoBench, cache_hit: float) -> None:
    """Grava a rodada em `eval_runs`: comparar semanas nao pode virar memoria."""
    with admin_session() as session:
        session.add(
            EvalRun(
                suite="bench",
                layer="L1",
                prompt_version="bench",
                provider=resultado.provedor,
                model=resultado.modelo,
                total=resultado.total,
                passed=resultado.acertos_tool,
                score=Decimal(str(round(resultado.taxa_acerto, 4))),
                details={
                    "args_agreement": round(resultado.taxa_args, 4),
                    "p50_ms": resultado.p50_ms,
                    "p95_ms": resultado.p95_ms,
                    "falhas": resultado.falhas,
                    "tokens_entrada": resultado.tokens_por_pergunta[0],
                    "tokens_saida": resultado.tokens_por_pergunta[1],
                    "usd_por_mil": round(resultado.custo_mil_perguntas(cache_hit), 6),
                    "usd_por_acerto": round(resultado.custo_por_acerto(cache_hit), 6),
                    "divergencias": resultado.divergencias[:20],
                },
            )
        )
