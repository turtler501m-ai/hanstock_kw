"""
KIS 한국투자증권 해외선물 API
- demo=True: 모의투자 (https://openapivts.koreainvestment.com:29443)
- demo=False: 실전투자 (https://openapi.koreainvestment.com:9443)

환경변수:
  모의: KIS_FUTURES_DEMO_APP_KEY, KIS_FUTURES_DEMO_APP_SECRET, KIS_FUTURES_DEMO_ACCOUNT
  실전: KIS_FUTURES_REAL_APP_KEY, KIS_FUTURES_REAL_APP_SECRET, KIS_FUTURES_REAL_ACCOUNT
"""

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from src.config import config
from src.utils.logger import logger
from urllib3.util import Retry
from requests.adapters import HTTPAdapter


HTTP = requests.Session()
HTTP.trust_env = False
HTTP.headers.update({"User-Agent": "python-requests/2.31.0"})

# Mount HTTPAdapter with automatic retries for transient errors (connection, 500, 502, 503, 504)
_adapter = HTTPAdapter(
    max_retries=Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False
    )
)
HTTP.mount("http://", _adapter)
HTTP.mount("https://", _adapter)

_KIS_FUTURES_THROTTLE_LOCK = threading.Lock()
_KIS_FUTURES_LAST_CALL = 0.0
_KIS_FUTURES_MIN_INTERVAL = 1.0  # 초당 1회 제한


def _kis_futures_throttle():
    global _KIS_FUTURES_LAST_CALL
    with _KIS_FUTURES_THROTTLE_LOCK:
        elapsed = time.monotonic() - _KIS_FUTURES_LAST_CALL
        if elapsed < _KIS_FUTURES_MIN_INTERVAL:
            time.sleep(_KIS_FUTURES_MIN_INTERVAL - elapsed)
        _KIS_FUTURES_LAST_CALL = time.monotonic()


class KISFuturesAPI:
    """
    KIS 해외선물 API 클라이언트.

    Parameters
    ----------
    demo : bool
        True이면 모의투자 환경, False이면 실전투자 환경을 사용합니다.
    notify_errors : bool
        오류 발생 시 알림 여부 (기본값 True).
    """

    _DEMO_BASE_URL = "https://openapivts.koreainvestment.com:29443"
    _REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"

    _DEMO_TOKEN_CACHE = Path("data") / "kis_futures_demo_token.json"
    _REAL_TOKEN_CACHE = Path("data") / "kis_futures_real_token.json"

    # TR ID 매핑: (demo_tr_id, real_tr_id)
    _TR_IDS = {
        "balance":    ("VTFO6501R", "OTFO6501R"),
        "positions":  ("VTFO6507R", "OTFO6507R"),
        "order":      ("VTFO1001U", "OTFO1001U"),
        "executions": ("VTFO6511R", "OTFO6511R"),
        # 기존 호환성을 위해 추가로 유지하는 TR ID
        "cancel":          ("VTFM3003U", "OTFM3003U"),
        "daily_orders":    ("VTFM3122R", "OTFM3122R"),
        "current_price":   ("HHDFC55010000", "HHDFC55010000"),
    }

    def __init__(self, demo: bool = True, notify_errors: bool = True):
        from src.online_access import require_online_access

        require_online_access("KIS futures API access")
        self.demo = demo
        self.notify_errors = notify_errors

        if demo:
            self.base_url = self._DEMO_BASE_URL
            self._token_cache = self._DEMO_TOKEN_CACHE
            self._app_key = config.kis_futures_demo_app_key or ""
            self._app_secret = config.kis_futures_demo_app_secret or ""
            self._account = config.kis_futures_demo_account or ""
            self._env_label = "demo"
        else:
            self.base_url = self._REAL_BASE_URL
            self._token_cache = self._REAL_TOKEN_CACHE
            self._app_key = config.kis_futures_real_app_key or ""
            self._app_secret = config.kis_futures_real_app_secret or ""
            self._account = config.kis_futures_real_account or ""
            self._env_label = "real"
            HTTP.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
            })

        self._configured = bool(self._app_key and self._app_secret and self._account)
        self.access_token = self._get_token() if self._configured else ""

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _not_configured(self) -> dict:
        """API 키가 설정되지 않은 경우 표준 응답을 반환합니다."""
        return {"status": "not_configured", "demo": self.demo}

    def _tr_id(self, key: str) -> str:
        """키에 해당하는 TR ID를 demo/real에 따라 반환합니다."""
        pair = self._TR_IDS.get(key, ("", ""))
        return pair[0] if self.demo else pair[1]

    def _app_key_hash(self) -> str:
        return hashlib.sha256(self._app_key.encode("utf-8")).hexdigest()

    def _check_configured(self) -> bool:
        """API 키, 시크릿, 계좌번호가 모두 설정되어 있는지 확인합니다."""
        return bool(self._app_key and self._app_secret and self._account)

    def _parse_account(self, account: str) -> tuple[str, str]:
        """
        계좌번호를 (CANO 8자리, ACNT_PRDT_CD 2자리)로 분리합니다.
        "12345678-08" 형식 및 "1234567890" 10자리 형식 모두 지원합니다.
        """
        if "-" in account:
            parts = account.split("-")
            return parts[0][:8], parts[1] if len(parts) > 1 else "08"
        if len(account) >= 10:
            return account[:8], account[8:10]
        return account[:8], "08"

    def _get_account_code(self) -> tuple[str, str]:
        """계좌번호를 (CANO 8자리, ACNT_PRDT_CD 2자리)로 분리합니다."""
        return self._parse_account(self._account)

    # ------------------------------------------------------------------
    # 토큰 발급 / 캐싱
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """캐시에서 유효한 토큰을 로드하거나, 없으면 새로 발급합니다."""
        cached = self._load_cached_token()
        if cached:
            return cached
        return self._fetch_token()

    def _load_cached_token(self) -> str:
        """캐시 파일에서 토큰을 읽어 반환합니다. 유효하지 않으면 빈 문자열 반환."""
        if not self._token_cache.exists():
            return ""
        try:
            cached = json.loads(self._token_cache.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(cached["expires_at"])
            # timezone-aware datetime이면 naive로 통일하여 비교
            if expires_at.tzinfo is not None:
                expires_at = expires_at.replace(tzinfo=None)
            key_matches = cached.get("app_key_hash") == self._app_key_hash()
            env_matches = cached.get("env") == self._env_label
            still_valid = expires_at > datetime.now() + timedelta(minutes=5)
            if key_matches and env_matches and still_valid:
                return cached["token"]
        except Exception:
            pass
        return ""

    def _fetch_token(self) -> str:
        """KIS 서버에서 새 액세스 토큰을 발급받고 캐시에 저장합니다."""
        _kis_futures_throttle()
        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        try:
            r = HTTP.post(url, json=body, timeout=10)
            if r.status_code != 200:
                logger.error(f"[KISFutures/{self._env_label}] Token fetch HTTP {r.status_code}: {r.text}")
                r.raise_for_status()
            data = r.json()
            token = data.get("access_token", "")
            expires_at = datetime.now() + timedelta(hours=23)
            self._token_cache.parent.mkdir(parents=True, exist_ok=True)
            self._token_cache.write_text(
                json.dumps({
                    "token": token,
                    "expires_at": expires_at.isoformat(),
                    "env": self._env_label,
                    "app_key_hash": self._app_key_hash(),
                }),
                encoding="utf-8",
            )
            return token
        except Exception as e:
            logger.error(f"[KISFutures/{self._env_label}] Token fetch failed: {e}")
            raise RuntimeError(f"KIS futures token fetch failed: {e}") from e

    # ------------------------------------------------------------------
    # HTTP 요청 공통 메서드
    # ------------------------------------------------------------------

    def _headers(self, tr_id: str) -> dict:
        cano, acnt_prdt_cd = self._get_account_code()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "tr_cont": "",
            "custtype": "P",
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
        }

    def _do_single_request(self, method: str, url: str, params=None, body=None, tr_id: str = "") -> "requests.Response":
        """단일 HTTP 요청을 수행하고 Response 객체를 반환합니다."""
        headers = self._headers(tr_id)
        if method == "GET":
            return HTTP.get(url, headers=headers, params=params, timeout=15)
        else:
            return HTTP.post(url, headers=headers, json=body, timeout=15)

    def _parse_response(self, r: "requests.Response") -> dict:
        """Response 객체를 dict로 변환합니다. 오류 응답도 처리합니다."""
        if r.status_code != 200:
            logger.error(f"[KISFutures/{self._env_label}] API error: {r.status_code} {r.text}")
            try:
                data = r.json()
            except ValueError:
                data = {}
            return {
                "rt_cd": data.get("rt_cd", "99999"),
                "msg_cd": data.get("msg_cd", ""),
                "msg1": data.get("msg1", f"HTTP {r.status_code}: {r.text}"),
                "_status_code": r.status_code,
                "demo": self.demo,
            }
        result = r.json()
        if result.get("rt_cd") != "0":
            logger.warning(f"[KISFutures/{self._env_label}] API warning: {result.get('msg1', '')}")
        result["demo"] = self.demo
        return result

    def _request(self, method: str, path: str, params=None, body=None, tr_id: str = "") -> dict:
        """HTTP 요청을 수행하고 결과 dict를 반환합니다. 401 응답 시 토큰 재발급 후 1회 재시도합니다."""
        _kis_futures_throttle()
        url = f"{self.base_url}{path}"
        try:
            r = self._do_single_request(method, url, params=params, body=body, tr_id=tr_id)

            # 401 토큰 만료 시 재발급 후 1회 재시도
            if r.status_code == 401:
                logger.warning(f"[KISFutures/{self._env_label}] Token expired (401), refreshing token and retrying...")
                self.access_token = ""
                if self._token_cache.exists():
                    try:
                        self._token_cache.unlink()
                    except OSError:
                        pass
                self.access_token = self._fetch_token()
                _kis_futures_throttle()
                r = self._do_single_request(method, url, params=params, body=body, tr_id=tr_id)

            return self._parse_response(r)
        except Exception as e:
            logger.error(f"[KISFutures/{self._env_label}] Request failed: {e}")
            return {"rt_cd": "99999", "msg1": str(e), "demo": self.demo}

    # ------------------------------------------------------------------
    # 공개 API 메서드
    # ------------------------------------------------------------------

    def get_balance(self) -> dict:
        """
        계좌 잔고/증거금 조회.
        - 모의 TR ID: VTFO6501R
        - 실전 TR ID: OTFO6501R
        """
        if not self._configured:
            return self._not_configured()

        today = datetime.now().strftime("%Y%m%d")
        cano, acnt_prdt_cd = self._get_account_code()
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "CRCY_CD": "KRW",
            "INQR_DT": today,
        }
        return self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-deposit",
            params=params,
            tr_id=self._tr_id("balance"),
        )

    def get_positions(self) -> dict:
        """
        보유 포지션 조회.
        - 모의 TR ID: VTFO6507R
        - 실전 TR ID: OTFO6507R
        """
        if not self._configured:
            return self._not_configured()

        cano, acnt_prdt_cd = self._get_account_code()
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_FUTR_FX_PDNO": "",
            "CRCY_CD": "",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        return self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-balance",
            params=params,
            tr_id=self._tr_id("positions"),
        )

    def place_order(self, symbol: str, side: str, qty: int, price: float = 0.0) -> dict:
        """
        주문 제출.

        Parameters
        ----------
        symbol : str
            종목 코드 (예: "MNQM25")
        side : str
            "buy" 또는 "sell"
        qty : int
            주문 수량
        price : float
            주문 가격. 0이면 시장가.

        Returns
        -------
        dict
            {"order_id": ..., "status": "submitted", "demo": bool} 또는 오류 dict
        - 모의 TR ID: VTFO1001U
        - 실전 TR ID: OTFO1001U
        """
        from src.online_access import require_online_access

        require_online_access("KIS futures order submission")
        if not self._configured:
            return self._not_configured()

        cano, acnt_prdt_cd = self._get_account_code()
        sll_buy_dvsn_cd = "02" if side in ("buy", "매수") else "01"
        is_market = price == 0 or price is None
        str_price = "" if is_market else str(price)

        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_FUTR_FX_PDNO": symbol,
            "SLL_BUY_DVSN_CD": sll_buy_dvsn_cd,
            "FM_LQD_USTL_CCLD_DT": "",
            "FM_LQD_USTL_CCNO": "",
            "PRIC_DVSN_CD": "2" if is_market else "1",
            "FM_LIMIT_ORD_PRIC": str_price,
            "FM_STOP_ORD_PRIC": "",
            "FM_ORD_QTY": str(qty),
            "FM_LQD_LMT_ORD_PRIC": "",
            "FM_LQD_STOP_ORD_PRIC": "",
            "CCLD_CNDT_CD": "2",
            "CPLX_ORD_DVSN_CD": "0",
            "ECIS_RSVN_ORD_YN": "N",
            "FM_HDGE_ORD_SCRN_YN": "N",
        }
        result = self._request(
            "POST",
            "/uapi/overseas-futureoption/v1/trading/order",
            body=body,
            tr_id=self._tr_id("order"),
        )

        # 표준 응답 형식으로 변환
        if result.get("rt_cd") == "0":
            output = result.get("output", {})
            return {
                "order_id": output.get("ODNO", output.get("KNT_ORGN_ODNO", "")),
                "status": "submitted",
                "demo": self.demo,
                "raw": result,
            }
        return result

    def get_executions(self) -> dict:
        """
        체결 내역 조회.
        - 모의 TR ID: VTFO6511R
        - 실전 TR ID: OTFO6511R
        """
        if not self._configured:
            return self._not_configured()

        today = datetime.now().strftime("%Y%m%d")
        cano, acnt_prdt_cd = self._get_account_code()
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "STRT_DT": today,
            "END_DT": today,
            "FUOP_DVSN_CD": "00",
            "FM_PDGR_CD": "",
            "CRCY_CD": "%%%",
            "FM_ITEM_FTNG_YN": "N",
            "SLL_BUY_DVSN_CD": "%%",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        return self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-ccld",
            params=params,
            tr_id=self._tr_id("executions"),
        )

    # ------------------------------------------------------------------
    # 기존 호환성 유지 메서드
    # ------------------------------------------------------------------

    def cancel_order(self, order_no: str, order_date: str = "") -> dict:
        """주문 취소."""
        if not self._configured:
            return self._not_configured()

        cano, acnt_prdt_cd = self._get_account_code()
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "ORGN_ORD_DT": order_date,
            "ORGN_ODNO": order_no,
            "FM_LIMIT_ORD_PRIC": "",
            "FM_STOP_ORD_PRIC": "",
            "FM_LQD_LMT_ORD_PRIC": "",
            "FM_LQD_STOP_ORD_PRIC": "",
            "FM_HDGE_ORD_SCRN_YN": "N",
            "FM_MKPR_CVSN_YN": "N",
        }
        return self._request(
            "POST",
            "/uapi/overseas-futureoption/v1/trading/order-rvsecncl",
            body=body,
            tr_id=self._tr_id("cancel"),
        )

    def get_daily_orders(self, start_date: str = None, end_date: str = None) -> dict:
        """일별 주문 내역 조회."""
        if not self._configured:
            return self._not_configured()

        if not start_date:
            start_date = datetime.now().strftime("%Y%m%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")

        cano, acnt_prdt_cd = self._get_account_code()
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "STRT_DT": start_date,
            "END_DT": end_date,
            "FUOP_DVSN_CD": "00",
            "FM_PDGR_CD": "",
            "CRCY_CD": "%%%",
            "FM_ITEM_FTNG_YN": "N",
            "SLL_BUY_DVSN_CD": "%%",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        return self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-daily-ccld",
            params=params,
            tr_id=self._tr_id("daily_orders"),
        )

    def get_current_price(self, symbol: str) -> dict:
        """현재가 조회 (TR ID: HHDFC55010000, 모의/실전 동일)."""
        if not self._configured:
            return self._not_configured()

        params = {"SRS_CD": symbol}
        result = self._request(
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/inquire-price",
            params=params,
            tr_id=self._tr_id("current_price"),
        )

        if result.get("rt_cd") == "0":
            output = result.get("output", {})
            return {
                "symbol": symbol,
                "price": float(output.get("last", 0) or 0),
                "change": output.get("diff", ""),
                "volume": output.get("acml_vol", ""),
                "status": "ok",
                "demo": self.demo,
                "raw": result,
            }
        return result

    # ------------------------------------------------------------------
    # _resolve_tr_id: 기존 테스트 호환성 유지
    # ------------------------------------------------------------------

    def _resolve_tr_id(self, tr_id: str) -> str:
        """
        국내주식 스타일 TR ID (T/J/C 시작)만 V 접두사로 변환합니다.
        해외선물 TR ID (O/H/V 시작 등)는 그대로 반환합니다.
        모의 환경에서만 변환이 적용됩니다.
        """
        if self.demo and tr_id and tr_id[0] in ("T", "J", "C"):
            return "V" + tr_id[1:]
        return tr_id
