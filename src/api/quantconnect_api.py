"""Small QuantConnect REST API client for dashboard status checks."""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass

import requests


BASE_URL = "https://www.quantconnect.com/api/v2"


@dataclass(frozen=True)
class QuantConnectCredentials:
    user_id: str
    api_token: str
    project_id: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.user_id and self.api_token)

    @property
    def project_configured(self) -> bool:
        return bool(self.project_id)


class QuantConnectAPI:
    def __init__(self, credentials: QuantConnectCredentials, base_url: str = BASE_URL):
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")

    def headers(self) -> dict[str, str]:
        timestamp = str(int(time.time()))
        stamped_token = f"{self.credentials.api_token}:{timestamp}".encode("utf-8")
        token_hash = hashlib.sha256(stamped_token).hexdigest()
        auth_source = f"{self.credentials.user_id}:{token_hash}".encode("utf-8")
        authentication = base64.b64encode(auth_source).decode("ascii")
        return {
            "Authorization": f"Basic {authentication}",
            "Timestamp": timestamp,
            "Content-Type": "application/json",
        }

    def authenticate(self, timeout: float = 10.0) -> dict:
        from src.online_access import require_online_access

        require_online_access("QuantConnect authentication")
        if not self.credentials.configured:
            return {
                "success": False,
                "configured": False,
                "error": "QUANTCONNECT_USER_ID and QUANTCONNECT_API_TOKEN are required.",
            }

        try:
            response = requests.post(
                f"{self.base_url}/authenticate",
                headers=self.headers(),
                json={},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            return {
                "success": False,
                "configured": True,
                "error": str(exc),
            }

        if response.ok:
            try:
                payload = response.json()
            except ValueError:
                payload = {"success": True}
            success = bool(payload.get("success", True))
            return {
                "success": success,
                "configured": True,
                "status_code": response.status_code,
                "error": "; ".join(payload.get("errors", [])) if not success else None,
            }

        return {
            "success": False,
            "configured": True,
            "status_code": response.status_code,
            "error": response.text[:300],
        }

    def _post(self, path: str, payload: dict | None = None, timeout: float = 10.0) -> dict:
        from src.online_access import require_online_access

        require_online_access("QuantConnect API access")
        if not self.credentials.configured:
            return {
                "success": False,
                "configured": False,
                "error": "QUANTCONNECT_USER_ID and QUANTCONNECT_API_TOKEN are required.",
            }

        try:
            response = requests.post(
                f"{self.base_url}{path}",
                headers=self.headers(),
                json=payload or {},
                timeout=timeout,
            )
        except (ValueError, requests.RequestException) as exc:
            return {
                "success": False,
                "configured": True,
                "error": str(exc),
            }

        try:
            data = response.json()
        except ValueError:
            data = {"success": response.ok, "errors": [response.text[:300]]}

        errors = data.get("errors") or []
        if errors and not data.get("error"):
            data["error"] = "; ".join(errors)
        data.setdefault("success", response.ok)
        data["status_code"] = response.status_code
        return data

    def read_project(self, project_id: str | int, timeout: float = 10.0) -> dict:
        return self._post("/projects/read", {"projectId": int(project_id)}, timeout=timeout)

    def read_project_nodes(self, project_id: str | int, timeout: float = 10.0) -> dict:
        return self._post("/projects/nodes/read", {"projectId": int(project_id)}, timeout=timeout)

    def create_compile(self, project_id: str | int, timeout: float = 10.0) -> dict:
        return self._post("/compile/create", {"projectId": int(project_id)}, timeout=timeout)

    def read_compile(self, project_id: str | int, compile_id: str, timeout: float = 10.0) -> dict:
        return self._post(
            "/compile/read",
            {"projectId": int(project_id), "compileId": compile_id},
            timeout=timeout,
        )

    def list_live_algorithms(self, project_id: str | int, timeout: float = 10.0) -> dict:
        return self._post("/live/list", {"projectId": int(project_id)}, timeout=timeout)

    def create_live_algorithm(
        self,
        project_id: str | int,
        compile_id: str,
        node_id: str,
        parameters: dict | None = None,
        timeout: float = 20.0,
    ) -> dict:
        payload = {
            "versionId": "-1",
            "projectId": int(project_id),
            "compileId": compile_id,
            "nodeId": node_id,
            "brokerage": {
                "id": "QuantConnectBrokerage",
                "user": "",
                "password": "",
                "environment": "live-paper",
                "account": "",
            },
            "dataProviders": {
                "QuantConnectBrokerage": {
                    "id": "QuantConnectBrokerage",
                },
            },
            "parameters": parameters or {},
            "notification": {},
        }
        return self._post("/live/create", payload, timeout=timeout)

    def read_live_algorithm(self, project_id: str | int, timeout: float = 10.0) -> dict:
        return self._post("/live/read", {"projectId": int(project_id)}, timeout=timeout)

    def read_live_portfolio(self, project_id: str | int, timeout: float = 10.0) -> dict:
        return self._post("/live/portfolio/read", {"projectId": int(project_id)}, timeout=timeout)

    def read_live_orders(
        self,
        project_id: str | int,
        algorithm_id: str,
        start: int = 0,
        end: int = 100,
        timeout: float = 10.0,
    ) -> dict:
        return self._post(
            "/live/orders/read",
            {
                "projectId": int(project_id),
                "algorithmId": algorithm_id,
                "start": start,
                "end": end,
            },
            timeout=timeout,
        )

    def create_live_command(self, project_id: str | int, command: dict, timeout: float = 10.0) -> dict:
        if not self.credentials.configured:
            return {
                "success": False,
                "configured": False,
                "error": "QUANTCONNECT_USER_ID and QUANTCONNECT_API_TOKEN are required.",
            }
        if not project_id:
            return {
                "success": False,
                "configured": True,
                "error": "QUANTCONNECT_PROJECT_ID is required.",
            }

        payload = self._post(
            "/live/commands/create",
            {"projectId": int(project_id), "command": command},
            timeout=timeout,
        )
        errors = payload.get("errors") or []
        return {
            "success": bool(payload.get("success", False)),
            "configured": True,
            "status_code": payload.get("status_code"),
            "errors": errors,
            "error": "; ".join(errors) if errors else None,
            "raw": payload,
        }
