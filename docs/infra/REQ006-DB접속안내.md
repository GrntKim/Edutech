# DB 접속 환경 설정 안내

description: Cloud SQL 로컬 개발 환경 구성 및 접속 절차
사람: JMK

---

# ⚡ 빠른 참조 (매일 쓰는 4가지)

## ① Proxy 켜기 — 작업 시작할 때 한 번

```bash
cloud-sql-proxy splendid-binder-504502-f8:asia-northeast3:edutech-db --port 5432 &
```

> `The proxy has started successfully and is ready for new connections!`
> 가 보이면 성공. **한 번 켜면 계속 유지되므로 스크립트 실행마다 다시 켤 필요 없습니다.**

⚠️ **`$INSTANCE_CONNECTION_NAME`으로 쓰면 안 됩니다.** `.env`는 python-dotenv가
파이썬 안에서 읽는 파일이라 셸이 자동으로 로드하지 않습니다. 위처럼 값을 직접 입력하세요.

## ② 확인

```bash
python scripts/check_db.py
```

**이 세 줄이 나오면 정상입니다.**

```
('edutech', 'app_user')
pgvector: ('vector',)
rows: 0
```

- 첫 줄이 `('postgres', ...)` → `.env`의 `DB_NAME` 확인
- 두 번째 값이 `postgres` → `.env`의 `DB_USER` 확인
- `rows`는 A2 적재 진행에 따라 달라집니다. 적재 전 0은 정상

## ③ 실행 중인지 확인

```bash
ps aux | grep cloud-sql-proxy
```

## ④ Proxy 끄기 — 작업 끝낼 때

```bash
pkill cloud-sql-proxy
```

> 백그라운드(`&`)로 띄웠기 때문에 터미널을 닫아도 남아 있을 수 있습니다.
> **다음 작업에서 포트 충돌이 나는 가장 흔한 원인이므로 꼭 종료하세요.**

같은 터미널에서 띄운 직후라면 `kill %1`도 됩니다.

---

# 🔴 오류 1순위 — `Project ... has been deleted` (403)

Proxy 실행 시 아래 메시지가 나오면 **거의 모든 팀원이 겪는 문제**입니다.

```
failed to connect to instance: ... googleapi: Error 403:
Project gcpgbsa02-0705-4119 has been deleted.
```

계정 재분배 **이전 프로젝트**가 자격증명에 quota project로 남아 있어서 발생합니다.
접속 대상 인스턴스와는 무관한 값입니다.

**해결:**

```bash
gcloud auth application-default set-quota-project splendid-binder-504502-f8
```

실행 후 Proxy를 껐다가(`pkill cloud-sql-proxy`) 다시 켜면 됩니다.

**그래도 안 되면** 자격증명을 새로 발급하세요.

```bash
gcloud auth application-default revoke
gcloud auth application-default login
gcloud auth application-default set-quota-project splendid-binder-504502-f8
```

---

## 0. 개정 이력(Revision History)

| 버전 | 날짜 | 수정 내용 요약 | 작성자 | 승인/확인 |
| --- | --- | --- | --- | --- |
| v1.0 | 2026-08-05 | 초안 작성. Cloud SQL 인스턴스 구축 완료에 따른 팀 접속 절차 정리 | 김준명 | 전체 공유 |
| v1.1 | 2026-08-05 | 실사용 피드백 반영<br>• 문서 최상단에 **빠른 참조** 섹션 신설: Proxy 실행·확인·종료 4단계를 앞으로 배치<br>• quota project 403 대응을 **독립 섹션으로 승격**(발생 빈도가 가장 높음)<br>• `$INSTANCE_CONNECTION_NAME` 셸 참조 불가 경고 추가 — `.env`는 셸이 로드하지 않음<br>• 백그라운드 종료 절차를 명시적으로 분리(포트 충돌의 주원인)<br>• 셸 별칭 등록 안내 추가 | 김준명 | 전체 공유 |

## 1. 문서 개요

| 항목 | 내용 |
| --- | --- |
| 문서 유형 | 개발 환경 설정 안내 |
| 관련 요구사항 | REQ-006 (INFRA-002, INFRA-003) |
| 대상 | A2(적재·검색), C(성취기준 원문 조회), D(금지 용어 파생) 담당자 및 전체 팀원 |
| 담당자 | 김준명 (E) |
| 작성일 | 2026-08-05 |
| 문서 버전 | v1.1 |

본 문서는 팀 공용 Cloud SQL 인스턴스에 로컬 개발 환경에서 접속하기 위한 절차를 안내한다. 인스턴스·테이블·계정은 이미 구축되어 있으며, 각 팀원은 아래 절차에 따라 자신의 로컬 환경만 설정하면 된다.

**접속 방식 요약**

본 프로젝트는 공인 IP 직접 접속을 허용하지 않는다. 승인된 네트워크 목록을 비워두어 IP 화이트리스트 경로를 닫고, **Cloud SQL Proxy를 통한 IAM 인증 경로 하나만** 사용한다.

이 방식을 택한 이유는 다음과 같다.

- IP 화이트리스트 방식은 팀원의 IP가 바뀔 때마다(대부분 유동 IP) 관리자가 콘솔에서 갱신해야 하며, 5인 프로젝트에서 관리 비용이 크다.
- Proxy 방식은 IAM 자격으로 인증하므로 접속 위치와 무관하게 동작하고, 권한 부여·회수가 IAM 멤버 관리로 일원화된다.
- Cloud Run 배포 시에도 동일하게 IAM 기반으로 연결되므로 로컬과 배포 환경의 인증 모델이 일치한다.

**구축 현황**

| 항목 | 값 |
| --- | --- |
| 인스턴스 연결 이름 | `splendid-binder-504502-f8:asia-northeast3:edutech-db` |
| 데이터베이스 | `edutech` |
| 테이블 | `curriculum_chunks` |
| 확장 | `pgvector` (설치 완료) |
| 앱 계정 | `app_user` — `SELECT` / `INSERT` / `UPDATE` 만 부여 |

---

### 1. 사전 준비 (SETUP-001)

각 팀원이 **최초 1회만** 수행한다.

| 항목 | 내용 | 핵심 포인트 | 비고 |
| --- | --- | --- | --- |
| 입력 | 개별 전달받은 접속 정보(`DB_PASSWORD`, `INSTANCE_CONNECTION_NAME`, `GEMINI_API_KEY`) | 팀 채널이 아닌 개별 전달로 수신 | 전달받지 못한 경우 김준명에게 요청 |
| 처리 | ① Cloud SQL Proxy 설치 ② GCP 인증 및 quota project 지정 ③ `.env` 작성 | **②의 quota project 지정을 반드시 포함할 것** | 생략 시 403 발생 |
| 출력 | 로컬에서 Proxy 실행 가능한 상태 | - | - |
| 예외 처리 | IAM 권한 미부여 시 Proxy 실행 단계에서 403 발생 | `Cloud SQL 클라이언트` 역할 필요 | 김준명에게 IAM 부여 요청 |

**① Cloud SQL Proxy 설치**

macOS:

```bash
brew install cloud-sql-proxy
```

Windows·Linux는 [공식 문서](https://cloud.google.com/sql/docs/postgres/sql-proxy)의 바이너리 다운로드 절차를 따른다.

**② GCP 인증 — 세 줄 모두 실행**

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project splendid-binder-504502-f8
gcloud config set project splendid-binder-504502-f8
```

> ⚠️ **두 번째 줄을 생략하면 안 된다.** 계정 재분배 이전 프로젝트가 자격증명에
> quota project로 남아 있어, 생략 시 Proxy 실행 단계에서
> `Project ... has been deleted` 403이 발생한다.

**③ `.env` 작성**

```bash
cp .env.example .env
```

전달받은 값을 채운다. `.env`는 `.gitignore`에 등록되어 있으며 **커밋하지 않는다.**

```
DB_NAME=edutech
DB_USER=app_user
DB_PASSWORD=<개별 전달>
DB_HOST=127.0.0.1
DB_PORT=5432
INSTANCE_CONNECTION_NAME=<개별 전달>
GEMINI_API_KEY=<개별 전달>
```

**④ (선택) 셸 별칭 등록**

매번 긴 연결 이름을 입력하는 것이 번거로우면 등록해두면 편하다.

```bash
# ~/.zshrc 또는 ~/.bashrc 에 추가
alias sqlproxy='cloud-sql-proxy splendid-binder-504502-f8:asia-northeast3:edutech-db --port 5432'
alias sqlproxy-stop='pkill cloud-sql-proxy'
```

등록 후 `source ~/.zshrc`로 반영하면 `sqlproxy &` / `sqlproxy-stop`으로 사용할 수 있다.

---

### 2. 작업 시 접속 절차 (SETUP-002)

| 항목 | 내용 | 핵심 포인트 | 비고 |
| --- | --- | --- | --- |
| 입력 | 사전 준비 완료 상태 | - | - |
| 처리 | Proxy를 백그라운드로 실행 → 애플리케이션·스크립트 실행 → 작업 종료 시 Proxy 종료 | **Proxy는 한 번 실행하면 유지된다.** 매 스크립트 실행마다 켤 필요 없음 | 작업 시작 시 1회 |
| 출력 | `127.0.0.1:5432`로 DB 접근 가능한 상태 | 로컬 포트로 접속하지만 실제 대상은 Cloud SQL 인스턴스 | - |
| 예외 처리 | 포트 충돌 시 `--port 5433` 사용 후 `.env`의 `DB_PORT` 동시 변경 | 이전 Proxy가 종료되지 않은 경우가 대부분 | 먼저 `pkill cloud-sql-proxy` 시도 |

**실행**

```bash
cloud-sql-proxy splendid-binder-504502-f8:asia-northeast3:edutech-db --port 5432 &
```

> **`$INSTANCE_CONNECTION_NAME`을 쓰면 실패한다.** `.env`는 python-dotenv가
> 파이썬 프로세스 안에서 읽는 파일이며, 셸은 이 파일을 자동으로 로드하지 않는다.
> 셸에서 참조하면 빈 문자열이 되어 `missing instance_connection_name` 오류가 난다.

끝의 `&`는 백그라운드 실행이며, 생략하면 해당 터미널이 점유되어 다른 명령을 실행할 수 없다.

**실행 여부 확인**

```bash
ps aux | grep cloud-sql-proxy
```

**종료**

```bash
pkill cloud-sql-proxy
```

> **작업이 끝나면 반드시 종료한다.** 백그라운드 프로세스는 터미널을 닫아도 남아 있을 수 있고,
> 다음 작업에서 `port 5432 already in use` 오류의 가장 흔한 원인이 된다.

같은 터미널 세션에서 띄운 직후라면 `kill %1`로도 종료할 수 있다.

---

### 3. 접속 검증 (SETUP-003)

| 항목 | 내용 | 핵심 포인트 | 비고 |
| --- | --- | --- | --- |
| 입력 | Proxy 실행 중, `.env` 작성 완료 | - | - |
| 처리 | `scripts/check_db.py` 실행 | DB명·계정·확장·테이블 4가지를 한 번에 확인 | 가상환경 활성화 후 실행 |
| 출력 | 접속 대상과 권한이 의도대로인지 확인 | `edutech` / `app_user`가 나와야 함 | - |
| 예외 처리 | 오류 발생 시 4장 대응표 참고 | - | - |

```bash
python scripts/check_db.py
```

**기대 출력**

```
('edutech', 'app_user')
pgvector: ('vector',)
rows: 0
```

Proxy 로그에 연결 수립·종료 메시지가 함께 찍히는 것은 정상이다.

| 출력 | 의미 |
| --- | --- |
| `('edutech', 'app_user')` | 접속 DB와 계정. 의도한 최소 권한 경로로 붙었음을 확인 |
| `pgvector: ('vector',)` | 이 DB에 확장이 설치되어 있음. 확장은 DB 단위이므로 `postgres`에 설치한 것은 무효 |
| `rows: 0` | `curriculum_chunks` 행 수. A2 적재 전에는 0이 정상 |

---

### 4. 오류 대응 (SETUP-004)

| 증상 | 원인 | 대응 |
| --- | --- | --- |
| **`Project ... has been deleted` (403)** | 계정 재분배 이전 프로젝트가 자격증명에 quota project로 남아 있음 | `gcloud auth application-default set-quota-project splendid-binder-504502-f8` 실행 후 Proxy 재시작. **가장 빈번한 오류** |
| **`missing instance_connection_name`** | `$INSTANCE_CONNECTION_NAME`을 셸에서 참조함 | `.env`는 셸이 로드하지 않는다. 연결 이름을 직접 입력할 것 |
| **`port 5432 already in use`** | 이전 Proxy가 종료되지 않았거나 로컬 PostgreSQL이 점유 | `pkill cloud-sql-proxy` 후 재시도. 로컬 PostgreSQL 때문이면 `--port 5433` 사용 + `.env`의 `DB_PORT`도 변경 |
| `failed to get instance metadata` (403), 위 삭제 메시지 없음 | IAM `Cloud SQL 클라이언트` 역할 미부여 | 김준명에게 IAM 부여 요청 |
| `connection refused` | Proxy가 실행 중이 아님 | `ps aux \| grep cloud-sql-proxy`로 확인 후 재실행 |
| `password authentication failed` | `DB_PASSWORD` 오류 | `.env` 값 확인. 따옴표 없이 값만 기입한다(`.env`는 셸이 아니라 리터럴로 읽힘) |
| `relation "curriculum_chunks" does not exist` | `postgres` DB에 접속함 | `.env`의 `DB_NAME`이 `edutech`인지 확인 |
| `permission denied for table curriculum_chunks` | `DELETE`·DDL 등 미부여 권한 시도 | 의도된 동작이다. 스키마 변경이 필요하면 김준명에게 요청 |
| `ModuleNotFoundError: No module named 'psycopg2'` | 본 프로젝트는 psycopg 3을 사용 | `import psycopg`로 수정. psycopg2 예제 코드를 그대로 참고하지 않는다 |

---

## 2. 권한 정책

| 계정 | 권한 | 용도 |
| --- | --- | --- |
| 관리자(`postgres`) | 전체 | 확장 설치, 테이블·인덱스 생성, 계정 관리. E만 사용 |
| `app_user` | `SELECT`, `INSERT`, `UPDATE` | 애플리케이션 및 전 팀원의 개발용 계정 |

`DELETE`는 의도적으로 부여하지 않았다. A2의 적재는 UPSERT 방식이라 불필요하며, 실수로 인한 데이터 손실을 방지한다.

DDL(`CREATE TABLE`, `ALTER` 등)도 앱 계정에 부여하지 않는다. 스키마는 관리자가 1회 수행하며, 코드에 DDL을 포함시키지 않는 것이 팀 규약이다(REQ-006 INFRA-002-3).

**스키마 변경이 필요한 경우**: 직접 실행하지 말고 김준명에게 요청한다. `app/lib/types.py`의 타입 정의와 함께 변경되어야 하는 경우가 많아 전원 합의가 필요할 수 있다.

---

## 3. 애플리케이션 코드에서의 사용

**`psycopg`를 직접 import하지 않는다.** DB 접근은 `app/lib/db.py`를 경유한다(REQ-006 NFR-006-4).

```python
from app.lib.db import get_connection, get_chunk_by_code, get_chunks_by_scope
```

`db.py`는 커넥션 관리와 함께 여러 에이전트가 공통으로 필요로 하는 범용 조회를 제공한다. C(성취기준 원문 확보)와 D(금지 용어 파생)가 동일 테이블을 동일 목적으로 조회하므로, 각자 쿼리를 작성하면 조회 범위가 어긋나 재생성 루프가 수렴하지 않을 위험이 있다.

단, 조회 결과를 어떻게 해석·가공할지는 각 에이전트의 정책이므로 `db.py`에 두지 않는다.

| 제공 | 내용 |
| --- | --- |
| `get_connection()` | 커넥션 컨텍스트 매니저. 정상 종료 시 commit, 예외 시 rollback, 항상 close |
| `get_cursor(dict_rows=False)` | 커넥션+커서 획득 편의 함수 |
| `get_chunk_by_code(achievement_code)` | 성취기준 코드로 단건 조회 |
| `get_chunks_by_scope(target_grade, subject=None)` | 누적 학년군 범위 내 청크 조회 |

`app/lib/db.py`는 현재 구현 중이며, 완료 시 팀 채널에 공지한다.

---

## 4. 참고 사항

**Proxy는 상시 실행이 아니다.** 작업 시작 시 한 번 띄우면 그 세션 동안 유지되며, 스크립트를 실행할 때마다 재시작할 필요는 없다. 작업이 끝나면 `pkill cloud-sql-proxy`로 종료한다.

**`.env`는 절대 커밋하지 않는다.** `.gitignore`에 등록되어 있으나, `git status`에 `.env`가 보인다면 커밋 전에 확인한다. 비밀번호를 전달할 때도 PR·이슈·채널 공개 대화가 아닌 개별 전달을 사용한다.

**테이블명 주의**: `curriculum_chunks`이다. 초기 논의에서 `curriculum_units`로 표기된 문서가 남아 있을 수 있으며, 발견 시 알려주면 정정한다.

**Subject enum 확장**: 2026-08-04 팀 합의로 `DOMESTIC_SCIENCE`(실과), `ART`(미술)가 추가되었다. `app/lib/types.py`를 최신 상태로 pull한 뒤 작업한다.