"""Relatorios de operacao (ORBI.md secao 14).

O resumo diario e a lista de trabalho do dia: volume, ambiguidade, p50/p95,
custo e — o que mais importa — os cinco termos mais buscados sem resultado, que
viram alias ou correcao de nome canonico.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

TOP_UNRESOLVED = 5


def daily_report(session: Session, tenant_id: uuid.UUID, day: date | None = None) -> dict[str, Any]:
    """Consolida um dia de um tenant a partir da auditoria."""
    target = day or datetime.now(UTC).date()
    start = datetime.combine(target, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    window = {"tenant_id": str(tenant_id), "start": start, "end": end}

    totals = session.execute(
        text(
            """
            SELECT
                count(*) AS questions,
                count(*) FILTER (WHERE status = 'ambiguous') AS ambiguous,
                count(*) FILTER (WHERE status = 'not_found') AS not_found,
                count(*) FILTER (WHERE status = 'denied') AS denied,
                count(*) FILTER (WHERE status LIKE 'erp_%' OR status = 'timeout') AS errors,
                count(*) FILTER (WHERE feedback = 'down') AS thumbs_down,
                coalesce(sum(cost_usd), 0) AS cost_usd
            FROM audit_logs
            WHERE tenant_id = :tenant_id AND occurred_at >= :start AND occurred_at < :end
            """
        ),
        window,
    ).one()

    latencies = session.execute(
        text(
            """
            SELECT
                percentile_disc(0.5) WITHIN GROUP (ORDER BY total_ms) AS p50,
                percentile_disc(0.95) WITHIN GROUP (ORDER BY total_ms) AS p95
            FROM (
                SELECT (
                    SELECT coalesce(sum((value)::int), 0)
                    FROM jsonb_each_text(latencies_ms)
                ) AS total_ms
                FROM audit_logs
                WHERE tenant_id = :tenant_id AND occurred_at >= :start AND occurred_at < :end
                  AND latencies_ms <> '{}'::jsonb
            ) AS medidas
            """
        ),
        window,
    ).one()

    unresolved = session.execute(
        text(
            """
            SELECT coalesce(resolved_entity->>'term', message_text) AS term, count(*) AS hits
            FROM audit_logs
            WHERE tenant_id = :tenant_id AND occurred_at >= :start AND occurred_at < :end
              AND status = 'not_found'
            GROUP BY 1
            HAVING coalesce(resolved_entity->>'term', message_text) IS NOT NULL
            ORDER BY hits DESC
            LIMIT :limit
            """
        ),
        {**window, "limit": TOP_UNRESOLVED},
    ).all()

    tools = session.execute(
        text(
            """
            SELECT tool_name, count(*) AS hits
            FROM audit_logs
            WHERE tenant_id = :tenant_id AND occurred_at >= :start AND occurred_at < :end
              AND tool_name IS NOT NULL
            GROUP BY tool_name
            ORDER BY hits DESC
            """
        ),
        window,
    ).all()

    questions = int(totals.questions or 0)
    return {
        "day": target.strftime("%d/%m/%Y"),
        "questions": questions,
        "ambiguity_rate": (totals.ambiguous or 0) / questions if questions else 0.0,
        "not_found_rate": (totals.not_found or 0) / questions if questions else 0.0,
        "denied": int(totals.denied or 0),
        "errors": int(totals.errors or 0),
        "thumbs_down": int(totals.thumbs_down or 0),
        "cost_usd": float(totals.cost_usd or 0),
        "p50_ms": int(latencies.p50 or 0),
        "p95_ms": int(latencies.p95 or 0),
        "top_unresolved": [(row.term, int(row.hits)) for row in unresolved],
        "by_tool": {row.tool_name: int(row.hits) for row in tools},
    }


def inactivity_alert(session: Session, tenant_id: uuid.UUID, hours: int = 24) -> bool:
    """Tenant sem atividade ha 24h e um alerta, nao um silencio confortavel."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    count = session.execute(
        text(
            "SELECT count(*) FROM audit_logs WHERE tenant_id = :tenant_id AND occurred_at >= :since"
        ),
        {"tenant_id": str(tenant_id), "since": since},
    ).scalar_one()
    return int(count) == 0


def weekly_active_users(session: Session, tenant_id: uuid.UUID) -> tuple[int, int]:
    """(usuarios ativos na semana, usuarios cadastrados) — criterio de validacao."""
    since = datetime.now(UTC) - timedelta(days=7)
    active = session.execute(
        text(
            "SELECT count(DISTINCT user_id) FROM audit_logs "
            "WHERE tenant_id = :tenant_id AND occurred_at >= :since AND user_id IS NOT NULL"
        ),
        {"tenant_id": str(tenant_id), "since": since},
    ).scalar_one()
    registered = session.execute(
        text("SELECT count(*) FROM users WHERE tenant_id = :tenant_id AND active"),
        {"tenant_id": str(tenant_id)},
    ).scalar_one()
    return int(active), int(registered)
