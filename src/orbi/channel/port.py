"""ChannelPort — a fronteira com o canal (ORBI.md secao 6.2).

Tres operacoes: `send_text`, `send_options` e `on_inbound`.

`send_options` e desenhado desde ja **prevendo botoes nativos**, mesmo que o
WhatsApp caia para lista numerada quando ha mais de tres opcoes. Isso importa
porque Slack, Telegram e Discord — os proximos canais — suportam botao, e a
desambiguacao vira um toque em vez de digitar "2".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ChannelOption:
    """Uma opcao de desambiguacao."""

    index: int
    label: str
    description: str | None = None

    @property
    def reply_id(self) -> str:
        return f"opt_{self.index}"


@dataclass(frozen=True)
class InboundEvent:
    """Mensagem normalizada, vinda de qualquer canal."""

    channel: str
    from_address: str
    to_address: str
    text: str
    message_id: str | None = None
    received_at: datetime = datetime.now(UTC)
    kind: str = "text"
    """`text`, `choice` (toque em botao) ou `reaction` (👍/👎)."""
    reaction: str | None = None
    reacted_message_id: str | None = None


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    provider_message_id: str | None = None
    error: str | None = None


@runtime_checkable
class ChannelPort(Protocol):
    name: str

    def send_text(self, to_address: str, text: str) -> DeliveryResult: ...

    def send_options(
        self, to_address: str, text: str, options: tuple[ChannelOption, ...]
    ) -> DeliveryResult: ...

    def on_inbound(self, payload: dict[str, Any]) -> list[InboundEvent]: ...


def options_from(entries: tuple[dict[str, Any], ...]) -> tuple[ChannelOption, ...]:
    return tuple(
        ChannelOption(
            index=index,
            label=str(entry.get("name", ""))[:24],
            description=str(entry.get("code") or "") or None,
        )
        for index, entry in enumerate(entries, start=1)
    )
