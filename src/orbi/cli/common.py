"""Apoio comum aos comandos da CLI."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from orbi.db.models import Tenant
from orbi.db.session import admin_session, tenant_session

console = Console()


def ok(message: str) -> None:
    console.print(f"[green]✓[/green] {message}")


def warn(message: str) -> None:
    console.print(f"[yellow]![/yellow] {message}")


def fail(message: str, code: int = 1) -> NoReturn:
    """Encerra o comando. Nunca retorna — o tipo diz isso ao verificador."""
    console.print(f"[red]✗[/red] {message}")
    raise typer.Exit(code)


def table(title: str, columns: list[str]) -> Table:
    built = Table(title=title, title_justify="left", header_style="bold")
    for column in columns:
        built.add_column(column)
    return built


def resolve_tenant_id(slug: str) -> uuid.UUID:
    """Traduz slug em id. Usa a sessao administrativa: e comando de operacao."""
    with admin_session() as session:
        tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).first()
        if tenant is None:
            fail(f"tenant '{slug}' nao encontrado. Veja `orbi tenant list`.")
        return tenant.id


@contextmanager
def tenant_context(slug: str) -> Iterator[tuple[uuid.UUID, Session]]:
    """Abre a sessao ja escopada ao tenant — a mesma porta do Runtime."""
    tenant_id = resolve_tenant_id(slug)
    with tenant_session(tenant_id) as session:
        yield tenant_id, session
