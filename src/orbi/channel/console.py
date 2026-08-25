"""Canal de console — desenvolvimento e demonstracao.

Nao e o "chat Web" que o MVP descartou (ORBI.md secao 4): e a mesma conversa do
WhatsApp reproduzida no terminal, para a equipe testar sem depender da Meta.
Nao existe interface de chat para o cliente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orbi.channel.port import ChannelOption, DeliveryResult, InboundEvent

CHANNEL_NAME = "whatsapp"
"""Reaproveita o canal do tenant: o console apenas substitui o transporte."""


@dataclass
class ConsoleChannel:
    """Guarda o que seria enviado. Usado pela CLI e pelos testes."""

    name: str = CHANNEL_NAME
    sent: list[tuple[str, str]] = field(default_factory=list)
    options_sent: list[tuple[str, tuple[ChannelOption, ...]]] = field(default_factory=list)
    echo: bool = False

    def send_text(self, to_address: str, text: str) -> DeliveryResult:
        self.sent.append((to_address, text))
        if self.echo and text:
            print(text)
        return DeliveryResult(delivered=bool(text))

    def send_options(
        self, to_address: str, text: str, options: tuple[ChannelOption, ...]
    ) -> DeliveryResult:
        self.options_sent.append((to_address, options))
        return self.send_text(to_address, text)

    def on_inbound(self, payload: dict[str, Any]) -> list[InboundEvent]:
        return [
            InboundEvent(
                channel=self.name,
                from_address=str(payload.get("from", "")),
                to_address=str(payload.get("to", "")),
                text=str(payload.get("text", "")),
            )
        ]