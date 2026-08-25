"""Acceptance eval do onboarding (ORBI.md secao 18).

O cliente escreve, antes de comecar, as 20 perguntas reais que quer ver
funcionando. Elas viram o criterio de aprovacao do `orbi onboard`. Se passarem,
ele nao tem argumento para dizer "nao me convenceu" — o criterio foi dele.

Formato do arquivo:

```json
{"cases": [
  {"question": "quanto tem de tubo pvc 100?", "role": "sales_rep",
   "expect_contains": ["TB PVC"], "expect_status": "ok"}
]}
```
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orbi.db.models import Tenant, User, UserIdentity
from orbi.db.session import admin_session
from orbi.evals.runner import _check_expectations
from orbi.llm.router import build_router
from orbi.runtime.pipeline import InboundMessage, OrbiRuntime

PASS_RATE = 1.0
"""O criterio e do cliente: passar quase todos nao e passar."""


@dataclass
class AcceptanceCase:
    question: str
    role: str = "sales_rep"
    status: str = ""
    detail: str = ""
    answer: str = ""

    @property
    def passed(self) -> bool:
        return not self.detail


@dataclass
class AcceptanceOutcome:
    total: int = 0
    passed_cases: int = 0
    cases: list[AcceptanceCase] = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.passed_cases / self.total if self.total else 0.0

    @property
    def passed(self) -> bool:
        return self.total > 0 and self.score >= PASS_RATE

    def summary(self) -> str:
        return f"{self.passed_cases}/{self.total} casos do cliente passando ({self.score:.0%})"

    def report(self) -> str:
        lines = [f"Acceptance eval — {self.summary()}", ""]
        for case in self.cases:
            mark = "ok" if case.passed else "FALHOU"
            lines.append(f"[{mark}] {case.question}")
            if not case.passed:
                lines.append(f"       {case.detail}")
        return "\n".join(lines)


def run_acceptance(tenant_id: uuid.UUID, path: Path) -> AcceptanceOutcome:
    """Roda as perguntas do cliente contra os dados reais dele."""
    with path.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)

    with admin_session() as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            raise ValueError("tenant inexistente")
        destination = tenant.channel_phone_number_id or tenant.channel_address or ""
        senders: dict[str, str] = {}
        rows = (
            session.query(UserIdentity, User)
            .join(User, User.id == UserIdentity.user_id)
            .filter(UserIdentity.tenant_id == tenant_id, User.active.is_(True))
            .all()
        )
        for user_identity, user in rows:
            senders.setdefault(user.role_code, user_identity.address)

    runtime = OrbiRuntime(build_router())
    outcome = AcceptanceOutcome()

    for raw in payload.get("cases", []):
        case = AcceptanceCase(question=raw["question"], role=raw.get("role", "sales_rep"))
        outcome.total += 1
        sender = senders.get(case.role)
        if sender is None:
            case.detail = f"nenhum usuario com papel {case.role} cadastrado"
            outcome.cases.append(case)
            continue

        result = runtime.handle(
            InboundMessage(
                channel="whatsapp",
                from_address=sender,
                to_address=destination,
                text=case.question,
            )
        )
        case.status = result.status
        case.answer = result.text
        problem = _check_expectations(raw, result)
        if problem is None:
            outcome.passed_cases += 1
        else:
            case.detail = problem
        outcome.cases.append(case)

    return outcome


def write_template(path: Path) -> None:
    """Gera o arquivo que o cliente preenche antes da PoC."""
    template = {
        "_comment": (
            "As 20 perguntas que voce quer ver funcionando. Escreva do jeito que a "
            "sua equipe pergunta no dia a dia."
        ),
        "cases": [
            {
                "question": "quanto tem de <produto que voce vende muito>?",
                "role": "sales_rep",
                "expect_status": "ok",
                "expect_contains": ["<parte do nome do produto>"],
            }
        ],
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(template, handle, ensure_ascii=False, indent=2)
