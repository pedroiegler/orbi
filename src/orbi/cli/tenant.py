"""`orbi tenant ...` — criar, ativar e desativar cliente."""

from __future__ import annotations

import uuid
from typing import Annotated

import typer
from sqlalchemy import select

from orbi.cli.common import console, fail, ok, resolve_tenant_id, table, warn
from orbi.core.crypto import CredentialCipher
from orbi.db.models import Tenant, TenantSettings, TenantTool
from orbi.db.seed import sync_global_config
from orbi.db.session import admin_session
from orbi.tools.registry import tool_names

app = typer.Typer(help="Clientes (tenants).", no_args_is_help=True)

SlugOption = Annotated[str, typer.Option("--tenant", "-t", help="Slug do cliente.")]


@app.command("add")
def add(
    slug: SlugOption,
    name: Annotated[str, typer.Option("--name", help="Razao social ou nome fantasia.")],
    phone: Annotated[
        str, typer.Option("--phone", help="Numero do WhatsApp do cliente, com DDI.")
    ],
    phone_number_id: Annotated[
        str, typer.Option("--phone-number-id", help="phone_number_id da Cloud API.")
    ] = "",
    plan: Annotated[str, typer.Option("--plan", help="essencial | time | operacao")] = "essencial",
    token: Annotated[
        str, typer.Option("--token", help="Token da Cloud API. Fica cifrado no banco.")
    ] = "",
) -> None:
    """Cadastra um cliente. O numero e do cliente, nao do Orbi."""
    with admin_session() as session:
        sync_global_config(session)
        if session.scalars(select(Tenant).where(Tenant.slug == slug)).first():
            fail(f"ja existe um tenant com o slug '{slug}'")

        tenant = Tenant(
            id=uuid.uuid4(),
            slug=slug,
            name=name,
            status="active",
            plan=plan,
            channel="whatsapp",
            channel_address=phone,
            channel_phone_number_id=phone_number_id or None,
            channel_token_encrypted=(
                CredentialCipher().encrypt({"access_token": token}) if token else None
            ),
        )
        session.add(tenant)
        session.flush()
        session.add(TenantSettings(tenant_id=tenant.id))
        for tool in tool_names():
            session.add(TenantTool(tenant_id=tenant.id, tool_name=tool, enabled=True))

    ok(f"tenant '{slug}' criado")
    if not token:
        warn("sem token de canal: cadastre com `orbi tenant set-token` antes do go-live")


@app.command("set-token")
def set_token(
    slug: SlugOption,
    token: Annotated[str, typer.Option("--token", help="Token da Cloud API.")],
    phone_number_id: Annotated[str, typer.Option("--phone-number-id")] = "",
) -> None:
    """Grava o token do canal, cifrado. O valor em claro nunca toca o banco."""
    tenant_id = resolve_tenant_id(slug)
    with admin_session() as session:
        tenant = session.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.channel_token_encrypted = CredentialCipher().encrypt({"access_token": token})
        if phone_number_id:
            tenant.channel_phone_number_id = phone_number_id
    ok(f"token do canal atualizado para '{slug}'")


@app.command("list")
def list_tenants() -> None:
    """Lista os clientes."""
    with admin_session() as session:
        tenants = session.scalars(select(Tenant).order_by(Tenant.created_at)).all()

    if not tenants:
        warn("nenhum tenant cadastrado")
        return

    view = table("Clientes", ["slug", "nome", "status", "plano", "numero", "debug"])
    for tenant in tenants:
        view.add_row(
            tenant.slug,
            tenant.name,
            tenant.status,
            tenant.plan,
            tenant.channel_address or "-",
            "sim" if tenant.debug_mode else "nao",
        )
    console.print(view)


@app.command("show")
def show(slug: SlugOption) -> None:
    """Mostra a configuracao de um cliente."""
    tenant_id = resolve_tenant_id(slug)
    with admin_session() as session:
        tenant = session.get(Tenant, tenant_id)
        settings = session.get(TenantSettings, tenant_id)
        tools = session.scalars(
            select(TenantTool).where(TenantTool.tenant_id == tenant_id)
        ).all()

    assert tenant is not None and settings is not None
    view = table(f"Tenant {tenant.slug}", ["campo", "valor"])
    view.add_row("nome", tenant.name)
    view.add_row("status", tenant.status)
    view.add_row("plano", f"{tenant.plan} (teto {tenant.monthly_query_cap}/mes)")
    view.add_row("numero", tenant.channel_address or "-")
    view.add_row("token de canal", "configurado" if tenant.channel_token_encrypted else "ausente")
    view.add_row(
        "limiares",
        f"top1 {settings.resolution_top1_threshold} · gap {settings.resolution_gap_threshold}",
    )
    view.add_row(
        "calibrado em",
        settings.calibrated_at.strftime("%d/%m/%Y %H:%M") if settings.calibrated_at else "nunca",
    )
    view.add_row(
        "tools",
        ", ".join(f"{tool.tool_name}{'' if tool.enabled else ' (off)'}" for tool in tools) or "-",
    )
    console.print(view)


@app.command("activate")
def activate(slug: SlugOption) -> None:
    """Reativa um cliente suspenso."""
    _set_status(slug, "active")


@app.command("deactivate")
def deactivate(slug: SlugOption) -> None:
    """Suspende o cliente. As perguntas passam a receber recusa educada."""
    _set_status(slug, "suspended")


@app.command("debug")
def debug(
    slug: SlugOption,
    enable: Annotated[bool, typer.Option("--on/--off", help="Codigo do trace no rodape.")] = True,
) -> None:
    """Liga o modo debug: a resposta ganha o codigo curto do trace."""
    tenant_id = resolve_tenant_id(slug)
    with admin_session() as session:
        tenant = session.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.debug_mode = enable
    ok(f"modo debug {'ligado' if enable else 'desligado'} para '{slug}'")


def _set_status(slug: str, status: str) -> None:
    tenant_id = resolve_tenant_id(slug)
    with admin_session() as session:
        tenant = session.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.status = status
    ok(f"tenant '{slug}' agora esta {status}")
