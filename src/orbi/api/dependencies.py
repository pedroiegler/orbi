"""Montagem do Runtime e despacho de mensagens.

Uma unica fabrica constroi o Runtime do processo. O despacho traduz evento do
canal em turno, devolve a resposta pelo mesmo canal e trata as duas coisas que
nao sao pergunta: reacao 👍/👎 e midia fora de escopo.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import text

from orbi.channel.port import ChannelPort, InboundEvent, options_from
from orbi.channel.whatsapp import WhatsAppChannel
from orbi.core.crypto import CredentialCipher
from orbi.core.settings import get_settings
from orbi.db.session import admin_session, tenant_session
from orbi.identity import resolver as identity
from orbi.llm.router import build_router
from orbi.observability.ops_channel import OpsNotifier, build_notifier
from orbi.render.renderer import ResultRenderer
from orbi.runtime.pipeline import InboundMessage, OrbiRuntime, TurnOutcome

logger = logging.getLogger(__name__)

FEEDBACK_WINDOW = timedelta(hours=6)


class TurnDispatcher:
    """Recebe evento do canal, roda o turno e responde."""

    def __init__(
        self,
        runtime: OrbiRuntime,
        ops: OpsNotifier,
        *,
        renderer: ResultRenderer | None = None,
        channel_factory: object | None = None,
    ) -> None:
        self._runtime = runtime
        self._ops = ops
        self._renderer = renderer or ResultRenderer()
        self._channel_factory = channel_factory or channel_for_tenant

    def dispatch(self, event: InboundEvent) -> TurnOutcome | None:
        if event.kind == "reaction":
            self._record_feedback(event)
            return None

        if event.kind == "unsupported":
            self._reply_unsupported(event)
            return None

        inbound = InboundMessage(
            channel=event.channel,
            from_address=event.from_address,
            to_address=event.to_address,
            text=event.text,
            message_id=event.message_id,
            received_at=event.received_at,
        )

        channel = self._channel_factory(event.channel, event.to_address)
        interim = self._schedule_interim(channel, event)
        try:
            outcome = self._runtime.handle(inbound)
        finally:
            interim.cancel()

        self._reply(channel, event, outcome)
        return outcome

    # --- respostas -------------------------------------------------------

    def _reply(
        self, channel: ChannelPort | None, event: InboundEvent, outcome: TurnOutcome
    ) -> None:
        if channel is None or not outcome.text:
            return
        if outcome.options:
            channel.send_options(
                event.from_address, outcome.text, options_from(outcome.options)
            )
            return
        channel.send_text(event.from_address, outcome.text)

    def _schedule_interim(
        self, channel: ChannelPort | None, event: InboundEvent
    ) -> threading.Timer:
        """Acima de 2,5s manda "consultando o ERP..." (ORBI.md secao 15).

        Percepcao de velocidade vale tanto quanto o numero — e a mensagem ainda
        mantem a janela de conversa ativa.
        """
        threshold = get_settings().slow_reply_threshold_ms / 1000

        def send() -> None:
            if channel is None:
                return
            try:
                channel.send_text(
                    event.from_address, self._renderer.render("slow.txt.j2", {})
                )
            except Exception as exc:  # nunca derruba o turno
                logger.warning("falha ao enviar aviso de demora: %s", type(exc).__name__)

        timer = threading.Timer(threshold, send)
        timer.daemon = True
        timer.start()
        return timer

    def _reply_unsupported(self, event: InboundEvent) -> None:
        channel = self._channel_factory(event.channel, event.to_address)
        if channel is None:
            return
        text = self._renderer.render(
            "out_of_scope.txt.j2",
            {
                "scope_label": "estoque, preço, títulos em aberto e último pedido, por texto",
                "examples": ["quanto tem de tubo pvc 100?"],
            },
        )
        channel.send_text(event.from_address, text)

    # --- feedback --------------------------------------------------------

    def _record_feedback(self, event: InboundEvent) -> None:
        """Reacao vira dado de treino (principio 16).

        A Cloud API nao devolve o `trace_id`, entao a reacao e atribuida ao
        ultimo turno daquele numero dentro da janela — que e como o usuario a
        entende.
        """
        if event.reaction is None:
            return

        tenant = identity.tenant_of(event.channel, event.to_address)
        if tenant is None:
            return

        with tenant_session(tenant.id) as session:
            try:
                user = identity.find_user(session, event.channel, event.from_address)
            except Exception:
                return
            row = session.execute(
                text(
                    """
                    SELECT trace_id, tool_name, resolved_entity, message_text
                    FROM audit_logs
                    WHERE tenant_id = :tenant_id AND user_id = :user_id
                      AND occurred_at > :since
                    ORDER BY occurred_at DESC
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": str(tenant.id),
                    "user_id": str(user.user_id),
                    "since": datetime.now(UTC) - FEEDBACK_WINDOW,
                },
            ).first()
            if row is None:
                return

            if event.reaction == "down":
                session.execute(
                    text(
                        """
                        INSERT INTO corrections (
                            tenant_id, trace_id, user_id, kind, status, term,
                            tool_name, wrong_entity_id, note
                        ) VALUES (
                            :tenant_id, :trace_id, :user_id, 'unclassified', 'open', :term,
                            :tool_name, :wrong_entity_id, :note
                        )
                        """
                    ),
                    {
                        "tenant_id": str(tenant.id),
                        "trace_id": row.trace_id,
                        "user_id": str(user.user_id),
                        "term": (row.message_text or "")[:160] or None,
                        "tool_name": row.tool_name,
                        "wrong_entity_id": (row.resolved_entity or {}).get("erp_entity_id"),
                        "note": "reacao negativa no WhatsApp",
                    },
                )

        with admin_session() as session:
            session.execute(
                text("UPDATE audit_logs SET feedback = :feedback WHERE trace_id = :trace_id"),
                {"feedback": event.reaction, "trace_id": row.trace_id},
            )

        if event.reaction == "down":
            self._ops.alert(
                "usuario marcou 👎",
                f"tenant {tenant.slug} · trace {row.trace_id}",
            )


def channel_for_tenant(channel_name: str, to_address: str) -> ChannelPort | None:
    """Monta o canal do tenant a partir do numero de destino.

    Cada tenant tem numero e token proprios: o token fica cifrado no banco e
    nunca aparece em log.
    """
    tenant = identity.tenant_of(channel_name, to_address)
    if tenant is None:
        return None

    with tenant_session(tenant.id) as session:
        row = identity.tenant_row(session, tenant.id)
        if row is None or not row.channel_phone_number_id or not row.channel_token_encrypted:
            logger.warning("tenant %s sem token de canal configurado", tenant.slug)
            return None
        token = CredentialCipher().decrypt(row.channel_token_encrypted)["access_token"]
        phone_number_id = row.channel_phone_number_id

    return WhatsAppChannel(phone_number_id=phone_number_id, access_token=token)


@lru_cache(maxsize=1)
def get_runtime() -> OrbiRuntime:
    ops = build_notifier()
    return OrbiRuntime(build_router(), on_alert=ops.as_callback())


@lru_cache(maxsize=1)
def get_dispatcher() -> TurnDispatcher:
    return TurnDispatcher(get_runtime(), build_notifier())


def reset_dependencies() -> None:
    get_runtime.cache_clear()
    get_dispatcher.cache_clear()
