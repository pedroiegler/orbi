"""Infraestrutura de teste do Orbi.

Duas coisas acontecem aqui e valem por metade da suite:

1. **Canary tenant.** O banco de teste tem um tenant com linhas envenenadas.
   Uma fixture global inspeciona o resultado de toda query de todo teste e falha
   o build se qualquer linha do canario aparecer. Nao e preciso lembrar de
   escrever o teste de vazamento: qualquer teste vira teste de vazamento
   (ORBI.md secao 11).

2. **Banco de teste isolado**, migrado com o mesmo Alembic da producao. Se a
   migration quebra, a suite inteira quebra — que e o comportamento desejado.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

TEST_DB_NAME = "orbi_test"
DEFAULT_ADMIN_DSN = "postgresql+psycopg://postgres:orbi-dev@localhost:5433"
DEFAULT_APP_DSN = "postgresql+psycopg://orbi_app:orbi-dev@localhost:5433"

CANARY_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-0000000ca9a1")
CANARY_SLUG = "canary"

os.environ.setdefault("ORBI_ENV", "test")
os.environ.setdefault("ORBI_SECRET_KEY", "hkFbLbNr4-tzB1SO0RRhIvFkNsjVeIz0e6MegGxxUAM=")
os.environ.setdefault("ORBI_LLM_PRIMARY", "rule_based")
os.environ.setdefault("ORBI_LLM_FALLBACK", "")
os.environ.setdefault("ORBI_EMBEDDING_PROVIDER", "hashing")
os.environ.setdefault(
    "ORBI_DATABASE_URL",
    os.environ.get("ORBI_TEST_DATABASE_URL", f"{DEFAULT_APP_DSN}/{TEST_DB_NAME}"),
)
os.environ.setdefault(
    "ORBI_DATABASE_ADMIN_URL",
    os.environ.get("ORBI_TEST_DATABASE_ADMIN_URL", f"{DEFAULT_ADMIN_DSN}/{TEST_DB_NAME}"),
)


# --- guarda do canario ----------------------------------------------------


class CanaryLeak(AssertionError):
    """Uma linha do tenant canario apareceu em um resultado de query."""


def _row_touches_canary(value: Any, depth: int = 0) -> bool:
    if depth > 3 or value is None:
        return False
    if isinstance(value, uuid.UUID):
        return value == CANARY_TENANT_ID
    if isinstance(value, str):
        return str(CANARY_TENANT_ID) in value
    if isinstance(value, (list, tuple, set)):
        return any(_row_touches_canary(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return any(_row_touches_canary(item, depth + 1) for item in value.values())
    for attribute in ("tenant_id", "id"):
        if hasattr(value, attribute) and _row_touches_canary(getattr(value, attribute), depth + 1):
            return True
    return False


def _scan_result(result: Any) -> Any:
    """Congela, inspeciona e devolve um resultado equivalente."""
    if not getattr(result, "returns_rows", False):
        return result
    try:
        frozen = result.freeze()
    except Exception:  # resultado nao congelavel: deixa passar sem inspecao
        return result
    for row in frozen():
        if _row_touches_canary(tuple(row)):
            raise CanaryLeak(
                "vazamento entre tenants: uma linha do tenant canario apareceu "
                "no resultado de uma query"
            )
    return frozen()


@pytest.fixture(autouse=True, scope="session")
def canary_guard() -> Iterator[None]:
    """Instrumenta `Session.execute` para toda a suite.

    A `Session` e a unica porta de acesso permitida (proibicao P6), entao
    instrumentar aqui cobre todo o codigo de dominio. O resultado e congelado,
    inspecionado e devolvido intacto para quem chamou.
    """
    from sqlalchemy.orm import Session

    original_session_execute = Session.execute

    def session_execute(self: Any, *args: Any, **kwargs: Any) -> Any:
        return _scan_result(original_session_execute(self, *args, **kwargs))

    Session.execute = session_execute  # type: ignore[method-assign]
    try:
        yield
    finally:
        Session.execute = original_session_execute  # type: ignore[method-assign]


# --- banco de teste -------------------------------------------------------


def _postgres_available() -> bool:
    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(f"{_admin_dsn_root()}/postgres", pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _admin_dsn_root() -> str:
    url = os.environ["ORBI_DATABASE_ADMIN_URL"]
    return url.rsplit("/", 1)[0]


def _database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


ALLOWED_TEST_DATABASES = (TEST_DB_NAME, "orbi_ci")
"""Nomes que a suite pode destruir. Qualquer outro aborta a execucao."""


def guard_target_database() -> None:
    """A suite recria o schema do zero: precisa provar que o alvo e de teste.

    Sem esta guarda, exportar `ORBI_DATABASE_ADMIN_URL` apontando para o banco de
    desenvolvimento — ou pior — e rodar `pytest` apaga o schema inteiro. Aconteceu
    uma vez durante a construcao; nao pode acontecer de novo.
    """
    for variable in ("ORBI_DATABASE_ADMIN_URL", "ORBI_DATABASE_URL"):
        name = _database_name(os.environ[variable])
        if name not in ALLOWED_TEST_DATABASES:
            raise pytest.UsageError(
                f"{variable} aponta para o banco '{name}', que nao e de teste. "
                f"A suite recria o schema do zero e so aceita: "
                f"{', '.join(ALLOWED_TEST_DATABASES)}. "
                "Rode `pytest` sem exportar essas variaveis."
            )


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    """Cria e migra o banco de teste. Pula a suite quando nao ha Postgres."""
    guard_target_database()
    if not _postgres_available():
        pytest.skip("PostgreSQL indisponivel: suba com docker/docker-compose.yml")

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    root = create_engine(f"{_admin_dsn_root()}/postgres", isolation_level="AUTOCOMMIT")
    with root.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB_NAME}
        ).scalar_one_or_none()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    root.dispose()

    admin = create_engine(os.environ["ORBI_DATABASE_ADMIN_URL"], isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orbi_app') THEN
                        CREATE ROLE orbi_app LOGIN PASSWORD 'orbi-dev' NOBYPASSRLS;
                    END IF;
                END
                $$
                """
            )
        )
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO postgres"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO orbi_app"))
    admin.dispose()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", os.environ["ORBI_DATABASE_ADMIN_URL"])
    command.upgrade(config, "head")

    from orbi.core.settings import reset_settings_cache
    from orbi.db.base import dispose_engines

    reset_settings_cache()
    dispose_engines()

    _seed_global_config()
    _create_canary_tenant()

    yield

    dispose_engines()


def _seed_global_config() -> None:
    from orbi.db.seed import sync_global_config
    from orbi.db.session import admin_session

    with admin_session() as session:
        sync_global_config(session)


def _create_canary_tenant() -> None:
    """Tenant com linhas envenenadas. Nenhuma query de nenhum teste pode ve-lo."""
    from datetime import UTC, datetime, timedelta

    from orbi.db.models import (
        CatalogItem,
        ConversationContext,
        EntityAlias,
        Tenant,
        TenantSettings,
        TenantTool,
        User,
        UserIdentity,
    )
    from orbi.db.session import admin_session

    with admin_session() as session:
        if session.get(Tenant, CANARY_TENANT_ID) is not None:
            return
        session.add(
            Tenant(
                id=CANARY_TENANT_ID,
                slug=CANARY_SLUG,
                name="Canary LTDA",
                status="active",
                channel_address="+5500000000000",
                channel_phone_number_id="canary-phone-id",
            )
        )
        session.add(TenantSettings(tenant_id=CANARY_TENANT_ID))
        session.flush()
        user = User(
            tenant_id=CANARY_TENANT_ID,
            name="Canario Vendedor",
            role_code="sales_rep",
        )
        session.add(user)
        session.flush()
        session.add(
            UserIdentity(
                tenant_id=CANARY_TENANT_ID,
                user_id=user.id,
                channel="whatsapp",
                address="+5500000000001",
                verified_at=datetime.now(UTC),
            )
        )
        session.add(
            CatalogItem(
                tenant_id=CANARY_TENANT_ID,
                erp_entity_id="canary-1",
                entity_type="product",
                name="TUBO CANARIO SECRETO 100MM",
                canonical_name="tubo canario secreto 100 mm",
                name_hash="canary",
                code="CANARY1",
            )
        )
        session.add(
            EntityAlias(
                tenant_id=CANARY_TENANT_ID,
                entity_type="product",
                alias="tubo canario",
                erp_entity_id="canary-1",
                confidence="confirmed",
            )
        )
        session.add(
            ConversationContext(
                tenant_id=CANARY_TENANT_ID,
                user_id=user.id,
                channel="whatsapp",
                expires_at=datetime.now(UTC) + timedelta(days=3650),
                recent_turns=[],
            )
        )
        session.add(TenantTool(tenant_id=CANARY_TENANT_ID, tool_name="check_stock"))


@pytest.fixture
def tenant_id(database: None) -> Iterator[uuid.UUID]:
    """Tenant limpo por teste, com settings e tools habilitadas."""
    from orbi.db.models import Tenant, TenantSettings, TenantTool
    from orbi.db.session import admin_session
    from orbi.tools.registry import tool_names

    new_id = uuid.uuid4()
    slug = f"t{new_id.hex[:10]}"
    with admin_session() as session:
        session.add(
            Tenant(
                id=new_id,
                slug=slug,
                name=f"Tenant {slug}",
                status="active",
                channel_address=f"+55{new_id.int % 10**11:011d}",
                channel_phone_number_id=f"phone-{slug}",
            )
        )
        session.add(TenantSettings(tenant_id=new_id))
        session.flush()
        for name in tool_names():
            session.add(TenantTool(tenant_id=new_id, tool_name=name, enabled=True))
    yield new_id


@pytest.fixture
def other_tenant_id(database: None) -> Iterator[uuid.UUID]:
    from orbi.db.models import Tenant, TenantSettings
    from orbi.db.session import admin_session

    new_id = uuid.uuid4()
    slug = f"o{new_id.hex[:10]}"
    with admin_session() as session:
        session.add(
            Tenant(
                id=new_id,
                slug=slug,
                name=f"Outro {slug}",
                status="active",
                channel_address=f"+55{new_id.int % 10**11:011d}",
                channel_phone_number_id=f"phone-{slug}",
            )
        )
        session.add(TenantSettings(tenant_id=new_id))
    yield new_id
