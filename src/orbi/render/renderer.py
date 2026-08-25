"""ResultRenderer — a resposta e montada por template (ORBI.md secao 6.14).

O LLM nao redige. O renderer escolhe o template Jinja por
`(tool, channel, locale)` e monta a resposta a partir do DTO tipado, ja filtrado
pela Field Policy.

Toda resposta mostra **qual entidade foi usada** — o *resolution receipt*. Isso
transforma erro silencioso em erro visivel e corrigivel: o usuario responde
"nao e esse" e o sistema ganha um dado de treino em vez de perder confianca.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
    select_autoescape,
)

from orbi.core.errors import RenderError
from orbi.render import formatting

TEMPLATES_DIR = Path(__file__).parent / "templates"

MAX_INLINE_LOCATIONS = 3
"""1 local: so o total. 2 ou 3: quebra inline. 4 ou mais: os 2 maiores + resto."""

TOP_LOCATIONS_WHEN_MANY = 2


@dataclass(frozen=True)
class RenderContext:
    """Tudo que o template pode usar alem do resultado."""

    channel: str = "whatsapp"
    locale: str = "pt_BR"
    timezone: str = formatting.DEFAULT_TIMEZONE
    debug_code: str | None = None
    """Codigo curto do trace, so para tenants em piloto (secao 14)."""
    default_location_id: str | None = None


class ResultRenderer:
    """Monta o texto final. Deterministico por construcao."""

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._environment = Environment(
            loader=FileSystemLoader(str(templates_dir or TEMPLATES_DIR)),
            autoescape=select_autoescape(enabled_extensions=(), default=False),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )
        self._environment.filters.update(
            {
                "number": formatting.number,
                "money": formatting.money,
                "day": formatting.day,
                "moment": formatting.moment,
                "plural": formatting.plural,
                "days_label": formatting.days_label,
            }
        )

    def render(
        self,
        template_name: str,
        payload: dict[str, Any],
        context: RenderContext | None = None,
    ) -> str:
        resolved = context or RenderContext()
        try:
            template = self._environment.get_template(self._pick(template_name, resolved))
        except TemplateNotFound as exc:
            raise RenderError(f"template ausente: {template_name}") from exc

        try:
            body = template.render(
                **payload,
                channel=resolved.channel,
                locale=resolved.locale,
                timezone=resolved.timezone,
            )
        except Exception as exc:
            raise RenderError(f"falha ao renderizar {template_name}: {exc}") from exc

        text = "\n".join(line.rstrip() for line in body.strip().splitlines())
        if resolved.debug_code:
            text = f"{text}\n\n_ref {resolved.debug_code}_"
        return text

    def _pick(self, template_name: str, context: RenderContext) -> str:
        """Escolhe por `(tool, channel, locale)`, caindo para o generico.

        Hoje so existe WhatsApp em pt_BR; a busca existe porque Slack e Telegram
        entram depois e vao querer botao no lugar de lista numerada.
        """
        stem = template_name.removesuffix(".txt.j2")
        candidates = [
            f"{stem}.{context.channel}.{context.locale}.txt.j2",
            f"{stem}.{context.channel}.txt.j2",
            f"{stem}.txt.j2",
        ]
        for candidate in candidates:
            if self._environment.loader is not None and self._exists(candidate):
                return candidate
        return template_name

    def _exists(self, name: str) -> bool:
        try:
            self._environment.get_template(name)
        except TemplateNotFound:
            return False
        return True


def stock_view(payload: dict[str, Any], default_location_id: str | None = None) -> dict[str, Any]:
    """Prepara a quebra por deposito conforme a regra progressiva da secao 6.14."""
    locations = list(payload.get("locations") or [])
    for entry in locations:
        entry["quantity"] = _location_quantity(entry, payload.get("basis"))
    locations = [entry for entry in locations if entry["quantity"] != 0]

    if default_location_id:
        # Quando o vendedor tem deposito proprio, a resposta lidera por ele.
        locations.sort(
            key=lambda entry: (
                str(entry.get("location_id")) != str(default_location_id),
                -float(entry["quantity"]),
            )
        )
    else:
        locations.sort(key=lambda entry: -float(entry["quantity"]))

    view = dict(payload)
    view["locations"] = locations
    view["location_mode"] = _location_mode(payload, locations)
    view["shown_locations"] = (
        locations[:TOP_LOCATIONS_WHEN_MANY]
        if view["location_mode"] == "top"
        else locations
    )
    view["other_locations"] = max(0, len(locations) - len(view["shown_locations"]))
    view["quantity"] = _total_quantity(payload)
    return view


def _location_mode(payload: dict[str, Any], locations: list[dict[str, Any]]) -> str:
    if payload.get("location"):
        return "single"
    if len(locations) <= 1:
        return "none"
    if len(locations) <= MAX_INLINE_LOCATIONS:
        return "inline"
    return "top"


def _location_quantity(entry: dict[str, Any], basis: Any) -> Decimal:
    if basis == "available" and entry.get("available") is not None:
        return Decimal(str(entry["available"]))
    return Decimal(str(entry.get("physical", 0)))


def _total_quantity(payload: dict[str, Any]) -> Decimal:
    if payload.get("basis") == "available" and payload.get("available") is not None:
        return Decimal(str(payload["available"]))
    return Decimal(str(payload.get("physical", 0)))
