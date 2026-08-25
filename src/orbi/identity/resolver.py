"""Identificacao: tenant, usuario e papel (ORBI.md secao 6.3).

```
tenant  ← numero de destino
user    ← numero de origem (via user_identities)
role    ← vinculo do usuario
```

**Cadastro previo e obrigatorio.** Numero desconhecido recebe recusa generica,
entra em rate limit agressivo e gera alerta — e nunca fica sabendo se existe
cadastro naquele numero.

Reciclagem de numero e tratada por re-verificacao com codigo; inatividade de 90
dias exige re-verificacao.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orbi.core.errors import UnknownSenderError
from orbi.db.models import Tenant, TenantSettings, User, UserIdentity
from orbi.db.session import tenant_session

INACTIVITY_DAYS = 90
VERIFICATION_CODE_TTL_MINUTES = 15


@dataclass(frozen=True)
class TenantIdentity:
    id: uuid.UUID
    slug: str
    name: str
    status: str
    plan: str
    monthly_query_cap: int
    debug_mode: bool

    @property
    def active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class UserIdentityInfo:
    user_id: uuid.UUID
    name: str
    role: str
    active: bool
    needs_reverification: bool
    default_location_id: str | None
    identity_id: uuid.UUID


def find_tenant(session: Session, channel: str, address: str) -> TenantIdentity | None:
    """Descobre o tenant pelo numero de destino.

    Usa a funcao `SECURITY DEFINER` criada na migration: a RLS esconde `tenants`
    de quem ainda nao declarou o tenant, e essa e a unica excecao — estreita,
    versionada e revisavel.
    """
    row = session.execute(
        text(
            "SELECT id, slug, name, status, plan, monthly_query_cap, debug_mode "
            "FROM orbi_tenant_by_channel(:channel, :address)"
        ),
        {"channel": channel, "address": address},
    ).first()
    if row is None:
        return None
    return TenantIdentity(
        id=row.id,
        slug=row.slug,
        name=row.name,
        status=row.status,
        plan=row.plan,
        monthly_query_cap=row.monthly_query_cap,
        debug_mode=row.debug_mode,
    )


def find_user(
    session: Session, channel: str, address: str, *, now: datetime | None = None
) -> UserIdentityInfo:
    """Encontra o usuario pelo numero de origem, dentro do tenant da sessao."""
    moment = now or datetime.now(UTC)
    row = session.execute(
        select(UserIdentity, User)
        .join(User, User.id == UserIdentity.user_id)
        .where(
            UserIdentity.channel == channel,
            UserIdentity.address == address,
        )
    ).first()
    if row is None:
        raise UnknownSenderError(f"numero sem cadastro no canal {channel}")

    identity, user = row
    needs_reverification = identity.verified_at is None or not identity.active
    if identity.last_activity_at is not None:
        idle = moment - identity.last_activity_at
        if idle > timedelta(days=INACTIVITY_DAYS):
            # Numero reciclado e o risco real: 90 dias parado exige confirmar.
            needs_reverification = True

    return UserIdentityInfo(
        user_id=user.id,
        name=user.name,
        role=user.role_code,
        active=user.active,
        needs_reverification=needs_reverification,
        default_location_id=user.default_location_id,
        identity_id=identity.id,
    )


def touch_activity(
    session: Session, identity_id: uuid.UUID, *, now: datetime | None = None
) -> None:
    moment = now or datetime.now(UTC)
    identity = session.get(UserIdentity, identity_id)
    if identity is not None:
        identity.last_activity_at = moment
        user = session.get(User, identity.user_id)
        if user is not None:
            user.last_seen_at = moment


def load_settings(session: Session, tenant_id: uuid.UUID) -> TenantSettings:
    settings = session.get(TenantSettings, tenant_id)
    if settings is None:
        settings = TenantSettings(tenant_id=tenant_id)
        session.add(settings)
        session.flush()
    return settings


# --- verificacao de numero ----------------------------------------------


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def start_verification(
    session: Session, identity_id: uuid.UUID, *, now: datetime | None = None
) -> str:
    """Gera o codigo de verificacao e devolve o valor em claro, uma unica vez."""
    moment = now or datetime.now(UTC)
    identity = session.get(UserIdentity, identity_id)
    if identity is None:
        raise UnknownSenderError("identidade inexistente")

    code = f"{secrets.randbelow(900_000) + 100_000}"
    identity.verification_code_hash = hash_code(code)
    identity.verification_expires_at = moment + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)
    identity.verified_at = None
    session.flush()
    return code


def confirm_verification(
    session: Session, identity_id: uuid.UUID, code: str, *, now: datetime | None = None
) -> bool:
    moment = now or datetime.now(UTC)
    identity = session.get(UserIdentity, identity_id)
    if identity is None or identity.verification_code_hash is None:
        return False
    if identity.verification_expires_at is None or identity.verification_expires_at < moment:
        return False
    if not secrets.compare_digest(identity.verification_code_hash, hash_code(code)):
        return False

    identity.verified_at = moment
    identity.verification_code_hash = None
    identity.verification_expires_at = None
    identity.active = True
    identity.last_activity_at = moment
    session.flush()
    return True


def tenant_of(channel: str, address: str) -> TenantIdentity | None:
    """Atalho para quem ainda nao tem sessao aberta (webhook e CLI).

    Usa um tenant nulo apenas para abrir a transacao: a funcao de lookup nao
    depende de `app.tenant_id`, e nenhuma outra leitura acontece nessa sessao.
    """
    with tenant_session(uuid.UUID(int=0)) as session:
        return find_tenant(session, channel, address)


def tenant_row(session: Session, tenant_id: uuid.UUID) -> Tenant | None:
    return session.get(Tenant, tenant_id)
