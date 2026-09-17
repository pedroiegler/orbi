"""`orbi tenant ...` — criar, ativar e desativar cliente."""

from __future__ import annotations

import uuid
from typing import Annotated

import typer
from sqlalchemy import func, select

from orbi.catalog.sync import deactivate_all
from orbi.cli.common import console, fail, ok, resolve_tenant_id, table, warn
from orbi.core import plans
from orbi.core.crypto import CredentialCipher
from orbi.core.errors import ConfigurationError
from orbi.db.models import Tenant, TenantSettings, TenantTool, User
from orbi.db.seed import sync_global_config
from orbi.db.session import admin_session, tenant_session
from orbi.llm import tenant_keys
from orbi.tools.registry import tool_names

app = typer.Typer(help="Clientes (tenants).", no_args_is_help=True)

SlugOption = Annotated[str, typer.Option("--tenant", "-t", help="Slug do cliente.")]


@app.command("add")
def add(
    slug: SlugOption,
    name: Annotated[str, typer.Option("--name", help="Razao social ou nome fantasia.")],
    phone: Annotated[str, typer.Option("--phone", help="Numero do WhatsApp do cliente, com DDI.")],
    phone_number_id: Annotated[
        str, typer.Option("--phone-number-id", help="phone_number_id da Cloud API.")
    ] = "",
    plan: Annotated[
        str, typer.Option("--plan", help=f"Um de: {', '.join(plans.codigos())}")
    ] = plans.PADRAO,
    token: Annotated[
        str, typer.Option("--token", help="Token da Cloud API. Fica cifrado no banco.")
    ] = "",
) -> None:
    """Cadastra um cliente. O numero e do cliente, nao do Orbi."""
    try:
        plano = plans.plano_de(plan)
    except plans.PlanoDesconhecido as exc:
        fail(str(exc))

    with admin_session() as session:
        sync_global_config(session)
        if session.scalars(select(Tenant).where(Tenant.slug == slug)).first():
            fail(f"ja existe um tenant com o slug '{slug}'")

        tenant = Tenant(
            id=uuid.uuid4(),
            slug=slug,
            name=name,
            status="active",
            plan=plano.codigo,
            monthly_query_cap=plano.teto_mensal,
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

    ok(f"tenant '{slug}' criado — {plano.descricao()}")
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


@app.command("set-llm")
def set_llm(
    slug: SlugOption,
    provider: Annotated[str, typer.Option("--provider", help="openai, anthropic ou gemini.")],
    api_key: Annotated[str, typer.Option("--api-key", help="Chave do projeto deste cliente.")],
    model: Annotated[str, typer.Option("--model", help="Modelo que este cliente usa.")],
) -> None:
    """Da a este cliente a propria chave de LLM, cifrada (D-041).

    Vale quando o cliente tem projeto proprio no provedor: o gasto dele ganha
    teto proprio, e a cota dele para de ser dividida com os outros.
    """
    tenant_id = resolve_tenant_id(slug)
    try:
        credencial = tenant_keys.montar_credencial(provider, api_key, model)
    except ConfigurationError as exc:
        fail(str(exc))

    with admin_session() as session:
        settings = session.get(TenantSettings, tenant_id)
        assert settings is not None
        settings.llm_credentials_encrypted = tenant_keys.cifrar(credencial)
    tenant_keys.limpar_cache()

    ok(f"'{slug}' passa a usar chave propria: {provider} · {model}")
    if tenant_keys.modelo_sem_preco(credencial):
        warn(
            f"'{model}' esta fora da tabela de precos: o custo por turno "
            "seria gravado como zero. Acrescente em llm/pricing.py."
        )
    console.print(
        "  [dim]Se a chave do cliente falhar (teto estourado, revogada), o turno "
        "cai para a chave global e um alerta e emitido — o cliente nao fica sem "
        "resposta, mas o gasto volta a ser nosso.[/dim]"
    )


@app.command("clear-llm")
def clear_llm(slug: SlugOption) -> None:
    """Devolve o cliente a chave global."""
    tenant_id = resolve_tenant_id(slug)
    with admin_session() as session:
        settings = session.get(TenantSettings, tenant_id)
        assert settings is not None
        tinha = settings.llm_credentials_encrypted is not None
        settings.llm_credentials_encrypted = None
    tenant_keys.limpar_cache()
    if not tinha:
        warn(f"'{slug}' ja usava a chave global: nada a fazer")
        return
    ok(f"'{slug}' voltou a usar a chave global")


@app.command("list")
def list_tenants(
    slugs: Annotated[
        bool,
        typer.Option("--slugs", help="So os slugs, um por linha. Para scripts e cron."),
    ] = False,
    ativos: Annotated[
        bool, typer.Option("--ativos/--todos", help="Filtra por status ativo.")
    ] = False,
) -> None:
    """Lista os clientes.

    `--slugs` existe porque o cron precisa de uma lista, e extrair slug de uma
    tabela desenhada com `awk` nao funciona: a borda vira um item vazio e o nome
    que quebra em duas linhas vira um item `|`. Cada cliente novo acrescentava
    uma falha por noite no log — e log com falha rotineira e log que ninguem le.
    """
    with admin_session() as session:
        consulta = select(Tenant).order_by(Tenant.created_at)
        if ativos:
            consulta = consulta.where(Tenant.status == "active")
        tenants = session.scalars(consulta).all()

    if slugs:
        for tenant in tenants:
            print(tenant.slug)
        return

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
        tools = session.scalars(select(TenantTool).where(TenantTool.tenant_id == tenant_id)).all()
        usuarios_ativos = (
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.tenant_id == tenant_id, User.active.is_(True))
            )
            or 0
        )

    assert tenant is not None and settings is not None
    view = table(f"Tenant {tenant.slug}", ["campo", "valor"])
    view.add_row("nome", tenant.name)
    view.add_row("status", tenant.status)
    plano = plans.plano_de(tenant.plan)
    view.add_row(
        "plano",
        f"{plano.nome} · R$ {plano.mensal_brl:.2f}/mes · teto {tenant.monthly_query_cap}/mes",
    )
    view.add_row(
        "usuarios",
        f"{usuarios_ativos} de {plano.max_usuarios}"
        + ("  ← no limite" if usuarios_ativos >= plano.max_usuarios else ""),
    )
    view.add_row("numero", tenant.channel_address or "-")
    view.add_row("token de canal", "configurado" if tenant.channel_token_encrypted else "ausente")
    view.add_row("chave de LLM", tenant_keys.descricao(settings.llm_credentials_encrypted))
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


@app.command("set-plan")
def set_plan(
    slug: SlugOption,
    plan: Annotated[str, typer.Option("--plan", help=f"Um de: {', '.join(plans.codigos())}")],
) -> None:
    """Troca o plano do cliente e ajusta o teto de consultas."""
    try:
        plano = plans.plano_de(plan)
    except plans.PlanoDesconhecido as exc:
        fail(str(exc))

    tenant_id = resolve_tenant_id(slug)
    with admin_session() as session:
        tenant = session.get(Tenant, tenant_id)
        assert tenant is not None
        usuarios = (
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.tenant_id == tenant_id, User.active.is_(True))
            )
            or 0
        )
        if usuarios > plano.max_usuarios:
            fail(
                f"'{slug}' tem {usuarios} usuarios ativos e o plano {plano.nome} "
                f"permite {plano.max_usuarios}. Desative usuarios antes de rebaixar."
            )
        tenant.plan = plano.codigo
        tenant.monthly_query_cap = plano.teto_mensal
    ok(f"'{slug}' agora e {plano.descricao()}")


@app.command("activate")
def activate(slug: SlugOption) -> None:
    """Reativa um cliente suspenso."""
    _set_status(slug, "active")


@app.command("deactivate")
def deactivate(slug: SlugOption) -> None:
    """Suspende o cliente e desativa o indice de resolucao.

    O catalogo e desativado, nunca apagado: a auditoria referencia esses ids.
    """
    tenant_id = resolve_tenant_id(slug)
    _set_status(slug, "suspended")
    with tenant_session(tenant_id) as session:
        deactivated = deactivate_all(session, tenant_id)
    ok(f"{deactivated} itens do catalogo desativados (nada foi apagado)")


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
