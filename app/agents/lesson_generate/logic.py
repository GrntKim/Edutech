from app.agents.lesson_generate.db_client import fetch_achievement_standard
from app.agents.lesson_generate.gemini_client import generate_json
from app.agents.lesson_generate.prompts import (
    SYSTEM_INSTRUCTION,
    build_generation_prompt,
)
from app.agents.lesson_generate.schema import (
    SCHOOL_LEVEL,
    GeneratedLessonContent,
    LessonOutput,
    ValidationResult,
    build_lesson_input,
    subject_label,
)
from app.lib.types import MappingResult, PipelineContext


def generate_lesson(
    mapping_result: MappingResult,
    context: PipelineContext,
    retry_feedback: ValidationResult | None = None,
) -> LessonOutput:
    """LG-001/LG-002/LG-003/LG-004: 교안 양식의 모든 빈칸을 한 번 채운다 (단일 시도).

    핵심 설계 원칙("B가 준 것은 그대로, 나머지는 Gemini로")에 따라, B가 이미
    판단해서 넘겨준 값(achievement_code/subject/unit_name/analogy)은 가공 없이
    그대로 echo하고, 양식에는 있지만 B·D 어디에서도 오지 않는 나머지 항목은
    Gemini 구조화 출력으로 직접 생성한다. achievement_code로 curriculum_units을
    조회해 학습 목표·평가 기준의 근거로 삼는다(LG-001). retry_feedback이 있으면
    (D의 재검증 실패) 프롬프트에 반영해 교정된 결과를 생성한다(LG-004). 최대
    3회 재시도 루프 자체는 오케스트레이터(D)의 책임이며, 이 함수는 한 번의
    생성만 담당한다.
    """
    lesson_input = build_lesson_input(mapping_result, context, retry_feedback)
    standard = fetch_achievement_standard(lesson_input.achievement_code)
    prompt = build_generation_prompt(lesson_input, standard)

    content: GeneratedLessonContent = generate_json(
        system_instruction=SYSTEM_INSTRUCTION,
        prompt=prompt,
        response_model=GeneratedLessonContent,
    )

    return LessonOutput(
        lesson_time=content.lesson_time,
        school_level=SCHOOL_LEVEL,
        grade=lesson_input.target_grade,
        topic=content.topic,
        subject=subject_label(lesson_input.subject),
        achievement_code=lesson_input.achievement_code,
        learning_objectives=content.learning_objectives,
        materials=content.materials,
        lesson_stages=content.lesson_stages,
        evaluation_criteria=content.evaluation_criteria,
        worksheet=content.worksheet,
    )
