from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from src.api.quantconnect_api import QuantConnectAPI, QuantConnectCredentials


class ExternalIntegrationService:
    def __init__(
        self,
        *,
        env_path_fn: Callable[[], Path],
        auth_cache_path_fn: Callable[[], Path],
        now_fn: Callable[[], datetime],
    ) -> None:
        self.env_path_fn = env_path_fn
        self.auth_cache_path_fn = auth_cache_path_fn
        self.now_fn = now_fn

    @staticmethod
    def read_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return default

    def quantconnect_credentials(self) -> QuantConnectCredentials:
        load_dotenv(dotenv_path=self.env_path_fn(), override=True)
        return QuantConnectCredentials(
            user_id=os.environ.get("QUANTCONNECT_USER_ID")
            or os.environ.get("QC_USER_ID")
            or "",
            api_token=os.environ.get("QUANTCONNECT_API_TOKEN")
            or os.environ.get("QC_API_TOKEN")
            or "",
            project_id=os.environ.get("QUANTCONNECT_PROJECT_ID")
            or os.environ.get("QC_PROJECT_ID")
            or "",
        )

    def quantconnect_auth_status(
        self,
        credentials: QuantConnectCredentials,
    ) -> dict:
        now = self.now_fn()
        cache_path = self.auth_cache_path_fn()
        cached = self.read_json(cache_path, {})
        if not isinstance(cached, dict):
            cached = {}
        cached_at = cached.get("checked_at")
        if cached_at:
            try:
                age = (now - datetime.fromisoformat(cached_at)).total_seconds()
            except (TypeError, ValueError):
                age = None
            if age is not None and age < 300:
                status = cached.get("status", {})
                if status:
                    return {**status, "cached": True}

        status = QuantConnectAPI(credentials).authenticate(timeout=5.0)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"checked_at": now.isoformat(), "status": status},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {**status, "cached": False}
