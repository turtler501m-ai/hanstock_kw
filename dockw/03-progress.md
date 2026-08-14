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
