"""Field Policy — a camada que quase todo projeto esquece (ORBI.md secao 6.8).

Vendedor consulta preco mas nao ve custo nem margem. A filtragem acontece na
**saida do Adapter**, por whitelist de campos por `(role, tool)`, e nao por
instrucao de prompt — que se contorna.

O que separa `check_price` de `check_price com custo` e esta camada, nao uma
tool diferente (D-018).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from orbi.tools.registry import CAP_PRICE_READ_COST, capabilities_for_role

SENSITIVE_FIELDS: frozenset[str] = frozenset({"unit_cost", "margin_percent", "cost", "margin"})
"""Campos que so aparecem para quem tem `price:read_cost`. O teste percorre a
resposta inteira, em qualquer profundidade, procurando por eles."""

_STOCK_FIELDS = frozenset(
    {
        "erp_entity_id",
        "name",
        "uom",
        "physical",
        "reserved",
        "available",
        "basis",
        "locations",
        "location",
    }
)

_PRICE_FIELDS = frozenset(
    {
        "erp_entity_id",
        "name",
        "unit_price",
        "currency",
        "uom",
        "quantity",
        "total",
        "customer_id",
        "customer_name",
        "price_list",
        "discount_percent",
    }
)

_PRICE_FIELDS_WITH_COST = _PRICE_FIELDS | {"unit_cost", "margin_percent"}

_INVOICE_FIELDS = frozenset(
    {
        "erp_entity_id",
        "number",
        "customer_id",
        "customer_name",
        "amount",
        "open_amount",
        "currency",
        "due_date",
        "issue_date",
        "days_overdue",
        "status",
    }
)

_ORDER_FIELDS = frozenset(
    {
        "erp_entity_id",
        "number",
        "customer_id",
        "customer_name",
        "ordered_at",
        "total",
        "currency",
        "status",
        "lines",
        "delivery_date",
    }
)

FIELD_POLICY: dict[tuple[str, str], frozenset[str]] = {
    # `finance` nao tem `stock:read` (ORBI.md secao 11): sem whitelist, a Field
    # Policy nega por omissao e a matriz do golden test bate com o registry.
    ("sales_rep", "check_stock"): _STOCK_FIELDS,
    ("admin", "check_stock"): _STOCK_FIELDS,
    ("sales_rep", "check_price"): _PRICE_FIELDS,
    ("finance", "check_price"): _PRICE_FIELDS_WITH_COST,
    ("admin", "check_price"): _PRICE_FIELDS_WITH_COST,
    ("finance", "list_open_invoices"): _INVOICE_FIELDS,
    ("admin", "list_open_invoices"): _INVOICE_FIELDS,
    ("sales_rep", "get_last_order"): _ORDER_FIELDS,
    ("finance", "get_last_order"): _ORDER_FIELDS,
    ("admin", "get_last_order"): _ORDER_FIELDS,
}


class FieldPolicyError(Exception):
    """Nao existe whitelist para esse par: falha fechada, nunca aberta."""


def allowed_fields(role: str, tool_name: str) -> frozenset[str]:
    try:
        return FIELD_POLICY[(role, tool_name)]
    except KeyError:
        raise FieldPolicyError(
            f"sem whitelist de campos para ({role}, {tool_name}): "
            "a Field Policy nega por omissao"
        ) from None


def can_see_cost(role: str) -> bool:
    return CAP_PRICE_READ_COST in capabilities_for_role(role)


def apply(role: str, tool_name: str, payload: BaseModel | list[BaseModel] | None) -> Any:
    """Filtra a saida do Adapter antes de qualquer renderizacao.

    Devolve `dict` (ou lista de `dict`) com apenas os campos permitidos ao papel.
    """
    whitelist = allowed_fields(role, tool_name)
    show_cost = can_see_cost(role)

    if payload is None:
        return None
    if isinstance(payload, list):
        return [_filter_model(item, whitelist, show_cost) for item in payload]
    return _filter_model(payload, whitelist, show_cost)


def _filter_model(model: BaseModel, whitelist: frozenset[str], show_cost: bool) -> dict[str, Any]:
    data = model.model_dump()
    filtered: dict[str, Any] = {}
    for key, value in data.items():
        if key not in whitelist:
            continue
        if key in SENSITIVE_FIELDS and not show_cost:
            continue
        filtered[key] = _scrub(value, show_cost)
    return filtered


def _scrub(value: Any, show_cost: bool) -> Any:
    """Remove campo sensivel em qualquer profundidade.

    Linha de pedido e quebra por deposito sao aninhadas; um campo de custo que
    aparecesse la seria um vazamento tao real quanto no primeiro nivel.
    """
    if isinstance(value, dict):
        return {
            key: _scrub(item, show_cost)
            for key, item in value.items()
            if show_cost or key not in SENSITIVE_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item, show_cost) for item in value]
    return value
