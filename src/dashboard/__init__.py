# -*- coding: utf-8 -*-
from contextlib import asynccontextmanager
import os
import socket
import subprocess

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.dashboard.core import app
from src import trader
from src.utils.logger import logger
from src.ai_stock import constants as _ai_stock_constants
from src.ai_stock.schemas import envelope as _ai_stock_envelope

# Import all route modules so decorators register on app
import src.dashboard.routes.pages as pages
import src.dashboard.routes.account as account
import src.dashboard.routes.settings as settings
import src.dashboard.routes.stock as stock
import src.dashboard.routes.mistock as mistock
import src.dashboard.routes.plunge_bounce as plunge_bounce
import src.dashboard.routes.narrative_momentum as narrative_momentum
import src.dashboard.routes.ai_stock as ai_stock
import src.dashboard.routes.market_regime as market_regime

for route_module in [
    pages,
    account,
    settings,
    stock,
    mistock,
    plunge_bounce,
    narrative_momentum,
    ai_stock,
    market_regime,
]:
    app.include_router(route_module.router)


def _runtime_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _log_server_lifecycle(event: str) -> None:
    flags = trader.runtime_flags()
    logger.info(
        "[SERVER_LIFECYCLE] service=dashboard event={} pid={} host={} revision={} "
        "trading_env={} dry_run={} order_submission_enabled={}",
        event,
        os.getpid(),
        socket.gethostname(),
        _runtime_revision(),
        flags.trading_env,
        flags.dry_run,
        flags.order_submission_enabled,
    )


@asynccontextmanager
async def _dashboard_lifespan(_app):
    _log_server_lifecycle("startup")
    settings.run_dashboard_startup_tasks()
    try:
        yield
    finally:
        _log_server_lifecycle("shutdown")


app.router.lifespan_context = _dashboard_lifespan


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/ai-stock"):
        market = request.query_params.get("market") or _ai_stock_constants.MARKET_ALL
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _ai_stock_error_response(exc.status_code, market, [detail])
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _request_validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/ai-stock"):
        market = request.query_params.get("market") or _ai_stock_constants.MARKET_ALL
        errors = []
        for item in exc.errors():
            loc = ".".join(str(part) for part in item.get("loc", []))
            msg = item.get("msg", "validation error")
            errors.append(f"{loc}: {msg}" if loc else str(msg))
        return _ai_stock_error_response(422, market, errors or ["validation error"])
    return await request_validation_exception_handler(request, exc)


def _ai_stock_error_response(status_code: int, market: str, errors: list[str]) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=_ai_stock_envelope(
            None,
            market=market,
            errors=errors,
            ok=False,
            meta={"data_quality": "error"},
        ),
    )

# Dynamically expose all names from core and all route files for backward compatibility
import src.dashboard.core as _core

for mod in [_core, pages, account, settings, stock, mistock, plunge_bounce, narrative_momentum, market_regime]:
    globals().update({k: v for k, v in mod.__dict__.items() if not k.startswith("__")})
