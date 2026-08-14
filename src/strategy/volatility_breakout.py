import math
from typing import Dict, Any, List
from src.config import config
from src.utils.logger import logger

class VolatilityBreakoutStrategy:
    """래리 윌리엄스 변동성 돌파 전략 (Larry Williams Volatility Breakout Strategy)"""

    def __init__(self, k: float = 0.5):
        self.k = k

    def calculate_target_price(self, daily_data: List[Dict[str, Any]]) -> float:
        """전일 가격 데이터(고가, 저가, 종가)를 바탕으로 당일 돌파 목표 매수가 계산
        
        daily_data는 날짜 오름차순으로 정렬되어 있으며, 마지막 요소가 전일 일봉이어야 합니다.
        """
        if not daily_data or len(daily_data) < 1:
            raise ValueError("[VBO] 돌파 타겟 계산을 위한 일봉 데이터가 부족합니다.")

        prev_day = daily_data[-1]
        
        # 키움/한국투자증권 API 키와 yfinance 키 호환성 처리
        high = float(prev_day.get("stck_hgpr", prev_day.get("High", 0)))
        low = float(prev_day.get("stck_lwpr", prev_day.get("Low", 0)))
        close = float(prev_day.get("stck_clpr", prev_day.get("Close", 0)))

        if high <= 0 or low <= 0 or close <= 0:
            raise ValueError(f"[VBO] 올바르지 않은 가격 데이터입니다. High={high}, Low={low}, Close={close}")

        # 변동폭(Range) = 전일 고가 - 전일 저가
        volt_range = high - low
        
        # 목표 매수가 = 당일 시가(또는 전일 종가로 대행) + (변동폭 * k)
        # 실전에서는 당일 아침 09:00 시가가 결정되면 시가 기준으로 잡는 것이 정확하지만,
        # 장중 실시간 모니터링 시에는 전일 종가를 기준 시가 대행으로 보편적 하이브리드 세팅을 합니다.
        target_price = close + (volt_range * self.k)
        
        logger.info(f"[VBO] 계산 완료 - 전일고가: {high:,.0f}, 전일저가: {low:,.0f}, 전일종가: {close:,.0f} | 변동폭: {volt_range:,.0f} -> 목표가: {target_price:,.0f}")
        return target_price

    def generate_signal(self, current_price: float, target_price: float, holding_qty: int, return_pct: float = 0.0) -> Dict[str, Any]:
        """실시간 주가를 비교하여 매수/매도/보유 시그널 생성
        
        - 미보유 상태에서 현재가가 목표가를 돌파하면 매수(buy) 신호 방출
        - 보유 상태에서 손절선(Stop-loss)에 도달하면 즉각 매도(sell) 신호 방출
        """
        # 1. 미보유 상태: 돌파 매수 타겟 감지
        if holding_qty == 0:
            if current_price >= target_price:
                return {
                    "action": "buy",
                    "reason": f"변동성 돌파 완료 (현재가: {current_price:,.0f} >= 목표가: {target_price:,.0f})",
                    "indicators": {
                        "target_price": target_price,
                        "current_price": current_price,
                        "strategy": "volatility_breakout"
                    }
                }
            return {
                "action": "hold",
                "reason": f"돌파 대기 중 (현재가: {current_price:,.0f} < 목표가: {target_price:,.0f})",
                "indicators": {
                    "target_price": target_price,
                    "current_price": current_price,
                    "strategy": "volatility_breakout"
                }
            }

        # 2. 보유 상태: 손절(Stop-loss) 안전장치 작동
        stop_loss_pct = float(getattr(config, "stop_loss_pct", -15.0) or -15.0)
        if return_pct <= stop_loss_pct:
            return {
                "action": "sell",
                "reason": f"변동성 돌파 보유분 손절 감지 (수익률: {return_pct:.1f}% <= 제한: {stop_loss_pct:.1f}%)",
                "indicators": {
                    "return_pct": return_pct,
                    "stop_loss_pct": stop_loss_pct,
                    "strategy": "volatility_breakout"
                }
            }

        # 3. 그 외 기본 보유 유지 (익일 아침 일괄 청산은 trader.py 스케줄러가 대행)
        return {
            "action": "hold",
            "reason": f"돌파 포지션 유지 중 (수익률: {return_pct:+.1f}%)",
            "indicators": {
                "return_pct": return_pct,
                "strategy": "volatility_breakout"
            }
        }
