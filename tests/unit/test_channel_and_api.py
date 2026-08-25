"""Canal WhatsApp e webhook: assinatura, parsing e botoes nativos."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest

from orbi.channel.port import ChannelOption, ChannelPort, options_from
from orbi.channel.whatsapp import (
    WhatsAppChannel,
    parse_webhook,
    verify_challenge,
    verify_signature,
)

APP_SECRET = "segredo-de-teste"


def _webhook(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "554330000001",
                                "phone_number_id": "phone-abc",
                            },
                            "messages": messages,
                        },
                    }
                ],
            }
        ],
    }


# --- assinatura ----------------------------------------------------------


def test_signature_is_required_and_verified() -> None:
    body = b'{"ok":true}'
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(APP_SECRET, body, signature)


@pytest.mark.parametrize(
    "signature",
    [None, "", "sha256=deadbeef", "md5=abc"],
)
def test_invalid_signatures_are_rejected(signature: str | None) -> None:
    assert not verify_signature(APP_SECRET, b'{"ok":true}', signature)


def test_tampered_body_fails_verification() -> None:
    body = b'{"ok":true}'
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert not verify_signature(APP_SECRET, b'{"ok":false}', signature)


def test_challenge_only_answers_with_the_right_token() -> None:
    assert verify_challenge("tok", "subscribe", "tok", "12345") == "12345"
    assert verify_challenge("tok", "subscribe", "outro", "12345") is None
    assert verify_challenge("tok", "unsubscribe", "tok", "12345") is None


# --- parsing -------------------------------------------------------------


def test_text_message_is_parsed() -> None:
    events = parse_webhook(
        _webhook(
            [
                {
                    "from": "554399990001",
                    "id": "wamid.1",
                    "timestamp": "1756000000",
                    "type": "text",
                    "text": {"body": "quanto tem de cimento?"},
                }
            ]
        )
    )
    assert len(events) == 1
    event = events[0]
    assert event.text == "quanto tem de cimento?"
    assert event.from_address == "554399990001"
    assert event.to_address == "phone-abc"
    assert event.kind == "text"


def test_button_reply_becomes_the_same_numbered_choice() -> None:
    """Botao nativo e lista numerada chegam identicos ao Runtime."""
    events = parse_webhook(
        _webhook(
            [
                {
                    "from": "554399990001",
                    "id": "wamid.2",
                    "type": "interactive",
                    "interactive": {
                        "type": "button_reply",
                        "button_reply": {"id": "opt_2", "title": "Tubo PVC 150"},
                    },
                }
            ]
        )
    )
    assert events[0].text == "2"
    assert events[0].kind == "choice"


def test_reaction_is_parsed_as_feedback() -> None:
    events = parse_webhook(
        _webhook(
            [
                {
                    "from": "554399990001",
                    "id": "wamid.3",
                    "type": "reaction",
                    "reaction": {"emoji": "👎", "message_id": "wamid.1"},
                }
            ]
        )
    )
    assert events[0].kind == "reaction"
    assert events[0].reaction == "down"
    assert events[0].reacted_message_id == "wamid.1"


def test_audio_is_marked_unsupported_instead_of_ignored() -> None:
    events = parse_webhook(
        _webhook([{"from": "5543", "id": "wamid.4", "type": "audio", "audio": {"id": "x"}}])
    )
    assert events[0].kind == "unsupported"


def test_empty_payload_yields_no_events() -> None:
    assert parse_webhook({}) == []
    assert parse_webhook({"entry": [{"changes": [{"value": {}}]}]}) == []


# --- envio ---------------------------------------------------------------


def _client(captured: list[dict[str, Any]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "url": str(request.url),
                "auth": request.headers.get("authorization"),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_send_text_posts_to_the_tenant_number() -> None:
    captured: list[dict[str, Any]] = []
    channel = WhatsAppChannel("phone-abc", "token-123", client=_client(captured))

    result = channel.send_text("554399990001", "37 un disponíveis")

    assert result.delivered
    assert result.provider_message_id == "wamid.out"
    assert "phone-abc/messages" in captured[0]["url"]
    assert captured[0]["auth"] == "Bearer token-123"
    assert captured[0]["body"]["text"]["body"] == "37 un disponíveis"


def test_empty_text_is_not_sent() -> None:
    """Recusa silenciosa a numero desconhecido nao vira mensagem."""
    captured: list[dict[str, Any]] = []
    channel = WhatsAppChannel("phone-abc", "token", client=_client(captured))
    assert channel.send_text("5543", "").delivered is False
    assert captured == []


def test_three_options_use_native_buttons() -> None:
    captured: list[dict[str, Any]] = []
    channel = WhatsAppChannel("phone-abc", "token", client=_client(captured))

    channel.send_options(
        "5543",
        "Qual deles?",
        (ChannelOption(1, "Tubo 100"), ChannelOption(2, "Tubo 150")),
    )

    body = captured[0]["body"]
    assert body["type"] == "interactive"
    ids = [button["reply"]["id"] for button in body["interactive"]["action"]["buttons"]]
    assert ids == ["opt_1", "opt_2"]


def test_more_than_three_options_fall_back_to_numbered_text() -> None:
    captured: list[dict[str, Any]] = []
    channel = WhatsAppChannel("phone-abc", "token", client=_client(captured))

    channel.send_options(
        "5543", "Qual deles?", tuple(ChannelOption(i, f"Item {i}") for i in range(1, 5))
    )

    assert captured[0]["body"]["type"] == "text"


def test_http_error_is_reported_without_leaking_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid token"}})

    channel = WhatsAppChannel(
        "phone-abc", "token-secreto", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = channel.send_text("5543", "oi")
    assert result.delivered is False
    assert "token-secreto" not in (result.error or "")


def test_whatsapp_channel_satisfies_the_port() -> None:
    assert isinstance(WhatsAppChannel("id", "token"), ChannelPort)


def test_options_are_built_from_runtime_entries() -> None:
    options = options_from(
        ({"name": "Tubo PVC 100", "code": "TB100"}, {"name": "Tubo PVC 150", "code": "TB150"})
    )
    assert options[0].index == 1
    assert options[1].reply_id == "opt_2"
