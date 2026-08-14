from pathlib import Path
from src.config import config
from src.utils.logger import logger
from src.notifier.slack import slack_error

class RiskEngine:
    def __init__(self):
        self.daily_loss_halt = False
        
    def check_kill_switch(self) -> bool:
        return Path(".runtime/kill_switch.json").exists()
        
    def check_daily_loss(self, pnl: int) -> bool:
        if self.check_kill_switch():
            self.daily_loss_halt = True
            logger.info("[WARN] Kill switch is active. Halting all buys.")
            return True
            
        if config.total_capital <= 0 or pnl >= 0:
            return False
        loss_pct = abs(pnl) / config.total_capital * 100
        if loss_pct >= config.max_daily_loss_pct:
            msg = f"Daily loss limit reached: {loss_pct:.1f}% >= {config.max_daily_loss_pct}%"
            if not self.daily_loss_halt:
                logger.info(f"[WARN] {msg}")
                slack_error(msg)
                self.daily_loss_halt = True
            return True
        return False

    def evaluate_order(self, action: str, qty: int, price: int, cash: int) -> dict:
        if self.check_kill_switch() and action == "buy":
            return {"approved": False, "reason": "System Kill Switch is ACTIVE (Buys blocked)"}
            
        if self.daily_loss_halt and action == "buy":
            return {"approved": False, "reason": "Daily loss halt active"}
        
        if action == "buy":
            cost = qty * price
            if cost > cash:
                return {"approved": False, "reason": f"Not enough cash: need={cost:,}, cash={cash:,}"}
                
        return {"approved": True, "reason": "OK"}
