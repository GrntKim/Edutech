"""B(실매핑) → C(내 교안 생성) 통합 확인 스크립트. (최종 프로젝트 키 절약판)

최종 프로젝트용 GEMINI_API_KEY의 할당량을 아끼기 위해, A2(hybrid_search)는
다시 호출하지 않는다. 2026-08-05 개인 키로 이미 실제 hybrid_search를 성공시켜
받아둔 결과([4과02-01] 동물의 생활, 1위)를 그대로 하드코딩해서 B의 입력으로 쓴다.
이 스크립트에서 실제로 Gemini를 호출하는 건 B(map_curriculum) 1번 + C(generate_lesson)
1번, 총 2번이 로직상 필요한 최소치다(단, 각 호출 내부에 실패 시 자동 재시도가 있어
타임아웃 등 일시 오류가 나면 실제 소모량은 더 늘 수 있다).

주의: A1(concept_collect)은 현재 app/lib/types.py에서 제거된 ACTIVE_CATEGORIES를
import하려다 ImportError가 나는 상태라(2026-08-05 확인, A1 소유 버그, 내 스코프 아님)
이 스크립트에서 직접 호출하지 못한다. 대신 A1의 산출물 타입(StructuredConcept)만
손으로 채운다.

사전 조건:
  - Cloud SQL Proxy 실행 중 (docs/infra/REQ006-DB접속안내.md) — C가 성취기준 조회에 필요
  - .env에 최종 프로젝트용 GEMINI_API_KEY, DB_* 값 존재

실행:
  PYTHONPATH=. python scripts/manual_test_b_to_c_integration.py
"""

from app.agents.curriculum_search.schema import CurriculumChunk, GradeBand, SearchResult, Subject
from app.agents.mapping.logic import map_curriculum
from app.agents.lesson_generate.logic import generate_lesson
from app.lib.types import PipelineContext, StructuredConcept

# A1이 정상 동작했다면 만들어줬을 산출물을 대신 손으로 채운다(A1은 현재 import 불가).
concept = StructuredConcept(
    is_ai_concept=True,
    concept_name="군집화",
    one_line_definition="비슷한 특징을 가진 데이터끼리 스스로 그룹으로 묶는 비지도학습 방법",
    core_mechanism="데이터 간 유사도를 계산해 가까운 것들을 같은 무리로 묶는다",
    key_operations=["유사도 계산", "그룹으로 묶기"],
    prerequisite_ideas=["기준을 정해 분류하기"],
    everyday_examples=["옷장에서 옷을 색깔별로 정리하기", "마트에서 비슷한 상품끼리 진열하기"],
    caution_terms=[],
)

context = PipelineContext(target_grade=4, subject_hint=None)

# 2026-08-05 개인 키로 실제 hybrid_search(query)를 돌려 받은 1위 결과를 그대로 재사용.
search_results = [
    SearchResult(
        chunk=CurriculumChunk(
            chunk_id="4과02-01",
            subject=Subject.SCIENCE,
            grade_band=GradeBand.G3_4,
            unit_name="동물의 생활",
            domain="",
            core_idea="",
            achievement_code="[4과02-01]",
            achievement_text="",
            explanation="",
            inquiry_activities=["동물 분류 기준 정하기"],
            source_page=0,
        ),
        similarity_score=0.346,
        rank=1,
        reasoning=(
            "동물을 형태적 특징에 따라 스스로 기준을 세워 무리 짓는 활동은 데이터의 속성과 "
            "특징을 바탕으로 그룹을 묶는 AI의 군집화 개념을 직관적으로 설명하기에 가장 적절합니다."
        ),
    )
]


def main() -> None:
    print("=== [B 실호출] map_curriculum ===")
    mapping_result = map_curriculum(concept, search_results, context)
    print(mapping_result.model_dump_json(indent=2))

    print("\n=== [C 실호출] generate_lesson ===")
    lesson_output = generate_lesson(mapping_result, context)
    print(f"topic={lesson_output.topic}")
    print(f"achievement_code={lesson_output.achievement_code}, subject={lesson_output.subject}")
    print(f"learning_objectives={lesson_output.learning_objectives}")

    print("\n=== 통합 확인 완료: B(실) → C(실) 정상 연결 ===")


if __name__ == "__main__":
    main()
