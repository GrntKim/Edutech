from pydantic import BaseModel, Field

from app.lib.types import MappingResult, PipelineContext, Subject

# school_level은 입력값이 아니라 시스템 스코프(A2가 초등 학년군만 다룸)에 따른 고정값이다.
SCHOOL_LEVEL = "초등학교"

_SUBJECT_LABELS = {"MATH": "수학", "SCIENCE": "과학"}


def subject_label(subject: Subject) -> str:
    """B가 넘겨준 Subject(MATH/SCIENCE) enum을 한글 라벨로 변환한다."""
    key = getattr(subject, "value", subject)
    return _SUBJECT_LABELS.get(key, str(key))


class ValidationResult(BaseModel):
    """D(REQ-005)의 검증 결과. C 입장에서는 입력값이며 D가 스키마를 소유한다.

    검증 실패 시 D는 이 객체 전체를 변환 없이 그대로 LessonInput.retry_feedback에
    재전달한다(D(REQ-005) ORCH-002 / VALID-000-1).
    """

    passed: bool
    violations: list[str] | None = None
    retry_feedback: str | None = None


class LessonInput(BaseModel):
    """RS-004-1: B의 MappingResult 일부(패스스루) + D의 PipelineContext(target_grade) +
    D의 ValidationResult를 하나로 병합한, C 내부 로직이 참조하는 입력 모델.

    concept_name/inquiry_activities는 B의 실제 MappingResult에 없어(§4 미해결
    사항) LessonInput에 포함하지 않는다. mapping_reason은 참고용으로만 받고
    현재 C 로직에서 직접 소비하지 않는다.
    """

    achievement_code: str
    subject: Subject
    unit_name: str
    analogy: str
    mapping_reason: str
    target_grade: int
    retry_feedback: ValidationResult | None = None


def build_lesson_input(
    mapping_result: MappingResult,
    context: PipelineContext,
    retry_feedback: ValidationResult | None = None,
) -> LessonInput:
    """B(MappingResult) + D(PipelineContext) + D(ValidationResult)를 LessonInput 하나로 병합.

    필드명이 바뀌는 경우 이 함수 안에서만 대응하고, 이후 로직은 LessonInput만 참조한다.
    """
    return LessonInput(
        achievement_code=mapping_result.achievement_code,
        subject=mapping_result.subject,
        unit_name=mapping_result.unit_name,
        analogy=mapping_result.analogy,
        mapping_reason=mapping_result.mapping_reason,
        target_grade=context.target_grade,
        retry_feedback=retry_feedback,
    )


class AchievementStandard(BaseModel):
    """curriculum_units 테이블 조회 결과(성취기준 원문·해설).

    app/lib/db.py는 E 소유이며 아직 빈 파일이라, app.lib.types에 기대지 않고
    lesson_generate 폴더 안에서 자체적으로(db_client.py) 조회하기 위해 이
    타입도 로컬로 정의한다.
    """

    code: str
    grade_band: str
    statement: str
    explanation: str


class LessonStage(BaseModel):
    """도입/전개/정리 각 단계의 시간·교수학습활동·도구 및 자료·유의점."""

    time: str
    activity: str
    tools: list[str]
    notes: list[str]


class LessonStages(BaseModel):
    intro: LessonStage
    development: LessonStage
    wrap_up: LessonStage


class EvaluationCriteria(BaseModel):
    """LG-001: 성취기준 원문·해설에 근거한 상/중/하 평가 기준."""

    high: str
    mid: str
    low: str


class GeneratedLessonContent(BaseModel):
    """Gemini에 한 번의 구조화 호출로 요청하는 생성 결과 형태.

    achievement_code/subject/unit_name/school_level처럼 LessonInput에서 그대로
    echo되는 필드는 포함하지 않고, B·D 어디에서도 오지 않아 C가 직접 생성해야
    하는 항목만 담는다("B가 준 것은 그대로, 나머지는 Gemini로").

    실제 채워진 양식 예시를 보면 "예상 문답"은 별도 칸이 아니라, 각 단계
    lesson_stages.*.activity 안에 교사의 실제 발화와 그에 대한 학생의 예상
    반응이 함께 서술되는 형태로 녹아 있다. 그래서 별도 필드를 두지 않는다.
    """

    lesson_time: str
    topic: str
    learning_objectives: list[str] = Field(min_length=1)
    materials: list[str] = Field(min_length=1)
    lesson_stages: LessonStages
    evaluation_criteria: EvaluationCriteria
    worksheet: list[str] = Field(min_length=1)


class LessonOutput(BaseModel):
    """RS-004-2: C가 생성하여 D로 전달하는 출력 스키마.

    첨부된 공식 교수학습과정안 양식의 모든 항목을 그대로 반영한다.
    """

    lesson_time: str
    school_level: str
    grade: int
    topic: str
    subject: str
    achievement_code: str
    learning_objectives: list[str]
    materials: list[str]
    lesson_stages: LessonStages
    evaluation_criteria: EvaluationCriteria
    worksheet: list[str]
