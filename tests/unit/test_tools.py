"""Contrato de argumentos e Tool Registry."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from orbi.tools.args import (
    CheckPriceArgs,
    CheckStockArgs,
    EntityTerm,
    InvalidEntityTerm,
    ListOpenInvoicesArgs,
)
from orbi.tools.registry import (
    FORBIDDEN_TOOL_PATTERNS,
    all_tools,
    capabilities_for_role,
    get_tool,
    tool_names,
    tools_for_role,
)

# --- EntityTerm: o LLM nunca emite identificadores (D-002) ---------------


@pytest.mark.parametrize(
    "term",
    [
        "4471",
        "004471",
        "44 71",
        "4471.0",
        "123.456.789-00",
        "12345678900",
        "12.345.678/0001-90",
        "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        "PRD-4471",
        "sku/1234",
        "#4471",
        "cod 4471",
        "7891234567890",
        "pedido 8842",
        "",
        "   ",
        "x" * 121,
    ],
)
def test_entity_term_rejects_identifiers(term: str) -> None:
    with pytest.raises(InvalidEntityTerm):
        EntityTerm(term)


@pytest.mark.parametrize(
    "term",
    [
        "tubo pvc 100",
        "tubo pvc esgoto 100mm",
        "cabo flexivel 2,5mm",
        "cimento",
        "construtora silva",
        "aquele tubo grosso de esgoto",
        "parafuso 1/2",
        "filial cambe",
        "tinta 3,6 l",
    ],
)
def test_entity_term_accepts_human_words(term: str) -> None:
    assert EntityTerm(term) == " ".join(term.split())


def test_entity_term_normalizes_whitespace() -> None:
    assert EntityTerm("  tubo   pvc  100 ") == "tubo pvc 100"


def test_pydantic_rejects_identifier_before_execution() -> None:
    with pytest.raises(ValidationError):
        CheckStockArgs(product_term="4471")


def test_args_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CheckStockArgs(product_term="cimento", produto="cimento")


def test_args_reject_product_id_field() -> None:
    with pytest.raises(ValidationError):
        CheckStockArgs(product_term="cimento", product_id=4471)


def test_optional_location_defaults_to_none() -> None:
    args = CheckStockArgs(product_term="tubo pvc 100")
    assert args.location_term is None


def test_price_quantity_accepts_json_numbers_as_decimal() -> None:
    args = CheckPriceArgs.model_validate({"product_term": "cimento", "quantity": 50})
    assert args.quantity == Decimal("50")


def test_price_quantity_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        CheckPriceArgs.model_validate({"product_term": "cimento", "quantity": 0})


def test_invoices_require_customer_term() -> None:
    with pytest.raises(ValidationError):
        ListOpenInvoicesArgs.model_validate({})


# --- Tool Registry -------------------------------------------------------


def test_registry_has_exactly_the_four_mvp_tools() -> None:
    assert set(tool_names()) == {
        "check_stock",
        "check_price",
        "list_open_invoices",
        "get_last_order",
    }


def test_no_entity_search_tool_exists() -> None:
    """P1: a resolucao e interna ao Runtime, nunca uma tool."""
    for name in tool_names():
        assert not any(pattern in name for pattern in FORBIDDEN_TOOL_PATTERNS), name


def test_json_schema_forbids_extra_properties() -> None:
    for spec in all_tools():
        schema = spec.json_schema()
        assert schema["input_schema"]["additionalProperties"] is False
        assert schema["name"] == spec.name
        assert schema["description"]


def test_json_schema_never_exposes_an_id_argument() -> None:
    for spec in all_tools():
        props = spec.json_schema()["input_schema"]["properties"]
        assert not any(key.endswith("_id") or key == "id" for key in props), spec.name


def test_tools_are_filtered_by_role_before_reaching_the_model() -> None:
    sales = {spec.name for spec in tools_for_role("sales_rep")}
    finance = {spec.name for spec in tools_for_role("finance")}
    admin = {spec.name for spec in tools_for_role("admin")}

    assert sales == {"check_stock", "check_price", "get_last_order"}
    assert finance == {"list_open_invoices", "get_last_order", "check_price"}
    assert admin == set(tool_names())


def test_sales_rep_has_no_cost_capability() -> None:
    assert "price:read_cost" not in capabilities_for_role("sales_rep")
    assert "price:read_cost" in capabilities_for_role("finance")


def test_get_tool_raises_for_unknown_name() -> None:
    with pytest.raises(KeyError):
        get_tool("search_product")


def test_every_spec_declares_its_five_artifacts() -> None:
    for spec in all_tools():
        assert spec.args_model is not None
        assert spec.json_schema()
        assert spec.template.endswith(".txt.j2")
        assert spec.eval_fixture.endswith(".json")
        assert spec.erp_operation
