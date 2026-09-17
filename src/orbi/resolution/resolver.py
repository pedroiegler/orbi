"""Entity Resolution — cascata de quatro estagios (ORBI.md secao 6.9).

```
1. alias aprendido do tenant   (barato, e o que mais acerta com o tempo)
2. codigo ou EAN exato          (so no caminho deterministico: o LLM nunca emite)
3. pg_trgm sobre o nome canonico (erro de digitacao, nome truncado)
4. embedding + pgvector          (variacao que a letra nao captura)
```

O criterio de decisao e assimetrico e essa e a decisao central do produto:

> Responder a entidade errada com confianca e um erro grave.
> Perguntar "qual desses?" e um custo pequeno.

Por isso a incerteza vira `AMBIGUOUS`, nunca um chute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from orbi.core.deadline import Deadline
from orbi.resolution.canonical import CanonicalNameBuilder, normalize
from orbi.resolution.embeddings import EmbeddingPort

ResolutionStatus = Literal["FOUND", "AMBIGUOUS", "NOT_FOUND"]
Stage = Literal["alias", "code", "trigram", "vector", "none"]

DEFAULT_TOP1 = Decimal("0.820")
DEFAULT_GAP = Decimal("0.050")
CANDIDATE_FLOOR = 0.30
"""Abaixo disso o candidato nem vira opcao: sugerir lixo confunde mais que ajuda."""

# Um termo so e tratado como codigo no caminho deterministico (o usuario
# respondeu um codigo). "100mm" continua sendo palavra, nao codigo.
_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\d{3,}$"),
    re.compile(r"(?i)^[a-z]{2,6}[-_./]?\d{3,}[a-z]{0,2}$"),
    re.compile(r"(?i)^\d{3,}[-_./][a-z0-9]{1,6}$"),
)


@dataclass(frozen=True)
class Candidate:
    erp_entity_id: str
    name: str
    canonical_name: str
    code: str | None
    score: float
    stage: Stage

    @property
    def label(self) -> str:
        return f"{self.name} ({self.code})" if self.code else self.name


@dataclass(frozen=True)
class Resolution:
    status: ResolutionStatus
    term: str
    entity_type: str
    entity: Candidate | None = None
    options: tuple[Candidate, ...] = ()
    stage: Stage = "none"

    @property
    def found(self) -> bool:
        return self.status == "FOUND"


@dataclass(frozen=True)
class Thresholds:
    """Calibrados por tenant, nunca fixos no codigo (secao 6.9)."""

    top1: float = float(DEFAULT_TOP1)
    gap: float = float(DEFAULT_GAP)
    options: int = 3

    @classmethod
    def from_settings_row(cls, row: object) -> Thresholds:
        return cls(
            top1=float(getattr(row, "resolution_top1_threshold", DEFAULT_TOP1)),
            gap=float(getattr(row, "resolution_gap_threshold", DEFAULT_GAP)),
            options=int(getattr(row, "ambiguity_options", 3)),
        )


class EntityResolver:
    """Resolve um termo humano em um `erp_entity_id`."""

    def __init__(
        self,
        session: Session,
        tenant_id: str,
        embedder: EmbeddingPort,
        thresholds: Thresholds | None = None,
        builder: CanonicalNameBuilder | None = None,
    ) -> None:
        self._session = session
        self._tenant_id = str(tenant_id)
        self._embedder = embedder
        self._thresholds = thresholds or Thresholds()
        # A pergunta passa pela mesma traducao do catalogo: comparar nome cru com
        # nome canonico seria comparar coisas diferentes.
        self._builder = builder or CanonicalNameBuilder()

    # --- entrada ---------------------------------------------------------

    def resolve(
        self,
        term: str,
        entity_type: str,
        *,
        deadline: Deadline | None = None,
        allow_code_match: bool = False,
    ) -> Resolution:
        """Executa a cascata, do estagio mais barato ao mais caro.

        `allow_code_match` so e verdadeiro no caminho deterministico (o usuario
        respondeu um codigo). O LLM nunca produz codigo: `EntityTerm` rejeita.
        """
        query = self._builder.build(term)
        if not query:
            return Resolution(status="NOT_FOUND", term=term, entity_type=entity_type)

        alias_hit = self._by_alias(normalize(term), entity_type) or self._by_alias(
            query, entity_type
        )
        if alias_hit is not None:
            candidato, confianca = alias_hit
            if confianca == "confirmed":
                # Dois usos independentes sem correcao: o vocabulario do cliente
                # ja provou que este termo aponta para esta entidade.
                return Resolution(
                    status="FOUND",
                    term=term,
                    entity_type=entity_type,
                    entity=candidato,
                    stage="alias",
                )
            alias_provisorio = candidato
        else:
            alias_provisorio = None

        if allow_code_match and looks_like_code(query):
            code_hit = self._by_code(query, entity_type)
            if code_hit is not None:
                return Resolution(
                    status="FOUND",
                    term=term,
                    entity_type=entity_type,
                    entity=code_hit,
                    stage="code",
                )

        candidates = self._by_trigram(query, entity_type)
        decision = self._decide(candidates, term, entity_type, "trigram")
        if decision.status == "FOUND":
            return self._conciliar(decision, alias_provisorio, term, entity_type)

        if deadline is not None and deadline.remaining_ms() < 100:
            # Sem orcamento para o estagio caro: devolve o que ja se sabe.
            return self._conciliar(decision, alias_provisorio, term, entity_type)

        merged = self._merge(candidates, self._by_vector(query, entity_type))
        return self._conciliar(
            self._decide(merged, term, entity_type, "vector"), alias_provisorio, term, entity_type
        )

    def _conciliar(
        self,
        decisao: Resolution,
        alias_provisorio: Candidate | None,
        term: str,
        entity_type: str,
    ) -> Resolution:
        """Reconcilia um alias `low` com o que o catalogo diz (D-013).

        Alias `low` nasceu de **um** toque de **uma** pessoa. Ele nao pode
        atropelar o catalogo em silencio: era isso que permitia um toque errado
        virar resposta confiante para toda a equipe daquele cliente, com nota
        maxima e sem passar por limiar nenhum.

        Tres casos, e so o terceiro e novo:

        - **o catalogo concorda** — resposta direta, e o uso conta para promover;
        - **o catalogo nao acha nada** — o alias e o unico sinal, entao vale; a
          resposta mostra qual entidade foi usada, como sempre;
        - **o catalogo aponta outra coisa** — discordancia. Perguntar, nunca
          chutar: e a regra do produto, e e exatamente aqui que o toque errado
          seria caro.
        """
        if alias_provisorio is None:
            return decisao

        if decisao.status == "FOUND" and decisao.entity is not None:
            if decisao.entity.erp_entity_id == alias_provisorio.erp_entity_id:
                return Resolution(
                    status="FOUND",
                    term=term,
                    entity_type=entity_type,
                    entity=alias_provisorio,
                    stage="alias",
                )
            return Resolution(
                status="AMBIGUOUS",
                term=term,
                entity_type=entity_type,
                options=(alias_provisorio, decisao.entity),
                stage=decisao.stage,
            )

        if decisao.status == "NOT_FOUND":
            return Resolution(
                status="FOUND",
                term=term,
                entity_type=entity_type,
                entity=alias_provisorio,
                stage="alias",
            )

        # Ambiguo: o alias entra na frente da lista, como sugestao — nao como
        # certeza. Quem escolhe continua sendo a pessoa.
        outros = tuple(
            opcao
            for opcao in decisao.options
            if opcao.erp_entity_id != alias_provisorio.erp_entity_id
        )
        return Resolution(
            status="AMBIGUOUS",
            term=term,
            entity_type=entity_type,
            options=(alias_provisorio, *outros)[: max(len(decisao.options), 2)],
            stage=decisao.stage,
        )

    def candidates(self, term: str, entity_type: str) -> list[Candidate]:
        """Ranking bruto, sem aplicar limiar.

        E o que a calibracao precisa: com os scores em maos, varrer pares de
        limiar vira aritmetica, sem repetir a consulta ao banco.
        """
        query = self._builder.build(term)
        if not query:
            return []
        return self._merge(
            self._by_trigram(query, entity_type), self._by_vector(query, entity_type)
        )

    # --- estagios --------------------------------------------------------

    def _by_alias(self, query: str, entity_type: str) -> tuple[Candidate, str] | None:
        """Devolve o candidato **e a confianca** do alias.

        A confianca importa: alias `low` nasceu de um unico toque de uma unica
        pessoa, e um toque errado nao pode valer mais que o catalogo inteiro.
        """
        row = self._session.execute(
            text(
                """
                SELECT c.erp_entity_id, c.name, c.canonical_name, c.code, a.confidence
                FROM entity_aliases a
                JOIN catalog c
                  ON c.tenant_id = a.tenant_id
                 AND c.entity_type = a.entity_type
                 AND c.erp_entity_id = a.erp_entity_id
                WHERE a.entity_type = :entity_type
                  AND a.alias = :alias
                  AND c.active
                  AND NOT c.flagged
                LIMIT 1
                """
            ),
            {"entity_type": entity_type, "alias": query},
        ).first()
        if row is None:
            return None
        return (
            Candidate(
                erp_entity_id=row.erp_entity_id,
                name=row.name,
                canonical_name=row.canonical_name,
                code=row.code,
                score=1.0,
                stage="alias",
            ),
            str(row.confidence),
        )

    def _by_code(self, query: str, entity_type: str) -> Candidate | None:
        row = self._session.execute(
            text(
                """
                SELECT erp_entity_id, name, canonical_name, code
                FROM catalog
                WHERE entity_type = :entity_type
                  AND active
                  AND NOT flagged
                  AND (lower(code) = :code OR barcode = :code OR erp_entity_id = :code)
                LIMIT 2
                """
            ),
            {"entity_type": entity_type, "code": query},
        ).all()
        if len(row) != 1:
            return None  # ambiguo por codigo e melhor tratar como nao encontrado
        found = row[0]
        return Candidate(
            erp_entity_id=found.erp_entity_id,
            name=found.name,
            canonical_name=found.canonical_name,
            code=found.code,
            score=1.0,
            stage="code",
        )

    def _by_trigram(self, query: str, entity_type: str) -> list[Candidate]:
        rows = self._session.execute(
            text(
                """
                SELECT erp_entity_id, name, canonical_name, code,
                       similarity(canonical_name, :q) AS sim,
                       word_similarity(:q, canonical_name) AS word_sim
                FROM catalog
                WHERE entity_type = :entity_type
                  AND active
                  AND NOT flagged
                  AND (canonical_name %> :q OR canonical_name % :q)
                ORDER BY GREATEST(
                    similarity(canonical_name, :q),
                    word_similarity(:q, canonical_name) * 0.95
                ) DESC
                LIMIT 10
                """
            ),
            {"q": query, "entity_type": entity_type},
        ).all()

        candidates = []
        for row in rows:
            # `word_similarity` mede o quanto o termo casa com um trecho do nome:
            # e o que faz "cimento" achar "cimento cp ii 50 kg". O desconto deixa
            # o casamento integral na frente do casamento parcial.
            score = max(float(row.sim or 0.0), float(row.word_sim or 0.0) * 0.95)
            if score < CANDIDATE_FLOOR:
                continue
            candidates.append(
                Candidate(
                    erp_entity_id=row.erp_entity_id,
                    name=row.name,
                    canonical_name=row.canonical_name,
                    code=row.code,
                    score=score,
                    stage="trigram",
                )
            )
        return candidates

    def _by_vector(self, query: str, entity_type: str) -> list[Candidate]:
        vector = self._embedder.embed([query])[0]
        literal = "[" + ",".join(f"{value:.6f}" for value in vector) + "]"
        rows = self._session.execute(
            text(
                """
                SELECT erp_entity_id, name, canonical_name, code,
                       1 - (embedding <=> CAST(:vec AS vector)) AS score
                FROM catalog
                WHERE entity_type = :entity_type
                  AND active
                  AND NOT flagged
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT 10
                """
            ),
            {"vec": literal, "entity_type": entity_type},
        ).all()

        return [
            Candidate(
                erp_entity_id=row.erp_entity_id,
                name=row.name,
                canonical_name=row.canonical_name,
                code=row.code,
                score=float(row.score or 0.0),
                stage="vector",
            )
            for row in rows
            if float(row.score or 0.0) >= CANDIDATE_FLOOR
        ]

    # --- decisao ---------------------------------------------------------

    @staticmethod
    def _merge(*groups: list[Candidate]) -> list[Candidate]:
        best: dict[str, Candidate] = {}
        for group in groups:
            for candidate in group:
                current = best.get(candidate.erp_entity_id)
                if current is None or candidate.score > current.score:
                    best[candidate.erp_entity_id] = candidate
        return sorted(best.values(), key=lambda item: item.score, reverse=True)

    def _decide(
        self, candidates: list[Candidate], term: str, entity_type: str, stage: Stage
    ) -> Resolution:
        ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
        if not ranked:
            return Resolution(status="NOT_FOUND", term=term, entity_type=entity_type, stage=stage)

        top = ranked[0]
        gap = top.score - (ranked[1].score if len(ranked) > 1 else 0.0)

        if top.score >= self._thresholds.top1 and gap >= self._thresholds.gap:
            return Resolution(
                status="FOUND", term=term, entity_type=entity_type, entity=top, stage=stage
            )

        return Resolution(
            status="AMBIGUOUS",
            term=term,
            entity_type=entity_type,
            options=tuple(ranked[: self._thresholds.options]),
            stage=stage,
        )


def looks_like_code(term: str) -> bool:
    """Termo que parece codigo interno, EAN ou id do ERP."""
    candidate = term.strip()
    if not candidate or " " in candidate:
        return False
    return any(pattern.match(candidate) for pattern in _CODE_PATTERNS)
