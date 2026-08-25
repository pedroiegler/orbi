"""Webhook do WhatsApp de ponta a ponta: assinatura, turno e resposta enviada."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from orbi.api import dependencies
from orbi.api.app import create_app
from orbi.api.dependencies import TurnDispatcher
from orbi.channel.console import ConsoleChannel
from orbi.core.settings import get_settings, reset_settings_cache
from orbi.observability.ops_channel import OpsNotifier
from tests.e2e.conftest import Harness

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

APP_SECRET = "webhook-secret-de-teste"
VERIFY_TOKEN = "verify-token-de-teste"


@pytest.fixture
def client(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ORBI_WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("ORBI_WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)
    reset_settings_cache()
    get_settings()

    channel = ConsoleChannel()
    dispatcher = TurnDispatcher(
        harness.runtime,
        OpsNotifier(),
        channel_factory=lambda channel_name, to_address: channel,
    )
    monkeypatch.setattr(dependencies, "get_dispatcher", lambda: dispatcher)

    with TestClient(create_app()) as test_client:
        test_client.channel = channel  # type: ignore[attr-defined]
        test_client.harness = harness  # type: ignore[attr-defined]
        yield test_client

    reset_settings_cache()


def _signed(client: Any, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )


def _message(harness: Harness, text: str) -> dict[str, Any]:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": harness.phones["tenant"]},
                            "messages": [
                                {
                                    "from": harness.phones["sales"],
                                    "id": "wamid.test",
                                    "timestamp": "1756000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


def test_health_reports_the_database(client: Any) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_verification_handshake(client: Any) -> None:
    response = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "987654",
        },
    )
    assert response.status_code == 200
    assert response.text == "987654"


def test_verification_with_a_wrong_token_is_forbidden(client: Any) -> None:
    response = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "1"},
    )
    assert response.status_code == 403


def test_unsigned_webhook_is_rejected(client: Any) -> None:
    response = client.post("/webhooks/whatsapp", json=_message(client.harness, "oi"))
    assert response.status_code == 403
    assert client.channel.sent == []


def test_signed_question_runs_the_turn_and_answers_on_the_channel(client: Any) -> None:
    response = _signed(client, _message(client.harness, "quanto tem de cimento?"))

    assert response.status_code == 200
    assert client.channel.sent, "a resposta precisa sair pelo canal"
    to_address, text = client.channel.sent[-1]
    assert to_address == client.harness.phones["sales"]
    assert "CIM CP-II 50KG" in text


def test_ambiguous_answer_is_sent_with_options(client: Any) -> None:
    _signed(client, _message(client.harness, "quanto tem de tubo pvc?"))
    assert client.channel.options_sent
    _, options = client.channel.options_sent[-1]
    assert options[0].reply_id == "opt_1"


def test_no_documentation_endpoints_are_exposed(client: Any) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
