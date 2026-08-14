from __future__ import annotations

import http.client
import os
import socket
import urllib.request


_INSTALLED = False
_ORIGINAL_SOCKET_CONNECT = socket.socket.connect


def _blocked(*args, **kwargs):
    target = kwargs.get("url")
    if target is None and args:
        target = args[-1]
    raise AssertionError(f"external network access is forbidden in unit tests: {target!r}")


def _guarded_socket_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else ""
    if host in {"127.0.0.1", "::1", "localhost"}:
        return _ORIGINAL_SOCKET_CONNECT(sock, address)
    return _blocked(sock, address)


def install_network_guard() -> None:
    global _INSTALLED
    if _INSTALLED or os.environ.get("RUN_NETWORK_TESTS") == "1":
        return

    os.environ.update(
        {
            "HANSTOCK_TESTING": "1",
            "SLACK_WEBHOOK_URL": "",
            "MISTOCK_SLACK_WEBHOOK_URL": "",
            "KISTOCK_APP_KEY": "",
            "KISTOCK_APP_SECRET": "",
            "KISTOCK_ACCOUNT": "",
            "KIS_WEBSOCKET_ENABLED": "false",
            "DASHBOARD_SNAPSHOT_REFRESH_ENABLED": "false",
            "DASHBOARD_AUTO_APPROVAL_SWEEP_ENABLED": "false",
        }
    )

    socket.create_connection = _blocked
    socket.socket.connect = _guarded_socket_connect
    http.client.HTTPConnection.connect = _blocked
    http.client.HTTPSConnection.connect = _blocked
    urllib.request.urlopen = _blocked

    try:
        import requests

        requests.sessions.Session.request = _blocked
    except ImportError:
        pass

    try:
        import curl_cffi.requests

        curl_cffi.requests.Session.request = _blocked
        curl_cffi.requests.AsyncSession.request = _blocked
    except (ImportError, AttributeError):
        pass

    try:
        import yfinance

        yfinance.download = _blocked
        yfinance.Ticker = _blocked
    except ImportError:
        pass

    _INSTALLED = True
