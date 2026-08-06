"""파이프라인 통합 확인 스크립트. 커밋 대상 아님, 로컬 실행 전용.

run_pipeline()과 동일한 순서로 A1→A2→B→(C+D 재시도 루프)를 직접 호출한다.
run_pipeline() 자체를 호출하지 않는 이유: PipelineResult에는 최종 교안만
담기고 A2의 중간 산출물(SearchResult 목록)이 없다. 검색 품질을 눈으로
확인하는 게 이번 실행의 주 목적이라, 같은 흐름을 여기서 한 번 더 조립해
중간 결과를 전부 출력한다(로직 재구현이 아니라 orchestrate 모듈의 실제
스테이지 함수를 그대로 호출 — run_pipeline() 안 부르는 대신 Gemini/DB를
두 번 소모하지도 않는다). D(validate)는 아직 스텁(passed=True 고정)이다.

사전 준비:
    1. Cloud SQL Proxy 실행 (docs/infra/REQ006-DB접속안내.md)
       cloud-sql-proxy <INSTANCE_CONNECTION_NAME> --port 5432 &
    2. .env에 GEMINI_API_KEY, DB_* 값 채워둘 것

사용법:
    python scripts/try_pipeline.py ["개념명" [학년]]
    python scripts/try_pipeline.py "분류" 4
    python scripts/try_pipeline.py            # 기본값: 분류, 4학년
"""

import json
import sys
import time
from pathlib import Path

# `python scripts/try_pipeline.py`로 직접 실행하면 Python이 scripts/만
# sys.path에 넣고 레포 루트는 안 넣는다 — app 패키지가 안 보여서 ModuleNotFoundError.
# PYTHONPATH=. 없이도 누구나 그대로 실행되도록 루트를 직접 꽂는다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import orchestrate  # noqa: E402
from app.lib.types import ConceptInput, PipelineContext  # noqa: E402


def _ms(start: float) -> str:
    return f"{(time.monotonic() - start) * 1000:.0f}ms"


def main() -> None:
    concept_name = sys.argv[1] if len(sys.argv) > 1 else "분류"
    target_grade = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    user_input = ConceptInput(
        raw_concept_name=concept_name, target_grade=target_grade, subject_hint=None
    )
    context = PipelineContext(target_grade=target_grade, subject_hint=None)

    print(f"=== 개념='{concept_name}' 학년={target_grade} ===\n")
    pipeline_start = time.monotonic()

    # ── A1 ──
    t = time.monotonic()
    concept_result = orchestrate.collect_concept(user_input, context)
    print(f"[A1 개념 수집] status={concept_result.status} ({_ms(t)})")
    if concept_result.status != "success":
        print(f"  -> 조기 종료 (정상 동작): {concept_result.status}")
        print(f"\n총 소요 {_ms(pipeline_start)}")
        return

    concept = concept_result.concept
    print(f"  concept_name  : {concept.concept_name}")
    print(f"  definition    : {concept.one_line_definition}")
    print(f"  caution_terms : {concept.caution_terms}")

    # ── A2 ── (검색 품질 확인이 이 스크립트의 주 목적)
    t = time.monotonic()
    search_results = orchestrate.search_curriculum(concept_result.search_query)
    print(f"\n[A2 교육과정 검색] {len(search_results)}건 ({_ms(t)})")
    if not search_results:
        print("  -> 조기 종료 (정상 동작): 검색 결과 없음")
        print(f"\n총 소요 {_ms(pipeline_start)}")
        return

    for r in search_results:
        print(
            f"  #{r.rank} [{r.chunk.achievement_code}] {r.chunk.unit_name} "
            f"(유사도={r.similarity_score:.3f})"
        )
        if r.reasoning:
            print(f"       reasoning: {r.reasoning}")

    # ── B ──
    t = time.monotonic()
    mapping = orchestrate.map_concept(concept, search_results, context)
    print(
        f"\n[B 매핑] [{mapping.achievement_code}] {mapping.unit_name} "
        f"(confidence={mapping.confidence:.2f}, flags={mapping.flags}) ({_ms(t)})"
    )
    print(f"  analogy: {mapping.analogy}")

    # ── C + D 재시도 루프 (run_pipeline()과 동일 로직) ──
    retry_count = 0
    retry_feedback = None
    lesson_plan: dict = {}
    validation = None

    while True:
        t = time.monotonic()
        lesson_plan = orchestrate.generate_lesson(mapping, context, retry_feedback)
        print(f"\n[C 교안 생성] retry={retry_count} ({_ms(t)})")

        t = time.monotonic()
        validation = orchestrate.validate(
            lesson_plan, context, subject=mapping.subject, caution_terms=concept.caution_terms
        )
        print(f"[D 검증] passed={validation.passed} ({_ms(t)}) — 현재 스텁(항상 통과)")

        if validation.passed or retry_count >= orchestrate.MAX_RETRIES:
            break
        retry_feedback = validation
        retry_count += 1

    print(f"\n=== 총 소요 {_ms(pipeline_start)} (목표: 60초) ===")
    print(json.dumps(lesson_plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
