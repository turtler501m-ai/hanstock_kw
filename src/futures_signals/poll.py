"""Poll a channel for new messages, extract trading signals, store to DB.

Usage: poll.py <channel_key> <id_or_username> <label>

For each new message (id > last_message_id):
  - Group recent context (last 10 messages from same channel)
  - Ask LLM to detect & extract signals
  - Store extracted signals to DB
  - Update poll_state.last_message_id
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Add project root to sys.path to allow running as a script directly
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.futures_signals import db as signals_db
from telethon import TelegramClient
from telethon.tl.types import PeerChannel

load_dotenv()
KST = timezone(timedelta(hours=9))

raw_api_id = os.environ.get("TELEGRAM_API_ID", os.environ.get("TG_API_ID", ""))
if not raw_api_id:
    raise ValueError("TELEGRAM_API_ID (or TG_API_ID) environment variable is required")
API_ID = int(raw_api_id)

API_HASH = os.environ.get("TELEGRAM_API_HASH", os.environ.get("TG_API_HASH", ""))
if not API_HASH:
    raise ValueError("TELEGRAM_API_HASH (or TG_API_HASH) environment variable is required")

TELEGRAM_SESSION_NAME = os.environ.get("TELEGRAM_SESSION_NAME", ".runtime/telegram_session")


def _normalize_futures_symbol(raw: str) -> str:
    """텔레그램 신호의 심볼을 KIS API 형식으로 변환.

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


def telegram_session_file() -> Path:
    path = Path(TELEGRAM_SESSION_NAME)
    if path.suffix == ".session":
        return path
    if path.suffix:
        return path.with_suffix(path.suffix + ".session")
    return Path(f"{TELEGRAM_SESSION_NAME}.session")

EXTRACT_PROMPT = """다음은 텔레그램 해외선물 시그널 채널의 최근 메시지들입니다.
한국 시그널 채팅 특성상 한 시그널이 여러 메시지로 쪼개져 올라오기도 합니다.

작업: **새 메시지 (NEW로 표시) 중에서 매매 시그널만** 추출해주세요.

매매 시그널 = 다음 두 가지 유형 모두 추출:
(1) 진입 신호: "진입", "매수", "롱", "숏", "매도", "들어갑니다", "대기", "추가진입", "재진입"
(2) 청산 신호: "익절", "청산", "손절", "반매도", "정리", "종결", "마감", "나감", "종료"

일반 채팅, 안부, 뉴스, 이벤트 안내, 가입/입금/정회원 유도는 제외.

重要:
- NEW 메시지 하나에 종목/방향이 모두 없더라도, 직전 CTX에서 같은 흐름의 종목/방향을 명확히 보완할 수 있으면 추출하세요.
- 청산 신호: "익절", "청산", "손절", "반매도" 키워드가 있으면 exit로 분류
- 진입/청산 모두 keyword 있으면両方 추출
- 같은 의미의 반복 메시지가 여러 개 있으면 가장 마지막 NEW message_id 하나만 추출하세요.

출력 형식 (JSON 배열, 시그널 없으면 빈 배열 []):
[
  {{
    "message_id": 123,
    "symbol": "나스닥",
    "direction": "long",
    "entry_price": 18500.0,
    "stop_loss": 18450.0,
    "target_price": 18600.0,
    "confidence": 0.9,
    "notes": "정확한 진입가 명시"
  }}
]

필드:
- direction: "long"(진입), "short"(진입), 또는 "exit"(청산/익절/손절)
- entry_price, stop_loss, target_price: 숫자 또는 null
- confidence: 0.0-1.0 (시그널 명확도)
- notes: 한 문장

반드시 **유효한 JSON 배열만** 출력. 분석/설명/마크다운 절대 금지. 시작은 `[` 끝은 `]`.

메시지 (CTX=과거 컨텍스트, NEW=새 메시지):
{messages}
"""


def format_messages_for_prompt(new_msgs: list[dict], context_msgs: list[dict]) -> str:
    lines = []
    for m in context_msgs:
        text = (m.get("raw_text", "") or "").replace("\n", " ")[:300]
        lines.append(f"CTX | id={m.get('telegram_message_id')} | {m.get('received_at', '')[:5]} | {text}")
    for m in new_msgs:
        text = (m.get("raw_text", "") or "").replace("\n", " ")[:300]
        lines.append(f"NEW | id={m.get('telegram_message_id')} | {m.get('received_at', '')[:5]} | {text}")
    return "\n".join(lines)


def _call_claude_for_signals(prompt: str) -> list[dict]:
    """Claude API로 텔레그램 메시지에서 선물 신호 추출."""
    try:
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.debug("ANTHROPIC_API_KEY not set, skipping LLM extraction")
            return []

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()

        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        return []
    except Exception as e:
        logger.warning(f"Claude API call failed: {e}")
        return []


def extract_signals_via_llm(prompt: str) -> list[dict]:
    """Extract signals via Claude API, falling back to regex if unavailable."""
    results = _call_claude_for_signals(prompt)
    if results:
        return results
    logger.debug("LLM returned no results, using regex fallback")
    return _extract_signals_fallback(prompt)


def _extract_signals_fallback(prompt: str) -> list[dict]:
    """Simple regex-based signal extraction as fallback."""
    signals = []
    lines = prompt.split("\n")

    symbol_map = {
        "나스닥": "나스닥", "NQ": "나스닥", "NASDAQ": "나스닥",
        "MNQ": "마이크로나스닥", "마이크로나스닥": "마이크로나스닥",
        "골드": "골드", "GC": "골드", " GOLD ": "골드",
        "크루드오일": "크루드오일", "CL": "크루드오일", "WTI": "크루드오일", "유일": "크루드오일",
        "항셍": "항셍", "HSI": "항셍", "항셍이": "항셍",
    }

    entry_keywords = ["진입", "진행", "대기", "포지션", "들어갑니다", "들어가요", "매수", "매도", "롱", "숏"]
    exit_keywords = ["익절", "청산", "손절", "반매도", "정리", "종결", "마감", "나감", "종료"]

    last_symbol = None
    last_entry_direction = None
    for line in lines:
        if not line.startswith("CTX") and not line.startswith("NEW"):
            continue
        
        msg_match = re.search(r"id=(\d+)", line)
        if not msg_match:
            continue
        msg_id = int(msg_match.group(1))
        text = line

        for kw, sym in symbol_map.items():
            if kw.lower() in text.lower() or kw in text:
                last_symbol = sym
                break

        for kw in ["매수", "롱", "BUY", "long"]:
            if kw in text:
                last_entry_direction = "long"
                break

        for kw in ["매도", "숏", "SELL", "short"]:
            if kw in text:
                last_entry_direction = "short"
                break

        if not line.startswith("NEW"):
            continue

        has_exit = any(kw in text for kw in exit_keywords)
        has_entry = any(kw in text for kw in entry_keywords)

        if has_exit and last_symbol:
            signals.append({
                "message_id": msg_id,
                "symbol": last_symbol,
                "direction": "exit",
                "entry_price": None,
                "stop_loss": None,
                "target_price": None,
                "confidence": 0.7,
                "notes": "regex exit"
            })

        if has_entry and last_entry_direction and last_symbol:
            signals.append({
                "message_id": msg_id,
                "symbol": last_symbol,
                "direction": last_entry_direction,
                "entry_price": None,
                "stop_loss": None,
                "target_price": None,
                "confidence": 0.6 if last_symbol else 0.5,
                "notes": "regex entry"
            })
    
    return signals


def normalize_direction(value: str) -> str:
    if value in ("long", "short", "exit"):
        return value
    if value in ("롱", "매수", "buy"):
        return "long"
    if value in ("숏", "매도", "sell"):
        return "short"
    if value in ("청산", "익절", "손절", "반매도", "정리", "종결", "close", "exit"):
        return "exit"
    return value


def as_float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.\-]", "", value)
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def normalize_signals(signals: list[dict]) -> list[dict]:
    normalized = []
    seen = set()
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        try:
            mid = int(sig.get("message_id"))
        except (TypeError, ValueError):
            continue
        symbol = str(sig.get("symbol") or "").strip()
        direction = normalize_direction(str(sig.get("direction")))
        if not symbol or direction not in ("long", "short", "exit"):
            continue
        try:
            confidence = float(sig.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        normalized_sig = {
            "message_id": mid,
            "symbol": symbol,
            "direction": direction,
            "entry_price": as_float_or_none(sig.get("entry_price")),
            "stop_loss": as_float_or_none(sig.get("stop_loss")),
            "target_price": as_float_or_none(sig.get("target_price")),
            "confidence": max(0.0, min(confidence, 1.0)),
            "notes": str(sig.get("notes") or "").strip()[:300],
        }
        key = (
            normalized_sig["symbol"].lower(),
            normalized_sig["direction"],
            normalized_sig["entry_price"],
            normalized_sig["stop_loss"],
            normalized_sig["target_price"],
        )
        if key in seen:
            normalized = [s for s in normalized if (
                s["symbol"].lower(),
                s["direction"],
                s["entry_price"],
                s["stop_loss"],
                s["target_price"],
            ) != key]
        seen.add(key)
        normalized.append(normalized_sig)
    return normalized


def store_signals(channel_key: str, new_msgs: list[dict], signals: list[dict]) -> list[dict]:
    msg_by_id = {int(m.get("telegram_message_id", 0)): m for m in new_msgs}
    inserted_signals = []
    for sig in signals:
        mid = sig.get("message_id")
        if mid not in msg_by_id:
            continue
        m = msg_by_id[mid]
        message_date = m.get("received_at", "")
        raw_text = m.get("raw_text", "")

        inserted = signals_db.insert_signal(
            channel_key=channel_key,
            message_id=mid,
            message_date=message_date,
            raw_text=raw_text,
            symbol=sig.get("symbol"),
            direction=sig.get("direction"),
            entry_price=sig.get("entry_price"),
            stop_loss=sig.get("stop_loss"),
            target_price=sig.get("target_price"),
            confidence=sig.get("confidence"),
            notes=sig.get("notes"),
        )
        if inserted:
            inserted_signals.append({**sig, "channel_key": channel_key, "message_date": message_date, "raw_text": raw_text})
    return inserted_signals


def load_channels() -> list[dict[str, str]]:
    """Load signal-tracking channels from config/channels.json."""
    channels_path = Path("config/channels.json")
    if not channels_path.exists():
        channels_path = Path(__file__).resolve().parents[2] / "config" / "channels.json"
    if not channels_path.exists():
        return []
    with open(channels_path, encoding="utf-8") as f:
        data = json.load(f)
    return [c for c in data if c.get("signal_tracking")]


async def fetch_new_and_context(client, target, last_message_id: int, context_size: int = 10):
    """Fetch new messages and context messages from a channel."""
    try:
        entity = await client.get_entity(PeerChannel(int(target)))
    except ValueError:
        entity = await client.get_entity(target.lstrip("@"))

    new_msgs = []
    context_msgs = []
    async for m in client.iter_messages(entity, limit=500):
        if m.id > last_message_id:
            new_msgs.append(m)
        else:
            if len(context_msgs) < context_size:
                context_msgs.append(m)
            else:
                break
    new_msgs.reverse()
    context_msgs.reverse()
    return entity, new_msgs, context_msgs


async def poll_channel(channel_key: str, target: str, label: str):
    """Poll a single channel for new messages."""
    from src.online_access import require_online_access

    require_online_access("Telegram polling")
    signals_db.init_db()

    state = signals_db.get_channel_state(channel_key)
    last_id = state["last_message_id"] if state else 0
    if os.environ.get("POLLING_BACKFILL") == "1":
        last_id = 0

    session_file = telegram_session_file()
    if not session_file.exists():
        print(f"[{channel_key}] session file not found: {session_file}")
        return

    client = TelegramClient(str(session_file), API_ID, API_HASH)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print(f"[{channel_key}] not authorized")
            return

        entity, new_msgs, context_msgs = await fetch_new_and_context(client, target, last_id)

        if not new_msgs:
            print(f"[{channel_key}] no new messages (last_id={last_id})")
            return

        if last_id == 0 and os.environ.get("POLLING_BACKFILL") != "1":
            latest_id = max(m.id for m in new_msgs)
            signals_db.update_channel_state(channel_key, latest_id)
            print(f"[{channel_key}] first run: set last_id={latest_id} (no extraction)")
            return

        print(f"[{channel_key}] {len(new_msgs)} new messages, {len(context_msgs)} ctx")

        # Convert messages to dict format for prompt
        new_dicts = [{
            "telegram_message_id": str(m.id),
            "received_at": m.date.astimezone(KST).isoformat(),
            "raw_text": m.message or "",
        } for m in new_msgs]
        ctx_dicts = [{
            "telegram_message_id": str(m.id),
            "received_at": m.date.astimezone(KST).isoformat(),
            "raw_text": m.message or "",
        } for m in context_msgs]

        prompt = EXTRACT_PROMPT.format(messages=format_messages_for_prompt(new_dicts, ctx_dicts))
        signals = normalize_signals(extract_signals_via_llm(prompt))
        print(f"[{channel_key}] extracted {len(signals)} signal(s)")

        # Store signals
        msg_by_id = {m.id: m for m in new_msgs}
        inserted_signals = []
        for sig in signals:
            mid = sig.get("message_id")
            if mid not in msg_by_id:
                continue
            m = msg_by_id[mid]
            message_date = m.date.astimezone(KST).isoformat()
            raw_text = m.message or ""

            inserted = signals_db.insert_signal(
                channel_key=channel_key,
                message_id=mid,
                message_date=message_date,
                raw_text=raw_text,
                symbol=sig.get("symbol"),
                direction=sig.get("direction"),
                entry_price=sig.get("entry_price"),
                stop_loss=sig.get("stop_loss"),
                target_price=sig.get("target_price"),
                confidence=sig.get("confidence"),
                notes=sig.get("notes"),
            )
            if inserted:
                inserted_signals.append({**sig, "channel_key": channel_key, "message_date": message_date, "raw_text": raw_text})

                # executor 실행 (예외 발생해도 폴링은 계속)
                try:
                    from src.futures_signals.executor import get_executor
                    executor = get_executor()

                    # DB 저장 신호를 FuturesSignal 호환 객체로 래핑하여 실행
                    class _SignalProxy:
                        def __init__(self, sig_dict, msg_id, src_key):
                            self.id = f"{src_key}:{msg_id}"
                            self.symbol = _normalize_futures_symbol(sig_dict.get("symbol", "MNQ"))
                            self.direction = sig_dict.get("direction", "")
                            self.stop_loss = sig_dict.get("stop_loss") or 0.0
                            self.take_profits = (sig_dict.get("target_price"),) if sig_dict.get("target_price") else ()
                            self.qty = sig_dict.get("qty", 1) or 1
                            entry_raw = sig_dict.get("entry_price")
                            if entry_raw is None or float(entry_raw) <= 0:
                                self._skip_execution = True
                                self.entry = 0.0
                            else:
                                self._skip_execution = False
                                self.entry = float(entry_raw)

                    proxy = _SignalProxy(sig, mid, channel_key)
                    if proxy.direction in ("long", "short"):
                        if not getattr(proxy, '_skip_execution', False):
                            executor.execute(proxy)
                        else:
                            logger.info(f"Skipping execution for signal {proxy.id}: entry price not set")
                except Exception as exc:
                    logger.warning(f"Executor error for signal {mid}: {exc}")

        latest_id = max(m.id for m in new_msgs)
        signals_db.update_channel_state(channel_key, latest_id)
        print(f"[{channel_key}] inserted {len(inserted_signals)}, last_id -> {latest_id}")
    finally:
        await client.disconnect()


async def main():
    signals_db.init_db()

    channels = load_channels()
    if not channels:
        print("No channels with signal_tracking=true found")
        sys.exit(1)

    for ch in channels:
        key = ch.get("key", "")
        target = ch.get("id_or_username", "")
        label = ch.get("label", "")
        if not key or not target:
            continue
        try:
            await poll_channel(key, str(target), label)
        except Exception as e:
            print(f"ERROR [{key}]: {e}")


if __name__ == "__main__":
    asyncio.run(main())
