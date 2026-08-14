from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone, timedelta
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))


async def get_channel_entity(client, target: str | int):
    """Get proper channel entity - use PeerChannel for numeric IDs."""
    try:
        from telethon.tl.types import PeerChannel
        if str(target).lstrip("-").isdigit():
            return await client.get_entity(PeerChannel(int(target)))
    except Exception:
        pass
    return await client.get_entity(str(target).lstrip("@"))


@dataclass(frozen=True)
class TelegramCollectorConfig:
    api_id: str
    api_hash: str
    session_name: str
    target_channels: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "TelegramCollectorConfig":
        channels = tuple(
            item.strip()
            for item in os.environ.get("TELEGRAM_TARGET_CHANNELS", "").split(",")
            if item.strip()
        )
        if not channels:
            channels = channels_from_legacy_json()
        return cls(
            api_id=os.environ.get("TELEGRAM_API_ID", ""),
            api_hash=os.environ.get("TELEGRAM_API_HASH", ""),
            session_name=os.environ.get("TELEGRAM_SESSION_NAME", ".runtime/futures_telegram"),
            target_channels=channels,
        )

    @property
    def missing(self) -> list[str]:
        missing = []
        if not self.api_id:
            missing.append("TELEGRAM_API_ID")
        if not self.api_hash:
            missing.append("TELEGRAM_API_HASH")
        if not self.target_channels:
            missing.append("TELEGRAM_TARGET_CHANNELS")
        return missing

    @property
    def configured(self) -> bool:
        return not self.missing

    @property
    def session_file(self) -> Path:
        path = Path(self.session_name)
        if path.suffix == ".session":
            return path
        return path.with_suffix(path.suffix + ".session") if path.suffix else Path(f"{self.session_name}.session")


def telethon_available() -> bool:
    return importlib.util.find_spec("telethon") is not None


def channels_from_legacy_json(path: str | Path = "config/channels.json") -> tuple[str, ...]:
    legacy_path = Path(path)
    if not legacy_path.exists():
        return ()
    try:
        payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    if not isinstance(payload, list):
        return ()
    channels = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("signal_tracking") is not True:
            continue
        target = str(item.get("id_or_username") or "").strip()
        if target:
            channels.append(target)
    return tuple(channels)


def collector_status(config: TelegramCollectorConfig | None = None) -> dict[str, Any]:
    cfg = config or TelegramCollectorConfig.from_env()
    dependency_available = telethon_available()
    session_available = cfg.session_file.exists()
    ready = cfg.configured and dependency_available and session_available
    missing = cfg.missing + ([] if dependency_available else ["telethon"])
    if cfg.configured and dependency_available and not session_available:
        missing.append("TELEGRAM_SESSION_LOGIN")
    return {
        "configured": cfg.configured,
        "dependency_available": dependency_available,
        "session_available": session_available,
        "ready": ready,
        "session_name": cfg.session_name,
        "session_file": str(cfg.session_file),
        "target_channels": list(cfg.target_channels),
        "missing": missing,
        "message": "ready" if ready else "telegram collector is not configured",
    }


async def collector_status_detail(config: TelegramCollectorConfig | None = None) -> dict[str, Any]:
    from telethon import TelegramClient

    cfg = config or TelegramCollectorConfig.from_env()
    base_status = collector_status(cfg)

    channel_results: list[dict[str, Any]] = []
    if base_status["ready"]:
        client = TelegramClient(cfg.session_name, int(cfg.api_id), cfg.api_hash)
        await client.connect()
        try:
            if await client.is_user_authorized():
                for channel in cfg.target_channels:
                    target = int(channel) if str(channel).lstrip("-").isdigit() else channel
                    try:
                        count = 0
                        async for _ in client.iter_messages(target, limit=1):
                            count += 1
                        channel_results.append({
                            "channel": channel,
                            "status": "ok",
                            "message_count": count,
                        })
                    except Exception as exc:
                        channel_results.append({
                            "channel": channel,
                            "status": "error",
                            "message": str(exc),
                            "message_count": 0,
                        })
        finally:
            await client.disconnect()

    return {**base_status, "channel_results": channel_results}


class TelegramSignalCollector:
    def __init__(self, config: TelegramCollectorConfig | None = None) -> None:
        self.config = config or TelegramCollectorConfig.from_env()

    async def fetch_recent_messages(self, *, limit_per_channel: int = 100) -> list[dict[str, Any]]:
        from src.online_access import require_online_access

        require_online_access("Telegram collection")
        status = collector_status(self.config)
        if not status["ready"]:
            raise RuntimeError(status["message"])

        from telethon import TelegramClient  # type: ignore

        client = TelegramClient(
            self.config.session_name,
            int(self.config.api_id),
            self.config.api_hash,
        )
        rows: list[dict[str, Any]] = []
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError("telegram session is not authorized; run an interactive Telethon login first")
            for channel in self.config.target_channels:
                try:
                    entity = await get_channel_entity(client, channel)
                    async for message in client.iter_messages(entity, limit=limit_per_channel):
                        text = getattr(message, "raw_text", "") or ""
                        if not text.strip():
                            continue
                        rows.append(
                            {
                                "telegram_message_id": str(message.id),
                                "channel": str(channel),
                                "received_at": message.date.astimezone(KST).isoformat() if message.date else None,
                                "raw_text": text,
                            }
                        )
                except Exception as exc:
                    rows.append(
                        {
                            "telegram_message_id": "",
                            "channel": str(channel),
                            "received_at": None,
                            "raw_text": "",
                            "collector_error": str(exc),
                        }
                    )
        finally:
            await client.disconnect()
        return rows
