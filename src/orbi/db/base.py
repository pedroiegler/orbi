"""Base declarativa e engines.

O engine da aplicacao NUNCA deve ser usado diretamente por codigo de dominio:
a unica porta de entrada e `orbi.db.session.tenant_session`, que emite
`SET LOCAL app.tenant_id` na transacao (D-005, proibicao P6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, Engine, MetaData, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from orbi.core.settings import get_settings

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


_app_engine: Engine | None = None
_admin_engine: Engine | None = None


def _build_engine(url: str, **kwargs: Any) -> Engine:
    settings = get_settings()
    return create_engine(
        url,
        echo=settings.database_echo,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_size,
        future=True,
        **kwargs,
    )


def app_engine() -> Engine:
    """Engine do papel `orbi_app` — sem BYPASSRLS."""
    global _app_engine
    if _app_engine is None:
        _app_engine = _build_engine(get_settings().database_url)
    return _app_engine


def admin_engine() -> Engine:
    """Engine administrativo, usado apenas por migration e por comandos de
    operacao que criam ou desativam tenant. Nunca pelo Runtime."""
    global _admin_engine
    if _admin_engine is None:
        _admin_engine = _build_engine(get_settings().database_admin_url)
    return _admin_engine


def dispose_engines() -> None:
    global _app_engine, _admin_engine
    for engine in (_app_engine, _admin_engine):
        if engine is not None:
            engine.dispose()
    _app_engine = None
    _admin_engine = None


AppSessionFactory = sessionmaker[Session]


def ping(engine: Engine) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1")).scalar_one() == 1
