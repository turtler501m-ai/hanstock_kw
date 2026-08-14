from __future__ import annotations

from datetime import date, datetime
import re
from uuid import uuid5, NAMESPACE_URL

from .models import Direction, FuturesSignal


_DIRECTION_ALIASES: dict[str, Direction] = {
    "LONG": "long",
    "BUY": "long",
    "BULL": "long",
    "매수": "long",
    "롱": "long",
    "SHORT": "short",
    "SELL": "short",
    "BEAR": "short",
    "매도": "short",
    "숏": "short",
}

_MONTH_ALIASES = {
    "JAN": "F",
    "FEB": "G",
    "MAR": "H",
    "APR": "J",
    "MAY": "K",
    "JUN": "M",
    "JUL": "N",
    "AUG": "Q",
    "SEP": "U",
    "SEPT": "U",
    "OCT": "V",
    "NOV": "X",
    "DEC": "Z",
}

_NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
_SYMBOL_RE = re.compile(
    r"(?<![A-Z0-9])/?([A-Z]{1,6})(?:[\s\-/]*([FGHJKMNQUVXZ])\s*(\d{1,4})|[\s\-/]+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)\s*(\d{2,4}))?(?![A-Z0-9])",
    re.IGNORECASE,
)
_DIRECTION_RE = re.compile(r"\b(LONG|SHORT|BUY|SELL|BULL|BEAR)\b|매수|매도|롱|숏", re.IGNORECASE)
_ENTRY_RE = re.compile(rf"(?:\b(?:ENTRY|ENTER|ENT|AT|LIMIT)\b|진입가|진입|@)\s*[:=\-]?\s*{_NUMBER}", re.IGNORECASE)
_MARKET_ENTRY_RE = re.compile(r"(?:진입가|진입|ENTRY|ENTER|ENT)\s*[:=\-]?\s*시장가|\bMARKET\b", re.IGNORECASE)
_STOP_RE = re.compile(rf"\b(?:SL|STOP|STOP\s*LOSS|S/L)\s*[:=\-]?\s*{_NUMBER}|손절\s*[:=\-]?\s*{_NUMBER}", re.IGNORECASE)
# Bug 3 fix: no whitespace between TP and the optional index digit(s) (\d{0,2}).
# "TP 19280"  -> TP matches, outer \s* consumes " ", _NUMBER captures "19280".
# "TP1 19280" -> TP\d{0,2} matches "TP1", outer \s* consumes " ", _NUMBER captures "19280".
_TP_RE = re.compile(
    rf"\b(?:TP\d{{0,2}}|TARGET|TAKE\s*PROFIT|PROFIT\s*TARGET)\s*[:=\-]?\s*{_NUMBER}"
    rf"|(?:익절|목표가|목표\d*|청산|청산가|수익청산|부분청산|전량청산|\d+\s*차\s*청산)\s*[:=\-]?\s*{_NUMBER}",
    re.IGNORECASE,
)
_QTY_RE = re.compile(
    r"(?:Qty|qty)\s*[:=\-]?\s*(\d+)"
    r"|(\d+)\s*(?:계약|contracts?|lots?)",
    re.IGNORECASE,
)

# Korean symbol map used in _find_symbol (Bug 1 fix)
_KR_SYMBOL_MAP: dict[str, str] = {
    "나스닥100": "NQ",
    "나스닥 100": "NQ",
    "미니나스닥": "MNQ",
    "나스닥": "MNQ",
    "골드": "GC",
    "금": "GC",
    "원유": "CL",
    "크루드": "CL",
    "유가": "CL",
    "에스피": "ES",
    "러셀": "RTY",
    "다우": "YM",
}

_NOISE_WORDS = {
    "LONG",
    "SHORT",
    "BUY",
    "SELL",
    "BULL",
    "BEAR",
    "ENTRY",
    "ENTER",
    "LIMIT",
    "STOP",
    "LOSS",
    "TARGET",
    "TAKE",
    "PROFIT",
    "TP",
    "SL",
}


class FuturesSignalParseError(ValueError):
    pass


def normalize_direction(value: str) -> Direction:
    key = value.strip().upper()
    try:
        return _DIRECTION_ALIASES[key]
    except KeyError as exc:
        raise FuturesSignalParseError(f"Unsupported futures direction: {value}") from exc


def normalize_symbol(value: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    if not compact:
        raise FuturesSignalParseError("Missing futures symbol")

    month_name = re.search(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)(\d{2,4})$", compact)
    if month_name:
        root = compact[: month_name.start()]
        year = _normalize_year(month_name.group(2))
        return f"{root}{_MONTH_ALIASES[month_name.group(1)]}{year}"

    match = re.match(r"^([A-Z]{1,6})([FGHJKMNQUVXZ])(\d{1,4})$", compact)
    if match:
        return f"{match.group(1)}{match.group(2)}{_normalize_year(match.group(3))}"
    return compact


def parse_futures_signal(
    text: str,
    *,
    source: str = "telegram",
    source_message_id: str | None = None,
    received_at: datetime | None = None,
) -> FuturesSignal:
    raw_text = text.strip()
    if not raw_text:
        raise FuturesSignalParseError("Cannot parse an empty futures signal")

    direction_match = _DIRECTION_RE.search(raw_text)
    if not direction_match:
        raise FuturesSignalParseError("Missing futures direction")
    direction = normalize_direction(direction_match.group(1) or direction_match.group(0))

    symbol = _find_symbol(raw_text)
    entry = _find_optional_price(_ENTRY_RE, raw_text)
    stop_loss = _find_optional_price(_STOP_RE, raw_text)
    take_profits = tuple(_match_price(match) for match in _TP_RE.finditer(raw_text))
    market_entry = _MARKET_ENTRY_RE.search(raw_text) is not None or ("시장가" in raw_text and "진입" in raw_text)
    qty = _parse_qty(raw_text)

    needs_review = False
    if entry is None and market_entry:
        entry = 0.0
        needs_review = True
    if stop_loss is None and market_entry:
        stop_loss = 0.0
        needs_review = True

    # Bug 2 fix: when entry is still None and it's not a market entry, try to find the first
    # number that appears after the direction keyword (e.g. "NQ LONG 19200 SL 19150 TP 19280").
    if entry is None and not market_entry:
        dir_end = direction_match.end()
        remaining = raw_text[dir_end:]
        first_num_match = re.search(
            r"(?:^|[\s@:=\-]+)([0-9]+(?:\.[0-9]+)?)(?!\s*(?:계약|contracts?|lots?))",
            remaining,
        )
        if first_num_match:
            candidate = float(first_num_match.group(1))
            # Only accept prices in a reasonable futures range to avoid picking up qty values
            if 100 <= candidate <= 1_000_000:
                entry = candidate

    if entry is None:
        raise FuturesSignalParseError("Missing entry")
    if stop_loss is None:
        raise FuturesSignalParseError("Missing stop loss")
    if not take_profits and not market_entry:
        raise FuturesSignalParseError("Missing take profit")

    if not needs_review:
        _validate_price_shape(direction, entry, stop_loss, take_profits)
    message_id = source_message_id or str(uuid5(NAMESPACE_URL, f"{source}:{raw_text}"))
    return FuturesSignal(
        id=f"{source}:{message_id}",
        source=source,
        source_message_id=message_id,
        received_at=received_at,
        raw_text=raw_text,
        symbol=symbol,
        direction=direction,
        entry=entry,
        stop_loss=stop_loss,
        take_profits=take_profits,
        qty=qty,
        status="needs_review" if needs_review else "parsed",
    )


def _parse_qty(text: str) -> int:
    """계약수 파싱. 실패 시 기본값 1."""
    match = _QTY_RE.search(text)
    if not match:
        return 1
    for group in match.groups():
        if group is not None:
            try:
                value = int(group)
                return value if value > 0 else 1
            except (TypeError, ValueError):
                pass
    return 1


def _current_quarter_code(root: str) -> str:
    """Return a full futures symbol like 'MNQ' + nearest quarter month code + 2-digit year."""
    today = date.today()
    month = today.month
    year = today.year % 100
    if month <= 3:
        mc = "H"
    elif month <= 6:
        mc = "M"
    elif month <= 9:
        mc = "U"
    else:
        mc = "Z"
    return f"{root}{mc}{year:02d}"


def _find_symbol(text: str) -> str:
    # English symbol regex path: prefer explicit contract symbols like "NQM26".
    for match in _SYMBOL_RE.finditer(text):
        candidate = match.group(0).strip()
        root = match.group(1).upper()
        if root not in _NOISE_WORDS:
            return normalize_symbol(candidate)

    # Bug 1 fix: fall back to Korean symbol aliases when no English symbol was found.
    # Sort by descending key length so longer matches win (e.g. "나스닥100" before "나스닥").
    for kr_name, fut_root in sorted(_KR_SYMBOL_MAP.items(), key=lambda x: -len(x[0])):
        if kr_name in text:
            return _current_quarter_code(fut_root)

    raise FuturesSignalParseError("Missing futures symbol")


def _find_required_price(pattern: re.Pattern[str], text: str, label: str) -> float:
    match = pattern.search(text)
    if not match:
        raise FuturesSignalParseError(f"Missing {label}")
    return _match_price(match)


def _find_optional_price(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    if not match:
        return None
    return _match_price(match)


def _match_price(match: re.Match[str]) -> float:
    for group in match.groups():
        if group is not None:
            return float(group)
    raise FuturesSignalParseError("Missing price")


def _normalize_year(value: str) -> str:
    if len(value) == 4:
        return value[-2:]
    return value.zfill(2)


def _validate_price_shape(direction: Direction, entry: float, stop_loss: float, take_profits: tuple[float, ...]) -> None:
    if direction == "long":
        if stop_loss >= entry:
            raise FuturesSignalParseError("Long stop loss must be below entry")
        if any(target <= entry for target in take_profits):
            raise FuturesSignalParseError("Long take profits must be above entry")
    else:
        if stop_loss <= entry:
            raise FuturesSignalParseError("Short stop loss must be above entry")
        if any(target >= entry for target in take_profits):
            raise FuturesSignalParseError("Short take profits must be below entry")
