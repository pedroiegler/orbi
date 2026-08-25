"""Testes da camada core: deadline, trace, cripto e resiliencia."""

from __future__ import annotations

import pytest

from orbi.core.crypto import CredentialCipher
from orbi.core.deadline import Deadline, DeadlineExceeded
from orbi.core.errors import ConfigurationError
from orbi.core.resilience import (
    Bulkhead,
    BulkheadFull,
    CircuitBreaker,
    CircuitOpen,
    retry_call,
)
from orbi.core.settings import Settings
from orbi.core.trace import get_trace_id, short_code, trace_context


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- Deadline ------------------------------------------------------------


def test_deadline_remaining_never_negative() -> None:
    deadline = Deadline(total_ms=50)
    deadline.started_at -= 10  # 10s no passado
    assert deadline.remaining_ms() == 0
    assert deadline.expired()


def test_deadline_ensure_raises_when_budget_is_gone() -> None:
    deadline = Deadline(total_ms=10)
    deadline.started_at -= 5
    with pytest.raises(DeadlineExceeded) as exc:
        deadline.ensure("erp")
    assert exc.value.stage == "erp"


def test_budget_for_is_capped_by_remaining_budget() -> None:
    """O timeout do estagio nunca ultrapassa o que sobra do turno."""
    deadline = Deadline(total_ms=1_000)
    assert deadline.budget_for("erp", desired_ms=6_000) <= 1_000


def test_stage_records_latency_per_step() -> None:
    deadline = Deadline(total_ms=5_000)
    with deadline.stage("llm"):
        pass
    with deadline.stage("erp"):
        pass
    assert set(deadline.stage_latencies_ms) == {"llm", "erp"}


# --- Trace ---------------------------------------------------------------


def test_trace_context_sets_and_clears() -> None:
    assert get_trace_id() is None
    with trace_context() as trace_id:
        assert get_trace_id() == trace_id
    assert get_trace_id() is None


def test_short_code_is_stable_and_readable() -> None:
    trace_id = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    code = short_code(trace_id)
    assert code == short_code(trace_id)
    assert len(code) == 6
    assert not set(code) & set("IOU")


# --- Cripto --------------------------------------------------------------


def test_credential_roundtrip() -> None:
    cipher = CredentialCipher(CredentialCipher.generate_key())
    payload = {"url": "https://erp.example", "api_key": "s3cr3t"}
    token = cipher.encrypt(payload)
    assert b"s3cr3t" not in token
    assert cipher.decrypt(token) == payload


def test_credential_with_wrong_key_fails_loudly() -> None:
    token = CredentialCipher(CredentialCipher.generate_key()).encrypt({"a": 1})
    other = CredentialCipher(CredentialCipher.generate_key())
    with pytest.raises(ConfigurationError):
        other.decrypt(token)


# --- Circuit breaker -----------------------------------------------------


def test_breaker_opens_after_threshold_and_half_opens_after_window() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    key = CircuitBreaker.key_for("t1", "odoo", "get_stock")

    for _ in range(5):
        breaker.record_failure(key)
    assert breaker.state(key) == "open"
    with pytest.raises(CircuitOpen):
        breaker.before_call(key)

    clock.advance(61)
    assert breaker.state(key) == "half_open"
    breaker.before_call(key)  # a sonda passa
    with pytest.raises(CircuitOpen):
        breaker.before_call(key)  # a segunda nao
    breaker.record_success(key)
    assert breaker.state(key) == "closed"


def test_breaker_key_isolates_operations() -> None:
    """Estoque lento nao pode derrubar consulta de titulo (D-008)."""
    breaker = CircuitBreaker()
    stock = CircuitBreaker.key_for("t1", "odoo", "get_stock")
    invoices = CircuitBreaker.key_for("t1", "odoo", "list_open_invoices")
    for _ in range(5):
        breaker.record_failure(stock)
    assert breaker.state(stock) == "open"
    assert breaker.state(invoices) == "closed"
    breaker.before_call(invoices)


def test_breaker_failures_outside_window_do_not_accumulate() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    key = CircuitBreaker.key_for("t1", "odoo", "get_stock")
    for _ in range(4):
        breaker.record_failure(key)
    clock.advance(31)
    breaker.record_failure(key)
    assert breaker.state(key) == "closed"


# --- Bulkhead ------------------------------------------------------------


def test_bulkhead_limits_per_tenant() -> None:
    bulkhead = Bulkhead(limit=2)
    bulkhead.acquire("t1")
    bulkhead.acquire("t1")
    with pytest.raises(BulkheadFull):
        bulkhead.acquire("t1")
    bulkhead.acquire("t2")  # outro tenant nao e afetado
    bulkhead.release("t1")
    bulkhead.acquire("t1")
    assert bulkhead.in_flight("t1") == 2


# --- Retry ---------------------------------------------------------------


def test_retry_retries_once_then_succeeds() -> None:
    calls: list[int] = []

    def operation(timeout_ms: int) -> str:
        calls.append(timeout_ms)
        if len(calls) == 1:
            raise TimeoutError("primeira falhou")
        return "ok"

    result = retry_call(
        operation,
        deadline=Deadline(total_ms=5_000),
        stage="erp",
        timeout_ms=1_000,
        retry_on=(TimeoutError,),
        sleep=lambda _: None,
        jitter=lambda: 0.5,
    )
    assert result == "ok"
    assert len(calls) == 2


def test_retry_does_not_exceed_remaining_budget() -> None:
    deadline = Deadline(total_ms=200)
    slept: list[float] = []

    def operation(timeout_ms: int) -> str:
        assert timeout_ms <= 200
        raise TimeoutError("sempre falha")

    with pytest.raises(TimeoutError):
        retry_call(
            operation,
            deadline=deadline,
            stage="erp",
            timeout_ms=6_000,
            retry_on=(TimeoutError,),
            sleep=slept.append,
            jitter=lambda: 0.5,
        )
    assert all(s <= 0.2 for s in slept)


def test_retry_does_not_retry_unlisted_errors() -> None:
    calls: list[int] = []

    def operation(timeout_ms: int) -> str:
        calls.append(timeout_ms)
        raise ValueError("erro de negocio nao se repete")

    with pytest.raises(ValueError):
        retry_call(
            operation,
            deadline=Deadline(total_ms=5_000),
            stage="erp",
            timeout_ms=1_000,
            retry_on=(TimeoutError,),
            sleep=lambda _: None,
        )
    assert len(calls) == 1


# --- Settings ------------------------------------------------------------


def test_production_requires_two_manufacturers_and_no_llm_rendering() -> None:
    settings = Settings(
        ORBI_ENV="production",
        ORBI_SECRET_KEY="x",
        ORBI_LLM_PRIMARY="anthropic",
        ORBI_LLM_FALLBACK="anthropic",
        ORBI_LLM_RENDERING_ENABLED=True,
        ORBI_WHATSAPP_VERIFY_TOKEN="v",
        ORBI_WHATSAPP_APP_SECRET="s",
    )
    problems = settings.validate_for_production()
    assert any("fabricante" in p for p in problems)
    assert any("template" in p for p in problems)


def test_rule_based_provider_is_rejected_in_production() -> None:
    settings = Settings(ORBI_ENV="production", ORBI_LLM_PRIMARY="rule_based")
    assert any("rule_based" in p for p in settings.validate_for_production())
