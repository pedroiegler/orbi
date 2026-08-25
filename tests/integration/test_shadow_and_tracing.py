"""Shadow evals e tracing: as duas coisas que rodam ao lado do turno."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from orbi.audit.logger import AuditRecord, write
from orbi.db.models import EvalRun
from orbi.db.session import admin_session, tenant_session
from orbi.evals.shadow import run_shadow, sample_questions
from orbi.llm.providers.rule_based import RuleBasedProvider
from orbi.llm.router import LLMRouter
from orbi.observability.tracing import LangfuseTracer, NullTracer, TracePort, build_tracer
from orbi.runtime.pipeline import TurnOutcome

pytestmark = pytest.mark.integration


def _audit(tenant: uuid.UUID, question: str, tool: str, args: dict[str, str]) -> None:
    with tenant_session(tenant) as session:
        write(
            session,
            AuditRecord(
                tenant_id=str(tenant),
                trace_id=str(uuid.uuid4()),
                channel="whatsapp",
                status="ok",
                policy_decision="ALLOW",
                policy_version_hash="hash",
                message_text=question,
                tool_name=tool,
                tool_args=args,
            ),
        )


# --- shadow evals ---------------------------------------------------------


def test_shadow_samples_real_questions_anonymized(tenant_id: uuid.UUID) -> None:
    _audit(tenant_id, "quanto tem de cimento? meu cpf e 123.456.789-00", "check_stock",
           {"product_term": "cimento"})
    for _ in range(9):
        _audit(tenant_id, "quanto tem de argamassa?", "check_stock", {"product_term": "argamassa"})

    with tenant_session(tenant_id) as session:
        sample = sample_questions(session, tenant_id)

    assert sample
    assert all("123.456.789-00" not in case["question"] for case in sample)


def test_shadow_detects_when_the_tool_choice_changes(tenant_id: uuid.UUID) -> None:
    """O turno original escolheu check_price; o prompt atual escolhe check_stock."""
    for _ in range(5):
        _audit(tenant_id, "quanto tem de cimento?", "check_price", {"product_term": "cimento"})

    result = run_shadow(
        tenant_id, "teste", llm=LLMRouter(primary=RuleBasedProvider()), record=False
    )

    assert result.sampled > 0
    assert result.tool_agreement < 1.0
    assert any("check_price" in divergence for divergence in result.divergences)


def test_shadow_reports_agreement_when_nothing_changed(tenant_id: uuid.UUID) -> None:
    for _ in range(5):
        _audit(tenant_id, "quanto tem de cimento?", "check_stock", {"product_term": "cimento"})

    result = run_shadow(
        tenant_id, "teste", llm=LLMRouter(primary=RuleBasedProvider()), record=False
    )

    assert result.tool_agreement == 1.0
    assert result.args_agreement == 1.0
    assert result.divergences == []


def test_shadow_never_touches_the_erp(tenant_id: uuid.UUID) -> None:
    """Reexecutar a consulta gastaria rate limit do cliente sem responder nada."""
    from orbi.evals import shadow

    source = (
        __import__("pathlib").Path(shadow.__file__).read_text(encoding="utf-8")
    )
    assert "gateway" not in source
    assert "ErpGateway" not in source


def test_shadow_records_the_run(tenant_id: uuid.UUID) -> None:
    for _ in range(5):
        _audit(tenant_id, "quanto tem de cimento?", "check_stock", {"product_term": "cimento"})

    run_shadow(tenant_id, "teste", llm=LLMRouter(primary=RuleBasedProvider()), record=True)

    with admin_session() as session:
        runs = session.scalars(select(EvalRun).where(EvalRun.suite == "shadow")).all()
    assert runs


def test_shadow_with_no_history_is_a_no_op(tenant_id: uuid.UUID) -> None:
    result = run_shadow(
        tenant_id, "teste", llm=LLMRouter(primary=RuleBasedProvider()), record=False
    )
    assert result.sampled == 0
    assert result.tool_agreement == 1.0


# --- tracing --------------------------------------------------------------


def _outcome() -> TurnOutcome:
    return TurnOutcome(
        trace_id=str(uuid.uuid4()),
        status="ok",
        text="CIM CP-II 50KG — 575 un disponíveis",
        tenant_slug="teste",
        tool_name="check_stock",
        role="sales_rep",
        entity={"erp_entity_id": "5120", "stage": "trigram"},
        latencies_ms={"llm": 420, "erp": 380},
    )


def test_without_keys_the_tracer_is_a_no_op() -> None:
    tracer = build_tracer()
    assert isinstance(tracer, NullTracer)
    assert isinstance(tracer, TracePort)
    tracer.record_turn(_outcome(), "quanto tem de cimento?", "sales_rep")


def test_tracer_sends_the_turn_with_stage_latencies() -> None:
    captured: list[dict[str, Any]] = []

    class FakeObservation:
        def end(self) -> None:
            captured.append({"ended": True})

    class FakeClient:
        def start_observation(self, **kwargs: Any) -> FakeObservation:
            captured.append(kwargs)
            return FakeObservation()

        def flush(self) -> None:
            captured.append({"flushed": True})

    tracer = LangfuseTracer(FakeClient())
    outcome = _outcome()
    tracer.record_turn(outcome, "quanto tem de cimento?", "sales_rep")
    tracer.flush()

    sent = captured[0]
    assert sent["name"] == "turn.check_stock"
    # `TraceContext` do Langfuse e um TypedDict: o mesmo trace_id da auditoria.
    assert sent["trace_context"]["trace_id"] == uuid.UUID(outcome.trace_id).hex
    assert sent["metadata"]["latency_erp_ms"] == 380
    assert sent["metadata"]["latency_total_ms"] == 800
    assert sent["metadata"]["role"] == "sales_rep"
    assert any("flushed" in entry for entry in captured)


def test_tracing_failure_never_breaks_the_turn() -> None:
    class BrokenClient:
        def start_observation(self, **kwargs: Any) -> Any:
            raise RuntimeError("langfuse fora do ar")

    LangfuseTracer(BrokenClient()).record_turn(_outcome(), "pergunta", "sales_rep")


def test_tracer_marks_failures_with_a_warning_level() -> None:
    captured: list[dict[str, Any]] = []

    class FakeClient:
        def start_observation(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return type("Obs", (), {"end": lambda self: None})()

    outcome = _outcome()
    outcome.status = "erp_timeout"
    LangfuseTracer(FakeClient()).record_turn(outcome, "pergunta", "sales_rep")
    assert captured[0]["level"] == "WARNING"


def test_shadow_tolerates_legacy_rows_without_structured_arguments(
    tenant_id: uuid.UUID,
) -> None:
    """A auditoria e append-only: linha antiga com argumento nao estruturado fica.

    O shadow precisa atravessar o historico como ele e, nao como gostaria que
    fosse — e ainda assim comparar a escolha de tool.
    """
    from sqlalchemy import text as sql

    for _ in range(5):
        _audit(tenant_id, "quanto tem de cimento?", "check_stock", {"product_term": "cimento"})

    with admin_session() as session:
        session.execute(
            sql("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_append_only")
        )
        session.execute(
            sql(
                "UPDATE audit_logs SET tool_args = to_jsonb('product_term=cimento'::text) "
                "WHERE tenant_id = :t"
            ),
            {"t": str(tenant_id)},
        )
        session.execute(sql("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_append_only"))

    result = run_shadow(
        tenant_id, "teste", llm=LLMRouter(primary=RuleBasedProvider()), record=False
    )

    assert result.sampled > 0
    assert result.tool_agreement == 1.0
