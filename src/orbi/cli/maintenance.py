"""`orbi maintenance ...` — o que o cron chama todo dia.

Particao do mes, expurgo por retencao, resumo diario e verificacao da corrente
de auditoria.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

import typer
from sqlalchemy import select, text

from orbi.audit.logger import verify_chain
from orbi.cli.common import console, fail, ok, resolve_tenant_id, table, warn
from orbi.db.models import Tenant
from orbi.db.session import admin_session, tenant_session
from orbi.observability.ops_channel import build_notifier, format_daily_summary
from orbi.observability.reports import daily_report, inactivity_alert, weekly_active_users
from orbi.policy.rate_limit import purge_expired as purge_rate_limits
from orbi.runtime.context import purge_expired as purge_contexts
from orbi.runtime.pending import purge_expired as purge_pending

app = typer.Typer(help="Rotinas de operacao (cron).", no_args_is_help=True)

AUDIT_RETENTION_MONTHS = 12
CONTEXT_RETENTION_DAYS = 30


@app.command("partitions")
def partitions(
    months_ahead: Annotated[int, typer.Option("--months-ahead")] = 2,
) -> None:
    """Garante as particoes de `audit_logs` dos proximos meses.

    Linha de auditoria nunca pode falhar por falta de particao.
    """
    created: list[str] = []
    with admin_session() as session:
        for offset in range(months_ahead + 1):
            target = (date.today().replace(day=1) + timedelta(days=32 * offset)).replace(day=1)
            name = session.execute(
                text("SELECT orbi_ensure_audit_partition(:target)"), {"target": target}
            ).scalar_one()
            created.append(str(name))
    ok("particoes garantidas: " + ", ".join(created))


@app.command("purge")
def purge(
    dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True,
) -> None:
    """Aplica a retencao: contexto 30 dias, auditoria 12 meses (DROP PARTITION)."""
    with admin_session() as session:
        contexts = purge_contexts(session) if not dry_run else 0
        pending = purge_pending(session) if not dry_run else 0
        counters = purge_rate_limits(session) if not dry_run else 0

        months_back = timedelta(days=31 * AUDIT_RETENTION_MONTHS)
        cutoff = (date.today().replace(day=1) - months_back).replace(day=1)
        stale = session.execute(
            text(
                """
                SELECT c.relname AS name
                FROM pg_class c
                JOIN pg_inherits i ON i.inhrelid = c.oid
                JOIN pg_class parent ON parent.oid = i.inhparent
                WHERE parent.relname = 'audit_logs' AND c.relname ~ '^audit_logs_[0-9]{6}$'
                ORDER BY c.relname
                """
            )
        ).all()
        expired = [
            row.name
            for row in stale
            if row.name.removeprefix("audit_logs_") < cutoff.strftime("%Y%m")
        ]
        if not dry_run:
            for name in expired:
                session.execute(text(f"DROP TABLE IF EXISTS {name}"))

    if dry_run:
        warn(f"simulacao: {len(expired)} particoes de auditoria acima da retencao")
        console.print("Use `--apply` para executar.")
        return
    ok(
        f"expurgo aplicado — {contexts} contextos, {pending} pendencias, "
        f"{counters} contadores, {len(expired)} particoes"
    )


@app.command("daily-summary")
def daily_summary(
    tenant: Annotated[str, typer.Option("--tenant", "-t", help="Vazio = todos.")] = "",
    send: Annotated[bool, typer.Option("--send/--print", help="Envia ao canal de ops.")] = False,
    day: Annotated[str, typer.Option("--day", help="AAAA-MM-DD. Padrao: hoje.")] = "",
) -> None:
    """Resumo diario por cliente — a interface de operacao do MVP."""
    target = datetime.strptime(day, "%Y-%m-%d").date() if day else datetime.now(UTC).date()
    notifier = build_notifier()

    with admin_session() as session:
        query = select(Tenant).where(Tenant.status == "active")
        tenants = session.scalars(query).all()
        selected = [t for t in tenants if not tenant or t.slug == tenant]

    if not selected:
        fail("nenhum tenant ativo encontrado")

    for tenant_row in selected:
        with tenant_session(tenant_row.id) as session:
            report = daily_report(session, tenant_row.id, target)
            report["by_tenant"] = {tenant_row.slug: report["questions"]}
            idle = inactivity_alert(session, tenant_row.id)
            active, registered = weekly_active_users(session, tenant_row.id)

        message = format_daily_summary(report)
        message += f"\nUsuarios ativos na semana: {active}/{registered}"
        if send:
            notifier.notify(message)
        else:
            console.print(message)
        if idle:
            notifier.alert("tenant sem atividade ha 24h", tenant_row.slug)


@app.command("verify-audit")
def verify_audit(
    tenant: Annotated[str, typer.Option("--tenant", "-t", help="Vazio = todos.")] = "",
) -> None:
    """Percorre a corrente de hash e diz se alguem mexeu."""
    with admin_session() as session:
        tenants = session.scalars(select(Tenant)).all()
        selected = [t for t in tenants if not tenant or t.slug == tenant]

    view = table("Integridade da auditoria", ["cliente", "linhas", "situacao"])
    broken = False
    for tenant_row in selected:
        with tenant_session(tenant_row.id) as session:
            report = verify_chain(session, str(tenant_row.id))
        broken = broken or not report.valid
        view.add_row(
            tenant_row.slug,
            str(report.rows),
            "integra" if report.valid else f"QUEBRADA em {report.broken_at}",
        )
    console.print(view)
    if broken:
        build_notifier().alert(
            "corrente de auditoria quebrada", "rode `orbi maintenance verify-audit`"
        )
        raise typer.Exit(2)


@app.command("shadow-evals")
def shadow_evals(
    tenant: Annotated[str, typer.Option("--tenant", "-t", help="Vazio = todos.")] = "",
    days: Annotated[int, typer.Option("--days", help="Janela de perguntas reais.")] = 1,
    alert_below: Annotated[
        float, typer.Option("--alert-below", help="Concordancia minima antes de alertar.")
    ] = 0.9,
) -> None:
    """Reexecuta 5% das perguntas reais contra o prompt atual.

    A regressao aparece antes do usuario. Nenhuma chamada ao ERP e nenhuma PII
    sai daqui — o shadow compara escolha de tool e argumentos.
    """
    from orbi.evals.shadow import run_shadow

    notifier = build_notifier()
    with admin_session() as session:
        tenants = session.scalars(select(Tenant).where(Tenant.status == "active")).all()
        selected = [(t.id, t.slug) for t in tenants if not tenant or t.slug == tenant]

    if not selected:
        fail("nenhum tenant ativo encontrado")

    view = table("Shadow evals", ["cliente", "amostra", "tool igual", "argumentos iguais"])
    for tenant_id, slug in selected:
        result = run_shadow(tenant_id, slug, days=days)
        view.add_row(
            slug,
            str(result.sampled),
            f"{result.tool_agreement:.0%}",
            f"{result.args_agreement:.0%}",
        )
        for divergence in result.divergences[:5]:
            warn(f"{slug}: {divergence}")
        if result.sampled and result.tool_agreement < alert_below:
            notifier.alert(
                "regressao no shadow eval",
                f"{slug}: concordancia de tool em {result.tool_agreement:.0%}",
            )
    console.print(view)


@app.command("recalibrate")
def recalibrate(
    tenant: Annotated[str, typer.Option("--tenant", "-t")],
    force: Annotated[
        bool, typer.Option("--force", help="Recalibra mesmo sem necessidade.")
    ] = False,
) -> None:
    """Recalibra os limiares quando o catalogo muda mais de 20%."""
    from orbi.resolution import calibration
    from orbi.resolution.embeddings import build_embedder

    tenant_id = resolve_tenant_id(tenant)
    embedder = build_embedder()
    with tenant_session(tenant_id) as session:
        if not force and not calibration.needs_recalibration(session, tenant_id):
            ok("limiares ainda validos: catalogo mudou menos de 20%")
            return
        result = calibration.calibrate(session, tenant_id, embedder)
        if result is None:
            fail("catalogo vazio: rode `orbi catalog sync` antes")
        calibration.apply(session, tenant_id, result, embedder)
    ok(f"recalibrado — {result.summary()}")
