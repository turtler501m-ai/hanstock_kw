import numpy as np
import yfinance as yf
from src.utils.logger import logger
from src.mistock import db as mistock_db
from src.strategy.portfolio_backtest import simulate_target_portfolio

def run_mistock_backtest(strategy_profile: dict, days: int = 250) -> dict:
    from src.online_access import require_online_access

    require_online_access("Mistock backtest data download")
    """Runs a real historical backtest using yfinance US stock data for Mistock watchlist."""
    rows = mistock_db.rows("SELECT symbol FROM watchlist")
    symbols = [r["symbol"] for r in rows] if rows else ["AAPL", "MSFT", "TSLA", "AMZN", "GOOG"]
    
    try:
        data = yf.download(
            symbols,
            period="2y",
            progress=False,
            group_by="ticker",
            auto_adjust=True,
        )
        if data.empty:
            raise ValueError("yfinance returned empty dataset")
    except Exception as e:
        logger.error(f"[MISTOCK BACKTEST] Failed to download data: {e}")
        return {"success": False, "message": f"Data download failed: {str(e)}"}
        
    dates = sorted(data.index.unique())
    if len(dates) < days + 60:
        days = len(dates) - 60
        if days <= 10:
            return {"success": False, "message": "Not enough historical data for backtesting"}
            
    initial_capital = 10000.0
    backtest_dates = dates[-days:]
    target_weights_by_day = []
    returns_by_day = []
    
    for step in range(len(backtest_dates) - 1):
        curr_date = backtest_dates[step]
        next_date = backtest_dates[step + 1]
        
        scores = {}
        for s in symbols:
            if s not in data.columns.get_level_values(0):
                scores[s] = 0.0
                continue
            prices_df = data[s]
            hist_prices = prices_df.loc[:curr_date]
            if len(hist_prices) < 60:
                scores[s] = 0.0
                continue
                
            closes = hist_prices["Close"].dropna().tolist()
            highs = hist_prices["High"].dropna().tolist()
            volumes = hist_prices["Volume"].dropna().tolist()
            if len(closes) < 60 or len(highs) < 60:
                scores[s] = 0.0
                continue
                
            current = closes[-1]
            from src.strategy.seven_split import calc_strategy_profile
            profile = calc_strategy_profile(closes, highs, volumes, strategy_model=strategy_profile.get("model") or "")
            rule_score = float(profile["score"])
            sma60 = profile["sma60"]
            macd_hist = profile["macd_hist"]
            
            trend = ((current / sma60) - 1) if sma60 > 0 else 0
            vol = np.std(np.diff(closes) / closes[:-1]) if len(closes) > 1 else 0.02
            raw_score = rule_score + (trend * 10) + max(macd_hist, 0) / max(current, 1) * 100
            risk_adjusted = max(0.0, raw_score - (vol * 20))
            scores[s] = risk_adjusted
            
        target_weights = {}
        score_sum = sum(scores.values())
        for s in symbols:
            target_weights[s] = scores[s] / score_sum if score_sum > 0 else 0.0
            
        cash_buffer = float(strategy_profile.get("cash_buffer", 0.02))
        max_single_weight = float(strategy_profile.get("max_single_weight", 0.3))
        investable = 1.0 - cash_buffer
        
        normalized_w = {}
        w_sum = sum(target_weights.values())
        for s in symbols:
            raw_w = target_weights.get(s, 0.0)
            normalized_w[s] = min(max_single_weight, investable * (raw_w / w_sum if w_sum > 0 else 0.0))
            
        period_returns = {}
        for s in symbols:
            try:
                if s not in data.columns.get_level_values(0):
                    continue
                curr_price = float(data[s].loc[curr_date, "Close"])
                next_price = float(data[s].loc[next_date, "Close"])
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
        sell_tax_bps=float(backtest_config.get("sell_tax_bps", 0.0)),
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
    from src.mistock.strategy import strategy_profile as mistock_profile

    walk_forward = {}
    for symbol in symbols[:10]:
        if symbol not in data.columns.get_level_values(0):
            continue
        frame = data[symbol]
        closes = frame["Close"].dropna().tolist()
        highs = frame["High"].dropna().tolist()
        volumes = frame["Volume"].dropna().tolist()
        walk_forward[symbol] = run_technical_walk_forward(
            closes,
            highs,
            volumes,
            profile_builder=lambda p, h, v: mistock_profile(p, h, v),
            min_score=float(strategy_profile.get("min_score", 4)),
            stop_loss_pct=abs(float(strategy_profile.get("stop_loss_pct", 12))),
            trailing_activation_pct=float(strategy_profile.get("trailing_stop_activation_pct", 10)),
            trailing_stop_pct=float(strategy_profile.get("trailing_stop_pct", 7)),
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
        "message": "Cost-adjusted US stock backtest completed using adjusted watchlist prices",
    }
