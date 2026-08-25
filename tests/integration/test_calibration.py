"""Calibracao por tenant: o criterio assimetrico em teste."""

from __future__ import annotations

import uuid

import pytest

from orbi.catalog.sync import CatalogSynchronizer
from orbi.db.session import tenant_session
from orbi.erp.adapters.memory import MemoryAdapter
from orbi.resolution import calibration
from orbi.resolution.embeddings import HashingEmbedder

pytestmark = pytest.mark.integration

EMBEDDER = HashingEmbedder()


def _seed(tenant: uuid.UUID) -> None:
    with tenant_session(tenant) as session:
        CatalogSynchronizer(session, tenant, EMBEDDER).run(list(MemoryAdapter().iter_catalog()))


def test_synthetic_set_is_built_from_the_real_catalog(tenant_id: uuid.UUID) -> None:
    _seed(tenant_id)
    with tenant_session(tenant_id) as session:
        cases = calibration.build_synthetic_set(session, tenant_id)

    assert cases
    variations = {case.variation for case in cases}
    assert {"exato", "truncado", "com_erro", "sem_medida"} <= variations
    assert all(case.expected_id for case in cases)


def test_synthetic_set_is_deterministic(tenant_id: uuid.UUID) -> None:
    """Mesmo catalogo, mesmo conjunto: calibracao precisa ser reprodutivel."""
    _seed(tenant_id)
    with tenant_session(tenant_id) as session:
        first = calibration.build_synthetic_set(session, tenant_id)
        second = calibration.build_synthetic_set(session, tenant_id)
    assert [case.term for case in first] == [case.term for case in second]


def test_calibration_respects_the_silent_error_ceiling(tenant_id: uuid.UUID) -> None:
    _seed(tenant_id)
    with tenant_session(tenant_id) as session:
        result = calibration.calibrate(session, tenant_id, EMBEDDER)

    assert result is not None
    assert result.silent_error_rate <= calibration.MAX_SILENT_ERROR_RATE
    assert 0.5 <= result.top1 <= 1.0
    assert result.accuracy > 0


def test_calibration_is_written_to_tenant_settings(tenant_id: uuid.UUID) -> None:
    _seed(tenant_id)
    with tenant_session(tenant_id) as session:
        result = calibration.calibrate(session, tenant_id, EMBEDDER)
        assert result is not None
        settings = calibration.apply(session, tenant_id, result, EMBEDDER)

    assert settings.calibrated_at is not None
    assert float(settings.resolution_top1_threshold) == result.top1
    assert settings.embedding_model_version == EMBEDDER.model_version
    assert settings.calibration_catalog_size > 0


def test_calibrated_thresholds_are_used_by_the_resolver(tenant_id: uuid.UUID) -> None:
    _seed(tenant_id)
    with tenant_session(tenant_id) as session:
        result = calibration.calibrate(session, tenant_id, EMBEDDER)
        assert result is not None
        calibration.apply(session, tenant_id, result, EMBEDDER)
        thresholds = calibration.current_thresholds(session, tenant_id)

    assert thresholds.top1 == result.top1
    assert thresholds.gap == result.gap


def test_recalibration_is_requested_when_the_catalog_moves(tenant_id: uuid.UUID) -> None:
    _seed(tenant_id)
    with tenant_session(tenant_id) as session:
        assert calibration.needs_recalibration(session, tenant_id)  # nunca calibrado
        result = calibration.calibrate(session, tenant_id, EMBEDDER)
        assert result is not None
        calibration.apply(session, tenant_id, result, EMBEDDER)
        assert not calibration.needs_recalibration(session, tenant_id)

        settings = session.get(
            __import__("orbi.db.models", fromlist=["TenantSettings"]).TenantSettings,
            tenant_id,
        )
        assert settings is not None
        settings.calibration_catalog_size = 100  # catalogo encolheu muito

    with tenant_session(tenant_id) as session:
        assert calibration.needs_recalibration(session, tenant_id)


def test_calibration_without_catalog_returns_nothing(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        assert calibration.calibrate(session, tenant_id, EMBEDDER) is None
