# -*- coding: utf-8 -*-
"""AI스톡 공통 상수: 시장 코드, 판단, 상태, 자동화 레벨.

모든 enum류는 단일 출처로 두고 서비스/저장소/API가 공유한다(§4.1·§4.2·§5.5).
"""
from __future__ import annotations

# 시장 코드 (§4.1). ALL은 조회 집계용일 뿐 저장하지 않는다.
MARKET_ALL = "ALL"
MARKET_KR = "KR"  # AI한스톡
MARKET_US = "US"  # AI미스톡
STORABLE_MARKETS = (MARKET_KR, MARKET_US)
QUERY_MARKETS = (MARKET_ALL, MARKET_KR, MARKET_US)

# 후보 판단 (§4.2). buy/sell은 사용하지 않는다.
DECISION_STRONG_WATCH = "strong_watch"
DECISION_WATCH = "watch"
DECISION_NEUTRAL = "neutral"
DECISION_AVOID = "avoid"
DECISION_INSUFFICIENT = "insufficient_data"
DECISIONS = (
    DECISION_STRONG_WATCH,
    DECISION_WATCH,
    DECISION_NEUTRAL,
    DECISION_AVOID,
    DECISION_INSUFFICIENT,
)

# 시장 국면 (§4 시장 브리핑)
REGIMES = ("bullish", "bearish", "sideways", "high_volatility", "insufficient_data")

# 관찰종목 상태 (§5.5)
WATCH_DISCOVERED = "discovered"
WATCH_WATCHING = "watching"
WATCH_CONFIRMED = "confirmed"
WATCH_REJECTED = "rejected"
WATCH_EXPIRED = "expired"
WATCH_EXECUTION_PLANNED = "execution_planned"
WATCH_STATUSES = (
    WATCH_DISCOVERED,
    WATCH_WATCHING,
    WATCH_CONFIRMED,
    WATCH_REJECTED,
    WATCH_EXPIRED,
    WATCH_EXECUTION_PLANNED,
)
# 허용된 상태 전이 (§5.5)
WATCH_TRANSITIONS = {
    WATCH_DISCOVERED: {WATCH_WATCHING, WATCH_REJECTED, WATCH_EXPIRED},
    WATCH_WATCHING: {WATCH_CONFIRMED, WATCH_REJECTED, WATCH_EXPIRED},
    WATCH_CONFIRMED: {WATCH_EXECUTION_PLANNED, WATCH_REJECTED, WATCH_EXPIRED},
    WATCH_EXECUTION_PLANNED: set(),
    WATCH_REJECTED: set(),
    WATCH_EXPIRED: set(),
}

# 스캔 상태 (§5.3)
SCAN_QUEUED = "queued"
SCAN_RUNNING = "running"
SCAN_COMPLETED = "completed"
SCAN_PARTIAL = "partial"
SCAN_FAILED = "failed"
SCAN_STATUSES = (SCAN_QUEUED, SCAN_RUNNING, SCAN_COMPLETED, SCAN_PARTIAL, SCAN_FAILED)
SCAN_ACTIVE = (SCAN_QUEUED, SCAN_RUNNING)

# 데이터 품질 (§4.3)
DATA_GOOD = "good"
DATA_LIMITED = "limited"
DATA_INSUFFICIENT = "insufficient"

# 종목 유형 (§4.6)
INSTRUMENT_STOCK = "stock"
INSTRUMENT_ETF = "etf"

# 자동화 레벨 (§1.2). 0=분석만 … 6=주문 자동 실행.
AUTOMATION_ANALYSIS = 0
AUTOMATION_DISCOVERY = 1
AUTOMATION_WATCH = 2
AUTOMATION_CONFIRM = 3
AUTOMATION_PLAN = 4
AUTOMATION_APPROVE = 5
AUTOMATION_EXECUTE = 6
AUTOMATION_LEVELS = tuple(range(0, 7))
DEFAULT_AUTOMATION_LEVEL = AUTOMATION_PLAN  # 기본: 실행 계획까지만 자동

# 2차 실시간 타이밍 (§4.8·§6.8)
SIGNAL_ENTRY = "entry"
SIGNAL_EXIT = "exit"
SIGNAL_HOLD = "hold"
SIGNAL_TYPES = (SIGNAL_ENTRY, SIGNAL_EXIT, SIGNAL_HOLD)
SIGNAL_TRIGGERS = ("breakout", "pullback", "momentum", "stop", "target", "invalidation", "time")

# 시황 브리핑 주기 (§4.7)
BRIEFING_PERIODS = ("monthly", "weekly", "daily")
