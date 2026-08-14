# 진행 내역

## 2026-08-14

### 완료

- 대상 GitHub 저장소가 빈 저장소임을 확인했다.
- 별도 로컬 작업 디렉터리 `C:\MSF-LOC\workstudy\hanstock_kw`에 clone했다.
- 한스톡·미스톡·환경설정 대시보드의 실행 기준 소스를 복사했다.
- 환경 비밀, 데이터베이스, 로그, 런타임 상태 및 Android 앱을 제외했다.
- 현재 결합 구조와 키움 REST API 전환 계획을 문서화했다.
- 전체 테스트가 참조하는 VM 운영 스크립트를 추가하고 관련 테스트 6건 통과를 확인했다.

### 대기

- 최초 기준선 커밋과 GitHub push
- 증권사 공통 인터페이스 설계 및 구현

### 검증 기록

- 최초 `python -m unittest discover -s tests`: 909건 실행, 실패 5건·오류 8건
- 누락된 `scripts/vm` 복사 후 VM 스크립트 테스트 6건 통과
- 최초 오류 8건은 복사 범위에서 빠졌던 VM 스크립트 파일 참조로 확인되어 해소
- 실패 5건 중 스케줄러 관련 3건은 원본 저장소에서도 동일하게 재현됨
- 나머지 2건은 전체 suite 실행 순서에서만 나타나는 상태 오염 가능성이 있으며 개별 실행은 통과
- VM 스크립트 추가 후 전체 재실행: 909건, 실패 5건·오류 3건. 오류는 제외된
  QuantConnect 문서, 로컬 `.env` 비밀 미복사 및 전체 suite 상태 오염과 연관되어 있다.
- `HANSTOCK_TESTING=1`, `ONLINE_ACCESS_BLOCKED=true`에서 애플리케이션 import 성공
- 필수 화면 `/`, `/mistock`, `/env-settings` 라우트 등록 확인
- `.runtime/`과 `data/`가 Git ignore 대상임을 확인
- 최초 기준선 커밋 `1930e4d`를 GitHub `main` 브랜치에 push 완료

## 2026-08-14 — Broker 공통 계약 1차

### 완료

- 멀티 에이전트로 KIS 계약, 직접 호출부, 키움 공식 API 대응을 병렬 분석했다.
- 증권사 중립 잔고·보유종목·시세·일봉·주문·체결 모델을 추가했다.
- `DomesticStockBroker` Protocol과 broker factory를 추가했다.
- 기존 KIS 응답을 typed 모델로 변환하는 `KISBrokerAdapter`를 추가했다.
- 점진적 이전을 위해 기존 KIS-shaped 메서드 위임을 호환 계층으로 유지했다.
- 대시보드 중앙 `_get_api()`를 broker factory에 연결했다.
- `DOMESTIC_STOCK_BROKER=kis` 기본 설정을 추가했다.

### 다음 작업

- 잔고 파서와 주문 라우터를 typed 모델 소비자로 전환
- 키움 OAuth 및 공통 HTTP/연속조회 클라이언트 구현
- 공개 KIS 구현의 `revise_order`/`get_order_snapshot` 불일치 해소

### 검증

- `python -m unittest tests.test_broker_contract tests.test_dashboard_settings_schema tests.test_dashboard_execution_plan tests.test_order_router`: 21건 통과
- `python -m unittest tests.test_dashboard_core`: 81건 통과

## 2026-08-14 — 키움 REST 전환 1차 구현

### 완료

- OAuth, 공통 POST, 연속조회와 운영·모의 호출 제한을 구현했다.
- 잔고·예수금·시세·일봉·업종일봉·거래량순위 조회를 구현했다.
- 매수·매도·정정·취소와 주문체결 조회를 구현했다.
- 기존 대시보드가 키움 adapter를 사용할 수 있도록 임시 호환 facade를 구현했다.
- 환경설정 화면에 broker 선택과 키움 운영·모의 자격증명을 추가했다.
- 환경설정 API가 KIS 및 키움 secret을 평문으로 반환하던 문제를 차단했다.
- 주문 취소 시 키움 필수값인 종목코드를 전달하도록 국내주식 라우트를 보완했다.
- 일반 국내주식 정정·취소 API 경로를 추가하고 기존 `/api/kis/*` 경로는 호환 유지했다.

### 검증

- 키움 client·adapter·factory·설정 테스트 31건 통과
- 대시보드·주문·트레이더 핵심 회귀 테스트 121건 통과
- 대시보드 설정 포함 집중 회귀 테스트 113건 통과
- 전체 suite 938건 실행: 기존 기준선 실패 5건·오류 3건과 신규 기대값 실패 1건 확인
- 신규 실패는 취소 요청에 `symbol`이 추가된 의도된 계약 변경으로 테스트를 갱신함
- `python -m compileall -q src` 통과

### 외부 검증 대기

- 애플리케이션 내부 국내·미국 OAuth 토큰 캐시와 자동 갱신 검증
- 국내·미국 모의계좌의 실제 잔고 조회
- 국내 모의계좌 매수·정정·취소·체결 동기화
- 미국주식 주문·정정·취소·체결 adapter와 미스톡 연동
- 미국 모의계좌 매수·정정·취소·체결 동기화
- 조건검색 및 실시간 주문체결 WebSocket

## 2026-08-14 — 국내·미국 모의계좌 인증 사전 확인

### 완료

- 키움에 등록한 VM 네트워크 환경에서 국내주식 모의계좌의 OAuth 토큰 발급을
  수동 smoke test로 확인했다.
- 같은 환경에서 미국주식 모의계좌의 OAuth 토큰 발급을 수동 smoke test로 확인했다.
- 두 시장의 계좌번호와 App Key/Secret은 환경 비밀로만 관리하고 문서에는 기록하지 않았다.

### 완료로 보지 않는 범위

- 위 결과는 자격증명과 허용 네트워크의 기본 인증 확인이다.
- 애플리케이션 내부 토큰 캐시, 만료 전 자동 갱신과 시장별 토큰 격리는 아직 검증 전이다.
- 국내·미국 잔고 조회와 주문·정정·취소·체결 동기화는 아직 실제 모의계좌 검증 전이다.
- 미국주식 주문 adapter와 실제 모의주문 연결은 별도 구현 대상이다.

## 2026-08-14 — 미국주식 모의계좌 조회 연동

### 완료

- `MISTOCK_STOCK_BROKER=kiwoom` 선택을 추가했다.
- 미국주식 모의계좌 전용 자격증명으로 키움 REST OAuth client를 생성한다.
- `ust21070` 잔고·보유종목 연속조회를 기존 미스톡 잔고 형식으로 변환한다.
- 환경설정 화면에 미국주식 broker 선택과 모의·실전 자격증명 필드를 추가했다.
- 현재 범위에서 키움 미국주식 실전 환경과 모든 주문 전송은 fail-closed로 차단했다.

### 검증

- 키움 client·국내외 adapter·broker·대시보드·미스톡 집중 회귀 테스트 175건 통과
- VM 서비스 및 배포 스크립트 테스트 9건 통과
- 전체 suite 947건 실행: 기준선과 동일한 실패 5건·오류 3건
