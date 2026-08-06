"""C(교안 생성) 에이전트 수동 end-to-end 테스트.

REQ004 문서에 나온 예시(군집화 - 동물의 생활 - [4과02-01])를 그대로 사용한다.
DB에서 실제로 확인된 값(scripts/manual_test_lesson_generate.py 작성 시 조회)이므로
achievement_code 조회(fetch_achievement_standard)도 정상 동작해야 한다.

사전 조건:
  - Cloud SQL Proxy가 켜져 있어야 한다 (docs/infra/REQ006-DB접속안내.md 참고)
  - .env에 GEMINI_API_KEY, DB_* 값이 채워져 있어야 한다

실행:
  python scripts/manual_test_lesson_generate.py
"""

from app.agents.lesson_generate.logic import generate_lesson
from app.agents.lesson_generate.docx_export import render_lesson_docx
from app.lib.types import MappingResult, PipelineContext, Subject

mapping_result = MappingResult(
    chunk_id="4과02-01",
    achievement_code="[4과02-01]",
    subject=Subject.SCIENCE,
    unit_name="동물의 생활",
    mapping_reason="군집화(비지도학습)는 동물을 특징에 따라 무리 짓는 활동과 원리가 유사하다.",
    analogy="동물을 다리 개수나 사는 곳 같은 특징으로 무리 짓듯이, AI도 데이터를 비슷한 특징끼리 스스로 묶는다.",
    confidence=0.9,
    criteria_scores={"관련성": 0.9, "학년적합성": 0.85},
    flags=[],
    concept_name="군집화",
    inquiry_activities=["동물 분류 기준 정하기"],
)

context = PipelineContext(target_grade=4, subject_hint=Subject.SCIENCE)

print("=== generate_lesson 호출 (Gemini 실제 호출, 최대 90초) ===")
lesson_output = generate_lesson(mapping_result, context)
print(lesson_output.model_dump_json(indent=2, exclude_none=True))

out_path = "scripts/_manual_test_output.docx"
docx_bytes = render_lesson_docx(lesson_output)
with open(out_path, "wb") as f:
    f.write(docx_bytes)
print(f"\n=== DOCX 저장 완료: {out_path} ===")
