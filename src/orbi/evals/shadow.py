"""Shadow evals (ORBI.md secao 14).

5% das perguntas reais, **anonimizadas**, são reexecutadas à noite contra a
versão candidata do prompt. A regressão aparece antes do usuário, e o eval set
cresce sozinho.

Duas garantias que fazem isso ser seguro de rodar em produção:

- **Nenhuma chamada ao ERP.** O shadow compara escolha de tool e argumentos —
  o que muda quando o prompt muda. Reexecutar a consulta gastaria rate limit do
  cliente sem responder nada de novo.
- **Texto anonimizado.** A pergunta passa pelo `PIIRedactor` antes de sair, como
  no turno real.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from orbi.core.deadline import Deadline
from orbi.db.models import EvalRun
from orbi.db.session import admin_session, tenant_session
from orbi.llm.pii import DEFAULT_REDACTOR
from orbi.llm.prompt import PromptBuilder, PromptContext
from orbi.llm.router import LLMRouter, build_router
from orbi.tools.registry import tools_for_role

SAMPLE_RATE = 0.05
MIN_SAMPLE = 5
MAX_SAMPLE = 200


@dataclass
class ShadowResult:
    tenant_slug: str
    sampled: int = 0
    same_tool: int = 0
    same_args: int = 0
    divergences: list[str] = field(default_factory=list)
    prompt_version: str = ""
    provider: str = ""
    model: str = ""

    @property
    def tool_agreement(self) -> float:
        return self.same_tool / self.sampled if self.sampled else 1.0

    @property
    def args_agreement(self) -> float:
        return self.same_args / self.sampled if self.sampled else 1.0

    def summary(self) -> str:
        return (
            f"{self.sampled} perguntas · tool igual {self.tool_agreement:.0%} · "
            f"argumentos iguais {self.args_agreement:.0%}"
        )


def sample_questions(
    session: Session, tenant_id: uuid.UUID, *, days: int = 1, rate: float = SAMPLE_RATE
) -> list[dict[str, Any]]:
    """Amostra determinística das perguntas do período, já anonimizada."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(
        text(
            """
            SELECT trace_id, message_text, tool_name, tool_args
            FROM audit_logs
            WHERE tenant_id = :tenant_id
              AND occurred_at >= :since
              AND message_text IS NOT NULL
              AND message_text <> '[anonimizado]'
              AND tool_name IS NOT NULL
            ORDER BY occurred_at
            """
        ),
        {"tenant_id": str(tenant_id), "since": since},
    ).all()
    if not rows:
        return []

    step = max(1, int(1 / rate))
    chosen = rows[::step][:MAX_SAMPLE]
    if len(chosen) < MIN_SAMPLE:
        chosen = rows[:MIN_SAMPLE]

    return [
        {
            "trace_id": row.trace_id,
            "question": DEFAULT_REDACTOR.redact(row.message_text),
            "tool_name": row.tool_name,
            "tool_args": _as_args(row.tool_args),
        }
        for row in chosen
    ]


def _as_args(stored: Any) -> dict[str, Any]:
    """A auditoria e append-only: linha antiga fica como esta, para sempre.

    Comparar argumentos so faz sentido quando os dois lados sao dicionario; o que
    nao for entra como vazio e o shadow compara apenas a escolha de tool.
    """
    return dict(stored) if isinstance(stored, dict) else {}


def run_shadow(
    tenant_id: uuid.UUID,
    tenant_slug: str,
    *,
    role: str = "admin",
    days: int = 1,
    llm: LLMRouter | None = None,
    record: bool = True,
) -> ShadowResult:
    """Reexecuta a amostra contra o prompt atual e compara com o que foi feito."""
    result = ShadowResult(tenant_slug=tenant_slug)
    with tenant_session(tenant_id) as session:
        questions = sample_questions(session, tenant_id, days=days)
    if not questions:
        return result

    router = llm or build_router()
    builder = PromptBuilder()

    for case in questions:
        request = builder.build(
            tenant_name=tenant_slug,
            question=case["question"],
            tools=tools_for_role(role),
            context=PromptContext(role=role, now=datetime.now(UTC)),
        )
        result.prompt_version = request.prompt_version
        envelope = router.complete(request, Deadline(total_ms=15_000))
        result.provider, result.model = envelope.provider, envelope.model
        result.sampled += 1

        if envelope.tool_name == case["tool_name"]:
            result.same_tool += 1
            if not case["tool_args"]:
                result.same_args += 1  # sem argumentos gravados, nada a divergir
            elif _args_equivalent(case["tool_args"], envelope.tool_args):
                result.same_args += 1
            else:
                result.divergences.append(
                    f"{case['trace_id'][:8]}: argumentos mudaram — "
                    f"{case['tool_args']} → {envelope.tool_args}"
                )
        else:
            result.divergences.append(
                f"{case['trace_id'][:8]}: {case['tool_name']} → {envelope.tool_name}"
            )

    if record:
        _record(result)
    return result


def _args_equivalent(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    normalized_previous = {k: str(v).strip().lower() for k, v in previous.items() if v is not None}
    normalized_current = {k: str(v).strip().lower() for k, v in current.items() if v is not None}
    return normalized_previous == normalized_current


def _record(result: ShadowResult) -> None:
    with admin_session() as session:
        session.add(
            EvalRun(
                suite="shadow",
                layer="L1",
                prompt_version=result.prompt_version or "desconhecida",
                provider=result.provider or "desconhecido",
                model=result.model or "desconhecido",
                total=result.sampled,
                passed=result.same_tool,
                score=Decimal(str(round(result.tool_agreement, 4))),
                details={
                    "tenant": result.tenant_slug,
                    "args_agreement": round(result.args_agreement, 4),
                    "divergences": result.divergences[:20],
                },
            )
        )
