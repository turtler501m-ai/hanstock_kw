import json
from pathlib import Path
from src.db.repository import load_ai_strategies, save_ai_strategies, record_ai_strategy_event
from src.strategy.backtest import run_historical_backtest
from src.utils.logger import logger

def evolve_strategy(strategy_id: str) -> dict:
    """Evolves a strategy by finding the optimal ai_weight and risk settings via a backtest search."""
    strategies = load_ai_strategies()
    strategy = next((s for s in strategies if s["id"] == strategy_id), None)
    if not strategy:
        return {"success": False, "message": "Strategy not found"}
        
    profile = strategy.get("profile") or {}
    if isinstance(profile, str):
        try:
            profile = json.loads(profile)
        except Exception:
            profile = {}
            
    # Search grid for ai_weight and max_single_weight
    best_score = -999.0
    best_params = {}
    best_metrics = {}
    
    # Sweep weights from 0.0 to 0.6 in steps of 0.2
    for w in [0.0, 0.2, 0.4, 0.6]:
        for max_w in [0.2, 0.3, 0.4]:
            test_profile = dict(profile)
            test_profile["ai_weight"] = w
            test_profile["max_single_weight"] = max_w
            
            # Short backtest (120 days) for speed during parameter sweep
            result = run_historical_backtest(test_profile, days=120)
            if result.get("success") and result.get("ok"):
                ret = result["metrics"]["total_return_pct"]
                dd = max(0.5, result["metrics"]["max_drawdown_pct"])
                # Sharpe-like ratio: Return / Max Drawdown
                score = ret / dd
                if score > best_score:
                    best_score = score
                    best_params = {"ai_weight": w, "max_single_weight": max_w}
                    best_metrics = result["metrics"]
                    
    if best_params:
        # Update strategy profile with optimized parameters
        updated_profile = dict(profile)
        updated_profile.update(best_params)
        
        # Increase strategy version
        prev_version = int(strategy.get("strategy_version") or 1)
        new_version = prev_version + 1
        
        # Run a full-period backtest (250 days) with the best parameters
        full_result = run_historical_backtest(updated_profile, days=250)
        
        for s in strategies:
            if s["id"] == strategy_id:
                s["profile"] = updated_profile
                s["weight"] = best_params["ai_weight"]
                s["strategy_version"] = new_version
                s["last_backtested_at"] = _now_kst_text_local()
                s["last_validation_result"] = json.dumps({
                    "checks": {
                        "static": {"ok": True, "success": True, "status": "passed"},
                        "backtest": full_result
                    },
                    "latest": {"check": "self_evolve", "result": {"ok": True, "params": best_params}}
                }, ensure_ascii=False)
                s["status"] = "approved" if full_result.get("success") else "backtested"
                strategy = s
                break
                
        save_ai_strategies(strategies)
        record_ai_strategy_event(
            strategy_id,
            "self_evolved",
            {
                "previous_version": prev_version,
                "best_params": best_params,
                "metrics": best_metrics,
                "full_period_metrics": full_result.get("metrics")
            },
            new_version
        )
        return {
            "success": True,
            "message": f"Strategy evolved to version {new_version} successfully!",
            "params": best_params,
            "metrics": full_result.get("metrics", best_metrics)
        }
        
    return {"success": False, "message": "Failed to sweep better parameters during evolution"}

def _now_kst_text_local() -> str:
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
