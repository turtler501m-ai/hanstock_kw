from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from src.dashboard.routes import market_regime


class _Service:
    def current(self):
        return {"market": "KR", "regime": "bull", "quality": "good"}

    def history(self, days=30):
        return [{"days": days, "regime": "bull"}]

    def diagnostics(self):
        return {"available": True, "checks": []}

    def refresh(self, market="KR"):
        return {"market": market, "regime": "sideways_low_vol", "quality": "good"}


class MarketRegimeDashboardApiTests(unittest.TestCase):
    @patch("src.dashboard.routes.market_regime._service", return_value=_Service())
    def test_read_endpoints(self, _mock_service):
        self.assertEqual(market_regime.current_market_regime()["regime"], "bull")
        self.assertEqual(market_regime.market_regime_history(7)[0]["days"], 7)
        self.assertTrue(market_regime.market_regime_diagnostics()["available"])

    @patch("src.dashboard.routes.market_regime._service", return_value=_Service())
    def test_manual_refresh_is_kr_only(self, _mock_service):
        response = market_regime.refresh_market_regime({})
        self.assertEqual(response["market"], "KR")

    @patch("src.dashboard.routes.market_regime._service")
    def test_collection_failure_is_reported_as_service_unavailable(self, service):
        service.return_value.refresh.side_effect = RuntimeError("broker down")
        with self.assertRaises(HTTPException) as raised:
            market_regime.refresh_market_regime({})
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("broker down", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
