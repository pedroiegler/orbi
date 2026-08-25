"""Sessao de banco — a unica porta de entrada (ORBI.md secao 11).

Isolamento entre tenants nao pode depender de o desenvolvedor lembrar de
filtrar por `tenant_id`. Toda transacao do Runtime nasce aqui, e aqui e emitido
`SET LOCAL app.tenant_id`, que e o que as politicas de RLS leem.

`admin_session` existe para operacao (criar tenant, rodar migration) e usa o
papel administrativo. Ela nunca deve ser usada no caminho da requisicao.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from orbi.db.base import admin_engine, app_engine

TENANT_SETTING = "app.tenant_id"


@contextmanager
def tenant_session(tenant_id: UUID | str) -> Iterator[Session]:
    """Abre uma transacao ja escopada ao tenant.

    O `SET LOCAL` vale ate o fim da transacao — nao vaza para a proxima conexao
    devolvida ao pool.
    """
    resolved = str(tenant_id)
    # Falha cedo se alguem passar algo que nao e um tenant_id.
    UUID(resolved)

    session = Session(bind=app_engine(), future=True, expire_on_commit=False)
    try:
        # `SET LOCAL` nao aceita parametro; `set_config(..., is_local => true)` e
        # o equivalente parametrizavel e evita concatenar string em SQL.
        session.execute(
            text(f"SELECT set_config('{TENANT_SETTING}', :tenant_id, true)"),
            {"tenant_id": resolved},
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def admin_session() -> Iterator[Session]:
    """Sessao administrativa: criacao de tenant, seed e manutencao.

    Nunca no caminho da requisicao.
    """
    session = Session(bind=admin_engine(), future=True, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_tenant(session: Session) -> str | None:
    """Le de volta o tenant da transacao. Usado por testes de isolamento."""
    value = session.execute(
        text(f"SELECT current_setting('{TENANT_SETTING}', true)")
    ).scalar_one_or_none()
    return value or None
