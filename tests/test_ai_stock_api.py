# -*- coding: utf-8 -*-
"""AI스톡 API 테스트 (§17): 라우트 등록·envelope·market 필터.

httpx/TestClient 미사용. 라우터 함수를 직접 호출하는 기존 테스트 패턴을 따른다.
"""
import unittest
import asyncio

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError

import src.dashboard as dashboard
from src.dashboard import app
from src.dashboard.routes import ai_stock as route
from src.db.repository import init_db, connect_db
from src.db import ai_stock_repository as repo
from src.ai_stock import market_data
from src.ai_stock.constants import DATA_GOOD, WATCH_WATCHING, WATCH_CONFIRMED

_AI_TABLES = [
    "ai_stock_timing_signals", "ai_stock_execution_runs", "ai_stock_execution_plans",
    "ai_stock_performance", "ai_stock_watch_events", "ai_stock_watchlist",
    "ai_stock_candidates", "ai_stock_automation_policies", "ai_stock_scans",
]


def _uptrend(n=40):
    return [100.0 + i for i in range(n)]


class _Provider:
    def universe_items(self, market):
        if market == "KR":
            return [{"symbol": "005930", "name": "삼성", "instrument_type": "stock"}]
        return [{"symbol": "AAPL", "name": "Apple", "instrument_type": "stock"}]

    def quote(self, market, symbol):
        return {"price": 140.0}

    def daily_series(self, market, symbol):
        return _uptrend()

    def index_series(self, market):
        return {"KOSPI": _uptrend(), "S&P500": _uptrend()}


class AiStockApiTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with connect_db() as conn:
            for t in _AI_TABLES:
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        self._orig = market_data.get_provider()
        market_data.set_provider(_Provider())

    def tearDown(self):
        market_data.set_provider(self._orig)

    def _check_envelope(self, body, market=None):
        for key in ("ok", "market", "data", "meta", "safety", "errors"):
            self.assertIn(key, body)
        for sk in ("dry_run", "trading_env", "enable_live_trading", "require_approval"):
            self.assertIn(sk, body["safety"])
        if market:
            self.assertEqual(body["market"], market)

    def test_routes_registered(self):
        paths = {getattr(r, "path", "") for r in app.routes}
        self.assertIn("/ai-stock", paths)
        self.assertIn("/api/ai-stock/status", paths)
        self.assertIn("/api/ai-stock/candidates", paths)

    def test_status_envelope(self):
        self._check_envelope(route.ai_stock_status())

    def test_candidates_market_filter(self):
        self._check_envelope(
            route.ai_stock_candidates(market="KR", scan_id=None, decision=None, min_score=None),
            market="KR",
        )

    def test_invalid_market_400(self):
        with self.assertRaises(HTTPException) as ctx:
            route.ai_stock_candidates(market="XX", scan_id=None, decision=None, min_score=None)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_http_error_uses_ai_stock_envelope(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/ai-stock/candidates",
            "query_string": b"market=XX",
            "headers": [],
        }
        request = Request(scope)
        response = asyncio.run(
            dashboard._http_exception_handler(request, HTTPException(status_code=400, detail="invalid market"))
        )
        self.assertEqual(response.status_code, 400)
        body = response.body.decode("utf-8")
        import json
        body = json.loads(body)
        self._check_envelope(body, market="XX")
        self.assertFalse(body["ok"])
        self.assertTrue(body["errors"])

    def test_validation_error_uses_ai_stock_envelope(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/ai-stock/timing-signals",
            "query_string": b"market=KR",
            "headers": [],
        }
        request = Request(scope)
        exc = RequestValidationError([{
            "loc": ("query", "candidate_id"),
            "msg": "Input should be a valid integer",
            "type": "int_parsing",
        }])
        response = asyncio.run(dashboard._request_validation_exception_handler(request, exc))
        self.assertEqual(response.status_code, 422)
        import json
        body = json.loads(response.body.decode("utf-8"))
        self._check_envelope(body, market="KR")
        self.assertFalse(body["ok"])
        self.assertIn("candidate_id", body["errors"][0])

    def test_scan_then_candidates(self):
        body = route.ai_stock_create_scan({"market": "KR", "strategy_id": "ai_stock_default_v1"})
        self.assertGreaterEqual(body["data"]["summary"]["candidate_count"], 1)
        cands = route.ai_stock_candidates(market="KR", scan_id=None, decision=None, min_score=None)
        self.assertTrue(cands["data"]["candidates"])

    def test_duplicate_scan_409(self):
        repo.create_scan(market="KR", strategy_id="ai_stock_default_v1")
        with self.assertRaises(HTTPException) as ctx:
            route.ai_stock_create_scan({"market": "KR", "strategy_id": "ai_stock_default_v1"})
        self.assertEqual(ctx.exception.status_code, 409)

    def test_overview_schema_same_for_markets(self):
        for m in ("KR", "US"):
            body = route.ai_stock_overview(market=m)
            self._check_envelope(body, market=m)
            self.assertIn("regimes", body["data"])

    def test_watchlist_add_and_remove(self):
        route.ai_stock_create_scan({"market": "KR", "strategy_id": "ai_stock_default_v1"})
        cand = route.ai_stock_candidates(market="KR", scan_id=None, decision=None, min_score=None)["data"]["candidates"][0]
        cid = cand["candidate_id"]
        route.ai_stock_watchlist_add({"candidate_id": cid})
        listing = route.ai_stock_watchlist(market="KR", status=None)
        self.assertEqual(listing["data"]["count"], 1)
        route.ai_stock_watchlist_remove(cid)
        listing2 = route.ai_stock_watchlist(market="KR", status=None)
        self.assertEqual(listing2["data"]["count"], 0)

    def test_briefing_detail_api(self):
        generated = route.ai_stock_briefings_generate(market="KR", period="daily")
        key = generated["data"]["key"]
        detail = route.ai_stock_briefing_detail("daily", key, market="KR")
        self._check_envelope(detail, market="KR")
        self.assertEqual(detail["data"]["key"], key)

    def test_strategy_validate_and_select_api(self):
        from src.db.strategy_repository import save_ai_strategies

        save_ai_strategies([{
            "id": "ai_stock_test_strategy",
            "name": "AI Stock Test",
            "model": "gpt-5-mini",
            "status": "approved",
            "profile": {"weights": {"rule": 1.0}},
            "strategy_version": 1,
        }])
        validated = route.ai_stock_strategy_validate("ai_stock_test_strategy", market="KR")
        self._check_envelope(validated, market="KR")
        self.assertEqual(validated["data"]["status"], "passed")
        selected = route.ai_stock_strategy_select("ai_stock_test_strategy", {"market": "KR"})
        self._check_envelope(selected, market="KR")
        self.assertTrue(selected["data"]["selected"])

    def test_policy_api_includes_operational_risk_flags(self):
        from src.ai_stock.automation_service import set_policy

        set_policy("ai_stock_default_v1", "KR", {
            "automation_level": 6,
            "auto_approve": 1,
            "auto_execute": 0,
            "allow_fallback_trade": 1,
            "allow_stale_data_trade": 1,
        })
        body = route.ai_stock_policies(market="KR", strategy_id="ai_stock_default_v1")
        self._check_envelope(body, market="KR")
        policy = body["data"]["policy"]
        self.assertIn("auto_approval_enabled", policy["risk_flags"])
        self.assertIn("fallback_trade_allowed", policy["risk_flags"])
        self.assertIn("stale_trade_allowed", policy["risk_flags"])
        self.assertTrue(policy["requires_live_guard_for_execute"])

    def test_automation_runs_api_returns_recent_operational_history(self):
        repo.log_execution_run({
            "strategy_id": "ai_stock_default_v1",
            "market": "KR",
            "run_type": "scheduled",
            "automation_level": 6,
            "status": "blocked",
            "blocked_stage": "execute",
            "blocked_reason": "live_guard",
        })

        body = route.ai_stock_runs(market="KR", strategy_id="ai_stock_default_v1")
        self._check_envelope(body, market="KR")
        self.assertEqual(body["data"]["count"], 1)
        run = body["data"]["runs"][0]
        self.assertEqual(run["blocked_stage"], "execute")
        self.assertEqual(run["blocked_reason"], "live_guard")

    def test_queue_approval_api_updates_plan(self):
        from src.ai_stock import watchlist_service, execution_plan_service
        from src.ai_stock.freshness import now
        from src.ai_stock.automation_service import set_policy
        from src.db.strategy_repository import save_ai_strategies

        save_ai_strategies([{
            "id": "ai_stock_default_v1",
            "name": "AI Stock Default",
            "model": "gpt-5-mini",
            "status": "approved",
            "profile": {"weights": {"rule": 1.0}},
            "strategy_version": 1,
        }])
        set_policy("ai_stock_default_v1", "KR", {"automation_level": 5})

        cid = repo.save_candidate({
            "scan_id": 1, "market": "KR", "symbol": "005930", "name": "삼성",
            "strategy_id": "ai_stock_default_v1", "current_price": 70000,
            "rule_score": 70, "technical_score": 75, "momentum_score": 70,
            "narrative_score": 60, "ai_score": 0, "risk_score": 20,
            "final_score": 80, "decision": "strong_watch", "data_quality": DATA_GOOD,
            "data_as_of": now().isoformat(),
        })
        watchlist_service.register(repo.get_candidate(cid))
        watchlist_service.transition(cid, WATCH_WATCHING)
        watchlist_service.transition(cid, WATCH_CONFIRMED)
        plan = execution_plan_service.create_plan(cid, options={"entry_price": 70000, "stop_price": 66000})
        body = route.ai_stock_queue_approval(plan["id"])
        self._check_envelope(body, market="KR")
        updated = body["data"]["plan"]
        self.assertEqual(updated["approval_market"], "KR")
        self.assertEqual(updated["approval_db"], "main")
        self.assertEqual(updated["approval_status"], "pending")
        with connect_db() as conn:
            conn.execute("DELETE FROM approvals WHERE id=?", (body["data"]["approval_id"],))
            conn.commit()

    def test_queue_approval_api_blocks_when_policy_level_too_low(self):
        from src.ai_stock import watchlist_service, execution_plan_service
        from src.ai_stock.freshness import now
        from src.ai_stock.automation_service import set_policy
        from src.db.strategy_repository import save_ai_strategies

        save_ai_strategies([{
            "id": "ai_stock_default_v1",
            "name": "AI Stock Default",
            "model": "gpt-5-mini",
            "status": "approved",
            "profile": {"weights": {"rule": 1.0}},
            "strategy_version": 1,
        }])
        set_policy("ai_stock_default_v1", "KR", {"automation_level": 4})
        cid = repo.save_candidate({
            "scan_id": 1, "market": "KR", "symbol": "005930", "name": "Samsung",
            "strategy_id": "ai_stock_default_v1", "current_price": 70000,
            "rule_score": 70, "technical_score": 75, "momentum_score": 70,
            "narrative_score": 60, "ai_score": 0, "risk_score": 20,
            "final_score": 80, "decision": "strong_watch", "data_quality": DATA_GOOD,
            "data_as_of": now().isoformat(),
        })
        watchlist_service.register(repo.get_candidate(cid))
        watchlist_service.transition(cid, WATCH_WATCHING)
        watchlist_service.transition(cid, WATCH_CONFIRMED)
        plan = execution_plan_service.create_plan(cid, options={"entry_price": 70000, "stop_price": 66000})

        with self.assertRaises(HTTPException) as ctx:
            route.ai_stock_queue_approval(plan["id"])
        self.assertEqual(ctx.exception.status_code, 409)
        runs = repo.list_execution_runs(market="KR", strategy_id="ai_stock_default_v1")
        self.assertTrue(any(r.get("blocked_stage") == "manual_approval" for r in runs))


if __name__ == "__main__":
    unittest.main()
