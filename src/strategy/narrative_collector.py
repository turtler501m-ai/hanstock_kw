# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from src import trader
from src.config import config
from src.strategy.narrative_momentum import load_json_file, save_json_file

BASE_DIR = Path(__file__).resolve().parents[2]
NARRATIVE_HISTORY_PATH = BASE_DIR / ".runtime" / "narrative_history.json"
THEME_MAP_PATH = BASE_DIR / "config" / "theme_map.json"

DEFAULT_QUERIES = [
    "한국 증시 반도체 AI 전력인프라",
    "한국 증시 2차전지 방산 조선 자동차",
    "한국 증시 바이오 금융 신재생에너지",
]

THEME_KEYWORDS = {
    "반도체": ["반도체", "HBM", "메모리", "D램", "낸드", "파운드리", "삼성전자", "SK하이닉스"],
    "AI": ["AI", "인공지능", "데이터센터", "서버", "클라우드", "엔비디아"],
    "전력인프라": ["전력", "전력망", "변압기", "송전", "배전", "전선", "데이터센터 전력"],
    "신재생에너지": ["태양광", "풍력", "신재생", "재생에너지", "에너지 전환"],
    "2차전지": ["2차전지", "배터리", "양극재", "음극재", "전고체", "리튬"],
    "방산": ["방산", "방위산업", "수출 계약", "K방산", "무기"],
    "조선": ["조선", "선박", "LNG선", "수주", "해양플랜트"],
    "자동차": ["자동차", "전기차", "현대차", "기아", "모빌리티", "하이브리드"],
    "바이오": ["바이오", "제약", "신약", "임상", "셀트리온", "삼성바이오"],
    "금융": ["금융", "은행", "보험", "증권", "금리", "배당"],
}

POSITIVE_WORDS = ["강세", "상승", "수혜", "호조", "기대", "확대", "수주", "증가", "돌파", "반등"]
NEGATIVE_WORDS = ["하락", "약세", "부진", "우려", "감소", "손실", "리스크", "급락"]


def collect_narrative_history(
    *,
    history_path: Path = NARRATIVE_HISTORY_PATH,
    theme_map_path: Path = THEME_MAP_PATH,
    today_str: str | None = None,
    rss_urls: list[str] | None = None,
    max_items: int = 80,
) -> dict[str, Any]:
    today = today_str or datetime.now(trader.KST).strftime("%Y-%m-%d")
    theme_map = load_json_file(theme_map_path, {})
    if not isinstance(theme_map, dict):
        theme_map = {}
    theme_names = [str(theme) for theme in theme_map.keys()]
    if not theme_names:
        theme_names = list(THEME_KEYWORDS.keys())

    if bool(getattr(config, "online_access_blocked", False)):
        return {
            "ok": False,
            "generated": False,
            "today": today,
            "article_count": 0,
            "errors": ["online access is blocked"],
        }

    urls = rss_urls if rss_urls is not None else _rss_urls_from_env()
    articles, errors = _fetch_articles(urls, max_items=max_items)
    entry = build_narrative_entry(articles, theme_names, today)
    narratives = entry.get("dominant_narratives", [])
    if not narratives:
        return {
            "ok": False,
            "generated": False,
            "today": today,
            "article_count": len(articles),
            "errors": errors + ["no matching narrative articles"],
            "history_path": _display_path(history_path),
        }

    previous = load_json_file(history_path, [])
    if not isinstance(previous, list):
        previous = []
    remaining = [row for row in previous if isinstance(row, dict) and str(row.get("date")) != today]
    history = [entry] + remaining[:9]
    save_json_file(history_path, history)
    return {
        "ok": True,
        "generated": True,
        "today": today,
        "article_count": len(articles),
        "narrative_count": len(narratives),
        "history_path": _display_path(history_path),
        "errors": errors,
        "entry": entry,
    }


def build_narrative_entry(articles: list[dict[str, str]], theme_names: list[str], today: str) -> dict[str, Any]:
    theme_hits: dict[str, list[dict[str, str]]] = defaultdict(list)
    keyword_counts: Counter[str] = Counter()
    for article in articles:
        text = _article_text(article)
        normalized = text.lower()
        for theme in theme_names:
            keywords = THEME_KEYWORDS.get(theme, [theme])
            hit_count = 0
            for keyword in keywords:
                if keyword.lower() in normalized:
                    hit_count += len(re.findall(re.escape(keyword.lower()), normalized))
            if hit_count > 0:
                theme_hits[theme].append(article)
                keyword_counts[theme] += hit_count

    narratives = []
    shifts = []
    for theme, hit_articles in sorted(theme_hits.items(), key=lambda item: (-keyword_counts[item[0]], item[0])):
        strength = min(95, 68 + keyword_counts[theme] * 7 + len(hit_articles) * 3)
        sentiment = _sentiment_for_articles(hit_articles)
        direction = "rising" if strength >= 70 else "stable"
        title_samples = [str(item.get("title") or "").strip() for item in hit_articles[:3]]
        narratives.append(
            {
                "theme": _theme_label(theme, hit_articles),
                "strength": strength,
                "sentiment": sentiment,
                "direction": direction,
                "affected_sectors": [theme],
                "key_facts": [title for title in title_samples if title],
            }
        )
        shifts.append({"theme": _theme_label(theme, hit_articles), "change": "rising" if direction == "rising" else "new"})

    mood_score = min(95, 55 + len(narratives) * 4)
    return {
        "date": today,
        "market_mood": "risk_on" if narratives else "neutral",
        "mood_score": mood_score,
        "dominant_narratives": narratives[:8],
        "narrative_shifts": shifts[:8],
        "source": "rss_keyword_collector",
    }


def _rss_urls_from_env() -> list[str]:
    raw = os.environ.get("NARRATIVE_NEWS_RSS_URLS", "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [f"https://news.google.com/rss/search?q={quote(query)}&hl=ko&gl=KR&ceid=KR:ko" for query in DEFAULT_QUERIES]


def _fetch_articles(urls: list[str], *, max_items: int) -> tuple[list[dict[str, str]], list[str]]:
    import requests

    articles: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for url in urls:
        try:
            response = requests.get(url, timeout=10, headers={"User-Agent": "hanstock-narrative-collector/1.0"})
            response.raise_for_status()
            parsed = _parse_rss(response.text)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")
            continue
        for article in parsed:
            key = str(article.get("link") or article.get("title") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            articles.append(article)
            if len(articles) >= max_items:
                return articles, errors
    return articles, errors


def _parse_rss(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall(".//item"):
        items.append(
            {
                "title": _child_text(item, "title"),
                "description": _strip_html(_child_text(item, "description")),
                "link": _child_text(item, "link"),
                "published": _child_text(item, "pubDate"),
            }
        )
    return items


def _child_text(item: ET.Element, tag: str) -> str:
    node = item.find(tag)
    return (node.text or "").strip() if node is not None else ""


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value or "").strip()


def _article_text(article: dict[str, str]) -> str:
    return f"{article.get('title', '')} {article.get('description', '')}"


def _sentiment_for_articles(articles: list[dict[str, str]]) -> str:
    text = " ".join(_article_text(article) for article in articles)
    positive = sum(text.count(word) for word in POSITIVE_WORDS)
    negative = sum(text.count(word) for word in NEGATIVE_WORDS)
    if positive >= negative + 2:
        return "bullish"
    if positive > negative:
        return "positive"
    if negative > positive:
        return "mixed"
    return "positive"


def _theme_label(theme: str, articles: list[dict[str, str]]) -> str:
    if articles:
        return f"{theme} 관련 시장 관심 확대"
    return theme


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(BASE_DIR))
    except ValueError:
        return str(path)
