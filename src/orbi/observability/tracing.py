"""Tracing do turno (ORBI.md secao 14).

O mesmo `trace_id` que vai para `audit_logs` vai para o Langfuse. Um problema
relatado no WhatsApp vira investigacao em segundos: o usuario cita o codigo curto
e o trace inteiro esta la.

Duas regras deste modulo:

- **Tracing nunca derruba o turno.** Qualquer falha aqui vira log e segue.
- **Nada de dado de cliente no trace.** Vai o que a operacao precisa — tool,
  status, latencia por etapa, tokens e custo — nunca o payload do ERP.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from orbi.core.settings import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover - apenas para tipagem
    from orbi.runtime.pipeline import TurnOutcome

logger = logging.getLogger(__name__)


@runtime_checkable
class TracePort(Protocol):
    """Fronteira com a ferramenta de observabilidade."""

    def record_turn(self, outcome: TurnOutcome, question: str, role: str) -> None: ...

    def flush(self) -> None: ...


class NullTracer:
    """Sem chaves configuradas, o trace vive so no `audit_logs`."""

    def record_turn(self, outcome: TurnOutcome, question: str, role: str) -> None:
        return None

    def flush(self) -> None:
        return None


class LangfuseTracer:
    """Envia um observation por turno, com a latencia de cada etapa.

    As etapas nao viram spans separados de proposito: o turno mede duracoes, nao
    marcos de relogio, e span com duracao inventada engana mais do que informa.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def record_turn(self, outcome: TurnOutcome, question: str, role: str) -> None:
        try:
            self._record(outcome, question, role)
        except Exception as exc:  # observabilidade nunca derruba o turno
            logger.warning("falha ao enviar trace: %s", type(exc).__name__)

    def _record(self, outcome: TurnOutcome, question: str, role: str) -> None:
        from langfuse.types import TraceContext

        latencies = dict(outcome.latencies_ms)
        observation = self._client.start_observation(
            trace_context=TraceContext(trace_id=uuid.UUID(outcome.trace_id).hex),
            name=f"turn.{outcome.tool_name or outcome.status}",
            input=question,
            output=outcome.text,
            level="DEFAULT" if outcome.status in _HEALTHY else "WARNING",
            status_message=outcome.reason_code,
            metadata={
                "tenant": outcome.tenant_slug,
                "role": role,
                "tool": outcome.tool_name,
                "status": outcome.status,
                "used_llm": outcome.used_llm,
                "entity": (outcome.entity or {}).get("erp_entity_id"),
                "resolution_stage": (outcome.entity or {}).get("stage"),
                "latency_total_ms": sum(latencies.values()),
                **{f"latency_{stage}_ms": value for stage, value in latencies.items()},
            },
        )
        observation.end()

    def flush(self) -> None:
        """Chamado no encerramento do processo: o envio e assincrono."""
        try:
            self._client.flush()
        except Exception as exc:
            logger.warning("falha ao descarregar traces: %s", type(exc).__name__)


_HEALTHY = {"ok", "ambiguous", "not_found", "out_of_scope"}


def build_tracer(settings: Settings | None = None) -> TracePort:
    resolved = settings or get_settings()
    public = resolved.langfuse_public_key.get_secret_value()
    secret = resolved.langfuse_secret_key.get_secret_value()
    if not public or not secret:
        return NullTracer()

    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning(
            "langfuse configurado mas ausente: instale com `pip install orbi[observability]`"
        )
        return NullTracer()

    return LangfuseTracer(
        Langfuse(public_key=public, secret_key=secret, host=resolved.langfuse_host)
    )
