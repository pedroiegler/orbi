"""`orbi corrections ...` — a fila que transforma erro em vocabulario.

Todo erro do usuario e um dado de treino (principio 16): erro de resolucao vira
alias, erro de tool vira caso no eval set, erro de dado vira conversa com o
cliente.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import typer
from sqlalchemy import select

from orbi.cli.common import console, fail, ok, resolve_tenant_id, table, warn
from orbi.db.models import Correction, Tenant
from orbi.db.session import admin_session, tenant_session
from orbi.resolution import aliases as alias_store

app = typer.Typer(help="Fila de correcoes vinda do feedback.", no_args_is_help=True)

KINDS = ("tool", "resolution", "erp_data", "expectation", "unclassified")


@app.command("list")
def list_corrections(
    tenant: Annotated[str, typer.Option("--tenant", "-t", help="Vazio lista todos.")] = "",
    status: Annotated[str, typer.Option("--status")] = "open",
    limit: Annotated[int, typer.Option("--limit")] = 30,
) -> None:
    """Lista as correcoes pendentes."""
    rows: list[tuple[str, Correction]] = []
    with admin_session() as session:
        tenants = {
            tenant_row.id: tenant_row.slug for tenant_row in session.scalars(select(Tenant)).all()
        }
        query = select(Correction).where(Correction.status == status)
        if tenant:
            query = query.where(Correction.tenant_id == resolve_tenant_id(tenant))
        for correction in session.scalars(
            query.order_by(Correction.created_at.desc()).limit(limit)
        ).all():
            rows.append((tenants.get(correction.tenant_id, "?"), correction))

    if not rows:
        ok("nenhuma correcao pendente")
        return

    view = table("Correcoes", ["id", "cliente", "tipo", "termo", "tool", "quando", "trace"])
    for slug, correction in rows:
        view.add_row(
            str(correction.id),
            slug,
            correction.kind,
            correction.term or "-",
            correction.tool_name or "-",
            correction.created_at.strftime("%d/%m %H:%M"),
            correction.trace_id[:8],
        )
    console.print(view)


@app.command("resolve")
def resolve(
    correction_id: Annotated[int, typer.Option("--id", help="Id da correcao.")],
    entity: Annotated[
        str, typer.Option("--entity", help="erp_entity_id correto. Vira alias do cliente.")
    ] = "",
    kind: Annotated[str, typer.Option("--kind", help=f"Um de: {', '.join(KINDS)}")] = "resolution",
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    """Resolve a correcao. Com `--entity`, o termo vira alias daquele cliente."""
    if kind not in KINDS:
        fail(f"tipo invalido: {kind}")

    with admin_session() as session:
        correction = session.get(Correction, correction_id)
        if correction is None:
            fail(f"correcao {correction_id} nao encontrada")
        tenant_id = correction.tenant_id
        term = correction.term
        entity_type = correction.entity_type or "product"
        correction.kind = kind
        correction.status = "resolved"
        correction.chosen_entity_id = entity or None
        correction.note = note or correction.note
        correction.resolved_at = datetime.now(UTC)
        correction.resolved_by = "cli"

    if entity and term:
        with tenant_session(tenant_id) as session:
            alias_store.record_choice(session, tenant_id, entity_type, term, entity)
        ok(f"correcao {correction_id} resolvida; '{term}' agora aponta para {entity}")
        return

    ok(f"correcao {correction_id} resolvida como '{kind}'")
    if kind == "tool":
        warn("erro de tool: acrescente o caso ao eval set em src/orbi/evals/datasets")


@app.command("discard")
def discard(
    correction_id: Annotated[int, typer.Option("--id")],
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    """Descarta a correcao (expectativa incorreta do usuario, por exemplo)."""
    with admin_session() as session:
        correction = session.get(Correction, correction_id)
        if correction is None:
            fail(f"correcao {correction_id} nao encontrada")
        correction.status = "discarded"
        correction.kind = "expectation"
        correction.note = note or correction.note
        correction.resolved_at = datetime.now(UTC)
        correction.resolved_by = "cli"
    ok(f"correcao {correction_id} descartada")


@app.command("add")
def add(
    tenant: Annotated[str, typer.Option("--tenant", "-t")],
    trace: Annotated[str, typer.Option("--trace", help="trace_id do turno.")],
    term: Annotated[str, typer.Option("--term")] = "",
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    """Abre uma correcao manualmente, a partir de um relato por telefone."""
    tenant_id = resolve_tenant_id(tenant)
    with admin_session() as session:
        session.add(
            Correction(
                tenant_id=tenant_id,
                trace_id=trace,
                kind="unclassified",
                status="open",
                term=term or None,
                note=note or None,
            )
        )
    ok("correcao registrada")
