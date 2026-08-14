import gymnasium as gym
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import pandas as pd
from typing import List, Dict

def calc_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    exp1 = prices.ewm(span=fast, adjust=False).mean()
    exp2 = prices.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    sig = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - sig
    return macd, sig, hist

class SimpleStockTradingEnv(gym.Env):
    """
    A Gym environment for single-stock target weight rating.
    Calculates weights independently per stock, avoiding dimension mismatch on Watchlist changes.
    """
    def __init__(self, tickers: List[str], start_date: str, end_date: str, initial_capital: float = 1e7):
        super(SimpleStockTradingEnv, self).__init__()
        self.tickers = tickers
        self.initial_capital = initial_capital
        self.start_date = start_date
        self.end_date = end_date
        
        self.data = self._fetch_and_prepare_data(tickers, start_date, end_date)
        
        # State space: [Price, RSI, MACD_hist, SMA_trend] (fixed at 4 dimensions)
        self.observation_space = gym.spaces.Box(low=-np.inf, high=np.inf, shape=(4,), dtype=np.float32)
        
        # Action space: Target weight rating [-1, 1] for a single stock
        self.action_space = gym.spaces.Box(low=-1.0, high=1.0, shape=(1,), dtype=np.float32)
        
        self.current_ticker = None
        self.current_step = 0
        self.portfolio_value = self.initial_capital
        self.weight = 0.0

    def _fetch_and_prepare_data(self, tickers: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
        from src.online_access import require_online_access

        require_online_access("yfinance RL data download")
        print(f"Downloading data for {tickers} from {start_date} to {end_date}...")
        data_dict = {}
        for ticker in tickers:
            symbol = f"{ticker}.KS"  # KOSPI format
            try:
                df = yf.download(symbol, start=start_date, end=end_date, progress=False)
                if df.empty:
                    continue
                
                # Handle pandas MultiIndex if yfinance returns it
                if isinstance(df.columns, pd.MultiIndex):
                    df = df.droplevel(1, axis=1)
                    
                close_col = 'Close'
                if close_col not in df.columns:
                    print(f"Warning: Close column not found for {ticker}")
                    continue
                    
                df['RSI'] = calc_rsi(df[close_col])
                macd, _, hist = calc_macd(df[close_col])
                df['MACD_hist'] = hist
                sma60 = df[close_col].rolling(window=60, min_periods=60).mean()
                df['SMA_trend'] = (df[close_col] - sma60) / sma60
                
                df = df.dropna()
                if not df.empty:
                    data_dict[ticker] = df
            except Exception as e:
                print(f"Failed to prepare data for {ticker}: {e}")
                
        return data_dict
        
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        valid_tickers = list(self.data.keys())
        if not valid_tickers:
            raise ValueError("No valid tickers with data found for training.")
        
        np_random = np.random.default_rng(seed)
        self.current_ticker = np_random.choice(valid_tickers)
        self.df = self.data[self.current_ticker]
        self.dates = sorted(list(self.df.index.unique()))
        
        self.current_step = 0
        self.portfolio_value = self.initial_capital
        self.weight = 0.0
        
        return self._get_obs(), {}
        
    def _get_obs(self):
        current_date = self.dates[self.current_step]
        row = self.df.loc[current_date]
        
        try:
            # Handle cases where row might be a series or dataframe (if duplicate dates)
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            price = float(row['Close'])
            rsi = float(row['RSI'])
            macd = float(row['MACD_hist'])
            trend = float(row['SMA_trend'])
            
            if np.isnan(price) or np.isnan(rsi) or np.isnan(macd) or np.isnan(trend):
                obs = [0.0, 0.5, 0.0, 0.0]
            else:
                obs = [price / 100000.0, rsi / 100.0, macd / 1000.0, trend]
        except Exception:
            obs = [0.0, 0.5, 0.0, 0.0]
            
        return np.array(obs, dtype=np.float32)
        
    def step(self, action):
        current_date = self.dates[self.current_step]
        
        # Map action [-1, 1] to target weight [0.0, 0.3] (maximum 30% allocation for safety)
        target_weight = float((action[0] + 1.0) / 2.0 * 0.3)
        
        stock_return = 0.0
        if self.current_step < len(self.dates) - 1:
            next_date = self.dates[self.current_step + 1]
            try:
                curr_row = self.df.loc[current_date]
                next_row = self.df.loc[next_date]
                if isinstance(curr_row, pd.DataFrame):
                    curr_row = curr_row.iloc[0]
                if isinstance(next_row, pd.DataFrame):
                    next_row = next_row.iloc[0]
                    
                curr_price = float(curr_row['Close'])
                next_price = float(next_row['Close'])
                if curr_price > 0 and not np.isnan(curr_price) and not np.isnan(next_price):
                    stock_return = (next_price / curr_price) - 1.0
            except (KeyError, IndexError, ValueError):
                pass
                
        # Calculate return based on target weight allocation
        portfolio_return = target_weight * stock_return
        self.portfolio_value *= (1.0 + portfolio_return)
        reward = portfolio_return * 100.0
        
        self.current_step += 1
        terminated = self.current_step >= len(self.dates) - 1
        truncated = False
        
        return self._get_obs(), reward, terminated, truncated, {"portfolio_value": self.portfolio_value, "ticker": self.current_ticker}
