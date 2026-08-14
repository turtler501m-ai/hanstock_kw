# 국내주식 Broker 계약 분석

작성일: 2026-08-14

## 병렬 분석 결과

- KIS 구현은 `src/trader.py`, `src/api/kis_api.py`, `src/kis_client.py`에 3중으로 존재한다.
- 구현별로 페이지 처리, 시세 실패 정책, 주문 snapshot 지원 범위가 다르다.
- 대시보드와 전략은 잔고의 `output1/output2/pdno/hldg_qty`, 주문의
  `rt_cd/msg1`에 직접 결합되어 있다.
- `revise_order()`는 대시보드에서 호출되지만 공개 `KIStockAPI`에는 없고 저수준
  `KISClient.revise_domestic_order()`에만 있어 잠재 결함이다.
- 조건검색은 키움에서 WebSocket capability이므로 핵심 주문·조회 Protocol과 분리한다.

## 이번 단계의 결정

1. `src/broker/models.py`에 증권사 중립 모델을 둔다.
2. `DomesticStockBroker`는 `fetch_*`, `submit_order` typed API를 제공한다.
3. `KISBrokerAdapter`는 typed API와 기존 KIS-shaped 메서드를 동시에 제공한다.
4. 기존 소비자는 한 번에 바꾸지 않고 대시보드 중앙 생성점부터 factory에 연결한다.
5. `DOMESTIC_STOCK_BROKER` 기본값은 `kis`로 유지한다.
6. 키움 선택은 구현 전까지 명시적으로 실패하여 잘못된 실거래 fallback을 막는다.

## 키움 공식 API 대응

| 기능 | 키움 TR | 경로/비고 |
|---|---|---|
| 토큰 | `au10001` | `/oauth2/token` |
| 계좌평가잔고 | `kt00018` | `/api/dostk/acnt` |
| 예수금 | `kt00001` | `/api/dostk/acnt` |
| 현재가 | `ka10001` | `/api/dostk/stkinfo` |
| 일봉 | `ka10081` | `/api/dostk/chart` |
| 업종 일봉 | `ka20006` | `/api/dostk/chart` |
| 거래량 순위 | `ka10030` | `/api/dostk/rkinfo` |
| 매수/매도 | `kt10000`/`kt10001` | `/api/dostk/ordr` |
| 정정/취소 | `kt10002`/`kt10003` | `/api/dostk/ordr` |
| 체결내역 | `kt00007` | 계좌별 주문체결 상세 |
| 조건검색 | `ka10171`~`ka10174` | WebSocket |

공식 문서:

- https://openapi.kiwoom.com/guide/apiguide?dummyVal=0
- https://openapi.kiwoom.com/intro?dummyVal=0
- https://openapi.kiwoom.com/guide/apiguide?jobTpCode=15
- https://openapi.kiwoom.com/m/guide/apiguide?jobTpCode=13

## 키움 구현 시 지켜야 할 제약

- 운영과 모의 App Key를 분리한다.
- 응답 Header `cont-yn=Y`와 `next-key`를 사용해 연속조회한다.
- 국내 실전은 주문·조회 각각 초당 5회, 모의는 TR별 초당 1회 제한을 적용한다.
- 모의 국내주식 주문은 KRX만 지원하므로 NXT/SOR 주문을 차단한다.
- 내부 모델에서 `symbol`과 `exchange`를 분리한다.
- 부호가 포함된 가격 문자열의 정규화 정책을 명시하고 fixture로 고정한다.
