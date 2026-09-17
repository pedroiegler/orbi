"""Rate limit no Postgres (D-017): sem Redis, com janela deslizante por bloco."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from orbi.db.session import tenant_session
from orbi.policy.rate_limit import (
    MINUTE,
    hit,
    purge_expired,
    unknown_sender_scope,
    user_scope,
)

pytestmark = pytest.mark.integration


def test_counter_allows_until_the_limit_then_denies(tenant_id: uuid.UUID) -> None:
    scope = user_scope(str(tenant_id), str(uuid.uuid4()))
    now = datetime.now(UTC)

    with tenant_session(tenant_id) as session:
        results = [hit(session, scope, limit=3, window_seconds=MINUTE, now=now) for _ in range(4)]

    assert [result.allowed for result in results] == [True, True, True, False]
    assert results[-1].hits == 4


def test_a_new_window_starts_a_new_count(tenant_id: uuid.UUID) -> None:
    scope = user_scope(str(tenant_id), str(uuid.uuid4()))
    now = datetime.now(UTC)

    with tenant_session(tenant_id) as session:
        for _ in range(3):
            hit(session, scope, limit=3, window_seconds=MINUTE, now=now)
        later = hit(session, scope, limit=3, window_seconds=MINUTE, now=now + timedelta(seconds=61))

    assert later.allowed
    assert later.hits == 1


def test_scopes_do_not_interfere(tenant_id: uuid.UUID) -> None:
    first = user_scope(str(tenant_id), "a")
    second = user_scope(str(tenant_id), "b")
    now = datetime.now(UTC)

    with tenant_session(tenant_id) as session:
        for _ in range(3):
            hit(session, first, limit=3, window_seconds=MINUTE, now=now)
        other = hit(session, second, limit=3, window_seconds=MINUTE, now=now)

    assert other.allowed


def test_unknown_sender_has_its_own_aggressive_scope(tenant_id: uuid.UUID) -> None:
    scope = unknown_sender_scope("whatsapp", "+5543999990000")
    now = datetime.now(UTC)

    with tenant_session(tenant_id) as session:
        results = [hit(session, scope, limit=3, window_seconds=600, now=now) for _ in range(5)]

    assert results[-1].allowed is False


def test_purge_removes_old_windows(tenant_id: uuid.UUID) -> None:
    scope = user_scope(str(tenant_id), str(uuid.uuid4()))
    old = datetime.now(UTC) - timedelta(days=5)

    with tenant_session(tenant_id) as session:
        hit(session, scope, limit=10, window_seconds=MINUTE, now=old)
        removed = purge_expired(session)

    assert removed >= 1
