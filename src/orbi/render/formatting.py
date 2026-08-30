"""Formatacao brasileira para os templates.

Numero, dinheiro e data sao formatados aqui, no codigo, e nao no template —
assim a regra vale para todos os canais e e testavel isoladamente.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/Sao_Paulo"


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def number(value: Any, decimals: int | None = None) -> str:
    """1234.5 → "1.234,5"; inteiro sai sem casas."""
    amount = _to_decimal(value)
    if amount is None:
        return "-"
    trim = decimals is None
    if decimals is None:
        decimals = 0 if amount == amount.to_integral_value() else 2
    quantized = amount.quantize(
        Decimal(1) if decimals == 0 else Decimal(f"0.{'0' * decimals}"),
        rounding=ROUND_HALF_UP,
    )
    formatted = f"{quantized:,.{decimals}f}"
    brazilian = formatted.replace(",", "@").replace(".", ",").replace("@", ".")
    if trim and "," in brazilian:
        # "2,50 un" e ruido; "2,5 un" e como a pessoa fala.
        brazilian = brazilian.rstrip("0").rstrip(",")
    return brazilian


def stock_quantity(value: Any, decimals: int = 2) -> str:
    """Quantidade de estoque, arredondada **para baixo**.

    Dinheiro arredonda para o mais proximo; estoque, nao. Um saldo de 0,996
    mostrado como "1" promete uma unidade que nao existe — e o vendedor descobre
    na hora de separar a mercadoria.

    Arredondar para baixo pode subestimar em fracao de unidade, o que e o erro
    seguro: quem prometeu menos entrega; quem prometeu mais explica.
    """
    amount = _to_decimal(value)
    if amount is None:
        return "-"
    truncado = amount.quantize(Decimal(f"0.{'0' * decimals}"), rounding=ROUND_DOWN)
    return number(truncado)


def money(value: Any, currency: str = "BRL") -> str:
    amount = _to_decimal(value)
    if amount is None:
        return "-"
    symbol = "R$" if currency == "BRL" else f"{currency} "
    return f"{symbol} {number(amount, 2)}".replace("  ", " ")


def day(value: Any, timezone: str = DEFAULT_TIMEZONE) -> str:
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, str) and value:
        try:
            return str(datetime.fromisoformat(value).strftime("%d/%m/%Y"))
        except ValueError:
            return value
    return "-"


def moment(value: Any, timezone: str = DEFAULT_TIMEZONE) -> str:
    parsed: Any = value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(parsed, datetime):
        stamp = parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo(timezone))
        return stamp.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y %H:%M")
    return day(parsed, timezone)


def plural(count: Any, singular: str, many: str) -> str:
    amount = _to_decimal(count)
    return singular if amount is not None and abs(amount) == 1 else many


def days_label(days: int) -> str:
    if days <= 0:
        return "a vencer"
    return f"vencido ha {days} {plural(days, 'dia', 'dias')}"
