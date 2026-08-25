"""Canal WhatsApp — Meta Cloud API direta (ORBI.md secao 12).

Um numero por tenant: a identificacao fica deterministica e o risco de banimento
fica isolado — um cliente com problema nao derruba os outros.

BSP nao entra agora. O gatilho de migracao esta escrito: mais de 10 tenants
ativos, necessidade real de transbordo humano, ou segundo incidente de
qualidade/banimento.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from orbi.channel.port import ChannelOption, DeliveryResult, InboundEvent
from orbi.core.settings import get_settings

logger = logging.getLogger(__name__)

CHANNEL_NAME = "whatsapp"
GRAPH_URL = "https://graph.facebook.com"
MAX_NATIVE_BUTTONS = 3
"""Acima disso a Cloud API exige lista; o texto numerado ja cobre o caso."""

MAX_TEXT_LENGTH = 4_000


class WhatsAppChannel:
    """Envia e recebe pela Cloud API."""

    name = CHANNEL_NAME

    def __init__(
        self,
        phone_number_id: str,
        access_token: str,
        *,
        api_version: str | None = None,
        client: httpx.Client | None = None,
        timeout_ms: int = 5_000,
    ) -> None:
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._api_version = api_version or get_settings().whatsapp_api_version
        self._client = client
        self._timeout = timeout_ms / 1000

    # --- envio -----------------------------------------------------------

    def send_text(self, to_address: str, text: str) -> DeliveryResult:
        if not text.strip():
            # Recusa silenciosa (numero desconhecido em rate limit) nao envia nada.
            return DeliveryResult(delivered=False, error="mensagem vazia")
        return self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_address,
                "type": "text",
                "text": {"preview_url": False, "body": text[:MAX_TEXT_LENGTH]},
            }
        )

    def send_options(
        self, to_address: str, text: str, options: tuple[ChannelOption, ...]
    ) -> DeliveryResult:
        """Botao nativo ate tres opcoes; acima disso, o texto numerado."""
        if not options or len(options) > MAX_NATIVE_BUTTONS:
            return self.send_text(to_address, text)

        return self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_address,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"body": text[:1024]},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {"id": option.reply_id, "title": option.label[:20]},
                            }
                            for option in options
                        ]
                    },
                },
            }
        )

    def _post(self, payload: dict[str, Any]) -> DeliveryResult:
        url = f"{GRAPH_URL}/{self._api_version}/{self._phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code >= 400:
                # O token nunca entra no log.
                logger.warning("whatsapp recusou o envio: %s", response.status_code)
                return DeliveryResult(delivered=False, error=f"HTTP {response.status_code}")
            body = response.json()
            message_id = (body.get("messages") or [{}])[0].get("id")
            return DeliveryResult(delivered=True, provider_message_id=message_id)
        except httpx.HTTPError as exc:
            logger.warning("falha de rede ao enviar pelo whatsapp: %s", type(exc).__name__)
            return DeliveryResult(delivered=False, error=type(exc).__name__)
        finally:
            if self._client is None:
                client.close()

    # --- recepcao --------------------------------------------------------

    def on_inbound(self, payload: dict[str, Any]) -> list[InboundEvent]:
        return parse_webhook(payload)


def parse_webhook(payload: dict[str, Any]) -> list[InboundEvent]:
    """Normaliza o payload do webhook em eventos do Orbi.

    Suporta texto, toque em botao (que vira a mesma escolha numerada) e reacao
    👍/👎, que alimenta a fila de correcoes.
    """
    events: list[InboundEvent] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            metadata = value.get("metadata", {}) or {}
            to_address = str(
                metadata.get("phone_number_id") or metadata.get("display_phone_number") or ""
            )
            for message in value.get("messages", []) or []:
                event = _to_event(message, to_address)
                if event is not None:
                    events.append(event)
    return events


def _to_event(message: dict[str, Any], to_address: str) -> InboundEvent | None:
    from_address = str(message.get("from", ""))
    message_id = message.get("id")
    received_at = _timestamp(message.get("timestamp"))
    kind = message.get("type")

    if kind == "text":
        return InboundEvent(
            channel=CHANNEL_NAME,
            from_address=from_address,
            to_address=to_address,
            text=str((message.get("text") or {}).get("body", "")),
            message_id=message_id,
            received_at=received_at,
        )

    if kind == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        reply_id = str(reply.get("id", ""))
        choice = reply_id.removeprefix("opt_")
        return InboundEvent(
            channel=CHANNEL_NAME,
            from_address=from_address,
            to_address=to_address,
            text=choice if choice.isdigit() else str(reply.get("title", "")),
            message_id=message_id,
            received_at=received_at,
            kind="choice",
        )

    if kind == "reaction":
        reaction = message.get("reaction") or {}
        emoji = str(reaction.get("emoji", ""))
        return InboundEvent(
            channel=CHANNEL_NAME,
            from_address=from_address,
            to_address=to_address,
            text="",
            message_id=message_id,
            received_at=received_at,
            kind="reaction",
            reaction=_feedback_of(emoji),
            reacted_message_id=str(reaction.get("message_id", "")) or None,
        )

    # Audio, imagem e documento estao fora do MVP: respondemos fora de escopo.
    if kind in {"audio", "image", "document", "video", "sticker", "location"}:
        return InboundEvent(
            channel=CHANNEL_NAME,
            from_address=from_address,
            to_address=to_address,
            text="",
            message_id=message_id,
            received_at=received_at,
            kind="unsupported",
        )
    return None


def _feedback_of(emoji: str) -> str | None:
    if emoji in {"👍", "👍🏻", "👍🏼", "👍🏽", "👍🏾", "👍🏿", "❤️", "🙏"}:
        return "up"
    if emoji in {"👎", "👎🏻", "👎🏼", "👎🏽", "👎🏾", "👎🏿"}:
        return "down"
    return None


def _timestamp(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def verify_signature(app_secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    """Valida `X-Hub-Signature-256`.

    Sem isso, qualquer um que descubra a URL do webhook fala pelo canal do
    cliente. E verificacao obrigatoria, nao opcional.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def verify_challenge(
    verify_token: str, mode: str | None, token: str | None, challenge: str | None
) -> str | None:
    """Responde ao handshake de verificacao do webhook."""
    if mode == "subscribe" and token and hmac.compare_digest(token, verify_token):
        return challenge
    return None
