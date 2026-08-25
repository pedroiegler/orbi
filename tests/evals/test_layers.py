"""Evals L1–L5 no CI (ORBI.md secao 14).

Regressao aqui bloqueia o merge. Nao e um teste de unidade: e a medida do
produto — selecao de tool, argumentos, resolucao, ponta a ponta e adversarial.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from orbi.db.models import EvalRun
from orbi.db.session import admin_session
from orbi.evals.runner import THRESHOLDS, run_e2e_layer, run_l1, run_l2, run_l3, run_layers
from orbi.llm.providers.rule_based import RuleBasedProvider
from orbi.llm.router import LLMRouter
from tests.e2e.conftest import Harness

pytestmark = [pytest.mark.evals, pytest.mark.integration]


def _llm() -> LLMRouter:
    return LLMRouter(primary=RuleBasedProvider())


def test_l1_tool_selection(harness: Harness) -> None:
    result = run_l1(_llm())
    assert result.total > 20, "o conjunto L1 precisa ser maior que uma amostra"
    assert result.passed, f"L1 em {result.score:.1%}: {result.failures[:5]}"


def test_l2_arguments(harness: Harness) -> None:
    result = run_l2(_llm())
    assert result.total > 10
    assert result.passed, f"L2 em {result.score:.1%}: {result.failures[:5]}"


def test_l3_entity_resolution(harness: Harness) -> None:
    result = run_l3(harness.tenant_id)
    assert not result.skipped
    assert result.passed, f"L3 em {result.score:.1%}: {result.failures[:5]}"


def test_l4_end_to_end(harness: Harness) -> None:
    result = run_e2e_layer("L4", harness.tenant_id, "l4_golden.json")
    assert not result.skipped
    assert result.passed, f"L4 em {result.score:.1%}: {result.failures[:5]}"


def test_l5_adversarial_has_no_tolerance(harness: Harness) -> None:
    """L5 exige 100%: injecao e escalada de privilegio nao tem nota de corte."""
    assert THRESHOLDS["L5"] == 1.0
    result = run_e2e_layer("L5", harness.tenant_id, "l5_adversarial.json")
    assert not result.skipped
    assert result.score == 1.0, f"L5 falhou: {result.failures}"


def test_run_layers_records_the_regression_table(harness: Harness) -> None:
    results = run_layers("L3", tenant_id=harness.tenant_id, record=True, llm=_llm())
    assert results

    with admin_session() as session:
        rows = session.scalars(
            select(EvalRun).where(EvalRun.layer == "L3").order_by(EvalRun.created_at.desc())
        ).all()
    assert rows
    assert rows[0].total > 0
    assert rows[0].prompt_version


def test_every_tool_has_eval_cases() -> None:
    """Quarto artefato do ToolSpec: tool sem caso de eval nao entra."""
    from orbi.evals.runner import load_dataset
    from orbi.tools.registry import all_tools

    for spec in all_tools():
        cases = load_dataset(spec.eval_fixture)
        assert len(cases) >= 3, f"{spec.name} precisa de casos de eval"
