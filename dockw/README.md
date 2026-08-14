# hanstock_kw 작업 문서

이 폴더는 Hanstock의 국내주식(한스톡), 미국주식(미스톡), 환경설정 대시보드를
키움증권 API 기반으로 전환하기 위한 분석, 계획, 진행 기록을 관리한다.

## 문서

- `01-current-analysis.md`: 최초 복사 범위와 현재 결합 구조
- `02-kiwoom-migration-plan.md`: 키움 REST API 전환 계획
- `03-progress.md`: 날짜별 진행 및 검증 내역
- `04-broker-contract-analysis.md`: 공통 Broker 계약과 키움 공식 API 대응표
- `05-kiwoom-rest-implementation.md`: 구현된 REST 기능과 실제 계정 검증 전 제한

## 관리 원칙

- 구현 변경과 함께 관련 문서를 같은 커밋에서 갱신한다.
- 완료 여부와 검증 명령을 `03-progress.md`에 남긴다.
- 계좌번호, API 키, 토큰 등 운영 비밀은 문서에 기록하지 않는다.
- 기본 안전값은 `DRY_RUN=true`, `TRADING_ENV=demo`,
  `ENABLE_LIVE_TRADING=false`, `REQUIRE_APPROVAL=true`로 유지한다.
