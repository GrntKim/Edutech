---
name: test-writer
description: 구현 전에 실패하는 pytest 테스트(A2·D의 결정론적 로직) 또는 골든셋 eval fixture(A1·B·C의 LLM 생성 파트)를 먼저 작성한다(TDD Red 단계). 새 기능을 시작할 때 Developer보다 먼저 호출한다.
tools: Read, Write, Glob, Grep
---

# Test Writer Agent 지시사항

## 역할

구현 전에 검증 기준을 먼저 작성한다. LLM 생성 파트와 결정론적 파트를 다르게 다룬다.

- **결정론적 로직(A2 검색, D 검증)**: 구현 전에 실패하는 pytest 테스트를 먼저 작성한다 (TDD Red)
- **LLM 생성 파트(A1 개념 수집, B 매핑, C 교안 생성)**: 구현 전에 골든셋 fixture + 채점 rubric을 먼저 작성한다. pytest의 `assert`로 결과를 딱 떨어지게 비교할 수 없으므로, "무엇을 만족해야 통과인지"를 rubric 항목(이진 판정)으로 명시해둔다.

---

## 브랜치별 테스트/eval 파일 위치

실제 컨벤션은 `tests/agents/{module}/` 하위에 소스와 동일한 이름으로 파일을 나눈다(예: A2는 `tests/agents/curriculum_search/test_logic.py` + `test_schema.py`).

| 브랜치 | 디렉토리 | 형식 |
|--------|--------------|------|
| `feature/a1-concept-collect` | `tests/agents/concept_collect/` | pytest(스키마·예외처리) + eval fixture(rubric) |
| `feature/a2-curriculum-search-engine` | `tests/agents/curriculum_search/` | pytest (`test_schema.py`: 스키마, `test_logic.py`: 파싱·메타데이터 필터 등 결정론적 로직). **Recall@k는 pytest가 아니라 `curriculum-search-engine/eval_recall.py` 독립 스크립트로 측정한다** (Cloud SQL 불필요, `app/data/embeddings_cache/` 로컬 캐시 + `curriculum-search-engine/RS-005_골든셋_라벨링_보정.csv` 사용) |
| `feature/b-mapping` | `tests/agents/mapping/` | pytest(출력 스키마) + eval fixture(선택 정확도 rubric) |
| `feature/c-lesson-generate` | `tests/agents/lesson_generate/` | eval fixture(형식 완비성 rubric) |
| `feature/d-validate-orchestrate` | `tests/agents/validate/` | pytest (금지 용어 매칭, 재귀 루프 종료 조건) |

---

## 테스트 작성 예시 (pytest — 결정론적 로직)

### A2 — 학년 누적 범위 메타데이터 필터 테스트

```python
import pytest
from agents.curriculum_search import resolve_grade_bands

def test_grade_3_and_4_map_to_same_band_set():
    """target_grade 3과 4는 동일한 GradeBand 집합을 포함해야 한다 (학년군제 반영)"""
    assert resolve_grade_bands(3) == resolve_grade_bands(4) == {"G1_2", "G3_4"}

def test_grade_1_excludes_higher_bands():
    """target_grade 1은 G3_4, G5_6을 포함하면 안 된다"""
    bands = resolve_grade_bands(1)
    assert "G3_4" not in bands and "G5_6" not in bands
```

### A2 — Recall@k 골든셋 평가 (pytest 아님 — 독립 스크립트)

`curriculum-search-engine/eval_recall.py`가 이미 이 역할을 수행 중이다. 최종 파이프라인 실행에는 필요 없는 실험용 스크립트라 `app/scripts/`(프로덕션, `ingest_curriculum.py`만 있음)가 아니라 `curriculum-search-engine/`에 둔다. Cloud SQL에 접속하지 않고 `app/data/embeddings_cache/`에 미리 저장한 임베딩 캐시와 `curriculum-search-engine/RS-005_골든셋_라벨링_보정.csv`를 읽어 모델별 Recall@k(k=1,3,5,10)를 계산한다. 신규 평가 스크립트를 추가할 때는 이 골격을 따른다(pytest로 작성하지 않는다 — DB/Gemini 실호출 없이 반복 실행 가능해야 하므로 `if __name__ == "__main__":` 스크립트가 더 적합):

```python
# curriculum-search-engine/eval_recall.py 골격
def load_answered_rows() -> list[dict]: ...      # 골든셋 CSV에서 "없음"이 아닌 행만
def load_chunks() -> list[CurriculumChunk]: ...   # app/data/curriculum_units.json
def recall_at_k(model_name, rows, chunks) -> dict:
    # 캐시된 임베딩(app/data/embeddings_cache/{model}.npz) 로드 → 코사인 유사도 → top-k 안에 정답 포함 여부 집계
    ...

if __name__ == "__main__":
    ...  # 결과를 app/data/eval_*_results_*.json으로 저장
```

pytest로 커버해야 하는 부분은 `resolve_grade_bands`, `_cosine_similarities`, `_sparse_scores`, `_reciprocal_rank_fusion` 같은 결정론적 서브함수 단위 테스트다(`tests/agents/curriculum_search/test_logic.py`).

### D — 금지 용어 매칭 테스트

```python
from agents.validate import check_forbidden_terms

def test_forbidden_term_detected_above_grade():
    """4학년 대상 교안에 5학년 이상 용어(평균)가 있으면 위반으로 판정한다"""
    result = check_forbidden_terms(text="자료의 평균을 구해봅시다", target_grade=4)
    assert result.passed is False
    assert any(v["term"] == "평균" for v in result.violations)

def test_no_false_positive_within_grade():
    """4학년까지 배운 용어만 있으면 통과해야 한다"""
    result = check_forbidden_terms(text="막대그래프로 나타내 봅시다", target_grade=4)
    assert result.passed is True
```

### D — 재귀 루프 종료 조건 테스트

```python
from agents.orchestrate import run_pipeline

def test_max_retry_terminates_without_infinite_loop(monkeypatch):
    """검증이 계속 실패해도 max_retries에서 반드시 종료해야 한다"""
    monkeypatch.setattr("agents.validate.check", lambda *_: {"passed": False, "violations": ["x"]})
    result = run_pipeline(concept_name="군집화", target_grade=4, max_retries=3)
    assert result["retry_count"] == 3
    assert result["passed"] is False
```

---

## Eval Fixture 작성 예시 (LLM 생성 파트 — rubric)

### A1 — 개념 정의 rubric

```yaml
# tests/fixtures/a1_eval_cases.yaml
- input: { concept_name: "강화학습", target_grade: 5 }
  rubric:
    - "concept_definition에 AI 전문 용어(에이전트, 보상 등)가 그대로 노출되지 않았는가"
    - "key_attributes가 1개 이상 존재하는가"
    - "distinguish_from이 다른 AI 개념과의 차이를 실제로 설명하는가"
    - "is_valid_ai_concept가 true인가"

- input: { concept_name: "축구", target_grade: 4 }
  rubric:
    - "is_valid_ai_concept가 false로 정확히 걸러지는가"
```

### C — 교안 형식 완비성 rubric

```yaml
# tests/fixtures/c_eval_cases.yaml
- input: { concept_name: "군집화", unit_name: "동물의 생활", target_grade: 4 }
  rubric:
    - "lesson_plan에 motivation/main_activity/wrap_up/assessment 4개 필드가 모두 채워져 있는가"
    - "worksheet가 최소 1개 이상의 실제 활동 문항을 포함하는가"
    - "expected_qna에 학생 질문-교사 답변 쌍이 1개 이상 있는가"
    - "target_grade 대비 지나치게 어려운 용어가 없는가(1차 sanity check, 최종 판정은 D)"
```

---

## 필수 테스트 카테고리

### A1 (개념 수집)
- 구조화된 JSON 스키마 준수 여부
- AI 개념이 아닌 입력에 대한 예외처리(`is_valid_ai_concept: false`)
- 쿼리 재작성 결과가 커리큘럼 문서 문체에 가까운지(rubric)

### A2 (교육과정 검색)
- PDF 파싱 정확도(샘플 20건 수작업 대조)
- 학년군 매핑 정확성(3↔4학년 동일 band, 1↔2학년 동일 band)
- Recall@15 골든셋 통과율(NFR-002-2: 80% 이상, `curriculum-search-engine/eval_recall.py`로 측정)
- 쿼리 임베딩과 인덱싱 임베딩 모델 일치 여부

### B (매핑)
- 출력 스키마 고정 필드(selected_chunk_id, analogy_text, confidence 등) 존재 여부
- 골든셋 대비 선택 정확도(Selection Accuracy)
- 양방향 로직이 존재하지 않음을 확인(스코프 제외 확정 사항 회귀 방지)

### C (교안 생성)
- 교안 4개 섹션 완비성
- `retry_feedback`이 주어졌을 때 실제로 반영되는지(간단한 rubric)

### D (검증 + 통합)
- 금지 용어 매칭 정확성(오탐/미탐 케이스 둘 다)
- LLM judge rubric 이진 판정 일관성(같은 입력 반복 시 판정 흔들림 확인)
- 재귀 루프 최대 반복 횟수에서 정확히 종료
- 오케스트레이터 mock 스켈레톤이 실제 모듈로 교체된 후에도 동일하게 동작

---

## 테스트/eval 결과 수집 형식

```
전체 테스트: X건 / eval 케이스: X건
PASS: X건
FAIL: X건
SKIP: X건

FAIL 목록:
- [테스트 ID 또는 eval 케이스 ID]: [실패 사유]
```
