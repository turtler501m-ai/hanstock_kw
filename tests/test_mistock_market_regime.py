import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.dashboard.routes import mistock


class _FakeClose:
    ndim = 1

    def __init__(self, values):
        self._values = values

    def dropna(self):
        return self

    def items(self):
        return iter(self._values)


class _FakeFrame:
    empty = False

    def __init__(self, values):
        self._close = _FakeClose(values)

    def __getitem__(self, key):
        if key != "Close":
            raise KeyError(key)
        return self._close


def _series(start, step=1.0, count=61):
    return [
        {"date": f"2026-06-{index + 1:02d}", "close": start + step * index}
        for index in range(count)
    ]


class MistockMarketRegimeTests(unittest.TestCase):
    def setUp(self):
        self.original_cache = mistock._MISTOCK_INDEX_CACHE

    def tearDown(self):
        mistock._MISTOCK_INDEX_CACHE = self.original_cache

    def test_route_registration_and_response_contract(self):
        routes = {
            (method, route.path)
            for route in mistock.router.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("GET", "/api/mistock/market-regime"), routes)

        rows = {key: _series(100) for key in mistock._MISTOCK_INDEX_TICKERS}
        rows["VIX"] = _series(20, 0)
        with patch.object(mistock, "_load_mistock_index_rows", return_value=rows), patch(
            "src.mistock.scheduler.is_us_market_open", return_value=True
        ):
            payload = mistock.mistock_market_regime()

        self.assertEqual(payload["source"], "yfinance_5m_cache")
        self.assertEqual(payload["cache_ttl_seconds"], 300)
        self.assertEqual(payload["regime"], "risk_on")
        self.assertEqual(payload["risk_multiplier"], 1.0)
        self.assertTrue(payload["market_session"]["open"])
        self.assertEqual(
            {row["key"] for row in payload["assets"]},
            set(mistock._MISTOCK_INDEX_TICKERS),
        )
        for row in payload["assets"]:
            self.assertIn("as_of", row)
            self.assertIn("data_quality", row)

    def test_projection_calculates_sma_trend_risk_and_partial_data(self):
        rows = {
            "QQQ": _series(100),
            "SPY": _series(200),
            "SOXX": _series(300),
            "VIX": _series(20, 0),
            "USDKRW": _series(1350, 0),
        }
        result = mistock._mistock_market_regime_projection(rows, False)
        assets = {row["key"]: row for row in result["assets"]}

        self.assertEqual(assets["QQQ"]["trend"], "up")
        self.assertAlmostEqual(assets["QQQ"]["sma20"], 150.5)
        self.assertAlmostEqual(assets["QQQ"]["sma60"], 130.5)
        self.assertEqual(assets["sp500"]["trend"], "unknown")
        self.assertEqual(assets["sp500"]["data_quality"], "unavailable")
        self.assertEqual(result["regime"], "risk_on")
        self.assertEqual(result["risk_multiplier"], 1.0)
        self.assertFalse(result["market_session"]["open"])
        self.assertTrue(result["partial"])

        rows["VIX"] = _series(31, 0)
        risk_off = mistock._mistock_market_regime_projection(rows, True)
        self.assertEqual(risk_off["regime"], "risk_off")
        self.assertEqual(risk_off["risk_multiplier"], 0.4)

    def test_loader_isolates_ticker_failure_and_reuses_cache(self):
        calls = []

        def download(ticker, **_kwargs):
            calls.append(ticker)
            if ticker == "SOXX":
                raise RuntimeError("feed unavailable")
            return _FakeFrame([("2026-08-20", 100), ("2026-08-21", 101)])

        mistock._MISTOCK_INDEX_CACHE = (0.0, {})
        fake_yfinance = SimpleNamespace(download=download)
        with patch.dict(sys.modules, {"yfinance": fake_yfinance}), patch(
            "src.online_access.require_online_access", return_value=None
        ), patch.object(mistock.time, "monotonic", side_effect=[1000.0, 1001.0, 1002.0]):
            first = mistock._load_mistock_index_rows()
            second = mistock._load_mistock_index_rows()

        self.assertNotIn("SOXX", first)
        self.assertIn("QQQ", first)
        self.assertIs(second, first)
        self.assertEqual(len(calls), len(mistock._MISTOCK_INDEX_TICKERS))

    def test_multi_currency_realized_pnl_includes_fx_fees_and_tax(self):
        pnl = mistock._mistock_insight_pnl([
            {"symbol": "AAPL", "action": "buy", "qty": 2, "price": 100,
             "exchange_rate": 1300, "fee": 1, "tax": 0, "ok": 1, "order_status": "filled"},
            {"symbol": "AAPL", "action": "sell", "qty": 1, "price": 120,
             "exchange_rate": 1400, "fee": 2, "tax": .5, "ok": 1, "order_status": "filled"},
        ], [])

        self.assertEqual(pnl["realized"]["value"], 20.0)
        self.assertEqual(pnl["realized_krw"]["value"], 38000.0)
        self.assertEqual(pnl["fx_effect_krw"], 10000.0)
        self.assertEqual(pnl["fees_usd"], 3.0)
        self.assertEqual(pnl["fees_krw"], 4100.0)
        self.assertEqual(pnl["tax_usd"], .5)
        self.assertEqual(pnl["tax_krw"], 700.0)
        self.assertEqual(pnl["realized_net_usd"]["value"], 16.5)
        self.assertEqual(pnl["realized_net_krw"]["value"], 33200.0)

    def test_missing_exchange_rate_makes_krw_and_fx_partial_null(self):
        pnl = mistock._mistock_insight_pnl([
            {"symbol": "AAPL", "action": "buy", "qty": 1, "price": 100,
             "exchange_rate": 1300, "ok": 1, "order_status": "filled"},
            {"symbol": "AAPL", "action": "sell", "qty": 1, "price": 110,
             "ok": 1, "order_status": "filled"},
        ], None)

        for key in ("realized_krw", "realized_net_krw", "fx_effect"):
            self.assertIsNone(pnl[key]["value"])
            self.assertEqual(pnl[key]["availability"], "partial")
            self.assertTrue(pnl[key]["reason"])
        self.assertIsNone(pnl["fees_krw"])
        self.assertIsNone(pnl["tax_krw"])
        self.assertIsNone(pnl["fx_effect_krw"])


if __name__ == "__main__":
    unittest.main()
