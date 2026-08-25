"""Cifragem das credenciais de ERP.

A credencial fica cifrada na aplicacao, com a chave fora do banco
(`ORBI_SECRET_KEY`). Quem tem acesso de leitura ao Postgres nao tem acesso ao
ERP do cliente (ORBI.md secao 11).
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from orbi.core.errors import ConfigurationError
from orbi.core.settings import get_settings


class CredentialCipher:
    """Cifra e decifra o dicionario de credenciais de uma conexao de ERP."""

    def __init__(self, key: str | None = None) -> None:
        resolved = key or get_settings().require_secret_key()
        try:
            self._fernet = Fernet(resolved.encode() if isinstance(resolved, str) else resolved)
        except (ValueError, TypeError) as exc:  # chave malformada
            raise ConfigurationError(
                "ORBI_SECRET_KEY invalida: esperado Fernet key base64 url-safe de 32 bytes"
            ) from exc

    def encrypt(self, payload: dict[str, Any]) -> bytes:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self._fernet.encrypt(raw)

    def decrypt(self, token: bytes | str) -> dict[str, Any]:
        raw_token = token.encode("utf-8") if isinstance(token, str) else token
        try:
            raw = self._fernet.decrypt(raw_token)
        except InvalidToken as exc:
            raise ConfigurationError(
                "credencial de ERP nao pode ser decifrada: "
                "ORBI_SECRET_KEY trocada ou dado corrompido"
            ) from exc
        decoded: dict[str, Any] = json.loads(raw.decode("utf-8"))
        return decoded

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("utf-8")
