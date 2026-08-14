import json
import threading
import time
import requests
import websocket
from src.config import config
from src.utils.logger import logger
from src.notifier.slack import slack_error, slack_order

class KISWebSocketClient(threading.Thread):
    """
    [유튜브 11~14편 구현]
    한국투자증권(KIS) 실시간 웹소켓 시세 수신 및 주문체결 통보 데몬
    - 별도의 멀티스레드로 동작하여 메인 프로세스를 간섭하지 않습니다.
    - 연결 해제 시 자동 재연결(Auto-Reconnect) 기능을 포함합니다.
    - 실시간 주문체결 통보(H0STCNI0/H0STCNI9) 수신 시, 이를 즉각 파싱하여 슬랙 알림을 전송합니다.
    """
    def __init__(self, notify_errors: bool = True):
        super().__init__()
        self.daemon = True
        self.notify_errors = notify_errors
        self.trading_env = config.trading_env
        
        # Real-time connection details
        self.base_url = "https://openapi.koreainvestment.com:9443" if self.trading_env == "real" else "https://openapivts.koreainvestment.com:29443"
        # KIS 실시간 WebSocket: 평문 ws:// (TLS 아님), 포트 실전 21000 / 모의 31000.
        # wss://로 접속하면 평문 서버 응답에 TLS 핸드셰이크가 깨져 SSL WRONG_VERSION_NUMBER가 난다.
        self.ws_url = "ws://ops.koreainvestment.com:21000" if self.trading_env == "real" else "ws://ops.koreainvestment.com:31000"
        
        self.ws = None
        self.running = False
        self.approval_key = None
        self.active_subscriptions = set() # Store subscribed tuples of (tr_id, tr_key)
        self.reconnect_count = 0
        self.last_message_at = None
        self.last_error = ""
        self.last_error_notification_at = 0.0
        self.last_quotes = {}
        self.last_orderbooks = {}

    def get_approval_key(self) -> str:
        """[3편/11편 구현] 웹소켓 등록용 실시간 Approval Key 발급 (REST API)"""
        from src.online_access import require_online_access

        require_online_access("KIS WebSocket")
        if self.approval_key:
            return self.approval_key
        
        url = f"{self.base_url}/oauth2/Approval"
        payload = {
            "grant_type": "client_credentials",
            "appkey": config.kistock_app_key,
            "secretkey": config.kistock_app_secret
        }
        try:
            res = requests.post(url, json=payload, timeout=10)
            res.raise_for_status()
            self.approval_key = res.json()["approval_key"]
            logger.info("Successfully fetched KIS WebSocket Approval Key")
            return self.approval_key
        except Exception as e:
            logger.error(f"Failed to fetch KIS WebSocket approval key: {e}")
            raise

    def run(self):
        from src.online_access import require_online_access

        require_online_access("KIS WebSocket")
        self.running = True
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close,
                    on_open=self.on_open
                )
                logger.info(f"Connecting to KIS WebSocket server: {self.ws_url}")
                # run_forever matches heartbeat constraints (PING every 30s)
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                self.last_error = str(e)
                logger.error(f"WebSocket client loop crash: {e}")
            
            if self.running:
                self.reconnect_count += 1
                logger.info("WebSocket connection lost. Reconnecting in 5 seconds...")
                time.sleep(5)

    def stop(self):
        self.running = False
        if self.ws:
            self.ws.close()
        logger.info("KIS WebSocket client thread stopped.")

    def on_open(self, ws):
        logger.info("[WS] Connection established. Initializing subscriptions...")
        
        # 1. Subscribe to Real-time Order Execution
        # Real trading TR: H0STCNI0, Paper trading TR: H0STCNI9
        tr_id = "H0STCNI0" if self.trading_env == "real" else "H0STCNI9"
        # 체결통보 tr_key는 반드시 HTS ID여야 한다. 계좌번호로 대체하면 KIS가 구독을
        # 거부하며 연결을 끊어 5초 재접속 루프가 발생하므로 fallback을 두지 않는다.
        tr_key = config.kistock_hts_id

        if tr_key:
            logger.info(f"[WS] Subscribing to Order Execution. TR_ID={tr_id}, HTS_ID={tr_key}")
            self.subscribe(tr_id, tr_key)
        else:
            logger.info("[WS] KISTOCK_HTS_ID is not configured. Skipping order execution subscription.")
            
        # 2. Resubscribe to other existing subscriptions if any (upon reconnection)
        for sub_tr_id, sub_tr_key in self.active_subscriptions:
            if sub_tr_id != tr_id or sub_tr_key != tr_key:
                logger.info(f"[WS] Resubscribing: TR_ID={sub_tr_id}, TR_KEY={sub_tr_key}")
                self._send_subscription(sub_tr_id, sub_tr_key, "1")

    def subscribe(self, tr_id: str, tr_key: str):
        """Register a new real-time topic subscription"""
        self.active_subscriptions.add((tr_id, tr_key))
        if self.ws and self.ws.sock and self.ws.sock.connected:
            self._send_subscription(tr_id, tr_key, "1")

    def subscribe_quote(self, symbol: str):
        """Subscribe domestic real-time execution/quote ticks."""
        self.subscribe("H0STCNT0", symbol)

    def subscribe_orderbook(self, symbol: str):
        """Subscribe domestic real-time orderbook ticks."""
        self.subscribe("H0STASP0", symbol)

    def unsubscribe(self, tr_id: str, tr_key: str):
        """Cancel an active real-time topic subscription"""
        self.active_subscriptions.discard((tr_id, tr_key))
        if self.ws and self.ws.sock and self.ws.sock.connected:
            self._send_subscription(tr_id, tr_key, "2")

    def _send_subscription(self, tr_id: str, tr_key: str, tr_type: str):
        """Format and send subscription frames"""
        payload = {
            "header": {
                "approval_key": self.get_approval_key(),
                "custtype": "P",
                "tr_type": tr_type, # "1" = Subscribe, "2" = Unsubscribe
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": tr_key
                }
            }
        }
        try:
            self.ws.send(json.dumps(payload))
        except Exception as e:
            logger.error(f"[WS] Failed to send subscription: {e}")

    def on_message(self, ws, message):
        """[11~14편 구현] Handle incoming WebSocket message frames"""
        try:
            self.last_message_at = time.strftime("%Y-%m-%d %H:%M:%S")
            if message.startswith("0") or message.startswith("1"):
                # Flat plain-text data frame separated by '|'
                # Format: EncryptDvsN|TR_ID|DataCount|Payload
                parts = message.split("|")
                if len(parts) >= 4:
                    tr_id = parts[1]
                    raw_payload = parts[3]
                    
                    # Handle Order Execution 통보 (H0STCNI0 / H0STCNI9)
                    if tr_id in {"H0STCNI0", "H0STCNI9"}:
                        self._process_order_execution(raw_payload)
                    elif tr_id == "H0STCNT0":
                        self._process_realtime_quote(raw_payload)
                    elif tr_id == "H0STASP0":
                        self._process_realtime_orderbook(raw_payload)
            else:
                # JSON control frame (PING / Subscription response)
                data = json.loads(message)
                header = data.get("header", {})
                if header.get("tr_id") == "PING":
                    # PING frame heartbeat check -> reply PONG
                    self.ws.send(json.dumps({"header": {"tr_id": "PONG"}}))
                elif "rt_cd" in data:
                    logger.info(f"[WS RESPONSE] {data.get('msg1') or 'Subscription success'}")
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[WS] Error processing message: {e}")

    def _process_realtime_quote(self, payload: str):
        fields = payload.split("^")
        if len(fields) < 3:
            return
        symbol = fields[0].strip()
        if not symbol:
            return
        self.last_quotes[symbol] = {
            "symbol": symbol,
            "time": fields[1].strip() if len(fields) > 1 else "",
            "price": self._to_float(fields[2]) if len(fields) > 2 else 0.0,
            "change": self._to_float(fields[4]) if len(fields) > 4 else 0.0,
            "change_rate": self._to_float(fields[5]) if len(fields) > 5 else 0.0,
            "volume": self._to_float(fields[13]) if len(fields) > 13 else 0.0,
            "raw": payload,
        }

    def _process_realtime_orderbook(self, payload: str):
        fields = payload.split("^")
        if len(fields) < 5:
            return
        symbol = fields[0].strip()
        if not symbol:
            return
        self.last_orderbooks[symbol] = {
            "symbol": symbol,
            "ask1": self._to_float(fields[3]) if len(fields) > 3 else 0.0,
            "bid1": self._to_float(fields[4]) if len(fields) > 4 else 0.0,
            "raw": payload,
        }

    @staticmethod
    def _to_float(value: str) -> float:
        try:
            return float(str(value).strip() or 0.0)
        except Exception:
            return 0.0

    def _process_order_execution(self, payload: str):
        """
        [12편/14편 구현] 파싱 규칙을 바탕으로 실시간 주문체결 평문 데이터를 파싱하여 슬랙 카드를 전송합니다.
        평문 구분자: '^'
        """
        try:
            fields = payload.split("^")
            if len(fields) < 12:
                logger.warning(f"[WS Execution] Plain payload is too short: {payload}")
                return
            
            # Fields Mapping based on KIS Websocket spec
            # 0: 고객ID, 1: 계좌번호, 2: 주문번호, 3: 원주문번호, 4: 매도매수구분 (01: 매도, 02: 매수)
            # 5: 주문구분, 6: 종목코드, 7: 주문수량, 8: 주문가격, 9: 체결수량, 10: 체결단가
            # 11: 체결시간, 15: 체결구분 (2: 체결, 4: 접수)
            action_code = fields[4].strip()
            action = "buy" if action_code == "02" else "sell"
            
            symbol = fields[6].strip()
            qty = float(fields[9].strip() or 0.0)
            price = float(fields[10].strip() or 0.0)
            exec_type = fields[15].strip() # "2" is execution fill, "4" is acceptance
            
            if exec_type == "2" and qty > 0:
                # Execution Filled! Send Slack Card
                logger.info(f"[WS Execution] Fill Event: {symbol} | {action} | Qty={qty} | Price={price}")
                
                # Fetch basic indicator details if possible
                indicators = {"rsi": 0.0, "sma20": 0.0, "sma60": 0.0, "rt": 0.0}
                
                # Non-blocking Slack order card transmission
                slack_order(
                    name=f"실시간 체결 통보 ({symbol})",
                    symbol=symbol,
                    action=action,
                    qty=qty,
                    price=price,
                    reason="KIS WebSocket 실시간 주문 체결 완료",
                    ok=True,
                    indicators=indicators
                )
        except Exception as e:
            logger.error(f"[WS Execution] Parsing failed: {e} | Payload: {payload}")

    def on_error(self, ws, error):
        self.last_error = str(error)
        if "Connection to remote host was lost" in self.last_error:
            logger.info(f"[WS] Recoverable disconnect: {error}")
            return
        logger.error(f"[WS ERROR] {error}")
        if not self.notify_errors:
            return

        # Avoid flooding Slack during reconnect loops while still surfacing outages.
        now = time.time()
        if now - self.last_error_notification_at < 300:
            return
        self.last_error_notification_at = now
        try:
            slack_error(f"KIS WebSocket error: {error}")
        except Exception as exc:
            logger.warning(f"[WS ERROR] Slack notification failed: {exc}")

    def on_close(self, ws, close_status_code, close_msg):
        logger.info(f"[WS CLOSED] Connection closed. status={close_status_code}, msg={close_msg}")
