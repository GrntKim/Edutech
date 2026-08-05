# edutech(가제)

학생 눈높이 맞춤 AI 개념 교안 생성 시스템

## 팀원

- 김준명
- 나윤서
- 박지민
- 이서현
- 현세은

## 개요

AI 개념(분류, 예측, 패턴 인식 등)을 설명할 때, 대상 학년까지 누적으로 배운 교육과정 범위 안에서만 비유와 설명을 구성하도록 검색·검증하며 교안을 자동 생성하는 시스템입니다.

## 기술 스택

- **웹 프레임워크**: FastAPI + Jinja2 (서버사이드 렌더링)
- **실행 환경**: Python 3.12
- **데이터 검증**: Pydantic v2
- **데이터베이스**: Cloud SQL for PostgreSQL + pgvector (드라이버: psycopg 3)
- **LLM**: Gemini API `gemini-3.6-flash`
- **배포**: Google Cloud Run
- **도메인**: Cloudflare

## 사전 준비물

시작하기 전에 아래 항목이 준비되어 있어야 합니다.

- Python 3.12.7 (프로젝트는 이 버전으로 고정되어 있습니다)
- pyenv (macOS) 또는 py 런처 (Windows)
- Git
- Cloud SQL Proxy (DB 접속에 필요합니다. 아래 4단계 참고)
- gcloud CLI

## 프로젝트 구조

```
edutech/
├── app/
│   ├── agents/                  각 판단 단계별 로직 (담당자별로 폴더 하나씩)
│   │   ├── concept_collect/     AI 개념 분석
│   │   ├── curriculum_search/   교육과정 성취기준 검색 (RAG Retrieval)
│   │   ├── mapping/             AI 개념과 단원 매핑
│   │   ├── lesson_generate/     교안 및 활동지 생성
│   │   ├── validate/            학년 제약조건 검증
│   │   └── orchestrate.py       전체 파이프라인 호출 순서
│   ├── lib/                     공통 유틸리티 (E 소유, 변경 시 전원 합의)
│   │   ├── db.py                Cloud SQL 연결 및 공용 조회
│   │   ├── gemini.py            Gemini API 호출 래퍼
│   │   └── types.py             공통 타입 정의 (Pydantic)
│   ├── scripts/
│   │   └── ingest_curriculum.py 교육과정 데이터 파싱 및 DB 적재 (로컬 실행)
│   ├── data/
│   │   └── curriculum_units.json  정리된 교육과정 원본 데이터
│   ├── static/                  CSS, JS 등 정적 파일
│   ├── templates/               Jinja2 HTML 템플릿
│   └── main.py                  FastAPI 진입점
├── scripts/
│   └── check_db.py              DB 접속 확인 스크립트
├── tests/
├── docs/
│   └── infra/
│       └── REQ006-DB접속안내.md  DB 접속 환경 설정 안내
├── requirements.txt
├── .python-version
├── .env.example
└── README.md
```

각 `agents/` 하위 폴더는 다음 세 파일로 구성됩니다.

- `logic.py`: Gemini 호출 및 처리 로직
- `prompts.py`: 프롬프트 템플릿
- `schema.py`: 해당 에이전트의 모듈 전용 타입 (공용 타입은 `lib/types.py`)

## 로컬 개발 환경 설정

### 1. Python 버전 맞추기

macOS (pyenv 사용):

```bash
pyenv install 3.12.7
pyenv local 3.12.7
python3 --version
```

`3.12.7`이 출력되지 않으면, pyenv가 셸에 제대로 연결되지 않은 상태입니다. `~/.zshrc`에 아래 내용이 있는지 확인하세요.

```bash
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
```

수정 후 터미널을 새로 열고 다시 확인합니다.

Windows (py 런처 사용):

python.org에서 Python 3.12.7을 별도로 설치한 뒤,

```powershell
py -3.12 --version
```

### 2. 가상환경 생성

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
```

### 3. 패키지 설치

```bash
pip install -r requirements.txt
```

### 4. DB 접속 준비

Cloud SQL은 공인 IP 직접 접속을 허용하지 않으며, **Cloud SQL Proxy를 통한 IAM 인증 경로만** 사용합니다.

```bash
# Proxy 설치 (macOS)
brew install cloud-sql-proxy

# GCP 인증 — 세 줄 모두 실행
gcloud auth application-default login
gcloud auth application-default set-quota-project <프로젝트ID>
gcloud config set project <프로젝트ID>
```

> 두 번째 명령을 생략하면 Proxy 실행 시 `Project ... has been deleted` 403이 발생합니다. 계정 재분배 이전 프로젝트가 자격증명에 남아 있어서입니다.

상세 절차와 오류 대응은 [`docs/infra/REQ006-DB접속안내.md`](docs/infra/REQ006-DB접속안내.md)를 참고하세요.

### 5. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 아래 값을 채워 넣습니다. 실제 값은 개별 전달받으세요. **절대 Git에 커밋하지 않습니다.**

```
DB_NAME=edutech
DB_USER=app_user
DB_PASSWORD=
DB_HOST=127.0.0.1
DB_PORT=5432
INSTANCE_CONNECTION_NAME=
GEMINI_API_KEY=
```

### 6. 접속 확인

```bash
# Proxy 실행 (작업 시작 시 한 번, 이후 유지됨)
cloud-sql-proxy <INSTANCE_CONNECTION_NAME> --port 5432 &

# 확인
python scripts/check_db.py
```

아래와 같이 출력되면 정상입니다.

```
('edutech', 'app_user')
pgvector: ('vector',)
rows: 0
```

작업이 끝나면 Proxy를 종료합니다. 남아 있으면 다음 작업에서 포트 충돌이 발생합니다.

```bash
pkill cloud-sql-proxy
```

### 7. 개발 서버 실행

```bash
fastapi dev app/main.py
```

터미널에 표시되는 주소로 접속해 정상 작동을 확인합니다.

## 데이터 준비 (최초 1회, A2 담당)

교육과정 데이터가 아직 DB에 적재되지 않았다면 아래 스크립트를 로컬에서 실행합니다. 이 작업은 서버 배포와 무관하게, 필요할 때 수동으로 실행합니다.

```bash
python app/scripts/ingest_curriculum.py
```

## 개발 규약

### 공통 모듈 경유

- **LLM 호출은 `app/lib/gemini.py`를 경유합니다.** `google.genai`를 직접 import하지 않습니다.
- **DB 접근은 `app/lib/db.py`를 경유합니다.** `psycopg`를 직접 import하지 않습니다.
- 재시도·타임아웃·키 관리를 한 곳에 모아 중복 구현과 재시도 중첩을 방지하기 위한 규약입니다.

### 재시도 계층 분리

`lib/gemini.py`는 **API 레벨 실패만** 재시도합니다(타임아웃·429·5xx). **응답이 스키마로 파싱되지 않는 경우는 재시도하지 않고 `GeminiSchemaError`를 올립니다.** 스키마 재생성 재시도는 각 에이전트의 책임입니다.

### 모델 및 파라미터

모델은 `gemini-3.6-flash`로 고정되어 있습니다. **이 세대부터 `temperature`/`top_p`/`top_k`는 지원 중단**되어 값을 넣어도 무시됩니다. 출력 제어는 `thinking_level`(`minimal`/`low`/`medium`/`high`)과 프롬프트 명시성으로 수행합니다.

### 데이터 계약

`app/lib/types.py`가 팀 전체 데이터 계약의 **정본**입니다. 문서와 코드가 다르면 코드를 따릅니다.

각 타입에는 소유자 주석이 있으며, 소유자가 아닌 사람이 필드를 변경하려면 팀 채널 합의가 필요합니다. 변경 시 팀 채널에 공지하고 전원에게 즉시 pull을 요청합니다.

### 파일 소유 경계

자신이 담당하는 폴더의 파일만 수정합니다. 다른 폴더의 수정이 필요해 보이면 직접 고치지 말고 담당자에게 요청합니다.

### psycopg 3 주의

이 프로젝트는 **psycopg 3**을 사용합니다. 인터넷 예제 대부분이 psycopg2 기준이라 그대로 참고하면 동작하지 않습니다.

| psycopg2 | psycopg 3 |
| --- | --- |
| `import psycopg2` | `import psycopg` |
| `RealDictCursor` | `from psycopg.rows import dict_row` |
| `pgvector.psycopg2` | `pgvector.psycopg` |

## 개발 워크플로

1. `main`을 최신화한 뒤 브랜치를 생성합니다.

   ```bash
   git switch main
   git pull
   git switch -c feature/<역할>-<작업>
   ```

   브랜치 이름은 `feature/a1-concept-collect`, `feature/e-db-connection` 형식을 따릅니다.

2. 자신이 맡은 폴더 내 파일만 수정합니다.

3. 작업 중 `main`을 반영해야 하면 아래를 실행합니다.

   ```bash
   git fetch origin
   git merge origin/main
   ```

   `git pull`은 현재 브랜치의 원격만 가져오므로 `main`을 반영하지 않습니다.

4. PR을 생성합니다. 머지는 **Squash**를 기본으로 하여 main 이력을 "PR 1개 = 커밋 1개"로 유지합니다.

5. 머지 후 브랜치를 삭제하고 로컬을 정리합니다.

   ```bash
   git switch main && git pull && git fetch --prune
   git branch -d feature/<브랜치명>
   ```

## 담당 역할

| 담당 | 에이전트 | 주요 파일 | 담당자 |
| --- | --- | --- | --- |
| A1 | AI 개념 분석 | `app/agents/concept_collect/` | 나윤서 |
| A2 | 교육과정 검색 및 데이터 적재 | `app/agents/curriculum_search/`, `app/scripts/ingest_curriculum.py` | 박지민 |
| B | 매핑 | `app/agents/mapping/` | 현세은 |
| C | 교안 생성 | `app/agents/lesson_generate/`, `app/templates/result.html` | 이서현 |
| D | 검증, 오케스트레이션 | `app/agents/validate/`, `app/agents/orchestrate.py` | 김준명 |
| E | 사이트 전반 로직, 인프라 | `app/main.py`, `app/lib/`, `app/static/`, `app/templates/`, 배포 전반 | 김준명 |

D와 E는 한 사람이 겸임하지만 브랜치와 PR은 역할별로 분리합니다.

## 참고 사항

- `.venv`, `.env`, `__pycache__`는 각자 로컬에만 존재하며 Git에 포함되지 않습니다.
- 데이터베이스는 팀 전체가 공유하는 단일 Cloud SQL 인스턴스를 사용합니다. 별도의 개인 DB를 만들 필요는 없습니다.
- 앱 계정(`app_user`)은 `SELECT` / `INSERT` / `UPDATE`만 가능합니다. `DELETE`와 DDL은 거부되며 이는 의도된 설정입니다. 스키마 변경이 필요하면 인프라 담당자에게 요청하세요.
- 배포는 Google Cloud Run을 통해 이루어지며, 파이프라인은 1차 프로토타입 이후 구성 예정입니다.