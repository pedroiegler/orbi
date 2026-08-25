"""Embeddings para o quarto estagio da cascata (ORBI.md secao 6.9).

Dois provedores atras de uma porta:

- `hashing` (padrao em dev e CI): vetorizacao por *hashing trick* sobre n-gramas
  de caractere e palavras, 768 dimensoes, L2-normalizado. E uma tecnica real e
  deterministica, roda offline e captura variacao lexical — truncamento,
  abreviacao e erro de digitacao — que e o caso dominante em catalogo brasileiro.
- `openai`: embedding multilingue por API, para producao (D-007).

O vetor tem sempre 768 dimensoes porque e o que a coluna do catalogo declara.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol, runtime_checkable

from orbi.core.errors import LLMError
from orbi.core.settings import EMBEDDING_DIMENSIONS, Settings, get_settings
from orbi.resolution.canonical import normalize

CHAR_NGRAM_RANGE = (3, 5)


@runtime_checkable
class EmbeddingPort(Protocol):
    name: str
    model_version: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Hashing trick sobre n-gramas de caractere e palavras inteiras."""

    name = "hashing"

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.model_version = f"hashing-ngram-{CHAR_NGRAM_RANGE[0]}{CHAR_NGRAM_RANGE[1]}-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        cleaned = normalize(text)
        if not cleaned:
            return vector

        padded = f" {cleaned} "
        for size in range(CHAR_NGRAM_RANGE[0], CHAR_NGRAM_RANGE[1] + 1):
            if len(padded) < size:
                continue
            for start in range(len(padded) - size + 1):
                _accumulate(vector, padded[start : start + size], weight=1.0, dims=self.dimensions)

        # Palavra inteira pesa mais: "cimento" casando com "cimento" vale mais
        # que a soma dos pedacos que ele compartilha com "cimenta".
        for word in cleaned.split():
            _accumulate(vector, f"w:{word}", weight=2.0, dims=self.dimensions)

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


def _accumulate(vector: list[float], token: str, weight: float, dims: int) -> None:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    index = int.from_bytes(digest[:4], "big") % dims
    sign = 1.0 if digest[4] % 2 == 0 else -1.0
    vector[index] += sign * weight


class OpenAIEmbedder:
    """Embedding por API, com reducao de dimensao nativa para 768."""

    name = "openai"

    def __init__(self, api_key: str, model: str, client: Any | None = None) -> None:
        self.model_version = model
        self.dimensions = EMBEDDING_DIMENSIONS
        self._api_key = api_key
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import openai
            except ImportError as exc:  # pragma: no cover - depende do extra
                raise LLMError(
                    "SDK da OpenAI ausente: instale com `pip install orbi[llm]`"
                ) from exc
            self._client = openai.OpenAI(api_key=self._api_key)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._ensure_client()
        response = client.embeddings.create(
            model=self.model_version,
            input=[normalize(text) for text in texts],
            dimensions=self.dimensions,
        )
        return [list(item.embedding) for item in response.data]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return max(-1.0, min(1.0, dot))


def build_embedder(settings: Settings | None = None) -> EmbeddingPort:
    resolved = settings or get_settings()
    if resolved.embedding_provider == "openai":
        return OpenAIEmbedder(
            api_key=resolved.openai_api_key.get_secret_value(),
            model=resolved.embedding_model,
        )
    return HashingEmbedder()
