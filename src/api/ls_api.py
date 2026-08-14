import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List
import requests
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from src.config import config
from src.utils.logger import logger
from src.notifier.slack import slack_error

HTTP = requests.Session()
HTTP.trust_env = False

# LS API 전역 스로틀: 초당 최대 5회 요청 강제 (Rate Limit 방지)
_LS_THROTTLE_LOCK = threading.Lock()
_LS_LAST_CALL: float = 0.0
_LS_MIN_INTERVAL: float = 0.2  # 초 단위 (초당 5회 제한 방지)


def _ls_throttle() -> None:
    """LS API 호출 전 최소 간격을 보장합니다."""
    global _LS_LAST_CALL
    with _LS_THROTTLE_LOCK:
        elapsed = time.monotonic() - _LS_LAST_CALL
        if elapsed < _LS_MIN_INTERVAL:
            time.sleep(_LS_MIN_INTERVAL - elapsed)
        _LS_LAST_CALL = time.monotonic()


class LSConfigError(RuntimeError):
    """Non-retryable LS app/environment mismatch."""


class LSRateLimitError(RuntimeError):
    """Non-retryable LS API rate limit response."""


class LSAccountError(RuntimeError):
    """Non-retryable LS account number error."""


NON_RETRYABLE_LS_ERRORS = (LSConfigError, LSRateLimitError, LSAccountError)


class LSSecuritiesAPI:
    TOKEN_CACHE = Path("data") / "ls_token.json"

    def __init__(self, notify_errors: bool = True) -> None:
        from src.online_access import require_online_access

        require_online_access("LS Securities API access")
        self.notify_errors = notify_errors
        # LS OpenAPI URL: 실전/모의 공통 도메인 사용 (포트는 실전/모의 설정에 따름)
        self.base_url = "https://openapi.ls-sec.co.kr:8080"
        HTTP.headers.update({"User-Agent": "python-requests/2.31.0"})
        self.access_token = self._load_or_fetch_token()

    def _app_key_hash(self) -> str:
        return hashlib.sha256(config.ls_app_key.encode("utf-8")).hexdigest()

    def _load_or_fetch_token(self) -> str:
        if self.TOKEN_CACHE.exists():
            try:
                cached = json.loads(self.TOKEN_CACHE.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(cached["expires_at"])
                if (
                    cached.get("trading_env") == config.ls_trading_env
                    and cached.get("app_key_hash") == self._app_key_hash()
                    and expires_at > datetime.now() + timedelta(minutes=10)
                ):
                    return cached["token"]
            except Exception:
                pass
        return self._fetch_token()

    def _fetch_token(self) -> str:
        _ls_throttle()
        url = f"{self.base_url}/oauth2/token"
        headers = {"content-type": "application/x-www-form-urlencoded"}
        payload = {
            "grant_type": "client_credentials",
            "appkey": config.ls_app_key,
            "appsecretkey": config.ls_app_secret,
            "scope": "oob"
        }
        try:
            r = HTTP.post(url, headers=headers, data=payload, timeout=10)
            if r.status_code != 200:
                logger.error(f"LS Token fetch HTTP {r.status_code}: {r.text}")
                r.raise_for_status()
            data = r.json()
            token = data.get("access_token", "")
            expires_in = int(data.get("expires_in", 86400))
            expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            self.TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
            self.TOKEN_CACHE.write_text(
                json.dumps({
                    "token": token,
                    "expires_at": expires_at.isoformat(),
                    "trading_env": config.ls_trading_env,
                    "app_key_hash": self._app_key_hash()
                }, indent=2),
                encoding="utf-8"
            )
            logger.info("New LS Securities Access Token fetched and cached successfully.")
            return token
        except Exception as e:
            logger.exception("Failed to fetch LS Securities access token")
            raise LSConfigError("LS credentials or token request failed") from e

    def _headers(self, tr_cd: str, tr_cont: str = "N") -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token}",
            "tr_cd": tr_cd,
            "tr_cont": tr_cont
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type(NON_RETRYABLE_LS_ERRORS),
        reraise=True
    )
    def get_overseas_quote(self, symbol: str) -> float:
        """[TR: g3101] 미국 주식 현재가 조회"""
        if not symbol:
            return 0.0
            
        _ls_throttle()
        url = f"{self.base_url}/overseas-stock/market-data"
        headers = self._headers(tr_cd="g3101")
        
        # 거래소 구분 코드 매핑 (단순 구현: 기본 나스닥으로 분류)
        # 실제 시스템에서는 종목별 거래소 매핑 데이터 사용 권장
        exchange = "NAS"
        
        body = {
            "g3101InBlock": {
                "excd": exchange,
                "symbol": symbol.upper().strip()
            }
        }
        
        try:
            r = HTTP.post(url, headers=headers, json=body, timeout=10)
            if r.status_code == 401:
                logger.warning("LS Unauthorized 401. Clearing token cache and retrying.")
                if self.TOKEN_CACHE.exists():
                    self.TOKEN_CACHE.unlink(missing_ok=True)
                self.access_token = self._load_or_fetch_token()
                raise RuntimeError("LS Token Expired")
                
            r.raise_for_status()
            data = r.json()
            out_block = data.get("g3101OutBlock", {})
            last_price = out_block.get("last")
            if last_price:
                return float(last_price)
            return 0.0
        except Exception as e:
            logger.error(f"Failed to fetch LS quote for {symbol}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type(NON_RETRYABLE_LS_ERRORS),
        reraise=True
    )
    def get_overseas_balance(self) -> Dict[str, Any]:
        """[TR: COSOQ00201] 해외주식 잔고 및 예수금 조회"""
        _ls_throttle()
        url = f"{self.base_url}/overseas-stock/balance"
        headers = self._headers(tr_cd="COSOQ00201")
        
        body = {
            "COSOQ00201InBlock": {
                "AcntNo": config.ls_account_no,
                "InptPwd": "",  # 모의투자는 공백 가능, 실전은 필요에 따라 암호 설정
                "CrcyCd": "USD"
            }
        }
        
        try:
            r = HTTP.post(url, headers=headers, json=body, timeout=15)
            r.raise_for_status()
            data = r.json()
            
            summary = data.get("COSOQ00201OutBlock1", {})
            holdings_list = data.get("COSOQ00201OutBlock2", [])
            
            # 한스톡 공통 규격으로 정규화
            result = {
                "foreign_deposit": float(summary.get("FrcrDps", 0.0)),
                "total_eval_amt": float(summary.get("TotAssAmt", 0.0)),
                "total_profit_loss": float(summary.get("PnlAmt", 0.0)),
                "holdings": []
            }
            
            for item in holdings_list:
                qty = int(item.get("BalQty", 0))
                if qty > 0:
                    result["holdings"].append({
                        "symbol": item.get("IsuNo", "").strip(),
                        "holding_qty": qty,
                        "avg_buy_price": float(item.get("Prcd", 0.0)),
                        "eval_profit_loss": float(item.get("EvalPnl", 0.0))
                    })
            return result
        except Exception as e:
            logger.error(f"Failed to fetch LS overseas balance: {e}")
            raise

    def place_overseas_order(self, symbol: str, action: str, price: float, qty: int) -> Dict[str, Any]:
        """[TR: COSAT00301] 미국 주식 지정가 매수/매도 주문 실행"""
        _ls_throttle()
        url = f"{self.base_url}/overseas-stock/order"
        headers = self._headers(tr_cd="COSAT00301")
        
        exchange = "NAS"
        order_type = "2" if action == "buy" else "1"  # 2: 매수, 1: 매도
        
        body = {
            "COSAT00301InBlock": {
                "AcntNo": config.ls_account_no,
                "InptPwd": "",
                "Excd": exchange,
                "IsuNo": symbol.upper().strip(),
                "OrdQty": int(qty),
                "OrdPrc": f"{price:.2f}",
                "PrcDv": "00",  # 00: 지정가
                "BnsGb": order_type
            }
        }
        
        try:
            r = HTTP.post(url, headers=headers, json=body, timeout=15)
            r.raise_for_status()
            data = r.json()
            
            # 오류 처리 확인
            error_code = data.get("rsp_cd")
            if error_code and error_code != "00000":
                error_msg = data.get("rsp_msg", "Unknown error")
                logger.error(f"LS Order failed: {error_code} - {error_msg}")
                raise LSRateLimitError(f"LS Order rejected: {error_msg}")
                
            out_block = data.get("COSAT00301OutBlock", {})
            return {
                "success": True,
                "order_no": out_block.get("OrdNo"),
                "raw_response": data
            }
        except Exception as e:
            logger.error(f"Failed to place LS order for {symbol}: {e}")
            if self.notify_errors:
                slack_error(f"[LS API ERROR] 주문 실패: {symbol} | {action} | {e}")
            raise
