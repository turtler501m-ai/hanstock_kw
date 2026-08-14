"""
FuturesExecutor: 텔레그램 신호를 받아 여러 실행기에 동시 실행
- MockSimulator: 항상 실행 (내부 PnL 추적)
- KIS 모의계좌: 항상 실행
- KIS 실계좌: live_trading_enabled=True 시만
- Bybit: bybit_enabled=True 시만
"""
import json
import re
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)

STATE_FILE = Path(".runtime/executor_state.json")


def _normalize_futures_symbol(raw: str) -> str:
    """텔레그램 신호의 심볼을 KIS API 형식으로 변환 (executor 독립 사본).

    예: "나스닥" → "MNQM25", "NQ" → "NQM25", "MNQ" → "MNQM25"
    월코드: H=3월, M=6월, U=9월, Z=12월
    """
    from datetime import date

    SYMBOL_MAP = {
        # 영문 약어
        "NQ": "NQ", "MNQ": "MNQ",
        "ES": "ES", "MES": "MES",
        "GC": "GC", "MGC": "MGC",
        "CL": "CL", "MCL": "MCL",
        "RTY": "RTY", "M2K": "M2K",
        # 한글
        "나스닥": "MNQ", "나스닥100": "NQ",
        "골드": "GC", "금": "GC",
        "원유": "CL", "유가": "CL",
        "S&P": "ES", "SP": "ES",
        "러셀": "RTY",
    }

    raw_upper = raw.upper().strip()

    # 이미 완전한 선물 코드인 경우 (예: MNQM25, NQU25)
    if re.match(r'^[A-Z]{2,4}[HMUZ]\d{2}$', raw_upper):
        return raw_upper

    # 현재 분기 계산
    today = date.today()
    month = today.month
    year = today.year % 100  # 2자리 연도

    # 가장 가까운 미래 만기월 선택
    if month <= 3:
        month_code, exp_year = 'H', year
    elif month <= 6:
        month_code, exp_year = 'M', year
    elif month <= 9:
        month_code, exp_year = 'U', year
    else:
        month_code, exp_year = 'Z', year

    # 기본 심볼 찾기 (한글/약어 → 표준 코드)
    base = SYMBOL_MAP.get(raw_upper, raw_upper)

    # 이미 월코드가 붙어있는 경우 (예: MNQM, NQU)
    if re.match(r'^[A-Z]{2,4}[HMUZ]$', base):
        return f"{base}{exp_year:02d}"

    return f"{base}{month_code}{exp_year:02d}"


@dataclass
class ExecutorState:
    mock_enabled: bool = True
    kis_demo_enabled: bool = True
    live_trading_enabled: bool = False
    bybit_enabled: bool = False
    default_qty: int = 1
    polling_interval_sec: int = 30


@dataclass
class ExecutionResult:
    signal_id: str
    timestamp: str
    mock: Optional[dict] = None
    kis_demo: Optional[dict] = None
    kis_live: Optional[dict] = None
    bybit: Optional[dict] = None
    errors: list = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class FuturesExecutor:
    def __init__(self):
        self.state = self._load_state()
        self._execution_log: list[ExecutionResult] = []

    def _load_state(self) -> ExecutorState:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                return ExecutorState(**data)
            except Exception:
                pass
        return ExecutorState()

    def save_state(self):
        STATE_FILE.write_text(
            json.dumps(asdict(self.state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def update_state(self, **kwargs) -> ExecutorState:
        for k, v in kwargs.items():
            if hasattr(self.state, k):
                setattr(self.state, k, v)
        self.save_state()
        return self.state

    def execute(self, signal) -> ExecutionResult:
        """signal: FuturesSignal 객체"""
        from src.online_access import require_online_access

        require_online_access("futures signal execution")
        result = ExecutionResult(
            signal_id=signal.id,
            timestamp=datetime.now().isoformat(),
        )

        qty = getattr(signal, "qty", self.state.default_qty) or self.state.default_qty

        # 1. Mock Simulator
        if self.state.mock_enabled:
            try:
                result.mock = self._execute_mock(signal, qty)
            except Exception as e:
                result.errors.append(f"mock: {e}")
                logger.error(f"Mock execution error: {e}")

        # 2. KIS 모의계좌
        if self.state.kis_demo_enabled:
            try:
                result.kis_demo = self._execute_kis(signal, qty, demo=True)
            except Exception as e:
                result.errors.append(f"kis_demo: {e}")
                logger.error(f"KIS demo execution error: {e}")

        # 3. KIS 실계좌
        if self.state.live_trading_enabled:
            try:
                result.kis_live = self._execute_kis(signal, qty, demo=False)
            except Exception as e:
                result.errors.append(f"kis_live: {e}")
                logger.error(f"KIS live execution error: {e}")

        # 4. Bybit
        if self.state.bybit_enabled and self.state.live_trading_enabled:
            try:
                result.bybit = self._execute_bybit(signal, qty)
            except Exception as e:
                result.errors.append(f"bybit: {e}")
                logger.error(f"Bybit execution error: {e}")

        self._execution_log.append(result)
        logger.info(
            f"Executed signal {signal.id}: mock={result.mock is not None}, "
            f"kis_demo={result.kis_demo is not None}, kis_live={result.kis_live is not None}"
        )
        return result

    def _execute_mock(self, signal, qty: int) -> dict:
        """내부 Mock Simulator - 가격 추적으로 PnL 계산"""
        symbol = _normalize_futures_symbol(signal.symbol)
        return {
            "status": "opened",
            "symbol": symbol,
            "direction": signal.direction,
            "entry": signal.entry,
            "qty": qty,
            "stop_loss": signal.stop_loss,
            "take_profits": list(signal.take_profits),
            "simulated": True,
        }

    def _execute_kis(self, signal, qty: int, demo: bool) -> dict:
        """KIS 해외선물 API 주문"""
        try:
            from src.api.kis_futures_api import KISFuturesAPI
            api = KISFuturesAPI(demo=demo)
            symbol = _normalize_futures_symbol(signal.symbol)
            direction = str(signal.direction or "").lower()
            if direction in ("long", "buy", "매수", "롱"):
                side = "buy"
            elif direction in ("short", "sell", "매도", "숏"):
                side = "sell"
            else:
                logger.warning(f"Unknown direction: {signal.direction}, skipping KIS order")
                return {"status": "skipped", "reason": f"unknown direction: {signal.direction}", "demo": demo}
            result = api.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                price=signal.entry,
            )
            return {**result, "demo": demo, "qty": qty}
        except ImportError:
            return {"status": "skipped", "reason": "KISFuturesAPI not available", "demo": demo}
        except Exception as e:
            raise

    def _execute_bybit(self, signal, qty: int) -> dict:
        """Bybit 선물 주문"""
        try:
            from src.api.bybit_api import BybitTrader
            trader = BybitTrader()
            symbol = _normalize_futures_symbol(signal.symbol)
            result = trader.process_signal({
                "direction": signal.direction,
                "symbol": symbol,
                "qty": qty,
            })
            return {**result, "qty": qty}
        except ImportError:
            return {"status": "skipped", "reason": "BybitTrader not available"}
        except Exception as e:
            raise

    def get_execution_log(self) -> list[dict]:
        return [
            {
                "signal_id": r.signal_id,
                "timestamp": r.timestamp,
                "mock": r.mock,
                "kis_demo": r.kis_demo,
                "kis_live": r.kis_live,
                "bybit": r.bybit,
                "errors": r.errors,
            }
            for r in self._execution_log
        ]

    def get_mock_performance(self) -> dict:
        """Mock 성과 집계"""
        trades = [r for r in self._execution_log if r.mock]
        return {
            "total_trades": len(trades),
            "open_positions": len([t for t in trades if t.mock.get("status") == "opened"]),
            "execution_log": [r.mock for r in trades if r.mock],
        }


# 싱글톤 인스턴스
_executor: Optional[FuturesExecutor] = None


def get_executor() -> FuturesExecutor:
    global _executor
    if _executor is None:
        _executor = FuturesExecutor()
    return _executor
