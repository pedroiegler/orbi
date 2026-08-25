"""`orbi erp ...` — credenciais, capabilities e circuito."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from orbi.cli.common import console, fail, ok, resolve_tenant_id, table, warn
from orbi.db.session import tenant_session
from orbi.erp import connection as erp_connection
from orbi.erp.errors import ErpError
from orbi.erp.gateway import reset_resilience_state
from orbi.erp.registry import available_adapters

app = typer.Typer(help="Conexao com o ERP do cliente.", no_args_is_help=True)

TenantOption = Annotated[str, typer.Option("--tenant", "-t")]


@app.command("add")
def add(
    tenant: TenantOption,
    adapter: Annotated[
        str, typer.Option("--adapter", help=f"Um de: {', '.join(available_adapters())}")
    ],
    credentials: Annotated[
        str,
        typer.Option(
            "--credentials",
            help='JSON com as credenciais, ex: \'{"url":"...","db":"...","api_key":"..."}\'',
        ),
    ],
    config: Annotated[
        str, typer.Option("--config", help="JSON de configuracao nao secreta.")
    ] = "{}",
) -> None:
    """Grava a credencial cifrada e consulta as capabilities do ERP."""
    if adapter not in available_adapters():
        fail(f"adapter desconhecido: {adapter}")

    try:
        parsed_credentials = json.loads(credentials)
        parsed_config = json.loads(config)
    except json.JSONDecodeError as exc:
        fail(f"JSON invalido: {exc}")

    tenant_id = resolve_tenant_id(tenant)
    with tenant_session(tenant_id) as session:
        erp_connection.store_credentials(
            session, tenant_id, adapter, parsed_credentials, parsed_config
        )
        try:
            tenant_erp = erp_connection.build_for_tenant(
                session, tenant_id, refresh_capabilities=True
            )
        except ErpError as exc:
            fail(f"credencial gravada, mas o ERP recusou: {exc}")

    ok(f"conexao '{adapter}' gravada para '{tenant}' (credencial cifrada)")
    _print_capabilities(tenant_erp.capabilities)


@app.command("test")
def test(tenant: TenantOption) -> None:
    """Valida credencial e disponibilidade — primeira etapa do onboarding."""
    tenant_id = resolve_tenant_id(tenant)
    with tenant_session(tenant_id) as session:
        try:
            tenant_erp = erp_connection.build_for_tenant(session, tenant_id)
            connected = tenant_erp.adapter.check_connection()
        except ErpError as exc:
            fail(f"falha ao conectar: {type(exc).__name__}: {exc}")

    if connected:
        ok(f"conexao com {tenant_erp.adapter_name} respondendo")
    else:
        fail("o ERP respondeu, mas recusou a credencial")


@app.command("capabilities")
def capabilities(
    tenant: TenantOption,
    refresh: Annotated[bool, typer.Option("--refresh", help="Consulta o ERP de novo.")] = False,
) -> None:
    """Mostra o que aquele ERP sabe fazer e sincroniza as tools do tenant."""
    tenant_id = resolve_tenant_id(tenant)
    with tenant_session(tenant_id) as session:
        tenant_erp = erp_connection.build_for_tenant(
            session, tenant_id, refresh_capabilities=refresh
        )
        disabled = erp_connection.sync_tenant_tools(
            session, tenant_id, tenant_erp.capabilities
        )
    _print_capabilities(tenant_erp.capabilities)
    if disabled:
        warn(f"tools desligadas automaticamente: {', '.join(disabled)}")


@app.command("reset-breaker")
def reset_breaker() -> None:
    """Fecha os circuitos abertos deste processo, apos resolver o incidente."""
    reset_resilience_state()
    ok("circuitos fechados")


def _print_capabilities(capabilities: object) -> None:
    data = capabilities.model_dump()  # type: ignore[attr-defined]
    view = table("Capabilities", ["campo", "valor"])
    for key in sorted(data):
        value = data[key]
        if isinstance(value, (set, frozenset, list, tuple)):
            value = ", ".join(sorted(str(item) for item in value))
        view.add_row(key, str(value))
    console.print(view)
