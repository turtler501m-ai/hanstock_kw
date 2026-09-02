from pathlib import Path
import numpy as np
import yfinance as yf
from src.utils.logger import logger
from src.db.repository import load_watchlist_data
from src.strategy.portfolio_backtest import simulate_target_portfolio

def run_historical_backtest(strategy_profile: dict, days: int = 250) -> dict:
    from src.online_access import require_online_access

    require_online_access("historical backtest data download")
    """Runs a real historical backtest using yfinance data for watchlist symbols."""
    watchlist_data = load_watchlist_data()
    symbols = watchlist_data.get("symbols", ["005930", "000660", "035420", "005380", "035720"])
    
    # 1. Download historical data
    # Download data for past 2 years to have enough history for indicators (60 days)
    yf_symbols = [f"{s}.KS" for s in symbols]
    try:
        data = yf.download(
            yf_symbols,
            period="2y",
            progress=False,
            group_by="ticker",
            auto_adjust=True,
        )
        if data.empty:
            raise ValueError("yfinance returned empty dataset")
    except Exception as e:
        logger.error(f"[BACKTEST] Failed to download data: {e}")
        return {"success": False, "message": f"Data download failed: {str(e)}"}
        
    # Get index dates
    dates = sorted(data.index.unique())
    if len(dates) < days + 60:
        days = len(dates) - 60
        if days <= 10:
            return {"success": False, "message": "Not enough historical data for backtesting"}
            
    # Portfolio simulation parameters
    initial_capital = 10_000_000.0
    # Load trained model if available
    model = None
    ai_weight = float(strategy_profile.get("ai_weight", 0.0))
    if ai_weight > 0:
        try:
            from stable_baselines3 import PPO
            model_path = Path("data/trained_models/ppo_kr_stock.zip")
            if model_path.exists():
                model = PPO.load(str(model_path))
        except Exception:
            pass
            
    # Active backtest window (the last 'days' dates)
    backtest_dates = dates[-days:]
    
    target_weights_by_day = []
    returns_by_day = []
    
    # Run loop
    for step in range(len(backtest_dates) - 1):
        curr_date = backtest_dates[step]
        next_date = backtest_dates[step + 1]
        
        # Step 1: Calculate scores for each ticker
        scores = {}
        features = {}
        for s in symbols:
            yf_s = f"{s}.KS"
            # Get historical prices up to curr_date
            if yf_s not in data.columns.get_level_values(0):
                scores[s] = 0.0
                continue
            prices_df = data[yf_s]
            hist_prices = prices_df.loc[:curr_date]
            if len(hist_prices) < 60:
                scores[s] = 0.0
                continue
                
            closes = hist_prices["Close"].dropna().tolist()
            highs = hist_prices["High"].dropna().tolist()
            volumes = hist_prices["Volume"].dropna().tolist()
            
            if len(closes) < 60:
                scores[s] = 0.0
                continue
                
            # Rule base score (out of 5.0)
            current = closes[-1]
            from src.strategy.seven_split import calc_strategy_profile
            profile = calc_strategy_profile(closes, highs, volumes, strategy_model=strategy_profile.get("model") or "")
            rule_score = float(profile["score"])
            rsi = profile["rsi"]
            macd_info = {
                "hist": profile["macd_hist"],
                "bull_cross": profile["macd_bull_cross"],
                "bear_cross": profile["macd_bear_cross"],
            }
            sma60 = profile["sma60"]
            
            # RL score
            trend = ((current / sma60) - 1) if sma60 > 0 else 0
            vol = np.std(np.diff(closes) / closes[:-1]) if len(closes) > 1 else 0.02
            raw_score = rule_score + (trend * 10) + max(macd_info["hist"], 0) / max(current, 1) * 100
            risk_adjusted = max(0.0, raw_score - (vol * 20))
            
            scores[s] = risk_adjusted
            features[s] = [current / 100000.0, rsi / 100.0, macd_info["hist"] / 1000.0, trend]
            
        # Step 2: Determine target weights
        target_weights = {}
        score_sum = sum(scores.values())
        
        if model and ai_weight > 0:
            # Query PPO
            raw_ratings = {}
            for s in symbols:
                obs = features.get(s, [0.0, 0.5, 0.0, 0.0])
                try:
                    action, _ = model.predict(np.array(obs, dtype=np.float32), deterministic=True)
                    raw_ratings[s] = float(action[0])
                except Exception:
                    raw_ratings[s] = -1.0
            try:
                ratings_arr = np.array([raw_ratings[s] for s in symbols], dtype=np.float32)
                exp_r = np.exp(ratings_arr)
                softmax_w = exp_r / np.sum(exp_r)
                for i, s in enumerate(symbols):
                    target_weights[s] = float(softmax_w[i])
            except Exception:
                # Fallback to score proportional
                for s in symbols:
                    target_weights[s] = scores[s] / score_sum if score_sum > 0 else 0.0
        else:
            # Score proportional allocation
            for s in symbols:
                target_weights[s] = scores[s] / score_sum if score_sum > 0 else 0.0
                
        # Limit single weight and cash buffer
        cash_buffer = float(strategy_profile.get("cash_buffer", 0.02))
        max_single_weight = float(strategy_profile.get("max_single_weight", 0.3))
        investable = 1.0 - cash_buffer
        
        normalized_w = {}
        w_sum = sum(target_weights.values())
        for s in symbols:
            raw_w = target_weights.get(s, 0.0)
            normalized_w[s] = min(max_single_weight, investable * (raw_w / w_sum if w_sum > 0 else 0.0))
            
        # Step 3: Build executable close-to-close returns for the simulator.
        period_returns = {}
        for s in symbols:
            yf_s = f"{s}.KS"
            try:
                if yf_s not in data.columns.get_level_values(0):
                    continue
                curr_price = float(data[yf_s].loc[curr_date, "Close"])
                next_price = float(data[yf_s].loc[next_date, "Close"])
                if curr_price > 0:
                    period_returns[s] = (next_price / curr_price) - 1.0
            except KeyError:
                pass
        target_weights_by_day.append(normalized_w)
        returns_by_day.append(period_returns)

    backtest_config = (
        strategy_profile.get("backtest")
        if isinstance(strategy_profile.get("backtest"), dict)
        else {}
    )
    simulation = simulate_target_portfolio(
        target_weights_by_day,
        returns_by_day,
        initial_capital=initial_capital,
        commission_bps=float(backtest_config.get("commission_bps", 3.0)),
        slippage_bps=float(backtest_config.get("slippage_bps", 5.0)),
        market_impact_bps=float(backtest_config.get("market_impact_bps", 2.0)),
        sell_tax_bps=float(backtest_config.get("sell_tax_bps", 18.0)),
        rebalance_threshold=float(backtest_config.get("rebalance_threshold", 0.02)),
    )
    metrics = simulation["metrics"]
    criteria = {
        "min_trade_count": int(backtest_config.get("min_trade_count", 10)),
        "min_win_rate": float(backtest_config.get("min_win_rate", 0.45)),
        "min_profit_factor": float(backtest_config.get("min_profit_factor", 1.05)),
        "max_drawdown_pct": float(backtest_config.get("max_drawdown_pct", 15.0)),
        "min_total_return_pct": float(backtest_config.get("min_total_return_pct", 0.0)),
        "costs_required": True,
    }
    passed = (
        metrics["trade_count"] >= criteria["min_trade_count"]
        and metrics["win_rate"] >= criteria["min_win_rate"]
        and metrics["profit_factor"] >= criteria["min_profit_factor"]
        and metrics["max_drawdown_pct"] <= criteria["max_drawdown_pct"]
        and metrics["total_return_pct"] > criteria["min_total_return_pct"]
    )
    
    from src.strategy.technical_backtest import run_technical_walk_forward
    from src.strategy.seven_split import calc_strategy_profile

    walk_forward = {}
    for symbol in symbols[:10]:
        yf_symbol = f"{symbol}.KS"
        if yf_symbol not in data.columns.get_level_values(0):
            continue
        frame = data[yf_symbol]
        closes = frame["Close"].dropna().tolist()
        highs = frame["High"].dropna().tolist()
        volumes = frame["Volume"].dropna().tolist()
        walk_forward[symbol] = run_technical_walk_forward(
            closes,
            highs,
            volumes,
            profile_builder=lambda p, h, v: calc_strategy_profile(p, h, v),
            min_score=float(strategy_profile.get("min_score", 4)),
            stop_loss_pct=abs(float(strategy_profile.get("stop_loss_pct", 10))),
            trailing_activation_pct=float(strategy_profile.get("trailing_stop_activation_pct", 10)),
            trailing_stop_pct=float(strategy_profile.get("trailing_stop_pct", 6)),
        )

    return {
        "success": True,
        "ok": True,
        "status": "passed" if passed else "failed",
        "metrics": metrics,
        "costs": simulation["costs"],
        "criteria": criteria,
        "equity_curve": simulation["equity_curve"],
        "dates": [d.strftime("%Y-%m-%d") for d in backtest_dates],
        "technical_walk_forward": walk_forward,
        "message": "Cost-adjusted historical backtest completed using adjusted watchlist prices",
    }
