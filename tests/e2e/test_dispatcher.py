"""Despacho: o que liga o canal ao Runtime e devolve a resposta.

Cobre as duas coisas que chegam pelo canal e **não** são pergunta: a reação
👍/👎 e a mídia fora de escopo — além do aviso de demora.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from orbi.api.dependencies import TurnDispatcher
from orbi.channel.console import ConsoleChannel
from orbi.channel.port import InboundEvent
from orbi.db.session import tenant_session
from orbi.observability.ops_channel import OpsNotifier
from orbi.runtime.pipeline import TurnOutcome
from tests.e2e.conftest import Harness

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


def _dispatcher(harness: Harness, channel: ConsoleChannel) -> TurnDispatcher:
    return TurnDispatcher(
        harness.runtime,
        OpsNotifier(),
        channel_factory=lambda channel_name, to_address: channel,
    )


def _event(harness: Harness, text_body: str, **overrides: Any) -> InboundEvent:
    payload: dict[str, Any] = {
        "channel": "whatsapp",
        "from_address": harness.phones["sales"],
        "to_address": harness.phones["tenant"],
        "text": text_body,
    }
    payload.update(overrides)
    return InboundEvent(**payload)


def test_question_is_answered_on_the_same_channel(harness: Harness) -> None:
    channel = ConsoleChannel()
    outcome = _dispatcher(harness, channel).dispatch(_event(harness, "quanto tem de cimento?"))

    assert outcome is not None and outcome.status == "ok"
    to_address, answer = channel.sent[-1]
    assert to_address == harness.phones["sales"]
    assert "CIM CP-II 50KG" in answer


def test_ambiguous_answer_goes_out_with_options(harness: Harness) -> None:
    channel = ConsoleChannel()
    _dispatcher(harness, channel).dispatch(_event(harness, "quanto tem de tubo pvc?"))

    assert channel.options_sent
    _, options = channel.options_sent[-1]
    assert [option.index for option in options] == [1, 2, 3]
    assert options[0].reply_id == "opt_1"


def test_button_reply_resolves_the_pending_choice(harness: Harness) -> None:
    """Botão nativo e "2" digitado chegam idênticos ao Runtime."""
    channel = ConsoleChannel()
    dispatcher = _dispatcher(harness, channel)
    dispatcher.dispatch(_event(harness, "quanto tem de tubo pvc?"))

    outcome = dispatcher.dispatch(_event(harness, "2", kind="choice"))

    assert outcome is not None
    assert outcome.status == "ok"
    assert outcome.used_llm is False


def test_unsupported_media_gets_the_out_of_scope_answer(harness: Harness) -> None:
    channel = ConsoleChannel()
    outcome = _dispatcher(harness, channel).dispatch(_event(harness, "", kind="unsupported"))

    assert outcome is None  # não é um turno
    assert "consulto" in channel.sent[-1][1].lower()


def test_thumbs_down_opens_a_correction_and_alerts(harness: Harness) -> None:
    channel = ConsoleChannel()
    ops = OpsNotifier()
    dispatcher = TurnDispatcher(harness.runtime, ops, channel_factory=lambda name, address: channel)
    dispatcher.dispatch(_event(harness, "quanto tem de cimento?"))

    dispatcher.dispatch(_event(harness, "", kind="reaction", reaction="down"))

    with tenant_session(harness.tenant_id) as session:
        corrections = session.execute(
            text(
                "SELECT status, kind, tool_name, wrong_entity_id FROM corrections "
                "WHERE tenant_id = :t"
            ),
            {"t": str(harness.tenant_id)},
        ).all()
        feedback = session.execute(
            text("SELECT feedback FROM audit_logs WHERE tenant_id = :t ORDER BY occurred_at DESC"),
            {"t": str(harness.tenant_id)},
        ).first()

    assert len(corrections) == 1
    assert corrections[0].status == "open"
    assert corrections[0].tool_name == "check_stock"
    assert corrections[0].wrong_entity_id == "5120"
    assert feedback is not None and feedback.feedback == "down"
    assert any("👎" in message for message in ops.sent)


def test_thumbs_up_is_recorded_without_opening_a_correction(harness: Harness) -> None:
    channel = ConsoleChannel()
    dispatcher = _dispatcher(harness, channel)
    dispatcher.dispatch(_event(harness, "quanto tem de cimento?"))

    dispatcher.dispatch(_event(harness, "", kind="reaction", reaction="up"))

    with tenant_session(harness.tenant_id) as session:
        corrections = session.execute(
            text("SELECT count(*) FROM corrections WHERE tenant_id = :t"),
            {"t": str(harness.tenant_id)},
        ).scalar_one()
        feedback = session.execute(
            text("SELECT feedback FROM audit_logs WHERE tenant_id = :t"),
            {"t": str(harness.tenant_id)},
        ).scalar_one()

    assert corrections == 0
    assert feedback == "up"


def test_reaction_from_an_unknown_number_is_ignored(harness: Harness) -> None:
    channel = ConsoleChannel()
    _dispatcher(harness, channel).dispatch(
        _event(
            harness,
            "",
            kind="reaction",
            reaction="down",
            from_address=harness.phones["stranger"],
        )
    )

    with tenant_session(harness.tenant_id) as session:
        corrections = session.execute(
            text("SELECT count(*) FROM corrections WHERE tenant_id = :t"),
            {"t": str(harness.tenant_id)},
        ).scalar_one()
    assert corrections == 0


def test_slow_turn_sends_the_interim_message(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acima do limiar, "consultando..." — percepção de velocidade e janela viva."""
    channel = ConsoleChannel()
    dispatcher = _dispatcher(harness, channel)

    original = harness.runtime.handle

    def slow(inbound: Any) -> TurnOutcome:
        time.sleep(0.25)
        return original(inbound)

    monkeypatch.setattr(harness.runtime, "handle", slow)
    monkeypatch.setattr(
        "orbi.api.dependencies.get_settings",
        lambda: type("S", (), {"slow_reply_threshold_ms": 50})(),
    )

    dispatcher.dispatch(_event(harness, "quanto tem de cimento?"))

    assert any("Consultando" in message for _, message in channel.sent)
    assert "CIM CP-II 50KG" in channel.sent[-1][1]


def test_fast_turn_does_not_send_the_interim_message(harness: Harness) -> None:
    channel = ConsoleChannel()
    _dispatcher(harness, channel).dispatch(_event(harness, "quanto tem de cimento?"))

    assert not any("Consultando" in message for _, message in channel.sent)


def test_unknown_tenant_number_produces_no_reply(harness: Harness) -> None:
    channel = ConsoleChannel()
    dispatcher = TurnDispatcher(
        harness.runtime, OpsNotifier(), channel_factory=lambda name, address: None
    )
    outcome = dispatcher.dispatch(
        _event(harness, "quanto tem de cimento?", to_address=f"+5599{uuid.uuid4().int % 10**9:09d}")
    )

    assert outcome is not None
    assert outcome.status == "unknown_tenant"
    assert channel.sent == []
