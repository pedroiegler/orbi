"""Adapter Conformance Kit.

Um adapter novo so entra em producao depois de passar aqui. O kit verifica o
contrato inteiro do `ErpAdapter`, e nao apenas "responde alguma coisa":

- integracao e por interface oficial (secao 10);
- semantica de estoque e honesta sobre a base do numero (secao 6.12);
- erros sao normalizados, sem payload nativo vazando (secao 6.11);
- o adapter e somente leitura (proibicao P12);
- `capabilities()` e coerente com o que o adapter de fato entrega.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from orbi.erp.errors import ErpError, ErpNotFound
from orbi.erp.port import (
    WRITE_METHOD_PREFIXES,
    Capabilities,
    CatalogItem,
    ErpAdapter,
    Invoice,
    Order,
    PriceResult,
    StockResult,
)
from orbi.tools.registry import tool_names
from tests.conformance.conftest import AdapterCase

pytestmark = pytest.mark.conformance


# --- contrato estrutural --------------------------------------------------


def test_adapter_satisfies_the_protocol(case: AdapterCase) -> None:
    assert isinstance(case.adapter, ErpAdapter)
    assert isinstance(case.adapter.name, str) and case.adapter.name


def test_adapter_exposes_no_write_method(case: AdapterCase) -> None:
    """P12: MVP e somente leitura."""
    for attribute in dir(case.adapter):
        assert not attribute.startswith(WRITE_METHOD_PREFIXES), attribute


def test_integration_mode_is_official_api(case: AdapterCase) -> None:
    """Secao 10: sem banco direto, sem agente, sem scraping — verificavel em teste."""
    assert case.adapter.capabilities().integration_mode == "official_api"


def test_capabilities_declare_only_known_tools(case: AdapterCase) -> None:
    capabilities = case.adapter.capabilities()
    assert isinstance(capabilities, Capabilities)
    assert capabilities.supported_tools
    assert capabilities.supported_tools <= set(tool_names())


def test_check_connection_is_true_for_a_live_erp(case: AdapterCase) -> None:
    assert case.adapter.check_connection() is True


# --- estoque --------------------------------------------------------------


def test_get_stock_returns_typed_result(case: AdapterCase) -> None:
    result = case.adapter.get_stock(case.product_id)
    assert isinstance(result, StockResult)
    assert result.erp_entity_id == str(case.product_id)
    assert result.name
    assert isinstance(result.physical, Decimal)


def test_stock_basis_matches_capabilities(case: AdapterCase) -> None:
    """Secao 6.12: nunca mentir sobre a base do numero."""
    capabilities = case.adapter.capabilities()
    result = case.adapter.get_stock(case.product_id)
    if capabilities.supports_reservations:
        assert result.basis == "available"
        assert result.available is not None
        assert result.reserved is not None
        assert result.available == result.physical - result.reserved
    else:
        assert result.basis == "physical"


def test_stock_quantity_follows_basis(case: AdapterCase) -> None:
    result = case.adapter.get_stock(case.product_id)
    expected = result.available if result.basis == "available" else result.physical
    assert result.quantity == expected


def test_stock_by_location_is_restricted_to_that_location(case: AdapterCase) -> None:
    if not case.location_id:
        pytest.skip("adapter sem deposito conhecido para o teste")
    total = case.adapter.get_stock(case.product_id)
    single = case.adapter.get_stock(case.product_id, case.location_id)
    assert single.physical <= total.physical
    assert single.locations == () or len(single.locations) <= 1


def test_stock_locations_sum_to_the_total(case: AdapterCase) -> None:
    result = case.adapter.get_stock(case.product_id)
    if not result.locations:
        pytest.skip("adapter sem quebra por deposito")
    assert sum((entry.physical for entry in result.locations), Decimal("0")) == result.physical


def test_missing_product_raises_normalized_error(case: AdapterCase) -> None:
    with pytest.raises(ErpNotFound):
        case.adapter.get_stock(case.missing_product_id)


# --- preco ----------------------------------------------------------------


def test_get_price_returns_typed_result(case: AdapterCase) -> None:
    result = case.adapter.get_price(case.product_id)
    assert isinstance(result, PriceResult)
    assert result.unit_price >= 0
    assert result.currency


def test_price_with_quantity_computes_total(case: AdapterCase) -> None:
    if not case.adapter.capabilities().supports_quantity_pricing:
        pytest.skip("ERP nao diferencia preco por quantidade")
    result = case.adapter.get_price(case.product_id, quantity=Decimal("10"))
    assert result.quantity == Decimal("10")
    assert result.total is not None
    assert result.total == (result.unit_price * Decimal("10")).quantize(Decimal("0.01"))


def test_price_for_customer_reports_the_customer(case: AdapterCase) -> None:
    if not case.adapter.capabilities().supports_customer_pricing:
        pytest.skip("ERP nao tem preco por cliente")
    result = case.adapter.get_price(case.product_id, customer_id=case.customer_id)
    assert result.customer_id == str(case.customer_id)
    assert result.customer_name


def test_price_exposes_cost_for_the_field_policy_to_filter(case: AdapterCase) -> None:
    """O adapter entrega custo; quem esconde e a Field Policy, nao o prompt."""
    result = case.adapter.get_price(case.product_id)
    assert "unit_cost" in type(result).model_fields


# --- financeiro e comercial ----------------------------------------------


def test_list_open_invoices_returns_typed_list(case: AdapterCase) -> None:
    if "list_open_invoices" not in case.adapter.capabilities().supported_tools:
        pytest.skip("ERP sem modulo financeiro exposto")
    invoices = case.adapter.list_open_invoices(case.customer_id)
    assert isinstance(invoices, list)
    for invoice in invoices:
        assert isinstance(invoice, Invoice)
        assert invoice.open_amount >= 0
        assert invoice.customer_id == str(case.customer_id)


def test_open_invoices_are_sorted_by_due_date(case: AdapterCase) -> None:
    if "list_open_invoices" not in case.adapter.capabilities().supported_tools:
        pytest.skip("ERP sem modulo financeiro exposto")
    invoices = case.adapter.list_open_invoices(case.customer_id)
    dues = [invoice.due_date for invoice in invoices]
    assert dues == sorted(dues)


def test_get_last_order_returns_order_or_none(case: AdapterCase) -> None:
    if "get_last_order" not in case.adapter.capabilities().supported_tools:
        pytest.skip("ERP sem modulo comercial exposto")
    order = case.adapter.get_last_order(case.customer_id)
    if order is None:
        return
    assert isinstance(order, Order)
    assert order.customer_id == str(case.customer_id)
    assert isinstance(order.ordered_at, datetime)
    for line in order.lines:
        assert line.quantity > 0


def test_unknown_customer_is_handled_without_leaking_native_errors(
    case: AdapterCase,
) -> None:
    try:
        result = case.adapter.list_open_invoices(case.missing_customer_id)
    except ErpError:
        return  # erro normalizado tambem e resposta valida
    assert result == []


# --- catalogo -------------------------------------------------------------


def test_iter_catalog_yields_typed_items(case: AdapterCase) -> None:
    items = []
    for index, item in enumerate(case.adapter.iter_catalog(entity_types=("product",))):
        assert isinstance(item, CatalogItem)
        assert item.entity_type == "product"
        assert item.erp_entity_id
        assert item.name
        items.append(item)
        if index >= 20:
            break
    assert items, "o ERP precisa devolver ao menos um produto"


def test_iter_catalog_ids_are_unique(case: AdapterCase) -> None:
    seen = set()
    for index, item in enumerate(case.adapter.iter_catalog(entity_types=("product",))):
        assert item.erp_entity_id not in seen
        seen.add(item.erp_entity_id)
        if index >= 50:
            break


def test_iter_catalog_supports_customers(case: AdapterCase) -> None:
    customers = []
    for index, item in enumerate(case.adapter.iter_catalog(entity_types=("customer",))):
        assert item.entity_type == "customer"
        customers.append(item)
        if index >= 5:
            break
    assert customers, "o ERP precisa devolver ao menos um cliente"


def test_incremental_catalog_is_declared_and_honored(case: AdapterCase) -> None:
    if not case.adapter.capabilities().supports_incremental_catalog:
        pytest.skip("ERP nao suporta leitura incremental")
    future = datetime(2999, 1, 1)
    items = list(case.adapter.iter_catalog(since=future, entity_types=("product",)))
    assert items == []
