from __future__ import annotations

import functools
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import config
from src.utils.logger import logger
from src.db import repository as _root

KST = timezone(timedelta(hours=9))

def connect_db():
    return _root.connect_db()

def init_db() -> None:
    _root.init_db()

TOKEN_USAGE_FILE = Path(".runtime/token_usage.json")

def _load_token_usage() -> dict:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    try:
        init_db()
        with connect_db() as conn:
            conn.row_factory = sqlite3.Row
            c = conn.execute("SELECT * FROM token_usage WHERE date = ?", (today,))
            row = c.fetchone()
            if row is not None:
                return {
                    "prompt_tokens": int(row["prompt_tokens"]),
                    "completion_tokens": int(row["completion_tokens"]),
                    "total_tokens": int(row["total_tokens"]),
                    "api_calls": int(row["api_calls"])
                }
    except (sqlite3.DatabaseError, OSError, ValueError, TypeError) as exc:
        logger.warning(f"Failed to load token usage from DB: {exc}")
        
    # Fallback to JSON
    if TOKEN_USAGE_FILE.exists():
        try:
            data = json.loads(TOKEN_USAGE_FILE.read_text(encoding="utf-8"))
            if today in data:
                return data[today]
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            logger.warning(f"Failed to read token usage fallback: {exc}")
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "api_calls": 0}


def update_token_usage(prompt: int, completion: int, total: int | None = None) -> None:
    prompt = int(prompt or 0)
    completion = int(completion or 0)
    total = int(total or (prompt + completion))
    today = datetime.now(KST).strftime("%Y-%m-%d")
    
    # Update in DB
    try:
        init_db()
        with connect_db() as conn:
            c = conn.execute("SELECT * FROM token_usage WHERE date = ?", (today,))
            row = c.fetchone()
            if row is not None:
                conn.execute(
                    """
                    UPDATE token_usage
                    SET prompt_tokens = prompt_tokens + ?,
                        completion_tokens = completion_tokens + ?,
                        total_tokens = total_tokens + ?,
                        api_calls = api_calls + 1
                    WHERE date = ?
                    """,
                    (prompt, completion, total, today)
                )
            else:
                conn.execute(
                    """
                    INSERT INTO token_usage (date, prompt_tokens, completion_tokens, total_tokens, api_calls)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (today, prompt, completion, total)
                )
            conn.commit()
    except (sqlite3.DatabaseError, OSError, ValueError, TypeError) as exc:
        logger.warning(f"Failed to update token usage in DB: {exc}")
        
    # Fallback/Sync to JSON
    try:
        TOKEN_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if TOKEN_USAGE_FILE.exists():
            try:
                data = json.loads(TOKEN_USAGE_FILE.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
                logger.warning(f"Failed to read token usage JSON before update: {exc}")
        today_data = data.setdefault(today, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "api_calls": 0})
        today_data["prompt_tokens"] += prompt
        today_data["completion_tokens"] += completion
        today_data["total_tokens"] += total
        today_data["api_calls"] += 1
        
        sorted_keys = sorted(data.keys())
        if len(sorted_keys) > 30:
            for key in sorted_keys[:-30]:
                data.pop(key, None)
        TOKEN_USAGE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (sqlite3.Error, OSError, ValueError, TypeError) as e:
        logger.warning(f"Failed to save token usage to JSON: {e}")
__all__ = ['KST', 'TOKEN_USAGE_FILE', '_load_token_usage', 'update_token_usage']
