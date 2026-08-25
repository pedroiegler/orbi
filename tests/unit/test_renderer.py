"""ResultRenderer: a resposta e template, e o template nunca mente."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from orbi.core.errors import RenderError
from orbi.erp.port import Invoice, PriceResult, StockLocation, StockResult
from orbi.policy import field_policy
from orbi.render.formatting import days_label, money, number
from orbi.render.renderer import RenderContext, ResultRenderer, stock_view
from orbi.tools.registry import all_tools

RENDERER = ResultRenderer()


def _stock(basis: str = "available", locations: int = 2) -> dict:
    all_locations = [
        StockLocation(
            location_id=str(index + 1),
            name=name,
            physical=Decimal(physical),
            reserved=Decimal(reserved),
            available=Decimal(physical) - Decimal(reserved),
        )
        for index, (name, physical, reserved) in enumerate(
            [
                ("Matriz", "30", "5"),
                ("Filial Cambe", "14", "2"),
                ("Deposito Norte", "9", "0"),
                ("Loja Centro", "4", "0"),
            ][:locations]
        )
    ]
    physical = sum((entry.physical for entry in all_locations), Decimal("0"))
    reserved = sum((entry.reserved or Decimal("0") for entry in all_locations), Decimal("0"))
    result = StockResult(
        erp_entity_id="4471",
        name="Tubo PVC Esgoto 100mm",
        uom="un",
        physical=physical,
        reserved=reserved if basis == "available" else None,
        available=physical - reserved if basis == "available" else None,
        basis=basis,  # type: ignore[arg-type]
        locations=tuple(all_locations),
    )
    payload = field_policy.apply("sales_rep", "check_stock", result)
    payload["code"] = "TBPVC100"
    return payload


# --- estoque -------------------------------------------------------------


def test_stock_answer_shows_the_entity_used() -> None:
    """Resolution receipt: toda resposta mostra qual entidade foi usada."""
    text = RENDERER.render("check_stock.txt.j2", stock_view(_stock()))
    assert "Tubo PVC Esgoto 100mm" in text
    assert "TBPVC100" in text


def test_available_basis_says_disponiveis() -> None:
    text = RENDERER.render("check_stock.txt.j2", stock_view(_stock("available")))
    assert "37 un disponíveis" in text
    assert "físico" not in text


def test_physical_basis_is_declared_explicitly() -> None:
    """P11/principio 6: nunca mentir sobre a base do numero."""
    text = RENDERER.render("check_stock.txt.j2", stock_view(_stock("physical")))
    assert "estoque físico" in text
    assert "não informa reservas" in text


def test_single_location_shows_only_the_total() -> None:
    text = RENDERER.render("check_stock.txt.j2", stock_view(_stock(locations=1)))
    assert "Matriz" not in text


def test_two_or_three_locations_are_broken_down_inline() -> None:
    text = RENDERER.render("check_stock.txt.j2", stock_view(_stock(locations=3)))
    assert "Matriz: 25" in text
    assert "Filial Cambe: 12" in text
    assert "Deposito Norte: 9" in text


def test_four_or_more_locations_show_the_two_largest() -> None:
    view = stock_view(_stock(locations=4))
    text = RENDERER.render("check_stock.txt.j2", view)
    assert "Matriz: 25" in text
    assert "Filial Cambe: 12" in text
    assert "e outros 2 locais" in text
    assert "Loja Centro" not in text


def test_default_location_leads_the_answer() -> None:
    view = stock_view(_stock(locations=3), default_location_id="3")
    assert view["locations"][0]["name"] == "Deposito Norte"


def test_location_filter_shows_only_that_location() -> None:
    payload = _stock(locations=1)
    payload["location"] = "Filial Cambe"
    text = RENDERER.render("check_stock.txt.j2", stock_view(payload))
    assert "Filial Cambe" in text


# --- preco ---------------------------------------------------------------


def _price(role: str) -> dict:
    result = PriceResult(
        erp_entity_id="4471",
        name="Tubo PVC Esgoto 100mm",
        unit_price=Decimal("38.90"),
        unit_cost=Decimal("24.10"),
        margin_percent=Decimal("38.05"),
        quantity=Decimal("50"),
        total=Decimal("1945.00"),
        customer_name="CONSTRUTORA SILVA",
        customer_id="9001",
        discount_percent=Decimal("5"),
    )
    return field_policy.apply(role, "check_price", result)


def test_price_for_sales_rep_never_prints_cost() -> None:
    text = RENDERER.render("check_price.txt.j2", _price("sales_rep"))
    assert "R$ 38,90" in text
    assert "Custo" not in text
    assert "24,10" not in text


def test_price_for_finance_prints_cost_and_margin() -> None:
    text = RENDERER.render("check_price.txt.j2", _price("finance"))
    assert "Custo R$ 24,10" in text
    assert "margem 38,1%" in text


def test_price_prints_the_total_for_the_quantity_asked() -> None:
    text = RENDERER.render("check_price.txt.j2", _price("sales_rep"))
    assert "50 un = R$ 1.945,00" in text


# --- financeiro ----------------------------------------------------------


def test_invoices_are_listed_with_due_dates_and_overdue_flag() -> None:
    invoices = [
        Invoice(
            erp_entity_id="70001",
            number="NF 12345",
            customer_id="9001",
            customer_name="CONSTRUTORA SILVA",
            amount=Decimal("4820.00"),
            open_amount=Decimal("4820.00"),
            due_date=date(2026, 8, 12),
            days_overdue=12,
            status="overdue",
        )
    ]
    payload = {
        "customer_name": "CONSTRUTORA SILVA",
        "invoices": field_policy.apply("finance", "list_open_invoices", invoices),
        "total_open": Decimal("4820.00"),
        "overdue_count": 1,
    }
    text = RENDERER.render("list_open_invoices.txt.j2", payload)
    assert "NF 12345" in text
    assert "R$ 4.820,00" in text
    assert "vencido ha 12 dias" in text


def test_no_invoices_is_a_clear_answer() -> None:
    text = RENDERER.render(
        "list_open_invoices.txt.j2",
        {
            "customer_name": "MARATEX",
            "invoices": [],
            "total_open": Decimal("0"),
            "overdue_count": 0,
        },
    )
    assert "nenhum título em aberto" in text


# --- mensagens de sistema ------------------------------------------------


def test_ambiguous_message_lists_numbered_options() -> None:
    text = RENDERER.render(
        "ambiguous.txt.j2",
        {
            "term": "tubo pvc",
            "options": [
                {"name": "Tubo PVC Esgoto 100mm", "code": "TBPVC100"},
                {"name": "Tubo PVC Esgoto 150mm", "code": "TBPVC150"},
            ],
        },
    )
    assert "1. Tubo PVC Esgoto 100mm" in text
    assert "2. Tubo PVC Esgoto 150mm" in text
    assert "Responda com o número" in text


def test_not_found_asks_for_the_code() -> None:
    text = RENDERER.render(
        "not_found.txt.j2",
        {"term": "cano roxo", "entity_label": "catálogo", "suggestions": []},
    )
    assert "código" in text


def test_denied_message_has_no_technical_detail() -> None:
    text = RENDERER.render("denied.txt.j2", {"reason_code": "TOOL_NOT_ALLOWED_FOR_ROLE"})
    assert "acesso" in text.lower()
    assert "TOOL_NOT_ALLOWED_FOR_ROLE" not in text


def test_erp_error_never_offers_a_cached_number() -> None:
    text = RENDERER.render("erp_error.txt.j2", {"kind": "timeout"})
    assert "Prefiro não responder" in text


def test_unknown_sender_message_reveals_nothing() -> None:
    text = RENDERER.render("unknown_sender.txt.j2", {})
    assert "cadastr" not in text.lower()
    assert "não existe" not in text.lower()


def test_debug_code_is_appended_only_when_asked() -> None:
    payload = stock_view(_stock())
    plain = RENDERER.render("check_stock.txt.j2", payload)
    with_code = RENDERER.render(
        "check_stock.txt.j2", payload, RenderContext(debug_code="7KQ2M1")
    )
    assert "7KQ2M1" not in plain
    assert "7KQ2M1" in with_code


def test_missing_template_fails_loudly() -> None:
    with pytest.raises(RenderError):
        RENDERER.render("inexistente.txt.j2", {})


def test_every_tool_has_its_template() -> None:
    """Quinto artefato do ToolSpec: sem template nao ha resposta."""
    for spec in all_tools():
        assert (
            RENDERER._environment.loader is not None
            and spec.template in RENDERER._environment.list_templates()
        ), spec.name


# --- formatacao ----------------------------------------------------------


def test_brazilian_number_formatting() -> None:
    assert number(Decimal("1234.5")) == "1.234,5"
    assert number(Decimal("37")) == "37"
    assert money(Decimal("1945")) == "R$ 1.945,00"


def test_days_label_reads_naturally() -> None:
    assert days_label(1) == "vencido ha 1 dia"
    assert days_label(12) == "vencido ha 12 dias"
    assert days_label(0) == "a vencer"


def test_datetime_is_rendered_in_local_time() -> None:
    from orbi.render.formatting import moment

    assert moment(datetime(2026, 8, 20, 9, 30)) == "20/08/2026 09:30"


# --- saida exata (o texto que o usuario recebe) --------------------------


def test_stock_answer_is_exactly_what_the_user_sees() -> None:
    text = RENDERER.render("check_stock.txt.j2", stock_view(_stock(locations=3)))
    assert text == (
        "Tubo PVC Esgoto 100mm (TBPVC100) — 46 un disponíveis\n"
        "  Matriz: 25\n"
        "  Filial Cambe: 12\n"
        "  Deposito Norte: 9"
    )


def test_physical_stock_answer_is_exactly_what_the_user_sees() -> None:
    text = RENDERER.render("check_stock.txt.j2", stock_view(_stock("physical", locations=1)))
    assert text == (
        "Tubo PVC Esgoto 100mm (TBPVC100) — 30 un em estoque físico\n"
        "Este ERP não informa reservas, então o número é o estoque físico."
    )


def test_price_answer_for_finance_is_exactly_what_the_user_sees() -> None:
    text = RENDERER.render("check_price.txt.j2", _price("finance"))
    assert text == (
        "Tubo PVC Esgoto 100mm — R$ 38,90 / un · CONSTRUTORA SILVA\n"
        "Desconto de tabela do cliente: 5%\n"
        "50 un = R$ 1.945,00\n"
        "Custo R$ 24,10 · margem 38,1%"
    )
