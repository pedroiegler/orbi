"""Canal de operacao (ORBI.md secao 14).

Como nao ha painel, a observabilidade **e** a interface de operacao. Um numero
interno de WhatsApp recebe os alertas imediatos e o resumo diario.

Dois cuidados que fazem o canal continuar util: throttle por assunto (alerta que
repete a cada segundo deixa de ser lido) e nenhum dado de cliente na mensagem.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from orbi.channel.port import ChannelPort
from orbi.core.settings import Settings, get_settings
from orbi.core.trace import get_trace_id

logger = logging.getLogger(__name__)

THROTTLE = timedelta(minutes=10)


@dataclass
class OpsNotifier:
    """Alertas imediatos e resumo diario, com throttle por assunto."""

    channel: ChannelPort | None = None
    to_address: str = ""
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    _last_sent: dict[str, datetime] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    sent: list[str] = field(default_factory=list)
    """Historico em memoria — util em teste e no `orbi ops tail`."""

    def alert(self, subject: str, detail: str = "") -> None:
        now = self.clock()
        with self._lock:
            last = self._last_sent.get(subject)
            if last is not None and now - last < THROTTLE:
                return
            self._last_sent[subject] = now

        trace = get_trace_id()
        suffix = f"\ntrace {trace}" if trace else ""
        message = f"⚠️ {subject}\n{detail}{suffix}".strip()
        self.sent.append(message)
        logger.warning("ops: %s | %s", subject, detail)
        self._deliver(message)

    def notify(self, message: str) -> None:
        """Mensagem sem throttle: resumo diario e relatorios de onboarding."""
        self.sent.append(message)
        self._deliver(message)

    def _deliver(self, message: str) -> None:
        if self.channel is None or not self.to_address:
            return
        try:
            self.channel.send_text(self.to_address, message)
        except Exception as exc:  # o alerta nunca pode derrubar o turno
            logger.error("falha ao enviar alerta de ops: %s", type(exc).__name__)

    def as_callback(self) -> Callable[[str, str], None]:
        return lambda subject, detail: self.alert(subject, detail)


def format_daily_summary(report: dict[str, Any]) -> str:
    """Resumo diario: volume, ambiguidade, p50/p95, custo e termos sem resultado.

    Os termos sem resultado sao a lista de trabalho do dia: cada um vira um alias
    ou uma correcao de nome canonico.
    """
    lines = [
        f"📊 Resumo do dia — {report.get('day', '')}",
        f"Perguntas: {report.get('questions', 0)}"
        f" · ambiguidade {report.get('ambiguity_rate', 0):.0%}"
        f" · sem resultado {report.get('not_found_rate', 0):.0%}",
        f"p50 {report.get('p50_ms', 0)} ms · p95 {report.get('p95_ms', 0)} ms",
        f"Custo de LLM: US$ {report.get('cost_usd', 0):.4f}",
    ]

    by_tenant = report.get("by_tenant") or {}
    if by_tenant:
        lines.append("Por cliente: " + " · ".join(f"{k}: {v}" for k, v in by_tenant.items()))

    missing = report.get("top_unresolved") or []
    if missing:
        lines.append("Termos sem resultado (viram alias):")
        lines.extend(
            f"  {index}. {term} ({count}x)"
            for index, (term, count) in enumerate(missing, 1)
        )
    else:
        lines.append("Nenhum termo sem resultado hoje.")

    return "\n".join(lines)


def build_notifier(settings: Settings | None = None) -> OpsNotifier:
    resolved = settings or get_settings()
    if not resolved.ops_phone or not resolved.ops_phone_number_id:
        # Sem numero de ops configurado o alerta ainda vai para o log.
        return OpsNotifier()

    from orbi.channel.whatsapp import WhatsAppChannel

    channel = WhatsAppChannel(
        phone_number_id=resolved.ops_phone_number_id,
        access_token=resolved.ops_access_token.get_secret_value(),
    )
    return OpsNotifier(channel=channel, to_address=resolved.ops_phone)
