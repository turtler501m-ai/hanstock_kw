"""Low-level, testable client for the Kiwoom REST API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import threading
import time
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import requests


LIVE_BASE_URL = "https://api.kiwoom.com"
MOCK_BASE_URL = "https://mockapi.kiwoom.com"


class KiwoomApiError(RuntimeError):
    """Raised when Kiwoom rejects or cannot complete a request."""


class RequestThrottle:
    """Thread-safe fixed-interval limiter with independently keyed lanes."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str, interval_seconds: float) -> None:
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_allowed.get(key, now) - now)
            if delay:
                self._sleep(delay)
                now = self._clock()
            self._next_allowed[key] = max(now, self._next_allowed.get(key, now)) + interval_seconds


@dataclass(frozen=True, slots=True)
class KiwoomPage:
    data: Mapping[str, Any]
    cont_yn: str = "N"
    next_key: str = ""


class KiwoomRestClient:
    """OAuth and JSON POST transport shared by higher-level adapters."""

    _shared_tokens: dict[tuple[str, str, str], tuple[str, datetime]] = {}
    _shared_token_lock = threading.Lock()

    def __init__(
        self,
        app_key: str,
        secret_key: str,
        *,
        environment: str = "mock",
        session: requests.Session | None = None,
        throttle: RequestThrottle | None = None,
        now: Callable[[], datetime] | None = None,
        timeout: float = 10.0,
    ) -> None:
        if environment not in {"mock", "live"}:
            raise ValueError("environment must be 'mock' or 'live'")
        self.app_key = app_key
        self._secret_key = secret_key
        self.environment = environment
        self.base_url = MOCK_BASE_URL if environment == "mock" else LIVE_BASE_URL
        self._session = session or requests.Session()
        self._throttle = throttle or RequestThrottle()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.timeout = timeout
        self._access_token = ""
        self._token_expires_at: datetime | None = None

    def _token_cache_key(self) -> tuple[str, str, str]:
        secret_digest = hashlib.sha256(self._secret_key.encode("utf-8")).hexdigest()
        return self.environment, self.app_key, secret_digest

    @classmethod
    def clear_shared_token_cache(cls) -> None:
        with cls._shared_token_lock:
            cls._shared_tokens.clear()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(environment={self.environment!r}, base_url={self.base_url!r})"

    def get_access_token(self) -> str:
        # Refresh early so a token cannot expire while an order is in flight.
        if self._access_token and self._token_expires_at and self._now() < self._token_expires_at - timedelta(seconds=30):
            return self._access_token

        cache_key = self._token_cache_key()
        with self._shared_token_lock:
            shared = self._shared_tokens.get(cache_key)
        if shared and self._now() < shared[1] - timedelta(seconds=30):
            self._access_token, self._token_expires_at = shared
            return self._access_token

        response = self._session.post(
            f"{self.base_url}/oauth2/token",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self._secret_key},
            timeout=self.timeout,
        )
        payload = self._decode_response(response, "OAuth token")
        token = str(payload.get("token") or payload.get("access_token") or "")
        if not token:
            raise KiwoomApiError("OAuth token response did not contain a token")
        self._access_token = token
        self._token_expires_at = self._parse_expiry(payload)
        with self._shared_token_lock:
            self._shared_tokens[cache_key] = (self._access_token, self._token_expires_at)
        return token

    def post(
        self,
        path: str,
        *,
        api_id: str,
        body: Mapping[str, Any] | None = None,
        continuation: tuple[str, str] | None = None,
        request_kind: str = "query",
    ) -> KiwoomPage:
        if request_kind not in {"query", "order"}:
            raise ValueError("request_kind must be 'query' or 'order'")
        lane = f"mock:{api_id}" if self.environment == "mock" else f"live:{request_kind}"
        self._throttle.wait(lane, 1.0 if self.environment == "mock" else 0.2)
        headers = {
            "authorization": f"Bearer {self.get_access_token()}",
            "api-id": api_id,
            "content-type": "application/json;charset=UTF-8",
        }
        if continuation:
            headers["cont-yn"], headers["next-key"] = continuation
        response = self._session.post(
            f"{self.base_url}/{path.lstrip('/')}", json=dict(body or {}), headers=headers, timeout=self.timeout
        )
        payload = self._decode_response(response, api_id)
        return KiwoomPage(
            data=payload,
            cont_yn=str(response.headers.get("cont-yn", "N")),
            next_key=str(response.headers.get("next-key", "")),
        )

    def post_all_pages(
        self,
        path: str,
        *,
        api_id: str,
        body: Mapping[str, Any] | None = None,
        request_kind: str = "query",
        max_pages: int = 100,
    ) -> list[KiwoomPage]:
        pages: list[KiwoomPage] = []
        continuation: tuple[str, str] | None = None
        for _ in range(max_pages):
            page = self.post(path, api_id=api_id, body=body, continuation=continuation, request_kind=request_kind)
            pages.append(page)
            if page.cont_yn.upper() != "Y" or not page.next_key:
                return pages
            continuation = ("Y", page.next_key)
        raise KiwoomApiError(f"{api_id} continuation exceeded {max_pages} pages")

    def _parse_expiry(self, payload: Mapping[str, Any]) -> datetime:
        expires_dt = payload.get("expires_dt")
        if expires_dt:
            try:
                parsed = datetime.strptime(str(expires_dt), "%Y%m%d%H%M%S")
                # Kiwoom documents ``expires_dt`` in Korean local time.
                return parsed.replace(tzinfo=ZoneInfo("Asia/Seoul")).astimezone(timezone.utc)
            except ValueError:
                pass
        return self._now() + timedelta(seconds=float(payload.get("expires_in", 86400)))

    @staticmethod
    def _decode_response(response: Any, operation: str) -> Mapping[str, Any]:
        try:
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise KiwoomApiError(f"Kiwoom {operation} request failed") from exc
        if not isinstance(payload, Mapping):
            raise KiwoomApiError(f"Kiwoom {operation} returned invalid JSON")
        return_code = payload.get("return_code")
        if return_code not in (None, 0, "0"):
            message = str(payload.get("return_msg") or "broker rejected request")
            raise KiwoomApiError(f"Kiwoom {operation} failed: {message}")
        return payload
