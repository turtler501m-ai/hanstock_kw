# Hanstock

키움 REST API 기반 국내·미국주식 자동매매와 AI 전략 운영 대시보드를 제공하는
Python/FastAPI 프로젝트입니다. 과거 KIS·해외선물·QuantConnect 연동은 제거되었으며,
현재 국내주식 브로커는 키움만 지원합니다.

## 빠른 실행

아래 `scripts/local/`, `scripts/vm/`, `tools/` 경로가 공식 진입점입니다.
저장소 루트의 동명 `.ps1`/`.cmd` 파일은 기존 사용자 명령을 유지하기 위한
호환 래퍼이며, 새 운영 절차에서는 사용하지 않습니다. 내부 문서와 자동화가
공식 경로로 전환된 뒤 다음 주요 릴리스에서 제거합니다.

로컬 Windows:

```powershell
.\scripts\local\server.cmd restart
```

VM/Linux:

```bash
./scripts/vm/server.sh restart
```

## 자동 배포

기본 배포 대상은 OCI 운영 VM(`168.110.102.249`, user `ubuntu`)입니다. 대시보드는 VM의 `127.0.0.1:8000`에 바인딩되므로 `scripts/local/connect-vm.ps1` 또는 SSH 터널을 통해 접속합니다. 자세한 대상/환경변수는 `scripts/local/README.md` 참조.

```powershell
.\scripts\local\deploy-vm.ps1
```

VM 폴더를 백업하고 새로 clone해서 현행화:

```powershell
.\scripts\local\deploy-vm.ps1 -FreshClone
```

## 배포 의존성

운영 배포에서는 검증된 정확한 버전이 기록된 constraints 파일을 함께 사용합니다.

```powershell
pip install -c constraints-deploy.txt -r requirements.txt
```

`requirements-*.txt`는 지원 버전 범위를, `constraints-deploy.txt`는 배포에 사용하는
직접 의존성의 정확한 버전을 나타냅니다. 현재 저장소는 해시 기반 lock 파일을
제공하지 않으므로 `--require-hashes`를 사용하지 않습니다.

## 검증

```powershell
powershell -ExecutionPolicy Bypass -File tools\verify-local.ps1
python -m unittest discover -s tests -t .
```

## 문서

전체 사용설명서는 아래 단일 문서에 정리되어 있습니다.

```text
doc/운영가이드.md
```

현재 운영 절차와 안전 설정은 `doc/운영가이드.md` 및 이 README를 우선합니다.
키움 전환 과정의 분석 자료는 `dockw/`에 보존되어 있습니다.
