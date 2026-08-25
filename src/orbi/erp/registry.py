"""Fabrica de adapters.

E o unico ponto do sistema que sabe quais ERPs existem. O Core nunca importa
`erp.adapters.*` diretamente — e isso que impede `if erp == "x"` de aparecer no
Runtime (proibicao P10).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from orbi.core.errors import ConfigurationError
from orbi.core.settings import get_settings
from orbi.erp.adapters import memory as memory_adapter
from orbi.erp.adapters import odoo as odoo_adapter
from orbi.erp.port import ErpAdapter

AdapterBuilder = Callable[[dict[str, Any], dict[str, Any]], ErpAdapter]

_BUILDERS: dict[str, AdapterBuilder] = {
    odoo_adapter.ADAPTER_NAME: odoo_adapter.build,
    memory_adapter.ADAPTER_NAME: memory_adapter.build,
}

DEV_ONLY_ADAPTERS: frozenset[str] = frozenset({memory_adapter.ADAPTER_NAME})
"""Adapters que nunca respondem a um cliente real."""


def available_adapters() -> tuple[str, ...]:
    return tuple(sorted(_BUILDERS))


def ensure_allowed_in_production(name: str) -> None:
    """Recusa adapter de desenvolvimento fora de desenvolvimento.

    Fica aqui, e nao em quem chama, porque este e o unico modulo que tem o
    direito de conhecer nomes de ERP (proibicao P10).
    """
    if name in DEV_ONLY_ADAPTERS and get_settings().is_production:
        raise ConfigurationError(
            f"adapter '{name}' e de desenvolvimento e nao pode ser usado em producao"
        )


def build_adapter(
    name: str, credentials: dict[str, Any], config: dict[str, Any] | None = None
) -> ErpAdapter:
    try:
        builder = _BUILDERS[name]
    except KeyError:
        raise ConfigurationError(
            f"adapter desconhecido: {name}. Disponiveis: {', '.join(available_adapters())}"
        ) from None

    ensure_allowed_in_production(name)
    return builder(credentials, config or {})


def register_adapter(name: str, builder: AdapterBuilder) -> None:
    """Usado por testes e por adapters de terceiros durante o desenvolvimento.

    Um adapter so entra no `_BUILDERS` de verdade depois de passar no
    Conformance Kit (ORBI.md secao 6.11).
    """
    _BUILDERS[name] = builder
