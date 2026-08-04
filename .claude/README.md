# `.claude/` — Claude Code 에이전트 하네스

이 폴더는 우리 팀 TDD 사이클(Red → Green → Refactor)을 Claude Code 서브에이전트로 자동화하기 위한 설정입니다. 브랜치(A1~E) 구분 없이 전원이 공용으로 씁니다 — 각자 로컬에서 `git pull`만 받으면 바로 사용할 수 있습니다.

폴더가 두 개로 나뉘어 있는 이유: `agents/`는 Task 도구로 호출되는 **서브에이전트**(자체 tools/컨텍스트를 가짐), `commands/`는 `/명령어`로 부르면 현재 대화에 프롬프트로 주입되는 **슬래시 커맨드**입니다. Claude Code는 이 두 경로(`.claude/agents/`, `.claude/commands/`)만 스캔하므로, 새 슬래시 커맨드를 추가할 땐 반드시 `commands/` 바로 아래에 둬야 인식됩니다(하위 폴더에 중첩하면 인식 안 됨).

## 있는 것

| 파일 | 역할 | 언제 호출되나 |
|------|------|--------------|
| `agents/orchestrator.md` | 전체 TDD 사이클 지휘자. 나머지 에이전트를 순서대로 호출 | 작업이 어느 정도 끝났을 때 `/orchestrator` 또는 `@ORCHESTRATOR`로 직접 호출 |
| `agents/test-writer.md` | 구현 전에 실패하는 테스트/eval fixture부터 작성 (Red) | Orchestrator가 Developer보다 먼저 호출 |
| `agents/developer.md` | 테스트를 통과시키는 최소 구현 (Green) | Test Writer 다음 |
| `agents/tester.md` | 실제로 테스트/eval 실행, PASS/FAIL/SKIP 수집 | Developer 다음 |
| `agents/refactor.md` | 통과 상태 유지하며 코드 품질 개선 (Refactor) | 전체 테스트 PASS 이후에만 |
| `agents/review.md` | 머지 전 방어적 코드 리뷰(Correctness/에러처리/테스트커버리지/성능/API설계/가독성/보안위임 7축) | 테스트 통과 후, 머지 전 |
| `agents/security-auditor.md` | Gemini API 키·Cloud SQL 접속정보 하드코딩 여부 점검 | 코드 작성 직후 + git commit 직전 |
| `agents/impact-assessor.md` | 변경이 공유 스키마(`app/lib/types.py`)나 다른 브랜치에 미치는 영향 분석, 리스크 등급 산정 | PR 생성 직전 |
| `agents/reporter.md` | 작업 결과 보고서(`reports/*.md`) 생성 | 사이클 마지막 |
| `commands/PR-report.md` | 커밋~PR 생성까지 전 과정 자동화 (`/PR-report`로 호출) | PR 올릴 때 |

## 어떻게 쓰나

- 기능 하나를 마무리했으면 `/orchestrator` 실행 → 위 순서대로 자동으로 돌아갑니다(테스트 작성 → 구현 → 실행 → 리팩터 → 리뷰 → 영향평가 → 보고서 → 보안점검).
- 커밋부터 PR 생성까지는 `/PR-report` 하나로 끝냅니다. **`main`에 직접 push/merge는 이 커맨드가 자체적으로 금지**하고 있고, 사용자가 명시적으로 요청하기 전까진 절대 실행하지 않습니다.
- 각 에이전트는 담당 브랜치의 실제 파일 위치(`app/agents/{module}/` 패키지 구조)와 REQ 문서 경로(`docs/*/REQ*.md`, A2는 `curriculum-search-engine/REQ-002_*.md`)를 알고 있으므로, 브랜치명만 알려주면 알아서 맞는 문서·파일을 찾아갑니다.

## 알아두면 좋은 것

- 결정론적 로직(A2 검색, D 검증)은 **pytest**로, LLM이 생성하는 파트(A1/B/C)는 **골든셋 + rubric eval**로 서로 다르게 검증합니다.
- 공유 계약은 `app/lib/types.py`(공유 타입) + 각자 REQ 문서의 스키마 절입니다. 이 둘을 건드리는 변경은 Impact Assessor가 자동으로 HIGH 리스크로 잡고 다운스트림 브랜치 리뷰를 요구합니다.
- A2의 Recall@k 품질 평가는 pytest가 아니라 `app/scripts/eval_recall.py` 같은 독립 스크립트로 돕니다(Cloud SQL 불필요, 로컬 임베딩 캐시 사용) — Tester가 이 차이를 알고 있습니다.
