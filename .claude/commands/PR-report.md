PR 작성 전 커밋부터 PR 생성까지 전 과정을 자동으로 수행해줘.

---

## 1. 현재 브랜치 및 변경 파일 확인

```bash
git branch --show-current
git status
git diff --stat
```

현재 브랜치명을 파악하고, 변경된 파일이 **해당 브랜치 담당 경로**(Developer Agent의 "구현 파일 위치" 표 기준 — 예: A2는 `app/agents/curriculum_search/`, `app/scripts/ingest_curriculum.py`, `tests/agents/curriculum_search/`) 내에 있는지 확인한다.
이 레포는 브랜치별 최상위 폴더(`API_Server/`, `Database/` 등)로 분리되어 있지 않고 **`app/` 하나의 트리를 모듈 단위로 나눠 소유**하는 구조다. **다른 브랜치 담당 모듈(`app/agents/{다른 모듈}/`, 다른 담당자의 `docs/*/REQ*.md` 등)의 파일은 절대 스테이징하지 않는다.**

---

## 2. 보안 점검 (커밋 전 필수)

변경된 파일에 대해 아래 패턴을 스캔한다.

```bash
# 하드코딩된 자격증명 탐지
git diff | grep -E "(password|secret|api_key|token|host)\s*=\s*['\"][^'\"]{4,}"

# os.getenv 기본값에 실제 인프라 정보 탐지
git diff | grep -E "os\.getenv\(.+,\s*['\"]"
```

| 점검 항목 | 기준 |
|-----------|------|
| 하드코딩된 자격증명 | Gemini API 키, Cloud SQL 접속 정보 하드코딩 없어야 함 |
| os.getenv() 기본값 | 실제 IP, DB명, 사용자명 기본값 없어야 함 |
| .env 파일 포함 여부 | .gitignore에 .env 있는지 확인 |
| app/data/raw/, app/data/embeddings_cache/ 포함 여부 | `.gitignore`에 등록되어 있는지 확인. 단 `app/data/curriculum_units.json`(ingest 산출물)은 의도적으로 git 추적 대상이므로 이 항목의 FAIL로 취급하지 않는다 |

- 탐지된 항목이 있으면 → **커밋 중단, 즉시 수정 요청**
- 이상 없으면 → "보안 점검 통과" 보고 후 계속 진행

---

## 2-b. 공용 문서(REQ SRS / README) 갱신 점검

이 레포에는 별도 `docs` 브랜치나 `docs/context/*` 위키가 없다 — 각 브랜치가 자기 REQ 문서를 직접 소유하고 코드와 같은 PR에서 갱신한다(예: A2는 `curriculum-search-engine/REQ-002_교육과정검색엔진_SRS.md`, B는 `docs/mapping/REQ003-매핑에이전트.md`). **자기 담당 REQ 문서는 코드 변경과 함께 자유롭게 커밋해도 된다.**

주의가 필요한 경우만 아래처럼 처리한다:

| 변경 유형 | 조치 |
|-----------|------|
| 자기 담당 REQ 문서(`docs/{내 모듈}/REQ*.md`, A2는 `curriculum-search-engine/REQ-002_*.md`) 수정 | 코드 PR에 함께 포함, 개정 이력(0장) 갱신 확인 |
| 다른 담당자의 REQ 문서 수정 | 원 담당자 리뷰 없이 커밋 금지 — 사용자에게 확인 요청 |
| `app/lib/types.py`(공유 타입) 변경 | Impact Assessor가 이미 HIGH로 판정하므로 여기선 추가 조치 불필요, PR 본문에 다운스트림 브랜치 리뷰 요청만 명시 |
| `README.md` 담당 역할표 변경 | 코드 PR에 포함 가능. 단 `docs/SRS_정합성_검토_2026-08-04.md` 이슈 2-2(README가 E 역할 분리를 반영 못함)처럼 이미 알려진 미해결 이슈면 PR 본문에 언급만 하고 이번 PR 스코프에 넣을지는 사용자에게 확인 |

절차:
1. 다른 담당자 소유 REQ 문서가 diff에 포함돼 있으면 → 사용자에게 의도한 변경인지 확인 후 진행.
2. `app/lib/types.py` 또는 REQ 스키마 절이 바뀌었으면 → PR 본문 "사후 영향 평가"에 영향받는 다운스트림 브랜치를 명시.
3. 해당 없음 → "공용 문서 갱신 불요" 한 줄 보고 후 계속.

---

## 3. 현재 브랜치 파일만 스테이징 및 커밋

미커밋 변경사항이 있는 경우에만 실행한다. 브랜치 폴더 단위 일괄 `git add`가 불가능한 구조이므로, `git status`로 변경 파일을 확인한 뒤 담당 경로(1단계에서 확인한 목록)에 해당하는 파일만 개별적으로 스테이징한다.

```bash
git add {담당 파일1} {담당 파일2} ...
git commit -m "..."
```

**커밋 금지 파일**: `.env`, `app/data/raw/`, `app/data/embeddings_cache/`, `*.pem`, `credentials.json`, `service-account*.json` (단 `app/data/curriculum_units.json`은 ingest 산출물로 의도적으로 추적되므로 예외)

---

## 4. base 브랜치 최신화

```bash
# 1) 원격 최신 상태 가져오기
git fetch origin

# 2) base 브랜치(main)와 현재 브랜치 간 diverge 여부 확인
git log HEAD..origin/main --oneline
git log origin/main..HEAD --oneline
```

- `origin/main`에 내 브랜치에 없는 커밋이 있으면 → **pull 먼저 수행**
- 충돌(conflict) 발생 시 → 사용자에게 충돌 파일 목록을 알리고 **중단**. 충돌 해결 후 재실행 요청.
- diverge 없으면 → 다음 단계로 진행

```bash
# diverge가 있는 경우에만 실행
git pull origin main
```

---

## 5. 변경사항 분석

```bash
# 베이스 대비 변경된 파일 목록
git diff --name-status origin/main...HEAD

# 커밋 히스토리
git log origin/main..HEAD --oneline
```

- 변경된 파일 수 및 목록 (추가/수정/삭제 구분)
- 각 커밋의 주요 내용 요약

---

## 6. 이전 PR 내용 확인

```bash
gh pr list --head {현재 브랜치} --state all --limit 1
gh pr view {PR번호} --json body
```

- 이전 PR이 있으면 body를 읽어 내용을 파악한다.
- 새 PR body 작성 시 이전 PR과 **중복되는 항목은 최신 내용으로 덮어써서 반영**, **새로 추가된 항목은 해당 섹션에 추가**한다.
- 이전 PR이 없으면 새로 작성한다.

---

## 7. PR 생성

위 분석 결과를 바탕으로 아래 형식으로 PR 본문을 작성하고 `gh pr create`를 실행한다.
PR base branch는 항상 `main`이다.

```
## 변경사항 요약
<!-- 변경된 파일별로 무엇을 왜 변경했는지 기술 (bullet 3개 이내) -->

## 사후 영향 평가
| 영향 범위 | 내용 | 조치 필요 여부 |
|-----------|------|---------------|
| 업스트림 의존성 | ... | Yes / No |
| 다운스트림 의존성 | ... | Yes / No |
| DB 스키마 변경 | ... | Yes / No |
| API 인터페이스 변경 | ... | Yes / No |

## 보안 평가
| 점검 항목 | 결과 |
|-----------|------|
| 하드코딩된 자격증명 | ✅/❌ |
| os.getenv() 기본값 인프라 노출 | ✅/❌ |
| .env, data/ gitignore 확인 | ✅/❌ |
| 외부 입력값 검증 | ✅/❌ |

## 테스트 체크리스트
- [ ] 로컬 실행 확인
- [ ] 주요 변경 함수 단위 테스트
- [ ] 관련 팀원에게 리뷰 요청

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## ⛔ 절대 금지 규칙 (Claude 포함 모든 실행 주체)

**아래 행동은 사용자의 명시적 승인 없이 절대 실행하지 않는다.**

1. `git push origin main` — main 브랜치 직접 push 금지
2. PR 없이 main에 직접 merge 금지
3. PR 리뷰(Approve) 없이 merge 금지
4. 다른 브랜치 폴더 파일을 현재 브랜치 커밋에 포함 금지
5. PR 생성 과정에서 요청하지 않은 파일을 추가로 커밋·push 금지

**이 규칙은 사용자가 명시적으로 "push해줘", "merge해줘"라고 말하기 전까지 유효하다.**

> 위반 시: 즉시 중단하고 사용자에게 보고한다.