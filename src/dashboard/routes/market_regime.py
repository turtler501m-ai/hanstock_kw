from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

from src.broker import create_domestic_stock_broker
from src.market_regime import MarketRegimeService


router = APIRouter(prefix="/api/market-regime", tags=["market-regime"])


def _service(*, refresh: bool = False) -> MarketRegimeService:
    # Stored dashboard reads must not initialize credentials or contact Kiwoom.
    broker = (
        create_domestic_stock_broker(order_submission_enabled=False)
        if refresh else None
    )
    return MarketRegimeService(broker)


@router.get("/current")
def current_market_regime():
    value = _service().current()
    if value is None:
        raise HTTPException(status_code=404, detail="market regime has not been collected")
    return value


@router.get("/history")
def market_regime_history(days: int = Query(default=30, ge=1, le=365)):
    return _service().history(days)


@router.get("/diagnostics")
def market_regime_diagnostics():
    return _service().diagnostics()


@router.post("/refresh")
def refresh_market_regime(_payload: dict | None = Body(default=None)):
    try:
        return _service(refresh=True).refresh("KR")
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"market regime collection failed: {type(exc).__name__}: {exc}",
        ) from exc
