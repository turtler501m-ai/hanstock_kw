from src.strategy.heikin_ashi_scalping import HeikinAshiScalpingStrategy


class AlphaHeikinAshiScalpingStrategy(HeikinAshiScalpingStrategy):
    """알파 하이킨아시 스캘핑 전략

    참고 영상의 핵심 규칙을 구현한 두 번 평균 처리 하이킨아시 색상 반전
    전략입니다. 알파 하이킨아시가 상승색으로 전환되고 EMA 추세, RSI 50선,
    반전 캔들이 함께 확인될 때 매수 후보 점수를 부여합니다.
    """
