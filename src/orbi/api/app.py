"""API do Orbi — apenas o webhook do canal e a saude do processo.

Nao existe API publica de consulta e nao existe painel: a administracao e pela
CLI (ORBI.md secao 4). O que vive aqui e o que a Meta precisa chamar.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, BackgroundTasks, FastAPI, Header, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse

from orbi.api import dependencies
from orbi.channel.whatsapp import parse_webhook, verify_challenge, verify_signature
from orbi.core.settings import get_settings
from orbi.db.base import app_engine, ping

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    settings = get_settings()
    problems = settings.validate_for_production() if settings.is_production else []
    if problems:
        # Producao nao sobe com configuracao insegura.
        raise RuntimeError("configuracao invalida para producao: " + "; ".join(problems))
    logger.info("orbi iniciado em modo %s", settings.env)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Orbi",
        description="Camada de linguagem natural sobre ERPs. Somente webhook de canal.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(webhook_router)
    return app


health_router = APIRouter(tags=["health"])
webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@health_router.get("/health")
def health() -> JSONResponse:
    """Saude do processo e o que ele mediu desde que subiu.

    Como nao ha painel, este e o lugar onde a operacao le latencia e taxa de
    ambiguidade sem abrir o banco.
    """
    settings = get_settings()
    try:
        database_ok = ping(app_engine())
    except Exception:
        database_ok = False

    payload = {
        "status": "ok" if database_ok else "degraded",
        "env": settings.env,
        "turns": dependencies.get_runtime().metrics.snapshot(),
    }
    code = status.HTTP_200_OK if database_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(payload, status_code=code)


@webhook_router.get("/whatsapp")
def whatsapp_verify(request: Request) -> Response:
    """Handshake de verificacao do webhook da Meta."""
    settings = get_settings()
    params = request.query_params
    challenge = verify_challenge(
        settings.whatsapp_verify_token.get_secret_value(),
        params.get("hub.mode"),
        params.get("hub.verify_token"),
        params.get("hub.challenge"),
    )
    if challenge is None:
        return PlainTextResponse("forbidden", status_code=status.HTTP_403_FORBIDDEN)
    return PlainTextResponse(challenge)


@webhook_router.post("/whatsapp")
async def whatsapp_inbound(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
) -> Response:
    """Recebe a mensagem, responde 200 na hora e processa em segundo plano.

    A Meta reenvia o evento se demorarmos: o turno roda fora do ciclo do webhook,
    e o `Deadline` continua valendo dentro dele.
    """
    settings = get_settings()
    raw = await request.body()

    app_secret = settings.whatsapp_app_secret.get_secret_value()
    if app_secret and not verify_signature(app_secret, raw, x_hub_signature_256):
        logger.warning("webhook com assinatura invalida")
        return PlainTextResponse("forbidden", status_code=status.HTTP_403_FORBIDDEN)
    if not app_secret and settings.is_production:
        return PlainTextResponse("misconfigured", status_code=status.HTTP_403_FORBIDDEN)

    try:
        payload = await request.json()
    except ValueError:
        return PlainTextResponse("bad request", status_code=status.HTTP_400_BAD_REQUEST)

    events = parse_webhook(payload)
    # Resolvido pelo modulo (e nao por import direto) para que o processo
    # possa trocar o despachante — a CLI e os testes fazem isso.
    dispatcher = dependencies.get_dispatcher()
    for event in events:
        background.add_task(dispatcher.dispatch, event)

    return PlainTextResponse("ok")


app = create_app()
