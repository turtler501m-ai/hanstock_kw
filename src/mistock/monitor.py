from __future__ import annotations

import os
import json
import sys
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to sys.path to allow running as a script directly
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.mistock import trader as mistock_trader
from src.mistock.config import config as mistock_config
from src.notifier.slack import send_mistock_slack
from src.utils.logger import logger

KST = timezone(timedelta(hours=9))


def is_us_market_open() -> bool:
    """
    현재 한국 시각(KST) 기준 미국 정규장 운영 시간 내에 있는지 검사합니다.
    """
    now = datetime.now(KST)
    is_dst = 3 <= now.month <= 11
    current_time_str = now.strftime("%H:%M")
    if is_dst:
        # 서머타임 운영: 22:30 ~ 익일 05:00 (안전 마진 포함 익일 05:05까지 모니터링 허용)
        return ("22:30" <= current_time_str <= "23:59") or ("00:00" <= current_time_str <= "05:05")
    else:
        # 일반 시간 운영: 23:30 ~ 익일 06:00 (안전 마진 포함 익일 06:05까지 모니터링 허용)
        return ("23:30" <= current_time_str <= "23:59") or ("00:00" <= current_time_str <= "06:05")


def run_monitoring_cycle() -> dict:
    """
    장중 미스톡 주문 상태 및 KIS API 서킷 브레이커 상태를 모니터링합니다.
    오류 발생 시 슬랙 긴급 알림을 발송합니다.
    """
    logger.info("[MISTOCK MONITOR] Starting health monitoring cycle.")
    
    # 장중 시간대가 아닌 경우 모니터링을 스킵합니다 (장외 시간 거짓 경보 방지)
    if not is_us_market_open():
        logger.info("[MISTOCK MONITOR] Skipped: Outside US market hours.")
        return {"status": "skipped", "reason": "outside_market_hours"}

    alerts = []

    # 1. KIS API 서킷 브레이커 오픈 검사
    try:
        if mistock_config.trading_env in {"demo", "real"}:
            client = mistock_trader._get_kis_client()
            now_utc = datetime.now(timezone.utc)
            cb_status = client.circuit.status(
                now_utc,
                max_errors=client.config.circuit_max_errors,
                cooldown_seconds=client.config.circuit_cooldown_seconds
            )
            if cb_status.get("opened", False):
                err_msg = (
                    f"🚨 *[미스톡 경보] KIS API 서킷 브레이커 오픈 감지!*\n"
                    f"연속 오류 횟수: {cb_status['error_count']}/{cb_status['max_errors']}회\n"
                    f"서킷 오픈 시각: {cb_status['opened_at']}\n"
                    f"재시도 대기 시간: {cb_status['retry_after_seconds']}초\n"
                    f"👉 대시보드에서 KIS API 상태 및 원본 에러 로그를 확인하고 Circuit Breaker를 리셋하세요."
                )
                alerts.append(err_msg)
    except Exception as e:
        logger.error(f"[MISTOCK MONITOR] Failed to check Circuit Breaker: {e}")
        alerts.append(f"⚠️ *[미스톡 모니터 오류] 서킷 브레이커 체크 실패:* {e}")

    # 2. 최신 스케줄러 결과 파일 검사
    try:
        result_path = Path(".runtime/mistock/daily_auto_last_result.json")
        if result_path.exists():
            data = json.loads(result_path.read_text(encoding="utf-8"))
            recorded_at_str = data.get("recorded_at", "")
            result = data.get("result", {})
            
            # 스케줄 결과 기록 시간이 최근 1시간 이내인지 확인 (크론 구동 누락 감지)
            if recorded_at_str:
                recorded_at = datetime.fromisoformat(recorded_at_str)
                # KST로 강제 변환
                if recorded_at.tzinfo is None:
                    recorded_at = recorded_at.replace(tzinfo=KST)
                else:
                    recorded_at = recorded_at.astimezone(KST)
                
                time_diff = (datetime.now(KST) - recorded_at).total_seconds()
                if time_diff > 4500: # 1시간 15분 이상 지났을 때
                    alerts.append(
                        f"⏰ *[미스톡 경보] 스케줄러 구동 지연 감지!*\n"
                        f"마지막 구동 기록 시각: {recorded_at_str} (약 {int(time_diff // 60)}분 전)\n"
                        f"👉 스케줄러 크론(Crontab) 및 서버 구동 로그를 확인해 주세요."
                    )
            
            # 스케줄러 내부 에러 검출
            if not result.get("ok", True) or result.get("status") == "failed":
                errors = result.get("errors", [])
                err_detail = "\n".join([f"- {e.get('symbol', 'UNKNOWN')}: {e.get('message', 'Reason unknown')}" for e in errors])
                alerts.append(
                    f"💥 *[미스톡 경보] 최근 스케줄러 실행 오류!*\n"
                    f"상세 에러 내역:\n{err_detail}"
                )
    except Exception as e:
        logger.error(f"[MISTOCK MONITOR] Failed to read scheduler result file: {e}")

    # 3. DB trades 테이블의 최근 주문 실패 검사
    try:
        db_path = Path(mistock_config.trade_db_path)
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            # 최근 1시간 이내에 실패한 주문을 쿼리
            one_hour_ago = (datetime.now(KST) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                SELECT ts, symbol, name, action, qty, price, response_msg 
                FROM trades 
                WHERE ok = 0 AND ts >= ?
                ORDER BY id DESC LIMIT 5
                """,
                (one_hour_ago,)
            )
            failed_trades = cur.fetchall()
            if failed_trades:
                trade_details = []
                for t in failed_trades:
                    trade_details.append(
                        f"- [{t['ts']}] {t['symbol']}({t['name']}) {t['action'].upper()} {t['qty']}주 @ ${t['price']:.2f}\n"
                        f"  사유: `{t['response_msg']}`"
                    )
                failed_trades_str = "\n".join(trade_details)
                alerts.append(
                    f"💸 *[미스톡 경보] 최근 1시간 내 주문 실패 이력 감지!*\n"
                    f"{failed_trades_str}"
                )
            conn.close()
    except Exception as e:
        logger.error(f"[MISTOCK MONITOR] Failed to query trades DB: {e}")
        alerts.append(f"⚠️ *[미스톡 모니터 오류] DB 주문 실패 이력 체크 실패:* {e}")

    # 경보 메시지 통합 및 슬랙 발송
    if alerts:
        logger.warning(f"[MISTOCK MONITOR] Found {len(alerts)} alerts. Sending Slack notification.")
        combined_text = "\n\n".join(alerts)
        send_mistock_slack(
            text="[미스톡] 장중 주문 오류 모니터링 경보",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"⚠️ *[미스톡 실시간 감시 알림]*\n\n{combined_text}"
                    }
                }
            ],
            color="#ef5350"  # 경보용 빨간색
        )
        return {"status": "alerted", "alerts_count": len(alerts)}
    
    logger.info("[MISTOCK MONITOR] Monitoring finished. All systems green.")
    return {"status": "healthy"}


if __name__ == "__main__":
    run_monitoring_cycle()
