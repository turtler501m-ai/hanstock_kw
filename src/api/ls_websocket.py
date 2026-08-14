import json
import threading
import time
import websocket
from src.api.ls_api import LSSecuritiesAPI
from src.utils.logger import logger

class LSWebSocketClient(threading.Thread):
    """LS증권 실시간 호가 및 체결 수신용 웹소켓 클라이언트"""
    def __init__(self, api_client: LSSecuritiesAPI) -> None:
        super().__init__()
        self.api_client = api_client
        self.url = "wss://openapi.ls-sec.co.kr:9443/ws"
        self.ws = None
        self.running = False
        self.daemon = True

    def run(self) -> None:
        from src.online_access import require_online_access

        require_online_access("LS Securities WebSocket")
        self.running = True
        logger.info(f"Connecting to LS Securities WebSocket: {self.url}")
        self.ws = websocket.WebSocketApp(
            self.url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open
        )
        self.ws.run_forever()

    def on_open(self, ws) -> None:
        logger.info("[LS_WS] WebSocket connection successfully established.")
        # 대표 예시로 애플(AAPL) 실시간 미국 체결(GSH) 구독 신청
        self.subscribe("GSH", "AAPL")

    def subscribe(self, tr_cd: str, symbol: str) -> None:
        if not self.ws:
            logger.warning("[LS_WS] Cannot subscribe, WebSocket not connected.")
            return
            
        payload = {
            "header": {
                "token": self.api_client.get_access_token(),
                "tr_type": "1"  # 1: 등록, 2: 해제
            },
            "body": {
                "tr_cd": tr_cd,
                "tr_key": symbol.upper().strip()
            }
        }
        try:
            self.ws.send(json.dumps(payload))
            logger.info(f"[LS_WS] Subscribed to {tr_cd} for {symbol}")
        except Exception as e:
            logger.error(f"[LS_WS] Subscription failed for {tr_cd} {symbol}: {e}")

    def on_message(self, ws, message) -> None:
        try:
            # 1. PING/PONG 처리 (LS OpenAPI는 특정 형식의 PING을 보냄)
            # 수신 데이터가 단순 PING 문자열 또는 tr_cd가 PING인 경우 즉시 PONG으로 응답
            if "PING" in message:
                self.ws.send(json.dumps({"header": {"tr_id": "PONG"}}))
                logger.debug("[LS_WS] PONG sent in response to PING.")
                return

            data = json.loads(message)
            header = data.get("header", {})
            tr_cd = data.get("tr_cd")
            
            # JSON 형태의 PING 처리 대응
            if header.get("tr_id") == "PING" or tr_cd == "PING":
                self.ws.send(json.dumps({"header": {"tr_id": "PONG"}}))
                logger.debug("[LS_WS] PONG sent in response to JSON PING.")
                return

            logger.info(f"[LS_WS MESSAGE] TR={tr_cd} | data={data}")
        except json.JSONDecodeError:
            # 평문 PING의 경우 JSON 파싱 실패하므로 여기서도 처리
            if "PING" in str(message):
                try:
                    self.ws.send(json.dumps({"header": {"tr_id": "PONG"}}))
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[LS_WS ERROR PARSING MESSAGE] {e} | Raw message: {message[:200]}")

    def on_error(self, ws, error) -> None:
        logger.error(f"[LS_WS ERROR] WebSocket error observed: {error}")

    def on_close(self, ws, status, msg) -> None:
        logger.warning(f"[LS_WS CLOSED] Status: {status}, Msg: {msg}")
        self.running = False

    def stop(self) -> None:
        self.running = False
        if self.ws:
            logger.info("[LS_WS] Closing WebSocket connection...")
            self.ws.close()
