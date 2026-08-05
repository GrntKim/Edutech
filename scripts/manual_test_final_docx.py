"""최종 프로젝트 키로 실제 DOCX 산출물 하나 만들어보는 스크립트.

B(map_curriculum)는 다시 부르지 않는다 — 방금 최종 키로 실제 호출해서 받은
MappingResult 값을 그대로 하드코딩한다. 이 스크립트에서 Gemini를 실제로 호출하는 건
C(generate_lesson) 1번뿐이다(render_lesson_docx는 순수 포맷팅이라 API 호출 없음).

사전 조건:
  - Cloud SQL Proxy 실행 중
  - .env에 최종 프로젝트용 GEMINI_API_KEY, DB_* 값 존재

실행:
  PYTHONPATH=.:app python scripts/manual_test_final_docx.py
"""

from app.agents.lesson_generate.logic import generate_lesson
from app.agents.lesson_generate.docx_export import render_lesson_docx
from app.lib.types import MappingResult, PipelineContext, Subject

# 방금 최종 키로 map_curriculum()을 실제 호출해서 받은 값 그대로.
mapping_result = MappingResult(
    chunk_id="4과02-01",
    achievement_code="[4과02-01]",
    subject=Subject.SCIENCE,
    unit_name="동물의 생활",
    mapping_reason=(
        "동물의 생활 단원에서 동물들의 특징을 관찰하고 스스로 기준을 정해 분류하는 탐구 활동은, "
        "데이터의 유사도를 계산하여 비슷한 속성을 가진 데이터끼리 무리를 짓는 AI의 군집화(Clustering) "
        "원리를 초등학교 4학년 수준에서 직관적으로 이해하기에 가장 적합합니다."
    ),
    analogy=(
        "여러 가지 동물 카드를 비슷한 생김새나 특징끼리 끼리끼리 모아놓는 것처럼, 인공지능도 정답이 없는 "
        "데이터들을 서로 비슷한 특징끼리 묶어서 그룹으로 만드는 것과 같아요."
    ),
    confidence=0.8925,
    criteria_scores={
        "semantic_similarity": 0.85,
        "educational_fit": 0.9,
        "student_level": 0.95,
        "analogy": 0.9,
        "achievement_alignment": 0.85,
    },
    flags=[],
    concept_name="군집화",
    inquiry_activities=["동물 분류 기준 정하기"],
)

context = PipelineContext(target_grade=4, subject_hint=Subject.SCIENCE)

print("=== [C 실호출, 최종 키] generate_lesson ===")
lesson_output = generate_lesson(mapping_result, context)
print(lesson_output.model_dump_json(indent=2, exclude_none=True))

out_path = "scripts/_final_key_output.docx"
docx_bytes = render_lesson_docx(lesson_output)
with open(out_path, "wb") as f:
    f.write(docx_bytes)
print(f"\n=== DOCX 저장 완료: {out_path} ===")
