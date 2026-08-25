"""Apoio comum aos comandos da CLI."""

from __future__ import annotations

import uuid
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from orbi.db.models import Tenant
from orbi.db.session import admin_session

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
