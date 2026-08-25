"""Runtime — o caminho da pergunta (ORBI.md secoes 5 e 6).

```
CHANNEL → IDENTITY → PROMPT → LLM → POLICY → RESOLUTION → ERP
        → FIELD POLICY → RENDER → AUDIT + TRACING
```

Single-shot: uma tool por turno, sem loop e sem agente. O LLM interpreta; este
modulo autoriza, executa e monta a resposta.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from orbi.audit import logger as audit
from orbi.core.deadline import Deadline, DeadlineExceeded
from orbi.core.errors import ConfigurationError, LLMError, UnknownSenderError
from orbi.core.resilience import CircuitOpen
from orbi.core.settings import Settings, get_settings
from orbi.core.trace import short_code, trace_context
from orbi.db.session import tenant_session
from orbi.erp import connection as erp_connection
from orbi.erp.errors import ErpAuthError, ErpError, ErpNotFound, ErpTimeout
from orbi.erp.gateway import ErpGateway
from orbi.identity import resolver as identity
from orbi.llm.port import ToolCallEnvelope
from orbi.llm.prompt import PromptBuilder, PromptContext
from orbi.llm.router import LLMRouter
from orbi.observability.metrics import TurnMetrics
from orbi.observability.tracing import TracePort, build_tracer
from orbi.policy import field_policy, rate_limit
from orbi.policy.decision import PolicyDecision, ReasonCode
from orbi.policy.engine import PolicySubject, evaluate
from orbi.render.renderer import RenderContext, ResultRenderer, stock_view
from orbi.resolution import aliases
from orbi.resolution.embeddings import EmbeddingPort, build_embedder
from orbi.resolution.resolver import Candidate, EntityResolver, Resolution, Thresholds
from orbi.runtime import context as conversation
from orbi.runtime import pending
from orbi.tools.args import EntityTerm, InvalidEntityTerm
from orbi.tools.registry import ToolSpec, get_tool, tools_for_role

SCOPE_LABEL = "estoque, preço, títulos em aberto e último pedido"


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    from_address: str
    to_address: str
    text: str
    message_id: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class TurnOutcome:
    """O que sai do turno: texto pronto e tudo que a observabilidade precisa."""

    trace_id: str
    status: str
    text: str
    tenant_slug: str | None = None
    tenant_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    role: str | None = None
    tool_name: str | None = None
    reason_code: str | None = None
    entity: dict[str, Any] | None = None
    options: tuple[dict[str, Any], ...] = ()
    latencies_ms: dict[str, int] = field(default_factory=dict)
    used_llm: bool = True
    provider: str | None = None
    cost_usd: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


OpsAlert = Callable[[str, str], None]
"""(assunto, detalhe) — alerta imediato no canal de operacao."""


class OrbiRuntime:
    """Orquestra um turno inteiro."""

    def __init__(
        self,
        llm: LLMRouter,
        *,
        renderer: ResultRenderer | None = None,
        embedder: EmbeddingPort | None = None,
        settings: Settings | None = None,
        prompt_builder: PromptBuilder | None = None,
        on_alert: OpsAlert | None = None,
        metrics: TurnMetrics | None = None,
        tracer: TracePort | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._llm = llm
        self._settings = settings or get_settings()
        self._renderer = renderer or ResultRenderer()
        self._embedder = embedder or build_embedder(self._settings)
        self._prompts = prompt_builder or PromptBuilder()
        self._alert = on_alert or (lambda subject, detail: None)
        self._metrics = metrics or TurnMetrics()
        self._tracer = tracer or build_tracer(self._settings)
        self._clock = clock

    @property
    def metrics(self) -> TurnMetrics:
        """Latencia por etapa, taxa de ambiguidade e custo acumulados."""
        return self._metrics

    # --- entrada ---------------------------------------------------------

    def handle(self, inbound: InboundMessage) -> TurnOutcome:
        with trace_context() as trace_id:
            deadline = Deadline(total_ms=self._settings.deadline_total_ms)
            try:
                outcome = self._handle(inbound, trace_id, deadline)
            except DeadlineExceeded as exc:
                outcome = TurnOutcome(
                    trace_id=trace_id,
                    status="timeout",
                    text=self._renderer.render("erp_error.txt.j2", {"kind": "timeout"}),
                    latencies_ms=dict(deadline.stage_latencies_ms),
                )
                self._alert("turno estourou o orcamento", str(exc))
            except ConfigurationError as exc:
                outcome = TurnOutcome(
                    trace_id=trace_id,
                    status="misconfigured",
                    text=self._renderer.render("erp_error.txt.j2", {"kind": "unavailable"}),
                    latencies_ms=dict(deadline.stage_latencies_ms),
                )
                self._alert("configuracao invalida", str(exc))
            self._metrics.record(outcome)
            # Mesmo trace_id da auditoria: um relato vira investigacao em segundos.
            self._tracer.record_turn(outcome, inbound.text, outcome.role or "desconhecido")
            return outcome

    # --- fluxo -----------------------------------------------------------

    def _handle(
        self, inbound: InboundMessage, trace_id: str, deadline: Deadline
    ) -> TurnOutcome:
        tenant = identity.tenant_of(inbound.channel, inbound.to_address)
        if tenant is None:
            # Numero de destino desconhecido: nem sabemos de quem e a caixa.
            self._alert(
                "mensagem para numero nao cadastrado",
                f"canal {inbound.channel} destino {inbound.to_address}",
            )
            return TurnOutcome(
                trace_id=trace_id,
                status="unknown_tenant",
                text=self._renderer.render("unknown_sender.txt.j2", {}),
            )

        with tenant_session(tenant.id) as session:
            try:
                user = identity.find_user(session, inbound.channel, inbound.from_address)
            except UnknownSenderError:
                return self._reject_unknown_sender(session, tenant, inbound, trace_id)

            settings_row = identity.load_settings(session, tenant.id)
            identity.touch_activity(session, user.identity_id)
            turn_context = conversation.load(
                session, tenant.id, user.user_id, inbound.channel
            )
            enabled = erp_connection.enabled_tools(session, tenant.id)
            tenant_erp = erp_connection.build_for_tenant(session, tenant.id)
            thresholds = Thresholds.from_settings_row(settings_row)

            # Resposta a uma desambiguacao aberta: resolve sem LLM (secao 6.10).
            choice = pending.resolve_choice(
                session,
                tenant_id=tenant.id,
                user_id=user.user_id,
                channel=inbound.channel,
                message=inbound.text,
            )

        render_context = RenderContext(
            channel=inbound.channel,
            locale=settings_row.locale,
            timezone=settings_row.timezone,
            debug_code=short_code(trace_id) if tenant.debug_mode else None,
            default_location_id=user.default_location_id,
        )

        if choice is not None:
            return self._run_after_choice(
                inbound, trace_id, deadline, tenant, user, choice, tenant_erp, render_context
            )

        envelope = self._ask_llm(inbound, tenant, user, turn_context, deadline, enabled)
        if envelope is None or not envelope.has_tool_call:
            return self._out_of_scope(
                inbound, trace_id, tenant, user, envelope, render_context, deadline
            )

        spec = get_tool(envelope.tool_name or "")
        subject = PolicySubject(
            tenant_id=str(tenant.id),
            tenant_status=tenant.status,
            user_id=str(user.user_id),
            user_active=user.active,
            role=user.role,
            needs_reverification=user.needs_reverification,
            enabled_tools=enabled,
            erp_supported_tools=frozenset(tenant_erp.capabilities.supported_tools),
            rate_limit_per_minute=settings_row.rate_limit_per_minute,
            rate_limit_per_day=settings_row.rate_limit_per_day,
            monthly_query_cap=tenant.monthly_query_cap,
        )

        args, prefilled = self._fill_from_slots(dict(envelope.tool_args), spec, turn_context)

        with tenant_session(tenant.id) as session:
            decision = evaluate(
                subject,
                spec.name,
                args,
                lambda subj: self._check_rate_limits(session, subj),
            )

        if decision.denied:
            return self._denied(
                inbound, trace_id, tenant, user, spec, args, decision, envelope,
                render_context, deadline,
            )

        return self._execute(
            inbound=inbound,
            trace_id=trace_id,
            deadline=deadline,
            tenant=tenant,
            user=user,
            spec=spec,
            decision=decision,
            envelope=envelope,
            tenant_erp=tenant_erp,
            thresholds=thresholds,
            prefilled=prefilled,
            render_context=render_context,
            turn_context=turn_context,
            settings_row=settings_row,
        )

    # --- etapas ----------------------------------------------------------

    def _reject_unknown_sender(
        self,
        session: Session,
        tenant: identity.TenantIdentity,
        inbound: InboundMessage,
        trace_id: str,
    ) -> TurnOutcome:
        """Recusa generica: nunca revela se o numero existe no sistema."""
        result = rate_limit.hit(
            session,
            rate_limit.unknown_sender_scope(inbound.channel, inbound.from_address),
            limit=rate_limit.UNKNOWN_SENDER_LIMIT,
            window_seconds=rate_limit.UNKNOWN_SENDER_WINDOW,
        )
        audit.write(
            session,
            audit.AuditRecord(
                tenant_id=str(tenant.id),
                trace_id=trace_id,
                channel=inbound.channel,
                status="unknown_sender",
                policy_decision="DENY",
                reason_code="UNKNOWN_SENDER",
                policy_version_hash="",
                message_text=None,
            ),
        )
        self._alert(
            "numero desconhecido tentando acesso",
            f"tenant {tenant.slug} · canal {inbound.channel} · tentativa {result.hits}",
        )
        text = "" if not result.allowed else self._renderer.render("unknown_sender.txt.j2", {})
        return TurnOutcome(
            trace_id=trace_id,
            status="unknown_sender",
            text=text,
            tenant_slug=tenant.slug,
            tenant_id=tenant.id,
            used_llm=False,
        )

    def _ask_llm(
        self,
        inbound: InboundMessage,
        tenant: identity.TenantIdentity,
        user: identity.UserIdentityInfo,
        turn_context: conversation.TurnContext,
        deadline: Deadline,
        enabled: frozenset[str],
    ) -> ToolCallEnvelope | None:
        """Uma chamada, uma re-tentativa de esclarecimento. Nunca um loop."""
        allowed = tuple(spec for spec in tools_for_role(user.role) if spec.name in enabled)
        if not allowed:
            return None

        prompt_context = PromptContext(
            role=user.role,
            now=self._clock(),
            recent_turns=list(turn_context.recent_turns),
            slots=turn_context.slots(),
            location_hint=user.default_location_id,
        )
        request = self._prompts.build(
            tenant_name=tenant.name,
            question=inbound.text,
            tools=allowed,
            context=prompt_context,
            timeout_ms=self._settings.llm_timeout_ms,
        )

        try:
            envelope = self._llm.complete(request, deadline)
        except LLMError as exc:
            self._alert("provedores de LLM indisponiveis", str(exc))
            return None

        if envelope.has_tool_call:
            return envelope

        if deadline.remaining_ms() < 800:
            return envelope

        retry = self._prompts.build(
            tenant_name=tenant.name,
            question=inbound.text,
            tools=allowed,
            context=prompt_context,
            timeout_ms=self._settings.llm_timeout_ms,
            clarification_hint=(
                "Escolha uma das ferramentas disponiveis para esta pergunta ou "
                "responda FORA_DE_ESCOPO."
            ),
        )
        try:
            return self._llm.complete(retry, deadline)
        except LLMError:
            return envelope

    def _execute(
        self,
        *,
        inbound: InboundMessage,
        trace_id: str,
        deadline: Deadline,
        tenant: identity.TenantIdentity,
        user: identity.UserIdentityInfo,
        spec: ToolSpec,
        decision: PolicyDecision,
        envelope: ToolCallEnvelope,
        tenant_erp: erp_connection.TenantErp,
        thresholds: Thresholds,
        render_context: RenderContext,
        turn_context: conversation.TurnContext,
        settings_row: Any,
        prefilled: dict[str, Candidate],
    ) -> TurnOutcome:
        args = decision.validated_args
        resolved: dict[str, Candidate] = dict(prefilled)

        with tenant_session(tenant.id) as session:
            resolver = EntityResolver(session, str(tenant.id), self._embedder, thresholds)
            for role_name, term in _terms_of(spec, args):
                if role_name in resolved:
                    continue  # veio do slot: ja esta resolvido (D-012)
                entity_type = _entity_type_for(role_name)
                resolution = resolver.resolve(term, entity_type, deadline=deadline)

                if resolution.status == "AMBIGUOUS":
                    return self._ask_which_one(
                        session, inbound, trace_id, tenant, user, spec, args,
                        resolution, envelope, decision, render_context, deadline,
                    )
                if resolution.status == "NOT_FOUND":
                    if role_name == "location_term":
                        continue  # deposito nao encontrado nao invalida a consulta
                    return self._not_found(
                        session, inbound, trace_id, tenant, user, spec, args,
                        resolution, envelope, decision, render_context, deadline,
                    )
                assert resolution.entity is not None
                resolved[role_name] = resolution.entity
                if resolution.stage == "alias":
                    aliases.record_successful_use(session, tenant.id, entity_type, term)

        gateway = tenant_erp.gateway(
            tenant.id,
            on_circuit_open=lambda tenant_id, adapter, operation: self._alert(
                "circuito aberto",
                f"tenant {tenant.slug} · adapter {adapter} · operacao {operation}",
            ),
        )

        try:
            payload = self._call_erp(gateway, spec, args, resolved, deadline)
        except (ErpError, CircuitOpen) as exc:
            return self._erp_failure(
                inbound, trace_id, tenant, user, spec, args, exc, envelope, decision,
                render_context, deadline,
            )

        view, entity, key_fields = self._present(
            spec, payload, user, resolved, render_context
        )
        text = self._renderer.render(spec.template, view, render_context)

        with tenant_session(tenant.id) as session:
            self._remember(
                session, turn_context, spec, resolved, inbound.text, text, settings_row
            )
            audit.write(
                session,
                self._audit_record(
                    tenant, user, trace_id, inbound, spec, _args_dict(args), decision, envelope,
                    status="ok", deadline=deadline, entity=entity,
                    erp_payload_hash=audit.hash_payload(_dump(payload)),
                    key_fields=key_fields,
                ),
            )

        return TurnOutcome(
            trace_id=trace_id,
            status="ok",
            text=text,
            tenant_slug=tenant.slug,
            tenant_id=tenant.id,
            user_id=user.user_id,
            role=user.role,
            tool_name=spec.name,
            entity=entity,
            latencies_ms=dict(deadline.stage_latencies_ms),
            provider=envelope.provider or None,
            cost_usd=envelope.cost_usd,
        )

    def _run_after_choice(
        self,
        inbound: InboundMessage,
        trace_id: str,
        deadline: Deadline,
        tenant: identity.TenantIdentity,
        user: identity.UserIdentityInfo,
        choice: pending.PendingChoice,
        tenant_erp: erp_connection.TenantErp,
        render_context: RenderContext,
    ) -> TurnOutcome:
        """O usuario respondeu "2": executa direto, sem passar pelo LLM."""
        spec = get_tool(choice.tool_name)
        args = spec.validate_args(choice.tool_args)
        entity = Candidate(
            erp_entity_id=choice.erp_entity_id,
            name=choice.name,
            canonical_name=choice.name.lower(),
            code=None,
            score=1.0,
            stage="alias",
        )
        resolved = {_primary_role(spec): entity}

        with tenant_session(tenant.id) as session:
            # A escolha do usuario vira vocabulario do tenant (D-013).
            aliases.record_choice(
                session,
                tenant.id,
                choice.entity_type,
                choice.term,
                choice.erp_entity_id,
                user.user_id,
            )
            turn_context = conversation.load(session, tenant.id, user.user_id, inbound.channel)
            settings_row = identity.load_settings(session, tenant.id)

        gateway = tenant_erp.gateway(tenant.id)
        envelope = ToolCallEnvelope(finish_reason="tool_call", tool_name=spec.name)

        try:
            payload = self._call_erp(gateway, spec, args, resolved, deadline)
        except (ErpError, CircuitOpen) as exc:
            return self._erp_failure(
                inbound, trace_id, tenant, user, spec, choice.tool_args, exc, envelope,
                None, render_context, deadline,
            )

        view, entity_dict, key_fields = self._present(
            spec, payload, user, resolved, render_context
        )
        text = self._renderer.render(spec.template, view, render_context)

        with tenant_session(tenant.id) as session:
            self._remember(
                session, turn_context, spec, resolved, inbound.text, text, settings_row
            )
            audit.write(
                session,
                audit.AuditRecord(
                    tenant_id=str(tenant.id),
                    trace_id=trace_id,
                    channel=inbound.channel,
                    user_id=str(user.user_id),
                    status="ok",
                    policy_decision="ALLOW",
                    reason_code=None,
                    policy_version_hash="",
                    message_text=inbound.text,
                    tool_name=spec.name,
                    tool_args=choice.tool_args,
                    resolved_entity=entity_dict,
                    latencies_ms=dict(deadline.stage_latencies_ms),
                    erp_payload_hash=audit.hash_payload(_dump(payload)),
                    key_fields=key_fields,
                ),
            )

        return TurnOutcome(
            trace_id=trace_id,
            status="ok",
            text=text,
            tenant_slug=tenant.slug,
            tenant_id=tenant.id,
            user_id=user.user_id,
            role=user.role,
            tool_name=spec.name,
            entity=entity_dict,
            latencies_ms=dict(deadline.stage_latencies_ms),
            used_llm=False,
        )

    def _call_erp(
        self,
        gateway: ErpGateway,
        spec: ToolSpec,
        args: Any,
        resolved: dict[str, Candidate],
        deadline: Deadline,
    ) -> Any:
        if spec.name == "check_stock":
            location = resolved.get("location_term")
            return gateway.get_stock(
                resolved["product_term"].erp_entity_id,
                location.erp_entity_id if location else None,
                deadline=deadline,
            )
        if spec.name == "check_price":
            customer = resolved.get("customer_term")
            quantity = getattr(args, "quantity", None)
            return gateway.get_price(
                resolved["product_term"].erp_entity_id,
                customer.erp_entity_id if customer else None,
                Decimal(str(quantity)) if quantity is not None else None,
                deadline=deadline,
            )
        if spec.name == "list_open_invoices":
            return gateway.list_open_invoices(
                resolved["customer_term"].erp_entity_id, deadline=deadline
            )
        if spec.name == "get_last_order":
            return gateway.get_last_order(
                resolved["customer_term"].erp_entity_id, deadline=deadline
            )
        raise ConfigurationError(f"tool sem execucao definida: {spec.name}")

    def _present(
        self,
        spec: ToolSpec,
        payload: Any,
        user: identity.UserIdentityInfo,
        resolved: dict[str, Candidate],
        render_context: RenderContext,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Field Policy → dados do template → recibo da entidade."""
        filtered = field_policy.apply(user.role, spec.name, payload)
        primary = resolved[_primary_role(spec)]
        entity = {
            "erp_entity_id": primary.erp_entity_id,
            "name": primary.name,
            "code": primary.code,
            "stage": primary.stage,
        }

        if spec.name == "check_stock":
            view = stock_view(filtered, render_context.default_location_id)
            view["code"] = primary.code
            key_fields = {
                "quantity": str(view["quantity"]),
                "basis": view.get("basis"),
            }
            return view, entity, key_fields

        if spec.name == "check_price":
            view = dict(filtered)
            view["code"] = primary.code
            key_fields = {
                "unit_price": str(filtered.get("unit_price")),
                "quantity": str(filtered.get("quantity")),
            }
            return view, entity, key_fields

        if spec.name == "list_open_invoices":
            invoices = list(filtered)
            total_open = sum(
                (Decimal(str(invoice["open_amount"])) for invoice in invoices), Decimal("0")
            )
            view = {
                "customer_name": primary.name,
                "invoices": invoices,
                "total_open": total_open,
                "overdue_count": sum(
                    1 for invoice in invoices if int(invoice.get("days_overdue", 0)) > 0
                ),
            }
            key_fields = {"count": str(len(invoices)), "total_open": str(total_open)}
            return view, entity, key_fields

        view = {"order": filtered, "customer_name": primary.name}
        key_fields = {
            "order": str(filtered.get("number")) if filtered else None,
            "total": str(filtered.get("total")) if filtered else None,
        }
        return view, entity, key_fields

    # --- respostas que nao consultam o ERP -------------------------------

    def _ask_which_one(
        self,
        session: Session,
        inbound: InboundMessage,
        trace_id: str,
        tenant: identity.TenantIdentity,
        user: identity.UserIdentityInfo,
        spec: ToolSpec,
        args: Any,
        resolution: Resolution,
        envelope: ToolCallEnvelope,
        decision: PolicyDecision,
        render_context: RenderContext,
        deadline: Deadline,
    ) -> TurnOutcome:
        """Na duvida, pergunta. Nunca chuta a entidade (principio 4)."""
        pending.create(
            session,
            tenant_id=tenant.id,
            user_id=user.user_id,
            channel=inbound.channel,
            trace_id=trace_id,
            tool_name=spec.name,
            tool_args=_args_dict(args),
            entity_type=resolution.entity_type,
            term=resolution.term,
            options=resolution.options,
        )
        options = [
            {"name": option.name, "code": option.code, "erp_entity_id": option.erp_entity_id}
            for option in resolution.options
        ]
        text = self._renderer.render(
            "ambiguous.txt.j2", {"term": resolution.term, "options": options}, render_context
        )
        audit.write(
            session,
            self._audit_record(
                tenant, user, trace_id, inbound, spec, _args_dict(args), decision, envelope,
                status="ambiguous", deadline=deadline,
                entity={"options": [option["erp_entity_id"] for option in options]},
            ),
        )
        return TurnOutcome(
            trace_id=trace_id,
            status="ambiguous",
            text=text,
            tenant_slug=tenant.slug,
            tenant_id=tenant.id,
            user_id=user.user_id,
            role=user.role,
            tool_name=spec.name,
            options=tuple(options),
            latencies_ms=dict(deadline.stage_latencies_ms),
            provider=envelope.provider or None,
        )

    def _not_found(
        self,
        session: Session,
        inbound: InboundMessage,
        trace_id: str,
        tenant: identity.TenantIdentity,
        user: identity.UserIdentityInfo,
        spec: ToolSpec,
        args: Any,
        resolution: Resolution,
        envelope: ToolCallEnvelope,
        decision: PolicyDecision,
        render_context: RenderContext,
        deadline: Deadline,
    ) -> TurnOutcome:
        text = self._renderer.render(
            "not_found.txt.j2",
            {
                "term": resolution.term,
                "entity_label": _entity_label(resolution.entity_type),
                "suggestions": [
                    {"name": option.name, "code": option.code}
                    for option in resolution.options[:2]
                ],
            },
            render_context,
        )
        audit.write(
            session,
            self._audit_record(
                tenant, user, trace_id, inbound, spec, _args_dict(args), decision, envelope,
                status="not_found", deadline=deadline, entity={"term": resolution.term},
            ),
        )
        return TurnOutcome(
            trace_id=trace_id,
            status="not_found",
            text=text,
            tenant_slug=tenant.slug,
            tenant_id=tenant.id,
            user_id=user.user_id,
            role=user.role,
            tool_name=spec.name,
            latencies_ms=dict(deadline.stage_latencies_ms),
            provider=envelope.provider or None,
        )

    def _denied(
        self,
        inbound: InboundMessage,
        trace_id: str,
        tenant: identity.TenantIdentity,
        user: identity.UserIdentityInfo,
        spec: ToolSpec,
        args: dict[str, Any],
        decision: PolicyDecision,
        envelope: ToolCallEnvelope,
        render_context: RenderContext,
        deadline: Deadline,
    ) -> TurnOutcome:
        reason = decision.reason_code.value if decision.reason_code else "DENY"
        text = self._renderer.render("denied.txt.j2", {"reason_code": reason}, render_context)
        with tenant_session(tenant.id) as session:
            audit.write(
                session,
                self._audit_record(
                    tenant, user, trace_id, inbound, spec, args, decision, envelope,
                    status="denied", deadline=deadline,
                ),
            )
        return TurnOutcome(
            trace_id=trace_id,
            status="denied",
            text=text,
            tenant_slug=tenant.slug,
            tenant_id=tenant.id,
            user_id=user.user_id,
            role=user.role,
            tool_name=spec.name,
            reason_code=reason,
            latencies_ms=dict(deadline.stage_latencies_ms),
            provider=envelope.provider or None,
        )

    def _out_of_scope(
        self,
        inbound: InboundMessage,
        trace_id: str,
        tenant: identity.TenantIdentity,
        user: identity.UserIdentityInfo,
        envelope: ToolCallEnvelope | None,
        render_context: RenderContext,
        deadline: Deadline,
    ) -> TurnOutcome:
        """Mensagem deterministica de fora de escopo. Nunca um segundo loop."""
        examples = [
            example
            for spec in tools_for_role(user.role)
            for example in spec.examples[:1]
        ]
        text = self._renderer.render(
            "out_of_scope.txt.j2",
            {"scope_label": SCOPE_LABEL, "examples": examples},
            render_context,
        )
        with tenant_session(tenant.id) as session:
            audit.write(
                session,
                audit.AuditRecord(
                    tenant_id=str(tenant.id),
                    trace_id=trace_id,
                    channel=inbound.channel,
                    user_id=str(user.user_id),
                    status="out_of_scope",
                    policy_decision="ALLOW",
                    policy_version_hash="",
                    message_text=inbound.text,
                    prompt_version=None,
                    llm_provider=envelope.provider if envelope else None,
                    llm_model=envelope.model if envelope else None,
                    tokens_in=envelope.tokens_in if envelope else None,
                    tokens_out=envelope.tokens_out if envelope else None,
                    cost_usd=envelope.cost_usd if envelope else None,
                    latencies_ms=dict(deadline.stage_latencies_ms),
                ),
            )
        return TurnOutcome(
            trace_id=trace_id,
            status="out_of_scope",
            text=text,
            tenant_slug=tenant.slug,
            tenant_id=tenant.id,
            user_id=user.user_id,
            role=user.role,
            latencies_ms=dict(deadline.stage_latencies_ms),
            provider=envelope.provider if envelope else None,
        )

    def _erp_failure(
        self,
        inbound: InboundMessage,
        trace_id: str,
        tenant: identity.TenantIdentity,
        user: identity.UserIdentityInfo,
        spec: ToolSpec,
        args: Any,
        error: Exception,
        envelope: ToolCallEnvelope,
        decision: PolicyDecision | None,
        render_context: RenderContext,
        deadline: Deadline,
    ) -> TurnOutcome:
        """ERP fora do ar responde a falha. Nunca estoque de cache (D-009)."""
        kind = _error_kind(error)
        text = self._renderer.render("erp_error.txt.j2", {"kind": kind}, render_context)
        self._alert(
            "falha ao consultar o ERP",
            f"tenant {tenant.slug} · tool {spec.name} · {type(error).__name__}",
        )
        with tenant_session(tenant.id) as session:
            audit.write(
                session,
                self._audit_record(
                    tenant, user, trace_id, inbound, spec, _args_dict(args), decision, envelope,
                    status=f"erp_{kind}", deadline=deadline,
                ),
            )
        return TurnOutcome(
            trace_id=trace_id,
            status=f"erp_{kind}",
            text=text,
            tenant_slug=tenant.slug,
            tenant_id=tenant.id,
            user_id=user.user_id,
            role=user.role,
            tool_name=spec.name,
            latencies_ms=dict(deadline.stage_latencies_ms),
            provider=envelope.provider or None,
        )

    # --- apoio -----------------------------------------------------------

    def _check_rate_limits(self, session: Session, subject: PolicySubject) -> ReasonCode | None:
        """Tres janelas: minuto e dia por usuario, mes por cliente.

        As duas primeiras contem abuso; a terceira e controle de margem — um
        cliente entusiasmado sozinho dobra a conta de LLM (ORBI.md secao 18).
        """
        minute = rate_limit.hit(
            session,
            rate_limit.user_scope(subject.tenant_id, subject.user_id),
            limit=subject.rate_limit_per_minute,
            window_seconds=rate_limit.MINUTE,
        )
        if not minute.allowed:
            return ReasonCode.RATE_LIMIT_MINUTE

        day = rate_limit.hit(
            session,
            rate_limit.user_scope(subject.tenant_id, subject.user_id) + ":day",
            limit=subject.rate_limit_per_day,
            window_seconds=rate_limit.DAY,
        )
        if not day.allowed:
            return ReasonCode.RATE_LIMIT_DAY

        month = rate_limit.hit(
            session,
            rate_limit.tenant_scope(subject.tenant_id),
            limit=subject.monthly_query_cap,
            window_seconds=rate_limit.MONTH,
        )
        if not month.allowed:
            return ReasonCode.PLAN_QUOTA_EXCEEDED
        return None

    def _fill_from_slots(
        self, args: dict[str, Any], spec: ToolSpec, turn_context: conversation.TurnContext
    ) -> tuple[dict[str, Any], dict[str, Candidate]]:
        """"e o preco dele?" — quem resolve e o slot, nao o modelo (D-012).

        O slot guarda o `erp_entity_id` ja resolvido, entao o termo nem volta
        pela cascata: nao ha o que adivinhar duas vezes.
        """
        prefilled: dict[str, Candidate] = {}
        for key in ("product_term", "customer_term"):
            if key not in spec.args_model.model_fields:
                continue
            value = args.get(key)
            slot = turn_context.slot_for(_entity_type_for(key))
            if slot is None:
                continue
            if value is not None and not conversation.is_anaphora(str(value)):
                continue

            entity_id, name = slot
            prefilled[key] = Candidate(
                erp_entity_id=entity_id,
                name=name,
                canonical_name=name.lower(),
                code=None,
                score=1.0,
                stage="alias",
            )
            try:
                args[key] = str(EntityTerm(name))
            except InvalidEntityTerm:
                # Nome do ERP com cara de codigo: o argumento segue como veio e a
                # resolucao e ignorada de qualquer forma.
                args[key] = str(value) if value else name[:60]
        return args, prefilled

    def _remember(
        self,
        session: Session,
        turn_context: conversation.TurnContext,
        spec: ToolSpec,
        resolved: dict[str, Candidate],
        question: str,
        answer: str,
        settings_row: Any,
    ) -> None:
        product = resolved.get("product_term")
        customer = resolved.get("customer_term")
        location = resolved.get("location_term")
        if product is not None:
            turn_context.last_product_id = product.erp_entity_id
            turn_context.last_product_name = product.name
        if customer is not None:
            turn_context.last_customer_id = customer.erp_entity_id
            turn_context.last_customer_name = customer.name
        if location is not None:
            turn_context.last_location_id = location.erp_entity_id
        turn_context.last_tool = spec.name
        conversation.remember_turn(turn_context, "user", question)
        conversation.remember_turn(turn_context, "assistant", answer)
        conversation.save(
            session, turn_context, ttl_seconds=int(settings_row.context_ttl_seconds)
        )

    def _audit_record(
        self,
        tenant: identity.TenantIdentity,
        user: identity.UserIdentityInfo,
        trace_id: str,
        inbound: InboundMessage,
        spec: ToolSpec,
        args: dict[str, Any],
        decision: PolicyDecision | None,
        envelope: ToolCallEnvelope,
        *,
        status: str,
        deadline: Deadline,
        entity: dict[str, Any] | None = None,
        erp_payload_hash: str | None = None,
        key_fields: dict[str, Any] | None = None,
    ) -> audit.AuditRecord:
        return audit.AuditRecord(
            tenant_id=str(tenant.id),
            trace_id=trace_id,
            channel=inbound.channel,
            user_id=str(user.user_id),
            status=status,
            policy_decision="ALLOW" if decision is None or decision.allowed else "DENY",
            reason_code=(
                decision.reason_code.value
                if decision is not None and decision.reason_code
                else None
            ),
            policy_version_hash=decision.policy_version_hash if decision else "",
            message_text=inbound.text,
            tool_name=spec.name,
            tool_args=args,
            resolved_entity=entity,
            prompt_version=None,
            llm_provider=envelope.provider or None,
            llm_model=envelope.model or None,
            tokens_in=envelope.tokens_in or None,
            tokens_out=envelope.tokens_out or None,
            cost_usd=envelope.cost_usd or None,
            latencies_ms=dict(deadline.stage_latencies_ms),
            erp_payload_hash=erp_payload_hash,
            key_fields=key_fields,
        )


# --- helpers de modulo ----------------------------------------------------


def _terms_of(spec: ToolSpec, args: Any) -> list[tuple[str, str]]:
    """Termos a resolver, na ordem em que importam."""
    terms: list[tuple[str, str]] = []
    for key in ("product_term", "customer_term", "location_term"):
        value = getattr(args, key, None)
        if value:
            terms.append((key, str(value)))
    return terms


def _primary_role(spec: ToolSpec) -> str:
    return "product_term" if spec.primary_entity == "product" else "customer_term"


def _entity_type_for(argument: str) -> str:
    return {
        "product_term": "product",
        "customer_term": "customer",
        "location_term": "location",
    }[argument]


def _entity_label(entity_type: str) -> str:
    return {"product": "catálogo", "customer": "cadastro de clientes", "location": "depósitos"}[
        entity_type
    ]


def _args_dict(args: Any) -> dict[str, Any]:
    if isinstance(args, dict):
        return {key: str(value) for key, value in args.items() if value is not None}
    dumped = args.model_dump() if hasattr(args, "model_dump") else {}
    return {key: str(value) for key, value in dumped.items() if value is not None}


def _dump(payload: Any) -> Any:
    if isinstance(payload, list):
        return [_dump(item) for item in payload]
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload


def _error_kind(error: Exception) -> str:
    if isinstance(error, CircuitOpen):
        return "circuit_open"
    if isinstance(error, ErpTimeout):
        return "timeout"
    if isinstance(error, ErpAuthError):
        return "auth"
    if isinstance(error, ErpNotFound):
        return "not_found"
    return "unavailable"
