"""Field Policy — a camada que quase todo projeto esquece (ORBI.md secao 6.8).

Vendedor consulta preco mas nao ve custo nem margem. A filtragem acontece na
**saida do Adapter** e nao por instrucao de prompt — que se contorna.

A whitelist e indexada por **permissao**, nao por nome de papel. `price:read_cost`
e o que libera custo, tanto faz se o papel se chama `finance` ou
`gerente_comercial`. E isso que permite cada cliente ter os proprios papeis sem
mexer no codigo (D-040).

O que separa `check_price` de `check_price com custo` continua sendo esta camada,
nao uma tool diferente (D-018).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from orbi.tools.registry import CAP_PRICE_READ_COST

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

TOOL_FIELDS: dict[str, frozenset[str]] = {
    "check_stock": _STOCK_FIELDS,
    "check_price": _PRICE_FIELDS,
    "list_open_invoices": _INVOICE_FIELDS,
    "get_last_order": _ORDER_FIELDS,
}
"""Campos base de cada tool: iguais para todo mundo que pode chama-la.

Tool sem entrada aqui nao tem resposta possivel — a Field Policy nega por
omissao, e e assim que uma tool nova nao vaza campo por esquecimento."""

CAPABILITY_FIELDS: dict[str, dict[str, frozenset[str]]] = {
    "check_price": {CAP_PRICE_READ_COST: frozenset({"unit_cost", "margin_percent"})},
}
"""Campos que uma permissao **acrescenta**, por tool.

O escopo por tool e proposital. Se a permissao acrescentasse campo em qualquer
tool, uma tool futura cujo DTO tivesse um campo de mesmo nome passaria a mostra-lo
sem ninguem ter revisado. Aqui, tool nova comeca sem extra nenhum."""


class FieldPolicyError(Exception):
    """Nao existe whitelist para essa tool: falha fechada, nunca aberta."""


def allowed_fields(capabilities: frozenset[str], tool_name: str) -> frozenset[str]:
    """Campos visiveis para quem tem essas permissoes, nesta tool."""
    try:
        campos = TOOL_FIELDS[tool_name]
    except KeyError:
        raise FieldPolicyError(
            f"sem whitelist de campos para a tool '{tool_name}': a Field Policy nega por omissao"
        ) from None

    for capability, extras in CAPABILITY_FIELDS.get(tool_name, {}).items():
        if capability in capabilities:
            campos = campos | extras
    return campos


def can_see_cost(capabilities: frozenset[str]) -> bool:
    return CAP_PRICE_READ_COST in capabilities


def apply(
    capabilities: frozenset[str],
    tool_name: str,
    payload: BaseModel | list[BaseModel] | None,
) -> Any:
    """Filtra a saida do Adapter antes de qualquer renderizacao.

    Devolve `dict` (ou lista de `dict`) com apenas os campos permitidos.
    """
    whitelist = allowed_fields(capabilities, tool_name)
    mostra_custo = can_see_cost(capabilities)

    if payload is None:
        return None
    if isinstance(payload, list):
        return [_filter_model(item, whitelist, mostra_custo) for item in payload]
    return _filter_model(payload, whitelist, mostra_custo)


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
