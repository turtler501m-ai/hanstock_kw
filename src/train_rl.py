import os
import sys
from pathlib import Path
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

# Add project root to sys.path to allow running as a script directly
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rl_env import SimpleStockTradingEnv
from src.trader import WATCHLIST

def train():
    # Environment Settings
    start_date = "2021-01-01"
    # End date should be recent, assuming current year is 2026.
    end_date = "2026-04-20" 
    
    print(f"Initializing Env for {len(WATCHLIST)} symbols...")
    
    env_kwargs = {
        "tickers": WATCHLIST,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": 10000000 
    }
    
    # Needs to be a vectorized environment for SB3
    env = DummyVecEnv([lambda: SimpleStockTradingEnv(**env_kwargs)])
    
    print("Initialize PPO Model...")
    model = PPO("MlpPolicy", env, verbose=1, tensorboard_log=None)
    
    print("Training started...")
    # Using 10,000 steps for quick demonstration. Real training should be much longer (e.g., 200,000+).
    model.learn(total_timesteps=10000)
    
    save_dir = Path("data/trained_models")
    save_dir.mkdir(parents=True, exist_ok=True)
    
    save_path = save_dir / "ppo_kr_stock.zip"
    model.save(str(save_path))
    print(f"Training finished. Model saved to {save_path}")

if __name__ == "__main__":
    train()
