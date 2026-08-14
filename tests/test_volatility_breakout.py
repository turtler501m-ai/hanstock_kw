import unittest
from src.strategy.volatility_breakout import VolatilityBreakoutStrategy

class TestVolatilityBreakoutStrategy(unittest.TestCase):
    def setUp(self):
        self.strategy = VolatilityBreakoutStrategy(k=0.5)

    def test_calculate_target_price_with_kis_format(self):
        # KIS 일봉 포맷 데이터 (stck_hgpr: 고가, stck_lwpr: 저가, stck_clpr: 종가)
        daily_data = [
            {
                "stck_hgpr": "80000",
                "stck_lwpr": "78000",
                "stck_clpr": "79000"
            }
        ]
        # Range = 80000 - 78000 = 2000
        # Target = 79000 + (2000 * 0.5) = 80000
        target = self.strategy.calculate_target_price(daily_data)
        self.assertEqual(target, 80000.0)

    def test_calculate_target_price_with_yfinance_format(self):
        # yfinance 일봉 포맷 데이터 (High: 고가, Low: 저가, Close: 종가)
        daily_data = [
            {
                "High": 50000.0,
                "Low": 48000.0,
                "Close": 49000.0
            }
        ]
        # Range = 50000 - 48000 = 2000
        # Target = 49000 + (2000 * 0.5) = 50000
        target = self.strategy.calculate_target_price(daily_data)
        self.assertEqual(target, 50000.0)

    def test_calculate_target_price_with_empty_or_invalid_data(self):
        # 빈 데이터 에러 확인
        with self.assertRaises(ValueError):
            self.strategy.calculate_target_price([])

        # 비정상 데이터 에러 확인
        invalid_data = [{"High": 0.0, "Low": 0.0, "Close": 0.0}]
        with self.assertRaises(ValueError):
            self.strategy.calculate_target_price(invalid_data)

    def test_generate_signal_buy_and_hold_when_not_holding(self):
        target_price = 50000.0
        
        # 1. 현재가가 목표가 돌파 못함 -> hold 시그널
        signal = self.strategy.generate_signal(
            current_price=49900.0,
            target_price=target_price,
            holding_qty=0
        )
        self.assertEqual(signal["action"], "hold")
        self.assertIn("돌파 대기 중", signal["reason"])

        # 2. 현재가가 목표가와 같거나 돌파함 -> buy 시그널
        signal = self.strategy.generate_signal(
            current_price=50100.0,
            target_price=target_price,
            holding_qty=0
        )
        self.assertEqual(signal["action"], "buy")
        self.assertIn("변동성 돌파 완료", signal["reason"])
        self.assertEqual(signal["indicators"]["current_price"], 50100.0)

    def test_generate_signal_sell_and_hold_when_holding(self):
        # 1. 정상 보유 유지 중 -> hold 시그널
        signal = self.strategy.generate_signal(
            current_price=51000.0,
            target_price=50000.0,
            holding_qty=10,
            return_pct=2.0
        )
        self.assertEqual(signal["action"], "hold")
        self.assertIn("돌파 포지션 유지 중", signal["reason"])

        # 2. 수익률이 설정된 손절선(-15.0%) 이하로 추락함 -> sell 시그널 (손절)
        signal = self.strategy.generate_signal(
            current_price=42000.0,
            target_price=50000.0,
            holding_qty=10,
            return_pct=-16.0
        )
        self.assertEqual(signal["action"], "sell")
        self.assertIn("손절 감지", signal["reason"])
        self.assertEqual(signal["indicators"]["return_pct"], -16.0)

if __name__ == "__main__":
    unittest.main()
