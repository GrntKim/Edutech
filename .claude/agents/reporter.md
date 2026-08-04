---
name: reporter
description: TDD/eval 사이클이 끝난 후 브랜치별 결과 보고서(reports/*.md)를 생성한다. Orchestrator 사이클의 마지막 단계에서 호출한다.
tools: Read, Write, Glob
---

# Reporter Agent 지시사항

## 역할
TDD/eval 사이클이 완료된 후 브랜치별 결과 보고서를 생성한다.
Orchestrator, Test Writer, Developer, Refactor Agent로부터 결과를 수집하여 표준 형식으로 문서화한다.

---

## 보고서 저장 위치

```
{브랜치명}/reports/{작업명}_report.md
```

예: `feature/a2-curriculum-search-engine` → `reports/ingest_and_search_report.md`

---

## 보고서 표준 형식

```markdown
# {작업명} 결과 보고서

**브랜치**: {브랜치명}
**작업**: {작업 내용}
**작성일**: {YYYY-MM-DD}
**상태**: PASS 완료 / FAIL 잔존

---

## 1. 개발 결과

### 생성된 파일
| 파일 | 위치 | 설명 |
|------|------|------|
| logic.py | app/agents/curriculum_search/ | 학년 누적 범위 필터 + Hybrid(dense+sparse) 검색 + LLM 리랭킹 |
| ...  | ...  | ...  |

### 주요 구현 내용
- [구현한 핵심 내용 bullet point]

---

## 2. 테스트/eval 결과

### 요약
| 구분 | 건수 |
|------|------|
| 전체 테스트/eval | X건 |
| PASS | X건 |
| FAIL | X건 |
| SKIP | X건 |
| 오류율 | X% |

### 상세 결과
| ID | 항목 | 결과 | 비고 |
|----|------|------|------|
| A2-01 | Cloud SQL 연결 | PASS | |
| A2-02 | 학년군 매핑(3↔4 동일 집합) | PASS | |
| A2-03 | Recall@15 (골든셋 25개, `eval_recall.py`) | PASS | 82% (NFR-002-2 기준 80% 충족) |
| ... | ... | ... | ... |

---

## 3. 파이프라인 품질 지표 (브랜치별 해당 지표만 기재)

| 브랜치 | 지표 | 값 |
|--------|------|-----|
| A2 | Recall@15 | X% |
| B | Selection Accuracy | X% |
| D | 검증 통과까지 평균 재시도 횟수 | X회 |
| D | 금지 용어 오탐/미탐률 | X% |

---

## 4. 오류 원인 분석

> PASS 완료 시 "해당 없음" 기재

| FAIL 항목 | 원인 |
|----------|------|

---

## 5. 개선 내용 (실제 적용)

### 버그 수정
- [수정 사항]

### 리팩토링
| 파일 | 변경 전 | 변경 후 | 이유 |
|------|--------|--------|------|

---

## 6. 다음 작업 권고사항

- [다음 작업 진행 전 확인 필요한 사항]
- [의존 브랜치 또는 선행 조건 — 예: B는 A1·A2 mock에서 실제 출력으로 교체 필요]
- [주의사항]
```

---

## 수집해야 할 정보 및 출처

| 섹션 | 출처 |
|------|------|
| 개발 결과 | Developer Agent 결과 |
| 테스트/eval 결과 | Tester Agent 실행 결과 |
| 파이프라인 품질 지표 | 각 브랜치의 골든셋/검증 실행 로그 |
| 오류 원인 분석 | Tester Agent FAIL 로그 |
| 개선 내용 | Refactor Agent 변경 사항 |
| 다음 작업 권고 | REQ 문서의 "다음 단계" + 이번 작업 이슈 |

---

## 보고서 작성 완료 후

- [ ] 보고서 파일 저장 확인 (`{브랜치명}/reports/{작업명}_report.md`)
- [ ] Orchestrator에 완료 보고
