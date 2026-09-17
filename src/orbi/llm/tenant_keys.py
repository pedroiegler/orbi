"""Chave de LLM por cliente (D-041).

A chave global continua sendo o caminho normal. Esta camada existe para o
cliente que ganha a **propria** chave no provedor, e com ela:

- um teto de gasto proprio, que corta so o gasto dele;
- cota que nao e dividida com os outros clientes;
- um segredo cujo vazamento tem raio de um cliente, nao de todos.

Duas decisoes que valem ler antes de mexer:

**A chave e cifrada com a mesma `ORBI_SECRET_KEY` das credenciais de ERP.**
Segredo de cliente nao fica em texto claro no banco, e nao existe um segundo
lugar de onde ele possa vazar.

**Falha na chave propria cai para a global, com alerta.** O cliente continua
atendido — teto de gasto estourado ou chave revogada nao pode tirar a operacao
do ar no meio do expediente. A contrapartida esta escrita e e real: naquele
turno o gasto volta a ser seu, e por isso a queda **avisa**. Cair em silencio
transformaria o teto do provedor numa proteccao que ninguem sabe que falhou.
A auditoria grava qual provedor de fato atendeu, entao a conta fecha depois.
"""

from __future__ import annotations

import hashlib
from typing import Any

from orbi.core.crypto import CredentialCipher
from orbi.core.errors import ConfigurationError
from orbi.core.settings import Settings, get_settings
from orbi.llm.port import LLMPort
from orbi.llm.pricing import TABELA

PROVEDORES_COM_CHAVE_PROPRIA: frozenset[str] = frozenset({"openai", "anthropic", "gemini"})
"""`rule_based` fica de fora: nao tem chave, e em producao nem e permitido."""


class TenantLLMError(ConfigurationError):
    """Credencial de LLM do cliente invalida. Recusa no cadastro, nunca no turno."""


def montar_credencial(provider: str, api_key: str, model: str) -> dict[str, str]:
    """Valida e monta o que sera cifrado. Falha alto, na porta de entrada."""
    if provider not in PROVEDORES_COM_CHAVE_PROPRIA:
        disponiveis = ", ".join(sorted(PROVEDORES_COM_CHAVE_PROPRIA))
        raise TenantLLMError(f"provedor '{provider}' nao aceita chave propria. Use: {disponiveis}")
    if not api_key.strip():
        raise TenantLLMError("chave vazia")
    if not model.strip():
        raise TenantLLMError("modelo vazio: sem ele o Orbi nao sabe o que cobrar nem o que chamar")
    return {"provider": provider, "api_key": api_key.strip(), "model": model.strip()}


def modelo_sem_preco(credencial: dict[str, str]) -> bool:
    """Modelo fora da tabela contabiliza zero, em silencio. Quem chama avisa."""
    return credencial["model"] not in TABELA


def cifrar(credencial: dict[str, str], cipher: CredentialCipher | None = None) -> bytes:
    return (cipher or CredentialCipher()).encrypt(dict(credencial))


def decifrar(blob: bytes, cipher: CredentialCipher | None = None) -> dict[str, str]:
    dados: dict[str, Any] = (cipher or CredentialCipher()).decrypt(blob)
    return {chave: str(valor) for chave, valor in dados.items()}


def descricao(blob: bytes | None) -> str:
    """Como a chave aparece na CLI: nunca inteira, sempre reconhecivel.

    Mostrar a chave completa num terminal a espalha para o historico do shell,
    para o scrollback e para qualquer captura de tela.
    """
    if blob is None:
        return "global (padrao)"
    try:
        credencial = decifrar(blob)
    except ConfigurationError:
        return "ILEGIVEL — ORBI_SECRET_KEY trocada?"
    final = credencial["api_key"][-4:]
    return f"{credencial['provider']} · {credencial['model']} · chave ...{final}"


_CACHE: dict[str, LLMPort] = {}


def _chave_de_cache(credencial: dict[str, str]) -> str:
    """Impressao digital da credencial, nunca a credencial.

    O cache existe porque construir o cliente HTTP a cada turno desperdicaria a
    conexao reaproveitada; a chave em claro num dicionario de modulo seria um
    segredo a mais para vazar num traceback.
    """
    material = f"{credencial['provider']}|{credencial['model']}|{credencial['api_key']}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def provider_do_tenant(blob: bytes, settings: Settings | None = None) -> LLMPort:
    """Provedor construido com a chave do cliente, reaproveitado entre turnos."""
    from orbi.llm import router as router_module

    credencial = decifrar(blob)
    impressao = _chave_de_cache(credencial)
    if impressao not in _CACHE:
        _CACHE[impressao] = router_module.build_provider_with(
            credencial["provider"],
            api_key=credencial["api_key"],
            model=credencial["model"],
            settings=settings or get_settings(),
        )
    return _CACHE[impressao]


def limpar_cache() -> None:
    """Usado pelos testes e por uma troca de chave em processo longo."""
    _CACHE.clear()
