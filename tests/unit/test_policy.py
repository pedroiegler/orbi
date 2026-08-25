"""Policy Layer e Field Policy.

Inclui o golden test obrigatorio da secao 6.8: a matriz combinatoria
`role x tool` inteira, com o resultado esperado escrito a mao.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from orbi.erp.port import Invoice, Order, OrderLine, PriceResult, StockLocation, StockResult
from orbi.policy import field_policy
from orbi.policy.decision import ReasonCode
from orbi.policy.engine import PolicySubject, evaluate, no_rate_limit, policy_version_hash
from orbi.policy.field_policy import FieldPolicyError
from orbi.tools.registry import tool_names

ALL_TOOLS = frozenset(tool_names())


def _subject(role: str = "sales_rep", **overrides: object) -> PolicySubject:
    defaults: dict[str, object] = {
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "tenant_status": "active",
        "user_id": "22222222-2222-4222-8222-222222222222",
        "user_active": True,
        "role": role,
        "enabled_tools": ALL_TOOLS,
        "erp_supported_tools": ALL_TOOLS,
    }
    defaults.update(overrides)
    return PolicySubject(**defaults)  # type: ignore[arg-type]


# --- matriz role x tool (golden test) ------------------------------------

EXPECTED_MATRIX: dict[tuple[str, str], bool] = {
    ("sales_rep", "check_stock"): True,
    ("sales_rep", "check_price"): True,
    ("sales_rep", "get_last_order"): True,
    ("sales_rep", "list_open_invoices"): False,
    ("finance", "check_stock"): False,
    ("finance", "check_price"): True,
    ("finance", "get_last_order"): True,
    ("finance", "list_open_invoices"): True,
    ("admin", "check_stock"): True,
    ("admin", "check_price"): True,
    ("admin", "get_last_order"): True,
    ("admin", "list_open_invoices"): True,
}

_ARGS: dict[str, dict[str, object]] = {
    "check_stock": {"product_term": "cimento"},
    "check_price": {"product_term": "cimento"},
    "list_open_invoices": {"customer_term": "construtora silva"},
    "get_last_order": {"customer_term": "construtora silva"},
}


@pytest.mark.parametrize(("pair", "expected"), sorted(EXPECTED_MATRIX.items()))
def test_role_tool_matrix(pair: tuple[str, str], expected: bool) -> None:
    role, tool = pair
    decision = evaluate(_subject(role), tool, _ARGS[tool], no_rate_limit)
    assert decision.allowed is expected, f"{role} x {tool}"
    if not expected:
        assert decision.reason_code == ReasonCode.TOOL_NOT_ALLOWED_FOR_ROLE


def test_matrix_covers_every_combination() -> None:
    """Se uma tool ou papel novo entrar, o golden test falha ate ser revisado."""
    combinations = {(role, tool) for role in ("sales_rep", "finance", "admin") for tool in ALL_TOOLS}
    assert combinations == set(EXPECTED_MATRIX)


# --- ordem das regras ----------------------------------------------------


def test_inactive_tenant_is_denied_first() -> None:
    decision = evaluate(
        _subject("admin", tenant_status="suspended"), "check_stock", _ARGS["check_stock"],
        no_rate_limit,
    )
    assert decision.reason_code == ReasonCode.TENANT_INACTIVE
    assert decision.checks == ("TenantActive",)


def test_inactive_user_is_denied_before_the_tool_is_looked_at() -> None:
    decision = evaluate(
        _subject("admin", user_active=False), "check_stock", _ARGS["check_stock"], no_rate_limit
    )
    assert decision.reason_code == ReasonCode.USER_INACTIVE


def test_user_needing_reverification_is_denied() -> None:
    decision = evaluate(
        _subject("admin", needs_reverification=True),
        "check_stock",
        _ARGS["check_stock"],
        no_rate_limit,
    )
    assert decision.reason_code == ReasonCode.USER_NEEDS_REVERIFICATION


def test_unknown_tool_is_denied() -> None:
    decision = evaluate(_subject("admin"), "search_product", {}, no_rate_limit)
    assert decision.reason_code == ReasonCode.TOOL_UNKNOWN


def test_tool_disabled_for_tenant_is_denied() -> None:
    subject = _subject("admin", enabled_tools=frozenset({"check_price"}))
    decision = evaluate(subject, "check_stock", _ARGS["check_stock"], no_rate_limit)
    assert decision.reason_code == ReasonCode.TOOL_DISABLED_FOR_TENANT


def test_tool_not_supported_by_the_erp_is_denied() -> None:
    """`capabilities()` desliga a tool sem `if erp == x` no Runtime."""
    subject = _subject("admin", erp_supported_tools=frozenset({"check_stock", "check_price"}))
    decision = evaluate(subject, "list_open_invoices", _ARGS["list_open_invoices"], no_rate_limit)
    assert decision.reason_code == ReasonCode.TOOL_NOT_SUPPORTED_BY_ERP


def test_identifier_in_args_is_denied_with_its_own_reason() -> None:
    decision = evaluate(_subject("admin"), "check_stock", {"product_term": "4471"}, no_rate_limit)
    assert decision.reason_code == ReasonCode.ARGS_CONTAIN_IDENTIFIER


def test_invalid_args_are_denied() -> None:
    decision = evaluate(_subject("admin"), "check_stock", {"produto": "cimento"}, no_rate_limit)
    assert decision.reason_code == ReasonCode.ARGS_INVALID


def test_rate_limit_is_the_last_check() -> None:
    decision = evaluate(
        _subject("admin"),
        "check_stock",
        _ARGS["check_stock"],
        lambda _subject: ReasonCode.RATE_LIMIT_MINUTE,
    )
    assert decision.reason_code == ReasonCode.RATE_LIMIT_MINUTE
    assert decision.checks[-1] == "RateLimit"


def test_allowed_decision_carries_validated_args() -> None:
    decision = evaluate(_subject("admin"), "check_price", {"product_term": "cimento"}, no_rate_limit)
    assert decision.allowed
    assert decision.validated_args.product_term == "cimento"
    assert decision.policy_version_hash


def test_policy_version_hash_changes_with_tenant_configuration() -> None:
    base = policy_version_hash(_subject("admin"))
    changed = policy_version_hash(_subject("admin", enabled_tools=frozenset({"check_stock"})))
    assert base != changed
    assert policy_version_hash(_subject("admin")) == base


# --- Field Policy --------------------------------------------------------


def _price() -> PriceResult:
    return PriceResult(
        erp_entity_id="4471",
        name="TB PVC ESG 100MM BR",
        unit_price=Decimal("38.90"),
        unit_cost=Decimal("24.10"),
        margin_percent=Decimal("38.05"),
        quantity=Decimal("10"),
        total=Decimal("389.00"),
    )


def test_sales_rep_never_sees_cost_or_margin() -> None:
    filtered = field_policy.apply("sales_rep", "check_price", _price())
    assert "unit_cost" not in filtered
    assert "margin_percent" not in filtered
    assert filtered["unit_price"] == Decimal("38.90")


def test_finance_sees_cost() -> None:
    filtered = field_policy.apply("finance", "check_price", _price())
    assert filtered["unit_cost"] == Decimal("24.10")
    assert filtered["margin_percent"] == Decimal("38.05")


def test_no_sensitive_field_survives_at_any_depth_for_sales_rep() -> None:
    """Custo aninhado em linha de pedido vazaria tanto quanto no primeiro nivel."""
    order = Order(
        erp_entity_id="8842",
        number="PV 8842",
        customer_id="9001",
        customer_name="CONSTRUTORA SILVA",
        ordered_at=__import__("datetime").datetime(2026, 8, 20, 9, 0),
        total=Decimal("1000.00"),
        status="sale",
        lines=(
            OrderLine(
                product_id="5120",
                product_name="CIM CP-II 50KG",
                quantity=Decimal("10"),
                unit_price=Decimal("32.30"),
                total=Decimal("323.00"),
            ),
        ),
    )
    filtered = field_policy.apply("sales_rep", "get_last_order", order)
    assert not _contains_sensitive(filtered)


def _contains_sensitive(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in field_policy.SENSITIVE_FIELDS or _contains_sensitive(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive(item) for item in value)
    return False


def test_stock_fields_pass_through_with_locations() -> None:
    stock = StockResult(
        erp_entity_id="4471",
        name="TB PVC ESG 100MM BR",
        physical=Decimal("44"),
        reserved=Decimal("7"),
        available=Decimal("37"),
        basis="available",
        locations=(
            StockLocation(
                location_id="1",
                name="Matriz",
                physical=Decimal("30"),
                reserved=Decimal("5"),
                available=Decimal("25"),
            ),
        ),
    )
    filtered = field_policy.apply("sales_rep", "check_stock", stock)
    assert filtered["basis"] == "available"
    assert filtered["locations"][0]["name"] == "Matriz"


def test_invoices_are_filtered_as_a_list() -> None:
    invoices = [
        Invoice(
            erp_entity_id="70001",
            number="NF 12345",
            customer_id="9001",
            customer_name="CONSTRUTORA SILVA",
            amount=Decimal("4820.00"),
            open_amount=Decimal("4820.00"),
            due_date=__import__("datetime").date(2026, 8, 12),
        )
    ]
    filtered = field_policy.apply("finance", "list_open_invoices", invoices)
    assert len(filtered) == 1
    assert filtered[0]["number"] == "NF 12345"


def test_missing_whitelist_fails_closed() -> None:
    """Papel sem whitelist para a tool nao recebe resposta nenhuma."""
    with pytest.raises(FieldPolicyError):
        field_policy.apply("sales_rep", "list_open_invoices", [])


def test_every_allowed_pair_has_a_field_whitelist() -> None:
    for role in ("sales_rep", "finance", "admin"):
        for tool in ALL_TOOLS:
            allowed = EXPECTED_MATRIX[(role, tool)]
            has_whitelist = (role, tool) in field_policy.FIELD_POLICY
            assert allowed == has_whitelist, f"{role} x {tool}"
