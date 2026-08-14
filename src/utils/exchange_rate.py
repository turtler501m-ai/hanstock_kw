# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
import yfinance as yf
from src.utils.logger import logger

_USD_KRW_RATE: float = float(os.environ.get("USDKRW_FALLBACK_RATE", "1380.0"))
_USD_KRW_LAST_FETCH: float = 0.0
_USD_KRW_CACHE_TTL: float = 3600.0  # 1 hour cache

def get_usd_krw_rate() -> float:
    global _USD_KRW_RATE, _USD_KRW_LAST_FETCH
    from src.online_access import is_online_access_blocked

    if is_online_access_blocked():
        return _USD_KRW_RATE
    if os.environ.get("HANSTOCK_TESTING") == "1":
        return _USD_KRW_RATE
    now = time.time()
    if now - _USD_KRW_LAST_FETCH > _USD_KRW_CACHE_TTL:
        try:
            ticker = yf.Ticker("USDKRW=X")
            info = ticker.fast_info
            rate = None
            
            # fast_info can be a dictionary-like object or a FastInfo object.
            # Handle both cases safely.
            if hasattr(info, "last_price"):
                rate = info.last_price
            elif hasattr(info, "lastPrice"):
                rate = info.lastPrice
            elif isinstance(info, dict):
                rate = info.get("last_price") or info.get("lastPrice")
            elif hasattr(info, "get"):
                try:
                    rate = info.get("last_price") or info.get("lastPrice")
                except Exception:
                    pass
            
            if not rate:
                # Use 3d period to guarantee closed/weekend markets return data
                hist = ticker.history(period="3d")
                if not hist.empty:
                    rate = float(hist["Close"].dropna().iloc[-1])
                    
            if rate and float(rate) > 0:
                _USD_KRW_RATE = float(rate)
                _USD_KRW_LAST_FETCH = now
                logger.info(f"Successfully updated USD/KRW exchange rate to {_USD_KRW_RATE:.2f} using yfinance.")
            else:
                raise ValueError("Exchange rate returned 0 or None")
        except Exception as e:
            logger.warning(f"Failed to fetch USD/KRW rate from yfinance: {e}")
            _USD_KRW_LAST_FETCH = now - _USD_KRW_CACHE_TTL + 300  # retry in 5 mins
    return _USD_KRW_RATE
