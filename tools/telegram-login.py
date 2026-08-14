from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from dotenv import load_dotenv


def _env(name: str) -> str:
    import os

    return os.environ.get(name, "").strip()


async def main() -> int:
    load_dotenv(override=True)

    api_id = _env("TELEGRAM_API_ID")
    api_hash = _env("TELEGRAM_API_HASH")
    session_name = _env("TELEGRAM_SESSION_NAME") or ".runtime/futures_telegram"

    if not api_id or not api_hash:
        print("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env", file=sys.stderr)
        return 1
    if not api_id.isdigit():
        print("TELEGRAM_API_ID must be numeric", file=sys.stderr)
        return 1

    try:
        from telethon import TelegramClient
    except ImportError:
        print("telethon is not installed. Run: python -m pip install telethon", file=sys.stderr)
        return 1

    Path(session_name).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(session_name, int(api_id), api_hash)

    print(f"Creating Telegram session: {session_name}")
    print("Enter your phone number when prompted, then enter the Telegram login code.")
    print("If two-step verification is enabled, enter your Telegram password when prompted.")

    await client.start()
    me = await client.get_me()
    print(f"Telegram session authorized: {getattr(me, 'username', None) or getattr(me, 'id', '')}")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
