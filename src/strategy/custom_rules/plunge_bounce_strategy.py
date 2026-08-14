import os
import yfinance as yf
from datetime import datetime, timezone, timedelta
from src.strategy.indicators import calc_rsi, calc_sma
from src.utils.logger import logger

class PlungeBounceStrategy:
    """
    ⚙️ 급락 반등 평균회귀 전략 (주식코딩)
    유튜브 '주식코딩' 영상의 AI 전종목 백테스트 결과를 기반으로 구현한 수익극대화 급락 반등 전략입니다.
    이격도 -15% 이하(일봉), RSI(14) < 30, 지수 200일선 필터, 거래대금 필터(1백만~5억원), 거래량 급증(1.4배) 조건을 결합하여 승률을 극대화합니다.
    """
    
    _index_cache = {}  # Class-level cache for index trend lookup
    _last_cache_time = None

    def __init__(self):
        # Allow customization via database settings with env fallback
        from src.db.repository import get_watchlist_setting
        self.deviation_threshold = float(get_watchlist_setting("PLUNGE_DEVIATION_THRESHOLD", os.environ.get("PLUNGE_DEVIATION_THRESHOLD", "-15.0")))
        self.rsi_threshold = float(get_watchlist_setting("PLUNGE_RSI_THRESHOLD", os.environ.get("PLUNGE_RSI_THRESHOLD", "30.0")))
        self.vol_ratio_threshold = float(get_watchlist_setting("PLUNGE_VOL_RATIO_THRESHOLD", os.environ.get("PLUNGE_VOL_RATIO_THRESHOLD", "1.4")))
        self.min_val_krw = float(get_watchlist_setting("PLUNGE_MIN_VAL_KRW", "1000000.0"))
        self.max_val_krw = float(get_watchlist_setting("PLUNGE_MAX_VAL_KRW", "500000000.0"))
        self.index_filter_enabled = get_watchlist_setting("PLUNGE_INDEX_FILTER_ENABLED", "1") == "1"

    def _is_index_above_sma(self, symbol: str) -> bool:
        """Determines if the relevant market index is trading above its 200-day SMA."""
        if not symbol:
            return True
            
        # Determine index symbol based on the stock symbol
        index_ticker = "^KS11"  # Default to KOSPI
        if symbol.endswith(".KQ"):
            index_ticker = "^KQ11"  # KOSDAQ
        elif not symbol.endswith(".KS") and not symbol.endswith(".KQ"):
            index_ticker = "SPY"  # US Market
            
        now = datetime.now()
        # Cache results for 1 hour to prevent redundant network requests during universe scans
        if index_ticker in self._index_cache and self._last_cache_time and (now - self._last_cache_time).total_seconds() < 3600:
            return self._index_cache[index_ticker]
            
        try:
            from src.online_access import require_online_access

            require_online_access("yfinance index trend lookup")
            df = yf.download(index_ticker, period="1y", progress=False, auto_adjust=True)
            if not df.empty and len(df) >= 200:
                closes = df["Close"].squeeze()
                sma200 = closes.rolling(window=200).mean().iloc[-1]
                latest_close = closes.iloc[-1]
                is_above = bool(latest_close > sma200)
                self._index_cache[index_ticker] = is_above
                self._last_cache_time = now
                logger.info(f"[PlungeBounce] Index {index_ticker} trend checked: latest={latest_close:.1f}, SMA200={sma200:.1f}, above={is_above}")
                return is_above
        except Exception as e:
            logger.warning(f"[PlungeBounce] Failed to fetch index trend for {index_ticker}: {e}")
            return True  # Fallback to True to not block trades if Yahoo is down
            
        return True

    def calculate_score(self, prices: list[float], indicators: dict) -> float:
        """
        Calculates a score. Returns 5.0 (highly recommended buy) if all entry rules and 
        yield-maximizing filters are met; otherwise returns 0.0.
        """
        pb_reasons = []
        
        if len(prices) < 22:
            pb_reasons.append("데이터 부족 (22일 미만)")
            indicators["pb_reasons"] = pb_reasons
            return 0.0
            
        current_price = prices[-1]
        symbol = indicators.get("symbol", "")
        
        # 1. Technical Indicators Calculation (Disparity)
        sma22 = calc_sma(prices, 22)
        disparity = ((current_price - sma22) / sma22) * 100
        disparity_passed = disparity <= self.deviation_threshold
        if not disparity_passed:
            pb_reasons.append(f"이격도 초과 ({disparity:.1f}% > {self.deviation_threshold:.1f}%)")
            
        # 2. RSI Oversold Filter
        rsi = indicators.get("rsi", 50.0)
        rsi_passed = rsi < self.rsi_threshold
        if not rsi_passed:
            pb_reasons.append(f"RSI 기준 미달 ({rsi:.1f} >= {self.rsi_threshold:.1f})")
            
        # 3. Volume Spike Filter
        volumes = indicators.get("volumes", [])
        if volumes and len(volumes) >= 21:
            avg_vol_20 = sum(volumes[-21:-1]) / 20
            vol_ratio = volumes[-1] / avg_vol_20 if avg_vol_20 > 0 else 1.0
        else:
            vol_ratio = 1.0
            
        vol_passed = vol_ratio >= self.vol_ratio_threshold
        if not vol_passed:
            pb_reasons.append(f"거래량 비율 미달 ({vol_ratio:.2f}x < {self.vol_ratio_threshold:.2f}x)")
            
        # 4. Transaction Value Filter
        is_kr = False
        if symbol:
            code = symbol.split(".")[0]
            if code.isdigit() and len(code) == 6:
                is_kr = True
                
        latest_volume = volumes[-1] if volumes else 0
        latest_val = latest_volume * current_price
        
        val_passed = False
        if is_kr:
            val_passed = self.min_val_krw <= latest_val <= self.max_val_krw
            if not val_passed:
                pb_reasons.append(f"거래대금 범위 초과 ({latest_val:,.0f} KRW)")
        else:
            val_passed = 800 <= latest_val <= 400_000
            if not val_passed:
                pb_reasons.append(f"거래대금 범위 초과 ({latest_val:,.0f} USD)")
                
        # 5. Market Index Trend Filter
        index_passed = True
        if self.index_filter_enabled:
            index_passed = self._is_index_above_sma(symbol)
            if not index_passed:
                pb_reasons.append("지수 200일선 하회")
                
        indicators["pb_reasons"] = pb_reasons
        
        if disparity_passed and rsi_passed and vol_passed and val_passed and index_passed:
            logger.info(f"[PlungeBounce] ALL triggers & filters PASSED for {symbol}: disparity={disparity:.2f}%, RSI={rsi:.1f}, vol_ratio={vol_ratio:.1f}x, val={latest_val:,.1f}")
            return 5.0
            
        return 0.0
