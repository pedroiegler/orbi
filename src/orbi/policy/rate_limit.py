"""Rate limit no Postgres, sem Redis (D-017).

Duas janelas por usuario (minuto e dia) e um teto mensal por tenant, que e o
controle de margem do plano. Numero desconhecido tambem passa por aqui, com
janela agressiva: e o que impede que uma varredura vire custo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

MINUTE = 60
DAY = 86_400

UNKNOWN_SENDER_LIMIT = 3
UNKNOWN_SENDER_WINDOW = 600


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    scope: str
    hits: int
    limit: int
    window_seconds: int

    @property
    def retry_after_seconds(self) -> int:
        return self.window_seconds


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=UTC)


def hit(
    session: Session,
    scope: str,
    limit: int,
    window_seconds: int,
    *,
    now: datetime | None = None,
) -> RateLimitResult:
    """Incrementa e decide, em uma unica ida ao banco.

    O upsert devolve a contagem ja atualizada; duas requisicoes simultaneas do
    mesmo usuario nao se atropelam.
    """
    moment = now or datetime.now(UTC)
    window_started_at = _window_start(moment, window_seconds)

    hits = session.execute(
        text(
            """
            INSERT INTO rate_limit_counters (scope, window_started_at, window_seconds, hits)
            VALUES (:scope, :window_started_at, :window_seconds, 1)
            ON CONFLICT (scope, window_started_at, window_seconds)
            DO UPDATE SET hits = rate_limit_counters.hits + 1, updated_at = now()
            RETURNING hits
            """
        ),
        {
            "scope": scope,
            "window_started_at": window_started_at,
            "window_seconds": window_seconds,
        },
    ).scalar_one()

    return RateLimitResult(
        allowed=int(hits) <= limit,
        scope=scope,
        hits=int(hits),
        limit=limit,
        window_seconds=window_seconds,
    )


def purge_expired(session: Session, older_than_seconds: int = DAY * 2) -> int:
    """Limpeza barata: contadores velhos nao servem para nada."""
    cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    result = session.execute(
        text("DELETE FROM rate_limit_counters WHERE window_started_at < :cutoff"),
        {"cutoff": cutoff},
    )
    return int(result.rowcount or 0)


def user_scope(tenant_id: str, user_id: str) -> str:
    return f"user:{tenant_id}:{user_id}"


def tenant_scope(tenant_id: str) -> str:
    return f"tenant:{tenant_id}"


def unknown_sender_scope(channel: str, address: str) -> str:
    return f"unknown:{channel}:{address}"
