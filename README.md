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
- **데이터베이스**: Cloud SQL for PostgreSQL + pgvector
- **LLM / 임베딩**: Gemini API
- **배포**: Google Cloud Run
- **도메인**: Cloudflare

## 사전 준비물

시작하기 전에 아래 항목이 준비되어 있어야 합니다.

- Python 3.12.7 (프로젝트는 이 버전으로 고정되어 있습니다)
- pyenv (macOS) 또는 py 런처 (Windows)
- Git

## 프로젝트 구조

```
edutech/
├── app/
│   ├── agents/                  각 판단 단계별 로직 (담당자별로 폴더 하나씩)
│   │   ├── concept_collect/     AI 개념 정의 수집
│   │   ├── curriculum_search/   교육과정 성취기준 검색 (RAG Retrieval)
│   │   ├── mapping/             AI 개념과 단원 매핑
│   │   ├── lesson_generate/     교안 및 활동지 생성
│   │   ├── validate/            학년 제약조건 검증
│   │   └── orchestrate.py       전체 파이프라인 호출 순서
│   ├── lib/                     공통 유틸리티
│   │   ├── db.py                Cloud SQL 연결
│   │   ├── gemini.py            Gemini API 호출
│   │   └── types.py             공통 타입 정의 (Pydantic)
│   ├── scripts/
│   │   └── ingest_curriculum.py 교육과정 데이터 파싱 및 DB 적재 (사전 준비 스크립트, 로컬 실행)
│   ├── data/
│   │   └── curriculum_units.json  정리된 교육과정 원본 데이터
│   ├── static/                  CSS, JS 등 정적 파일
│   ├── templates/               Jinja2 HTML 템플릿
│   └── main.py                  FastAPI 진입점
├── requirements.txt
├── .python-version
├── .env.example
└── README.md
```

각 `agents/` 하위 폴더는 다음 세 파일로 구성됩니다.

- `logic.py`: Gemini 호출 및 처리 로직
- `prompts.py`: 프롬프트 템플릿
- `schema.py`: 해당 에이전트의 입출력 타입

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

### 4. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 아래 값을 채워 넣습니다. 실제 값은 팀 채널로 공유받으세요. 절대 Git에 커밋하지 않습니다.

```
DATABASE_URL=postgresql://<user>:<password>@<address>:5432/<dbname>
GEMINI_API_KEY=<api_key>
```

### 5. 개발 서버 실행

```bash
fastapi dev app/main.py
```

터미널에 표시되는 주소로 접속해 정상 작동을 확인합니다.

## 데이터 준비 (최초 1회, A2 담당)

교육과정 데이터가 아직 DB에 적재되지 않았다면 아래 스크립트를 로컬에서 실행합니다. 이 작업은 서버 배포와 무관하게, 필요할 때 수동으로 실행합니다.

```bash
python app/scripts/ingest_curriculum.py
```

## 개발 워크플로

1. 이슈 또는 담당 파일을 기준으로 브랜치를 생성합니다.
2. 자신이 맡은 `agents/xxx/` 폴더 내 파일만 수정합니다.
3. 작업 완료 후 PR을 생성합니다.
4. 리뷰 및 머지 후 자동으로 배포됩니다. (배포 파이프라인은 추후 설정 예정)

## 담당 역할

| 담당 | 에이전트 | 주요 파일 | 담당자 |
| --- | --- | --- | --- |
| A1 | 개념 수집 | `agents/concept_collect/` | 박지민 |
| A2 | 교육과정 검색 및 데이터 적재 | `agents/curriculum_search/`, `scripts/ingest_curriculum.py` | 나윤서 |
| B | 매핑 | `agents/mapping/` | 현세은 | 
| C | 교안 생성 | `agents/lesson_generate/`, `templates/result.html` | 이서현 | 
| D | 검증, 오케스트레이션, 인프라 | `agents/validate/`, `agents/orchestrate.py`, `lib/`, 배포 전반 | 김준명 | 

## 참고 사항

- `.venv`, `.env`, `__pycache__`는 각자 로컬에만 존재하며 Git에 포함되지 않습니다.
- 데이터베이스는 팀 전체가 공유하는 단일 Cloud SQL 인스턴스를 사용합니다. 별도의 개인 DB를 만들 필요는 없습니다.
- 배포는 Google Cloud Run을 통해 이루어지며, 세부 절차는 인프라 담당자가 별도로 안내합니다.
