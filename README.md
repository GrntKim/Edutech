# MATCHU (매츄)

성취기준에서 출발하는 초등 AI 수업 교수학습과정안·활동지 자동 생성 시스템

> 서비스명은 **MATCHU(매츄)** 입니다. 우리말 '맞추다'에서 온 이름으로, AI 개념을
> 대상 학년과 교육과정 성취기준에 **맞춘다**는 뜻입니다. 

## 팀원

- 김준명
- 나윤서
- 박지민
- 이서현
- 현세은

## 개요

초등 교사가 AI 개념을 수업에 끌어올 때 가장 어려운 지점은 "이 개념을 어느 교과
어느 성취기준에 붙일 것인가"입니다. MATCHU는 그 연결을 대신 찾아줍니다.

**교사가 입력하는 것은 두 가지뿐입니다 — AI 개념과 대상 학년.** 과목은 지정하지
않습니다. 어느 성취기준이 선택되느냐에 따라 과목이 사후적으로 결정되는 구조이기
때문입니다. "분류"를 4학년으로 넣으면 수학 성취기준에 붙을 수도, 과학 성취기준에
붙을 수도 있으며 그 판단은 시스템이 합니다.

- **대상**: 초등 1~6학년, 6개 교과(수학·과학·국어·사회·실과·미술)
- **데이터**: 2022 개정 교육과정 성취기준 424개
  (수학 121 · 과학 102 · 국어 87 · 사회 49 · 실과 39 · 미술 26)
- **학년 누적 범위**: 대상 학년까지 배운 학년군만 검색 대상입니다. 4학년이면
  1~2학년군과 3~4학년군, 6학년이면 세 학년군 전부입니다
  (`app/lib/types.py`의 `GRADE_TO_BANDS`)
- **산출물**: 교수학습과정안 · 학생 활동지 · 평가기준. 화면에서 확인하고 DOCX로
  내려받습니다

**생성물은 초안입니다.** 교사 검토를 전제로 하며, 결과 화면의 근거 블록에도 같은
주의문이 표시됩니다. 이 저장소는 학생 팀 프로젝트 프로토타입이며, 생성된 교안의
교육적 효과는 검증하지 않았습니다.

## 기술 스택

- **웹 프레임워크**: FastAPI + Jinja2 (서버사이드 렌더링) + htmx
- **실행 환경**: Python 3.12
- **데이터 검증**: Pydantic v2
- **데이터베이스**: Cloud SQL for PostgreSQL + pgvector (드라이버: psycopg 3)
- **LLM**: Gemini API `gemini-3.6-flash`
- **임베딩**: `nlpai-lab/KoE5` (sentence-transformers, 컨테이너에 상주)
- **배포**: Google Cloud Run
- **도메인**: 별도 DNS를 붙이지 않았습니다. Cloud Run이 제공하는 `run.app` 주소를
  그대로 쓰며 관리형 인증서로 HTTPS가 적용됩니다

## 파이프라인 구조

폼 제출 한 번이 다섯 단계를 순서대로 지나갑니다. 라우트는 개별 에이전트를 직접
알지 않으며 `app/agents/orchestrate.py`의 `run_pipeline()`만 호출합니다.

| 단계 | 이름 | 하는 일 |
| --- | --- | --- |
| A1 | 개념 분석 | 입력된 AI 개념을 정의·핵심 원리·주의 용어로 구조화합니다. AI 개념이 아니거나 너무 넓으면 여기서 중단합니다 |
| A2 | 성취기준 검색 | 대상 학년까지의 학년군으로 범위를 좁혀, 임베딩 검색으로 후보 성취기준을 뽑습니다 |
| B | 교육과정 매핑 | 후보 중 개념과 가장 잘 맞닿는 성취기준을 고르고 비유 방향을 정합니다 |
| C | 교수학습과정안 작성 | 매핑 결과로 교안·활동지·평가기준을 생성합니다 |
| D | 검증 | 해당 학년이 아직 배우지 않은 용어가 학생용 문장에 섞였는지, 원리 개수가 맞는지 검사합니다 |

D가 위반을 찾으면 그 피드백을 들고 **C로 되돌아가 다시 생성합니다(최대 3회,
`MAX_RETRIES`)**. 3회를 넘기면 마지막 결과를 경고 문구와 함께 그대로 반환합니다 —
사용자가 빈손으로 끝나지 않게 하기 위해서입니다. 재시도해도 완전히 다른 위반이
계속 나오는 경우(수렴 불가)는 3회를 채우지 않고 조기 종료합니다.

각 단계 전환은 콜백으로 화면에 전달되며, 브라우저는 진행 패널을 폴링해 진행률을
갱신합니다. 단계별 상세 규격은 `docs/` 아래 REQ 문서를 참고하세요.

## 문서 안내

`docs/`에는 요구사항 명세(SRS)가 역할별로 하나씩 있습니다.

| 문서 | 담당 | 다루는 내용 |
| --- | --- | --- |
| [`docs/concept_collect/REQ001-개념 수집.md`](docs/concept_collect/REQ001-개념%20수집.md) | A1 | AI 개념 분석 — 입력 판별 기준, 구조화 스키마, 주의 용어 추출 |
| [`docs/curriculum_search/REQ002-교육과정검색엔진.md`](docs/curriculum_search/REQ002-교육과정검색엔진.md) | A2 | 성취기준 검색 엔진 — 임베딩·학년군 필터·리랭킹. LLM 판단이 아닌 결정론적 알고리즘 |
| [`docs/mapping/REQ003-매핑에이전트.md`](docs/mapping/REQ003-매핑에이전트.md) | B | 개념-단원 매핑 — 후보 중 최적 비유 선택 및 양방향 매핑 |
| [`docs/lesson_generate/REQ004-교안생성.md`](docs/lesson_generate/REQ004-교안생성.md) | C | 교안·활동지 생성 — 동기유발-본활동-정리-평가 구성, 결과 화면, DOCX 내보내기 |
| [`docs/validate/REQ005-검증.md`](docs/validate/REQ005-검증.md) | D | 난이도·제약 검증 — 금지 용어 검사, 재생성 피드백 루프 |
| [`docs/infra/REQ006-인프라.md`](docs/infra/REQ006-인프라.md) | E | 배포 아키텍처, 공통 유틸리티, 웹 인터페이스 셸, 시각 처리 규약(저장 UTC / 표시 KST) |
| [`docs/infra/REQ006-DB접속안내.md`](docs/infra/REQ006-DB접속안내.md) | E | 로컬에서 Cloud SQL에 붙는 절차와 오류 대응 |
| [`docs/measurements/2026-08-10-a1-recheck.md`](docs/measurements/2026-08-10-a1-recheck.md) | A2 | A2 검색 성능 재측정 기록 |

읽는 순서: **REQ006-인프라 → REQ001 → REQ002 → REQ003 → REQ004 → REQ005**.
전체 구조를 먼저 잡고 파이프라인 순서대로 따라가는 순서입니다. 로컬에서 코드를
돌려볼 목적이라면 REQ006-DB접속안내부터 보면 됩니다.

A2 검색 엔진의 실험 기록(임베딩 모델 벤치마킹, 골든셋, 학년 가중치 스윕)은
`docs/`가 아니라 [`curriculum-search-engine/`](curriculum-search-engine/)에 있으며,
특히 [`RS-006_검색구조_의사결정_기록.md`](curriculum-search-engine/RS-006_검색구조_의사결정_기록.md)에
검색 구조를 왜 그렇게 정했는지가 정리돼 있습니다.

> REQ006-인프라 문서의 도메인 관련 서술(Cloudflare 경유)은 실제 구성과 다릅니다.
> 실제로는 Cloudflare를 쓰지 않으며 `run.app` 주소를 그대로 씁니다.

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
│   │   ├── concept_collect/     A1 — AI 개념 분석
│   │   ├── curriculum_search/   A2 — 교육과정 성취기준 검색 (RAG Retrieval)
│   │   ├── mapping/             B  — AI 개념과 단원 매핑
│   │   ├── lesson_generate/     C  — 교안 및 활동지 생성, DOCX 내보내기
│   │   │   ├── docx_export.py   DOCX 문서 생성
│   │   │   ├── db_client.py     교안 생성에 필요한 조회
│   │   │   └── assets/fonts/    DOCX 임베딩용 한글 폰트 (NanumGothic)
│   │   ├── validate/            D  — 학년 제약조건 검증
│   │   └── orchestrate.py       전체 파이프라인 호출 순서 및 재시도 루프
│   ├── lib/                     공통 유틸리티 (E 소유, 변경 시 전원 합의)
│   │   ├── db.py                Cloud SQL 연결 및 공용 조회
│   │   ├── gemini.py            Gemini API 호출 래퍼
│   │   ├── auth.py              세션 인증, 관리자 권한, 생성 횟수 제한
│   │   └── types.py             공통 타입 정의 (Pydantic)
│   ├── scripts/
│   │   └── ingest_curriculum.py 교육과정 데이터 파싱 및 DB 적재 (A2 소유, 로컬 실행)
│   ├── data/
│   │   ├── curriculum_units.json  정리된 교육과정 원본 데이터 (성취기준 424개)
│   │   └── a1_queries*.csv        A1 측정용 질의 세트
│   ├── static/
│   │   ├── style.css
│   │   ├── logo.svg             서비스 로고 (시작 화면)
│   │   ├── wordmark.svg         워드마크 (헤더·슬로건)
│   │   ├── favicon.svg
│   │   └── vendor/htmx.min.js   htmx (CDN 미사용, 저장소에 고정)
│   ├── templates/               Jinja2 HTML 템플릿
│   └── main.py                  FastAPI 진입점 (라우트, 생성 job 상태 관리)
├── config/
│   └── mapping_weights.yaml     B 매핑 가중치
├── curriculum-search-engine/    A2 검색 엔진 실험·평가 기록 (RS-003/005/006/007, eval 스크립트)
├── scripts/                     점검·측정·수동 테스트용 스크립트
│   ├── check_db.py              DB 접속 확인
│   └── create_admin.py          관리자 계정 생성
├── tests/
├── docs/                        요구사항 명세(SRS) — 위 "문서 안내" 참고
├── Dockerfile
├── requirements.txt
├── .python-version
├── .env.example
└── README.md
```

각 `agents/` 하위 폴더는 다음 세 파일을 기본으로 구성됩니다.

- `logic.py`: Gemini 호출 및 처리 로직
- `prompts.py`: 프롬프트 템플릿
- `schema.py`: 해당 에이전트의 모듈 전용 타입 (공용 타입은 `lib/types.py`)

### 라우트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/` | 서비스 소개 화면 |
| GET · POST | `/generate` | 생성 폼 / 폼 제출(파이프라인을 백그라운드로 시작하고 진행 패널을 즉시 반환) |
| GET | `/generate/progress/{job_id}` | 진행 패널 폴링. 완료되면 결과 조각으로 교체 |
| GET · POST | `/signup`, `/login` | 회원가입 · 로그인 |
| POST | `/logout` | 로그아웃 (세션 레코드 삭제) |
| GET | `/mypage` | 생성 이력 목록 |
| POST | `/mypage/settings` | 기본 학년 설정 |
| GET | `/mypage/requests/{request_id}` | 생성 이력 상세 |
| GET | `/mypage/requests/{request_id}/redownload` | 저장된 결과로 DOCX 재생성·다운로드 |
| POST | `/mypage/requests/{request_id}/delete` | 이력 삭제 (soft-delete) |
| GET | `/admin` | 관리자 대시보드 |
| GET | `/admin/chunks` | 성취기준 청크 목록 |
| GET · POST | `/admin/chunks/{chunk_id}/edit`, `/admin/chunks/{chunk_id}` | 청크 수정 |

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

## 배포

Google Cloud Run 단일 서비스로 배포합니다. 별도 서버나 백엔드 프레임워크는 두지
않습니다.

```bash
gcloud run deploy <서비스명> \
  --source . \
  --region <리전> \
  --no-cpu-throttling \
  --max-instances=1 \
  --memory=4Gi \
  --cpu=2 \
  --add-cloudsql-instances <INSTANCE_CONNECTION_NAME> \
  --set-env-vars "DB_NAME=edutech,DB_USER=app_user,INSTANCE_CONNECTION_NAME=<...>" \
  --set-secrets "DB_PASSWORD=<시크릿명>:latest,GEMINI_API_KEY=<시크릿명>:latest"
```

### 옵션이 왜 필요한가

각 옵션에는 이유가 있습니다. 모르고 빼면 원인을 찾기 어려운 실패로 이어집니다.

- **`--no-cpu-throttling`** — 파이프라인은 응답을 이미 돌려보낸 뒤 백그라운드에서
  실행됩니다(`app/main.py`의 `_run_generation`). Cloud Run 기본값은 요청 응답이
  끝나면 CPU를 회수하므로, 이 옵션이 없으면 백그라운드 작업이 진행되지 않아
  진행 표시가 결과로 바뀌지 않고 무한 대기합니다.
- **`--max-instances=1`** — 생성 작업 상태(`_generation_jobs`)를 프로세스 메모리에
  둡니다. 인스턴스가 둘 이상이면 진행 폴링 요청이 다른 인스턴스로 가서 job을 찾지
  못하고 404가 납니다. 세션 어피니티는 best-effort라 대안이 되지 못합니다.
  스케일아웃이 필요해지면 이 상태를 Cloud SQL로 옮기는 것이 정공법입니다.
- **`--memory=4Gi --cpu=2`** — A2가 쓰는 임베딩 모델(KoE5)이 프로세스에 상주합니다.
  이보다 작으면 모델 로드 중 컨테이너가 죽습니다.
- **`--add-cloudsql-instances`** — Cloud SQL 유닉스 소켓을
  `/cloudsql/<INSTANCE_CONNECTION_NAME>`에 마운트합니다. 이게 없으면 DB에 붙지
  못합니다.
서비스 설정을 콘솔이나 Cloud Build 트리거로 관리하더라도 위 네 가지는 동일하게
적용돼 있어야 합니다. CLI로 배포하든 아니든 조건은 같습니다.

### 배포 환경의 DB 접속

DB 접속 방식은 코드가 **`K_SERVICE` 환경변수 유무로 자동 분기**합니다
(`app/lib/db.py`). Cloud Run에는 이 변수가 항상 있으므로 유닉스 소켓 경로로 붙고,
로컬에는 없으므로 `DB_HOST`/`DB_PORT`(Cloud SQL Proxy)로 붙습니다.

따라서 **배포 환경에는 `DB_HOST`/`DB_PORT` 대신 `INSTANCE_CONNECTION_NAME`을
넘겨야 합니다.** 로컬 `.env`를 그대로 올리면 안 됩니다.

`GEMINI_API_KEY`는 환경변수로 직접 넘기거나, `GEMINI_API_KEY_SECRET_NAME`에
Secret Manager 버전 경로를 넘겨도 됩니다(`app/lib/gemini.py`의 `_resolve_api_key`).

### 리버스 프록시 뒤 스킴 인식

Cloud Run은 TLS를 종료한 뒤 컨테이너에는 평문 HTTP로 요청을 넘깁니다. 템플릿이
쓰는 `url_for()`는 절대 URL을 만들기 때문에, 앱이 스킴을 그대로 믿으면 HTTPS
페이지에 `http://` 자산 링크가 박히고 브라우저가 혼합 콘텐츠로 차단합니다 —
CSS와 로고가 통째로 안 나오는 증상입니다.

Dockerfile의 실행 명령에 `--proxy-headers --forwarded-allow-ips '*'`가 들어 있는
이유가 이것입니다. `X-Forwarded-Proto`를 신뢰해 원래 스킴을 복원합니다. 실행
명령을 바꿀 때 이 두 플래그를 빠뜨리지 마세요.

### 콜드스타트

`--min-instances`는 기본값 0입니다. 유휴 상태에서 첫 요청은 컨테이너 기동과 임베딩
모델 로드를 기다려야 해서 눈에 띄게 느립니다. 없애려면 `--min-instances=1`로 올리면
되지만 **인스턴스가 상시 과금**되므로, 시연 시점에만 올리고 끝나면 0으로 되돌립니다.

이미지 빌드 시 KoE5 모델을 미리 받아 캐시해 둡니다(Dockerfile). 런타임에
`HF_HUB_OFFLINE=1`이 기본 설정되므로 이 캐시가 없으면 첫 요청이 아예 실패합니다.

### 도메인

별도 DNS를 붙이지 않았습니다. Cloud Run이 발급하는 `*.run.app` 주소를 그대로 쓰며,
관리형 인증서로 HTTPS가 즉시 적용됩니다.

## 개발 규약

### 공통 모듈 경유

- **LLM 호출은 `app/lib/gemini.py`를 경유합니다.** `google.genai`를 직접 import하지 않습니다.
- **DB 접근은 `app/lib/db.py`를 경유합니다.** `psycopg`를 직접 import하지 않습니다.
- 재시도·타임아웃·키 관리를 한 곳에 모아 중복 구현과 재시도 중첩을 방지하기 위한 규약입니다.

### 재시도 계층 분리

`lib/gemini.py`는 **API 레벨 실패만** 재시도합니다(타임아웃·429·5xx). **응답이 스키마로 파싱되지 않는 경우는 재시도하지 않고 `GeminiSchemaError`를 올립니다.** 스키마 재생성 재시도는 각 에이전트의 책임입니다.

### 모델 및 파라미터

모델은 `gemini-3.6-flash`로 고정되어 있습니다(재현성 — 변경 시 팀 합의 필요).
**이 세대부터 `temperature`/`top_p`/`top_k`는 지원 중단**되어 값을 넣어도
무시됩니다. 출력 제어는 `thinking_level`(`minimal`/`low`/`medium`/`high`)과 프롬프트
명시성으로 수행합니다. `lib/gemini.py`의 호출 함수도 `temperature`를 받지 않고
`thinking_level`을 받습니다.

### 데이터 계약

`app/lib/types.py`가 팀 전체 데이터 계약의 **정본**입니다. 문서와 코드가 다르면 코드를 따릅니다.

각 타입에는 소유자 주석이 있으며, 소유자가 아닌 사람이 필드를 변경하려면 팀 채널 합의가 필요합니다. 변경 시 팀 채널에 공지하고 전원에게 즉시 pull을 요청합니다.

### 파일 소유 경계

자신이 담당하는 폴더의 파일만 수정합니다. 다른 폴더의 수정이 필요해 보이면 직접 고치지 말고 담당자에게 요청합니다.

폴더 단위로 나뉘지 않는 예외가 하나 있습니다. **`app/scripts/ingest_curriculum.py`는
A2 소유입니다.** `app/agents/curriculum_search/`와 같은 사람이 관리하며, 파일
상단에 다른 파일들과 같은 `# 소유:` 주석 태그가 아직 붙어 있지 않으니 경로만 보고
공용 스크립트로 오해하지 마세요.

### psycopg 3 주의

이 프로젝트는 **psycopg 3**을 사용합니다. 인터넷 예제 대부분이 psycopg2 기준이라 그대로 참고하면 동작하지 않습니다.

| psycopg2 | psycopg 3 |
| --- | --- |
| `import psycopg2` | `import psycopg` |
| `RealDictCursor` | `from psycopg.rows import dict_row` |
| `pgvector.psycopg2` | `pgvector.psycopg` |

### 시각 처리

DB에는 항상 UTC(`timestamptz`)로 저장하고, 화면에 찍기 직전에만 KST로 변환합니다.
템플릿에서 `created_at.strftime(...)`을 직접 호출하지 말고 Jinja 필터
`{{ item.created_at | kst }}`를 쓰세요. 상세는
[`docs/infra/REQ006-인프라.md`](docs/infra/REQ006-인프라.md)의 시각 처리 규약 절을
참고하세요.

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
- 앱 계정(`app_user`)은 `SELECT` / `INSERT` / `UPDATE` / `DELETE`가 가능하며,
  **DDL은 거부됩니다.** 의도된 설정입니다. 스키마 변경이 필요하면 인프라 담당자에게
  요청하세요. (생성 이력 삭제는 하드 DELETE가 아니라 soft-delete이며, 실제
  `DELETE`가 쓰이는 곳은 로그아웃 시 세션 레코드 삭제입니다.)
- 생성 횟수에 제한이 있습니다(일 5회 / 주 15회, 관리자 계정은 예외).
- 이 저장소는 학생 팀 프로젝트 프로토타입입니다. 생성된 교안의 교육적 효과는
  검증하지 않았으며, 모든 산출물은 교사 검토를 전제로 한 초안입니다.
