"""Adapter Conformance Kit — parametrizacao.

O kit roda contra todo adapter registrado. O `memory` roda sempre (e o que
sustenta os evals L4 no CI). O `odoo` roda quando ha instancia viva, declarada
por `ORBI_ODOO_URL`, `ORBI_ODOO_DB`, `ORBI_ODOO_USER` e `ORBI_ODOO_API_KEY`.

Um ERP esta pronto quando passa neste kit — e isso e o que transforma "integrar
novo ERP" em tarefa estimavel (ORBI.md secao 6.11).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from orbi.erp.port import ErpAdapter


@dataclass
class AdapterCase:
    """Adapter mais os identificadores reais que o kit precisa para consultar."""

    adapter: ErpAdapter
    product_id: str
    customer_id: str
    location_id: str | None = None
    missing_product_id: str = "999999999"
    missing_customer_id: str = "999999999"
    notes: dict[str, Any] = field(default_factory=dict)


def _memory_case() -> AdapterCase:
    from orbi.erp.adapters.memory import MemoryAdapter

    return AdapterCase(
        adapter=MemoryAdapter(),
        product_id="4471",
        customer_id="9001",
        location_id="1",
    )


def _odoo_case() -> AdapterCase | None:
    url = os.environ.get("ORBI_ODOO_URL")
    if not url:
        return None
    from orbi.erp.adapters.odoo import OdooAdapter

    adapter = OdooAdapter(
        url=url,
        db=os.environ.get("ORBI_ODOO_DB", "orbi"),
        username=os.environ.get("ORBI_ODOO_USER", "admin"),
        api_key=os.environ.get("ORBI_ODOO_API_KEY", "admin"),
        default_timeout_ms=int(os.environ.get("ORBI_ODOO_TIMEOUT_MS", "20000")),
    )
    product_id = os.environ.get("ORBI_ODOO_PRODUCT_ID")
    customer_id = os.environ.get("ORBI_ODOO_CUSTOMER_ID")
    if not product_id or not customer_id:
        pytest.skip("defina ORBI_ODOO_PRODUCT_ID e ORBI_ODOO_CUSTOMER_ID")
    return AdapterCase(
        adapter=adapter,
        product_id=product_id,
        customer_id=customer_id,
        location_id=os.environ.get("ORBI_ODOO_LOCATION_ID"),
    )


def _cases() -> list[pytest.ParameterSet]:
    cases: list[pytest.ParameterSet] = [pytest.param(_memory_case, id="memory")]
    if os.environ.get("ORBI_ODOO_URL"):
        cases.append(pytest.param(_odoo_case, id="odoo", marks=pytest.mark.odoo))
    return cases


@pytest.fixture(params=_cases())
def case(request: pytest.FixtureRequest) -> AdapterCase:
    built = request.param()
    if built is None:
        pytest.skip("adapter indisponivel")
    return built
