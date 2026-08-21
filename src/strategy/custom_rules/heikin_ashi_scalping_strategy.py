from src.strategy.heikin_ashi_scalping import HeikinAshiScalpingStrategy


STRATEGY_PROFILE = {
    "strategy_type": "trend_pullback_continuation",
    "risk_level": "balanced",
    "focus": ["rising_ema200", "double_heikin_ashi_reversal", "real_high_breakout", "adx_trend_quality"],
    "avoid": ["flat_adx", "extreme_atr", "synthetic_price_execution", "duplicate_symbol_exposure"],
    "market_regime_filter": ["bull"],
    "risk": {
        "max_risk_per_trade_pct": 0.5,
        "max_total_open_risk_pct": 1.0,
        "max_strategy_exposure_pct": 30.0,
    },
}


class AlphaHeikinAshiScalpingStrategy(HeikinAshiScalpingStrategy):
    """Alpha HA 상승추세 눌림목 재개 전략.

    완료된 일봉과 실제 OHLC를 사용해 상승 EMA200 안의 조정 종료를 찾고,
    Double HA 확인 후 3봉 이내 실제 신호봉 고점 돌파에서 진입합니다.
    """
    """알파 하이킨아시 스캘핑 전략

    참고 영상의 핵심 규칙을 구현한 두 번 평균 처리 하이킨아시 색상 반전
    전략입니다. 알파 하이킨아시가 상승색으로 전환되고 EMA 추세, RSI 50선,
    반전 캔들이 함께 확인될 때 매수 후보 점수를 부여합니다.
    """
