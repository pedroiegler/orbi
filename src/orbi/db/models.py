"""Modelo de dados (ORBI.md secao 16).

Um PostgreSQL, `tenant_id` em tudo que e do cliente, RLS forcado. As politicas
de RLS e o particionamento de `audit_logs` vivem nas migrations, porque
politica de seguranca precisa de historico versionado.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from orbi.core.settings import EMBEDDING_DIMENSIONS
from orbi.db.base import Base

# --- tipos reutilizados ---------------------------------------------------

UUIDPk = Mapped[uuid.UUID]


def _uuid_pk() -> Any:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _tenant_fk(**kwargs: Any) -> Any:
    return mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        **kwargs,
    )


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --- configuracao global (sem tenant) ------------------------------------


class Role(Base):
    """Papel visivel ao cliente. Preset sobre capabilities internas (D-018)."""

    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Capability(Base):
    __tablename__ = "capabilities"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class RoleCapability(Base):
    __tablename__ = "role_capabilities"

    role_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.code", ondelete="CASCADE"), primary_key=True
    )
    capability_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("capabilities.code", ondelete="CASCADE"), primary_key=True
    )


class Tool(Base):
    """Espelho da declaracao do Tool Registry. O codigo e a fonte; esta tabela
    existe para configuracao por tenant e para auditoria historica."""

    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    erp_operation: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = _now()


class RoleTool(Base):
    __tablename__ = "role_tools"

    role_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.code", ondelete="CASCADE"), primary_key=True
    )
    tool_name: Mapped[str] = mapped_column(
        String(64), ForeignKey("tools.name", ondelete="CASCADE"), primary_key=True
    )


# --- tenant ---------------------------------------------------------------


class Tenant(Base):
    __tablename__ = "tenants"

    id: UUIDPk = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="essencial")
    monthly_query_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=5_000)

    # Um numero por tenant: a identificacao fica deterministica (secao 6.2).
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="whatsapp")
    channel_address: Mapped[str | None] = mapped_column(String(32), unique=True)
    channel_phone_number_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    channel_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)

    debug_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint("status in ('active','suspended','inactive')", name="status_valid"),
    )


class TenantSettings(Base):
    """Limiares calibrados por tenant. Nunca fixos no codigo (secao 6.9)."""

    __tablename__ = "tenant_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    resolution_top1_threshold: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0.820")
    )
    resolution_gap_threshold: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0.050")
    )
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calibration_catalog_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calibration_silent_error_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    embedding_model_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ambiguity_options: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    pending_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    context_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    rate_limit_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="pt_BR")
    timezone: Mapped[str] = mapped_column(String(48), nullable=False, default="America/Sao_Paulo")
    updated_at: Mapped[datetime] = _now()


class TenantTool(Base):
    """Permissao efetiva = `tenant_tools` ∩ `role_tools` (secao 6.8)."""

    __tablename__ = "tenant_tools"

    tenant_id: Mapped[uuid.UUID] = _tenant_fk(primary_key=True)
    tool_name: Mapped[str] = mapped_column(
        String(64), ForeignKey("tools.name", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    """Preenchido automaticamente quando `capabilities()` do adapter nao atende."""
    updated_at: Mapped[datetime] = _now()


class User(Base):
    __tablename__ = "users"

    id: UUIDPk = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    role_code: Mapped[str] = mapped_column(String(32), nullable=False)
    """Codigo do papel **naquele cliente**: pode ser um dos tres padroes ou um
    papel proprio do tenant (D-040).

    Sem chave estrangeira de proposito: os papeis proprios vivem em
    `tenant_roles`, e uma FK para a tabela global recusaria justamente eles. A
    protecao continua existindo e e mais forte: papel desconhecido resolve para
    **nenhuma permissao**, e a Policy Layer nega tudo — falha fechada."""
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_location_id: Mapped[str | None] = mapped_column(String(64))
    erp_user_ref: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (Index("ix_users_tenant", "tenant_id"),)


class UserIdentity(Base):
    """Vinculo canal → usuario. Cadastro previo obrigatorio (secao 6.3)."""

    __tablename__ = "user_identities"

    id: UUIDPk = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="whatsapp")
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_code_hash: Mapped[str | None] = mapped_column(String(128))
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint("channel", "address", name="uq_user_identities_channel_address"),
        Index("ix_user_identities_tenant", "tenant_id"),
    )


# --- ERP ------------------------------------------------------------------


class TenantRole(Base):
    """Papel proprio de um cliente (D-040).

    Cada empresa tem processo diferente: "gerente que ve tudo menos custo",
    "comprador que so ve estoque". Sem isso, atender o processo de um cliente
    exigiria deploy — e o ORBI.md sempre prometeu que seria configuracao.

    Quando um cliente nao define nada, valem os tres papeis padrao do codigo.
    """

    __tablename__ = "tenant_roles"

    tenant_id: Mapped[uuid.UUID] = _tenant_fk(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class TenantRoleCapability(Base):
    """As permissoes de um papel proprio.

    A lista e a definicao inteira do papel: nao ha heranca do padrao. Papel
    customizado com a lista vazia nao consulta nada — que e o comportamento
    seguro para quem esqueceu de preencher.
    """

    __tablename__ = "tenant_role_capabilities"

    tenant_id: Mapped[uuid.UUID] = _tenant_fk(primary_key=True)
    role_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    capability_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("capabilities.code", ondelete="RESTRICT"), primary_key=True
    )


class ErpConnection(Base):
    __tablename__ = "erp_connections"

    id: UUIDPk = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    adapter: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="principal")
    credentials_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    """Fernet. Chave em ORBI_SECRET_KEY, fora do banco."""
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    """Somente o que nao e segredo (url publica, timezone do ERP, ids de deposito)."""
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capabilities_cache: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    capabilities_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_knowledge_model_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint("tenant_id", "label", name="uq_erp_connections_tenant_id_label"),
        Index("ix_erp_connections_tenant", "tenant_id"),
    )


class KnowledgeModel(Base):
    """Saida do Discovery: dado versionado, nunca codigo (secao 9)."""

    __tablename__ = "knowledge_models"

    id: UUIDPk = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    adapter: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    surface_map: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    deep_model: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    field_confidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    seed_questions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    abbreviations: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _now()
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tenant_id", "adapter", "version", name="uq_knowledge_models_version"),
        CheckConstraint("status in ('candidate','active','archived')", name="status_valid"),
        Index("ix_knowledge_models_tenant", "tenant_id"),
    )


# --- catalogo -------------------------------------------------------------


class CatalogItem(Base):
    """Indice de resolucao. O Orbi nao copia o ERP (secao 7)."""

    __tablename__ = "catalog"

    id: UUIDPk = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    erp_entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str | None] = mapped_column(String(64))
    barcode: Mapped[str | None] = mapped_column(String(32))
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    model_version: Mapped[str | None] = mapped_column(String(64))
    search_text: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('portuguese', coalesce(canonical_name, ''))", persisted=True),
    )
    name_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Content firewall: nome com padrao de instrucao fica fora do indice ate revisao."""
    flagged_reason: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    erp_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "entity_type", "erp_entity_id", name="uq_catalog_tenant_entity"
        ),
        CheckConstraint(
            "entity_type in ('product','customer','location')", name="entity_type_valid"
        ),
        Index("ix_catalog_tenant_type_active", "tenant_id", "entity_type", "active"),
        Index("ix_catalog_tenant_code", "tenant_id", "code"),
        # Indices da cascata de resolucao. Declarados aqui, e nao so na
        # migration, para que `alembic check` compare modelo e banco de verdade.
        Index(
            "ix_catalog_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
        Index(
            "ix_catalog_canonical_trgm",
            "canonical_name",
            postgresql_using="gin",
            postgresql_ops={"canonical_name": "gin_trgm_ops"},
        ),
        Index("ix_catalog_search_text", "search_text", postgresql_using="gin"),
    )


class CatalogAbbreviation(Base):
    """Dicionario de abreviacoes por tenant, alimentado pelo Discovery."""

    __tablename__ = "catalog_abbreviations"

    id: UUIDPk = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    short: Mapped[str] = mapped_column(String(32), nullable=False)
    expanded: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint("tenant_id", "short", name="uq_catalog_abbreviations_tenant_short"),
        Index("ix_catalog_abbreviations_tenant", "tenant_id"),
    )


class EntityAlias(Base):
    """Vocabulario aprendido — a defesa competitiva mais duravel (secao 6.10)."""

    __tablename__ = "entity_aliases"

    id: UUIDPk = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    alias: Mapped[str] = mapped_column(String(160), nullable=False)
    erp_entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    corrections: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "entity_type", "alias", name="uq_entity_aliases_tenant_type_alias"
        ),
        CheckConstraint("confidence in ('low','confirmed')", name="confidence_valid"),
        Index("ix_entity_aliases_tenant", "tenant_id"),
        Index(
            "ix_entity_aliases_alias_trgm",
            "alias",
            postgresql_using="gin",
            postgresql_ops={"alias": "gin_trgm_ops"},
        ),
    )


class CatalogSyncRun(Base):
    __tablename__ = "catalog_sync_runs"

    id: UUIDPk = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    cursor: Mapped[str | None] = mapped_column(String(64))
    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_deactivated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embeddings_computed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    change_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status in ('running','completed','failed','aborted')", name="status_valid"
        ),
        Index("ix_catalog_sync_runs_tenant", "tenant_id", "started_at"),
    )


# --- conversa -------------------------------------------------------------


class PendingResolution(Base):
    """Desambiguacao com TTL. O usuario responde '2' e o Runtime resolve sem
    nova chamada ao LLM (secao 6.10)."""

    __tablename__ = "pending_resolutions"

    id: UUIDPk = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="whatsapp")
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    term: Mapped[str] = mapped_column(String(160), nullable=False)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chosen_erp_entity_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        Index("ix_pending_resolutions_lookup", "tenant_id", "user_id", "channel", "expires_at"),
    )


class ConversationContext(Base):
    """Slots resolvidos, nao so mensagens: e o que faz 'e o preco dele?'
    funcionar de forma deterministica (D-012)."""

    __tablename__ = "conversation_contexts"

    tenant_id: Mapped[uuid.UUID] = _tenant_fk(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    channel: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_product_id: Mapped[str | None] = mapped_column(String(64))
    last_product_name: Mapped[str | None] = mapped_column(Text)
    last_customer_id: Mapped[str | None] = mapped_column(String(64))
    last_customer_name: Mapped[str | None] = mapped_column(Text)
    last_location_id: Mapped[str | None] = mapped_column(String(64))
    last_tool: Mapped[str | None] = mapped_column(String(64))
    recent_turns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = _now()


# --- auditoria e feedback -------------------------------------------------


class AuditLog(Base):
    """Append-only, particionada por mes, com encadeamento de hash (secao 11).

    UPDATE e DELETE sao revogados no banco pela migration.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    message_text: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(64))
    tool_args: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resolved_entity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    policy_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    policy_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    llm_provider: Mapped[str | None] = mapped_column(String(32))
    llm_model: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latencies_ms: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    erp_payload_hash: Mapped[str | None] = mapped_column(String(64))
    key_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    feedback: Mapped[str | None] = mapped_column(String(8))
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_audit_logs"),
        Index("ix_audit_logs_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_audit_logs_trace", "trace_id"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )


class Correction(Base):
    """Fila de correcoes: todo erro do usuario e um dado de treino (principio 16)."""

    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="unclassified")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    term: Mapped[str | None] = mapped_column(String(160))
    entity_type: Mapped[str | None] = mapped_column(String(16))
    tool_name: Mapped[str | None] = mapped_column(String(64))
    wrong_entity_id: Mapped[str | None] = mapped_column(String(64))
    chosen_entity_id: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(
            "kind in ('tool','resolution','erp_data','expectation','unclassified')",
            name="kind_valid",
        ),
        CheckConstraint("status in ('open','resolved','discarded')", name="status_valid"),
        Index("ix_corrections_tenant_status", "tenant_id", "status"),
    )


class RateLimitCounter(Base):
    """Rate limit no Postgres, sem Redis (D-017)."""

    __tablename__ = "rate_limit_counters"

    scope: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    window_seconds: Mapped[int] = mapped_column(Integer, primary_key=True)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = _now()


class EvalRun(Base):
    """Tabela de regressao historica dos evals (secao 14)."""

    __tablename__ = "eval_runs"

    id: UUIDPk = _uuid_pk()
    suite: Mapped[str] = mapped_column(String(32), nullable=False)
    layer: Mapped[str] = mapped_column(String(8), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _now()


TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "tenant_settings",
    "tenant_tools",
    "users",
    "user_identities",
    "erp_connections",
    "tenant_roles",
    "tenant_role_capabilities",
    "knowledge_models",
    "catalog",
    "catalog_abbreviations",
    "entity_aliases",
    "catalog_sync_runs",
    "pending_resolutions",
    "conversation_contexts",
    "audit_logs",
    "corrections",
)
"""Tabelas que carregam `tenant_id` e recebem RLS na migration."""
