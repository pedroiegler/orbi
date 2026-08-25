"""`orbi catalog ...` — sync, status, abreviacoes e alias."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

import typer
from sqlalchemy import func, select

from orbi.catalog.sync import ABORT_CHANGE_RATIO, CatalogSynchronizer
from orbi.cli.common import console, ok, resolve_tenant_id, table, warn
from orbi.db.models import CatalogAbbreviation, CatalogItem, CatalogSyncRun, EntityAlias
from orbi.db.session import tenant_session
from orbi.erp import connection as erp_connection
from orbi.observability.ops_channel import build_notifier
from orbi.resolution.embeddings import build_embedder

app = typer.Typer(help="Indice de resolucao do cliente.", no_args_is_help=True)

TenantOption = Annotated[str, typer.Option("--tenant", "-t")]


@app.command("sync")
def sync(
    tenant: TenantOption,
    mode: Annotated[str, typer.Option("--mode", help="full | incremental")] = "full",
    since_hours: Annotated[
        int, typer.Option("--since-hours", help="Janela do incremental.")
    ] = 4,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "Aplica mesmo mudando mais de 30% do catalogo. Use so quando "
                "voce sabe o motivo da mudanca (renomeacao em massa, por exemplo)."
            ),
        ),
    ] = False,
) -> None:
    """Sincroniza o catalogo. Idempotente e retomavel."""
    tenant_id = resolve_tenant_id(tenant)
    notifier = build_notifier()
    embedder = build_embedder()
    since = datetime.now(UTC) - timedelta(hours=since_hours) if mode == "incremental" else None

    with tenant_session(tenant_id) as session:
        tenant_erp = erp_connection.build_for_tenant(session, tenant_id)
        items = tenant_erp.adapter.iter_catalog(since)
        synchronizer = CatalogSynchronizer(
            session,
            tenant_id,
            embedder,
            abort_ratio=Decimal("1.00") if force else ABORT_CHANGE_RATIO,
        )
        report = synchronizer.run(
            items,
            mode=mode,
            on_alert=lambda message: notifier.alert("sync abortado", f"{tenant}: {message}"),
        )

    if report.aborted:
        warn(f"sync abortado: {report.error}")
        console.print(
            "Se a mudanca e esperada (renomeacao em massa, troca de catalogo), "
            "repita com `--force`."
        )
        raise typer.Exit(2)
    ok(f"sync {mode} concluido — {report.summary()}")
    if report.flagged:
        warn(f"{report.flagged} nomes sinalizados pelo content firewall (fora do indice)")


@app.command("status")
def status(tenant: TenantOption) -> None:
    """Mostra o estado do indice e das ultimas sincronizacoes."""
    tenant_id = resolve_tenant_id(tenant)
    with tenant_session(tenant_id) as session:
        counts = session.execute(
            select(
                CatalogItem.entity_type,
                func.count().label("total"),
                func.count().filter(CatalogItem.active.is_(True)).label("ativos"),
                func.count().filter(CatalogItem.flagged.is_(True)).label("sinalizados"),
                func.count().filter(CatalogItem.embedding.is_(None)).label("sem_vetor"),
            ).group_by(CatalogItem.entity_type)
        ).all()
        runs = session.scalars(
            select(CatalogSyncRun).order_by(CatalogSyncRun.started_at.desc()).limit(5)
        ).all()
        aliases_count = session.scalar(
            select(func.count()).select_from(EntityAlias).where(EntityAlias.tenant_id == tenant_id)
        )

    view = table(f"Catalogo de {tenant}", ["tipo", "total", "ativos", "sinalizados", "sem vetor"])
    for row in counts:
        view.add_row(
            row.entity_type,
            str(row.total),
            str(row.ativos),
            str(row.sinalizados),
            str(row.sem_vetor),
        )
    console.print(view)
    console.print(f"Vocabulario aprendido: {aliases_count} aliases")

    if runs:
        history = table("Ultimas sincronizacoes", ["quando", "modo", "status", "resumo"])
        for run in runs:
            history.add_row(
                run.started_at.strftime("%d/%m %H:%M"),
                run.mode,
                run.status,
                f"{run.items_seen} lidos · {run.items_upserted} gravados · "
                f"{run.items_deactivated} desativados",
            )
        console.print(history)


@app.command("abbreviations")
def abbreviations(
    tenant: TenantOption,
    add: Annotated[
        str, typer.Option("--add", help="Par no formato abreviacao=expansao, ex: tb=tubo")
    ] = "",
) -> None:
    """Dicionario de abreviacoes do cliente, usado no nome canonico."""
    tenant_id = resolve_tenant_id(tenant)

    if add:
        if "=" not in add:
            warn("use o formato abreviacao=expansao")
            raise typer.Exit(1)
        short, expanded = (part.strip().lower() for part in add.split("=", 1))
        with tenant_session(tenant_id) as session:
            existing = session.scalars(
                select(CatalogAbbreviation).where(
                    CatalogAbbreviation.tenant_id == tenant_id,
                    CatalogAbbreviation.short == short,
                )
            ).first()
            if existing is None:
                session.add(
                    CatalogAbbreviation(
                        tenant_id=tenant_id, short=short, expanded=expanded, source="manual"
                    )
                )
            else:
                existing.expanded = expanded
        ok(f"'{short}' passa a significar '{expanded}' — rode `orbi catalog sync` para aplicar")
        return

    with tenant_session(tenant_id) as session:
        rows = session.scalars(
            select(CatalogAbbreviation)
            .where(CatalogAbbreviation.tenant_id == tenant_id)
            .order_by(CatalogAbbreviation.short)
        ).all()

    if not rows:
        warn("nenhuma abreviacao propria: apenas o dicionario base esta em uso")
        return
    view = table(f"Abreviacoes de {tenant}", ["abreviacao", "expansao", "origem"])
    for row in rows:
        view.add_row(row.short, row.expanded, row.source)
    console.print(view)


@app.command("aliases")
def aliases(
    tenant: TenantOption,
    limit: Annotated[int, typer.Option("--limit")] = 30,
) -> None:
    """Vocabulario aprendido — o ativo que mais compoe com o tempo."""
    tenant_id = resolve_tenant_id(tenant)
    with tenant_session(tenant_id) as session:
        rows = session.scalars(
            select(EntityAlias)
            .where(EntityAlias.tenant_id == tenant_id)
            .order_by(EntityAlias.hits.desc())
            .limit(limit)
        ).all()

    if not rows:
        warn("nenhum alias aprendido ainda")
        return
    view = table(f"Aliases de {tenant}", ["termo", "entidade", "confianca", "usos", "correcoes"])
    for row in rows:
        view.add_row(
            row.alias, row.erp_entity_id, row.confidence, str(row.hits), str(row.corrections)
        )
    console.print(view)
