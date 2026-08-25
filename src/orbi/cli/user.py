"""`orbi user ...` — cadastro, papel e verificacao de numero."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select

from orbi.cli.common import console, fail, ok, resolve_tenant_id, table, warn
from orbi.db.models import User, UserIdentity
from orbi.db.session import admin_session, tenant_session
from orbi.identity import resolver as identity
from orbi.tools.registry import ROLE_CAPABILITIES

app = typer.Typer(help="Usuarios e vinculo de numero.", no_args_is_help=True)

ROLES = tuple(ROLE_CAPABILITIES)


@app.command("add")
def add(
    tenant: Annotated[str, typer.Option("--tenant", "-t")],
    phone: Annotated[str, typer.Option("--phone", help="Numero com DDI, ex: +5543999990001")],
    role: Annotated[str, typer.Option("--role", help=f"Um de: {', '.join(ROLES)}")],
    name: Annotated[str, typer.Option("--name")] = "",
    location: Annotated[
        str, typer.Option("--location", help="erp_entity_id do deposito padrao.")
    ] = "",
    verified: Annotated[
        bool,
        typer.Option(
            "--verified/--needs-verification", help="Marca o numero como ja confirmado."
        ),
    ] = True,
) -> None:
    """Cadastra o usuario e vincula o numero. Cadastro previo e obrigatorio."""
    if role not in ROLES:
        fail(f"papel invalido: {role}. Use um de: {', '.join(ROLES)}")

    tenant_id = resolve_tenant_id(tenant)
    with admin_session() as session:
        existing = session.scalars(
            select(UserIdentity).where(
                UserIdentity.channel == "whatsapp", UserIdentity.address == phone
            )
        ).first()
        if existing is not None:
            fail(f"o numero {phone} ja esta vinculado a outro usuario")

        user = User(
            tenant_id=tenant_id,
            name=name or phone,
            role_code=role,
            active=True,
            default_location_id=location or None,
        )
        session.add(user)
        session.flush()
        session.add(
            UserIdentity(
                tenant_id=tenant_id,
                user_id=user.id,
                channel="whatsapp",
                address=phone,
                active=True,
                verified_at=datetime.now(UTC) if verified else None,
            )
        )

    ok(f"{name or phone} cadastrado como {role} em '{tenant}'")


@app.command("import")
def import_csv(
    tenant: Annotated[str, typer.Option("--tenant", "-t")],
    path: Annotated[Path, typer.Option("--file", help="CSV com colunas: name,phone,role,location")],
) -> None:
    """Importa usuarios de CSV — etapa do `orbi onboard`."""
    if not path.exists():
        fail(f"arquivo nao encontrado: {path}")

    tenant_id = resolve_tenant_id(tenant)
    imported = skipped = 0
    with path.open(encoding="utf-8") as handle, admin_session() as session:
        for row in csv.DictReader(handle):
            phone = (row.get("phone") or "").strip()
            role = (row.get("role") or "").strip()
            if not phone or role not in ROLES:
                skipped += 1
                continue
            if session.scalars(
                select(UserIdentity).where(
                    UserIdentity.channel == "whatsapp", UserIdentity.address == phone
                )
            ).first():
                skipped += 1
                continue

            user = User(
                tenant_id=tenant_id,
                name=(row.get("name") or phone).strip(),
                role_code=role,
                active=True,
                default_location_id=(row.get("location") or "").strip() or None,
            )
            session.add(user)
            session.flush()
            session.add(
                UserIdentity(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    channel="whatsapp",
                    address=phone,
                    active=True,
                    verified_at=datetime.now(UTC),
                )
            )
            imported += 1

    ok(f"{imported} usuarios importados, {skipped} ignorados")


@app.command("list")
def list_users(tenant: Annotated[str, typer.Option("--tenant", "-t")]) -> None:
    """Lista os usuarios do cliente."""
    tenant_id = resolve_tenant_id(tenant)
    with tenant_session(tenant_id) as session:
        rows = session.execute(
            select(User, UserIdentity)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .order_by(User.created_at)
        ).all()

    if not rows:
        warn("nenhum usuario cadastrado")
        return

    view = table(f"Usuarios de {tenant}", ["nome", "papel", "numero", "ativo", "verificado"])
    for user, user_identity in rows:
        view.add_row(
            user.name,
            user.role_code,
            user_identity.address,
            "sim" if user.active else "nao",
            user_identity.verified_at.strftime("%d/%m/%Y") if user_identity.verified_at else "nao",
        )
    console.print(view)


@app.command("set-role")
def set_role(
    phone: Annotated[str, typer.Option("--phone")],
    role: Annotated[str, typer.Option("--role", help=f"Um de: {', '.join(ROLES)}")],
) -> None:
    """Troca o papel de um usuario pelo numero."""
    if role not in ROLES:
        fail(f"papel invalido: {role}")

    with admin_session() as session:
        user_identity = session.scalars(
            select(UserIdentity).where(
                UserIdentity.channel == "whatsapp", UserIdentity.address == phone
            )
        ).first()
        if user_identity is None:
            fail(f"numero {phone} nao encontrado")
        user = session.get(User, user_identity.user_id)
        assert user is not None
        user.role_code = role
    ok(f"{phone} agora e {role}")


@app.command("deactivate")
def deactivate(phone: Annotated[str, typer.Option("--phone")]) -> None:
    """Desativa o acesso de um numero."""
    with admin_session() as session:
        user_identity = session.scalars(
            select(UserIdentity).where(
                UserIdentity.channel == "whatsapp", UserIdentity.address == phone
            )
        ).first()
        if user_identity is None:
            fail(f"numero {phone} nao encontrado")
        user_identity.active = False
        user = session.get(User, user_identity.user_id)
        assert user is not None
        user.active = False
    ok(f"{phone} desativado")


@app.command("verify")
def verify(
    phone: Annotated[str, typer.Option("--phone")],
    code: Annotated[str, typer.Option("--code", help="Codigo recebido. Vazio gera um novo.")] = "",
) -> None:
    """Gera ou confirma o codigo de re-verificacao do numero."""
    with admin_session() as session:
        user_identity = session.scalars(
            select(UserIdentity).where(
                UserIdentity.channel == "whatsapp", UserIdentity.address == phone
            )
        ).first()
        if user_identity is None:
            fail(f"numero {phone} nao encontrado")

        if not code:
            generated = identity.start_verification(session, user_identity.id)
            ok(f"codigo gerado para {phone}: {generated} (validade de 15 minutos)")
            return

        if identity.confirm_verification(session, user_identity.id, code):
            ok(f"{phone} verificado")
        else:
            fail("codigo invalido ou expirado")


@app.command("erase")
def erase(
    tenant: Annotated[str, typer.Option("--tenant", "-t")],
    phone: Annotated[str, typer.Option("--phone")],
) -> None:
    """LGPD: anonimiza o texto das perguntas preservando as metricas."""
    from orbi.audit.logger import erase_user_data

    tenant_id = resolve_tenant_id(tenant)
    with admin_session() as session:
        user_identity = session.scalars(
            select(UserIdentity).where(
                UserIdentity.channel == "whatsapp", UserIdentity.address == phone
            )
        ).first()
        if user_identity is None:
            fail(f"numero {phone} nao encontrado")
        erased = erase_user_data(session, str(tenant_id), str(user_identity.user_id))
    ok(f"{erased} registros anonimizados; metricas e auditoria preservadas")
