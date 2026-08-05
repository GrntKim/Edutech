from pydantic import BaseModel, Field

from app.lib.types import MappingResult, PipelineContext, Subject, ValidationResult

# school_level은 입력값이 아니라 시스템 스코프(A2가 초등 학년군만 다룸)에 따른 고정값이다.
SCHOOL_LEVEL = "초등학교"

_SUBJECT_LABELS = {
    "MATH": "수학",
    "SCIENCE": "과학",
    "DOMESTIC_SCIENCE": "실과",
    "ART": "미술",
}


def subject_label(subject: Subject) -> str:
    """B가 넘겨준 Subject(MATH/SCIENCE/DOMESTIC_SCIENCE/ART) enum을 한글 라벨로 변환한다."""
    key = getattr(subject, "value", subject)
    return _SUBJECT_LABELS.get(key, str(key))


class LessonInput(BaseModel):
    """RS-004-1: B의 MappingResult 일부(패스스루) + D의 PipelineContext(target_grade) +
    D의 ValidationResult(app.lib.types 소유)를 하나로 병합한, C 내부 로직이
    참조하는 입력 모델.

    concept_name/inquiry_activities는 실제 B.MappingResult(app/lib/types.py)에
    존재함이 확인되어 포함한다. mapping_reason은 참고용으로만 받고 현재 C
    로직에서 직접 소비하지 않는다.
    """

    achievement_code: str
    subject: Subject
    unit_name: str
    analogy: str
    concept_name: str
    inquiry_activities: list[str]
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
        concept_name=mapping_result.concept_name,
        inquiry_activities=mapping_result.inquiry_activities,
        mapping_reason=mapping_result.mapping_reason,
        target_grade=context.target_grade,
        retry_feedback=retry_feedback,
    )


class AchievementStandard(BaseModel):
    """curriculum_chunks 테이블 조회 결과(성취기준 원문·해설).

    app/lib/db.py는 E 소유이며 아직 빈 파일이라, app.lib.types에 기대지 않고
    lesson_generate 폴더 안에서 자체적으로(db_client.py) 조회하기 위해 이
    타입도 로컬로 정의한다.
    """

    code: str
    grade_band: str
    statement: str
    explanation: str


class StageActivity(BaseModel):
    """도입/전개/정리 한 단계 안의 개별 "학습내용" 행(실제 양식의 표 한 줄).

    실제 수업지도안은 도입/전개/정리 하나를 통짜 텍스트로 채우지 않고, 그
    안에서도 "전시학습 상기" → "학습 문제 안내하기" 처럼 학습내용별로 행이
    나뉘어 있고, 행마다 각자의 시간·교사활동·학생활동·자료 및 유의점을
    가진다. content_label이 그 행의 이름(표의 "학습내용" 칸)이다.
    """

    content_label: str
    time: str
    teacher: str
    student: str
    tools: list[str]
    notes: list[str]


class LessonStages(BaseModel):
    """도입(전시학습 상기/학습 문제 안내하기 2행)/전개(활동1/활동2 2행)/
    정리(학습 내용 정리/차시예고 2행) 각 단계가 고정된 content_label 행 개수로 구성된다."""

    intro: list[StageActivity] = Field(min_length=2)
    development: list[StageActivity] = Field(min_length=2)
    wrap_up: list[StageActivity] = Field(min_length=2)


class EvaluationCriteria(BaseModel):
    """LG-001: 성취기준 원문·해설에 근거한 상/중/하 평가 기준."""

    high: str
    mid: str
    low: str


class WorksheetTable(BaseModel):
    """관찰 결과·계산 결과를 기록하는 실제 표(시각 요소).

    사진 등 이미지 생성 도구는 없어서, 대신 실물 표/격자로 시각적 구조를
    제공한다(예: 동물별 특징 기록표, 필터 계산 격자).
    """

    headers: list[str]
    rows: list[list[str]] = Field(min_length=1)


class WorksheetSection(BaseModel):
    """활동지의 단계별 섹션 하나(예: "1단계. 픽셀 그림 알아보기").

    instruction은 그 단계에서 학생이 무엇을 관찰·계산·기록해야 하는지 설명하는
    안내문이고, items는 학생이 직접 채워야 하는 문항/빈칸 목록이다. table은
    관찰·기록용 표가 필요한 경우에만 채우는 선택 필드다(시각 요소).
    """

    step_label: str
    instruction: str
    items: list[str] = Field(default_factory=list)
    table: WorksheetTable | None = None


class Worksheet(BaseModel):
    """LG-003: 완결된 한 페이지 활동지.

    첨부된 실제 활동지 예시("AI는 사진의 특징을 어떻게 찾을까?")처럼 미션형
    제목·동기유발 설명·단계별 섹션·마무리 반성 질문으로 구성된 하나의 문서다.
    단순 문항 나열(list[str])이 아니라 이 구조로 생성한다.
    """

    icon: str
    title: str
    mission: str
    sections: list[WorksheetSection] = Field(min_length=1)
    reflection_questions: list[str] = Field(min_length=1)


class GeneratedLessonContent(BaseModel):
    """Gemini에 한 번의 구조화 호출로 요청하는 생성 결과 형태.

    achievement_code/subject/unit_name/school_level처럼 LessonInput에서 그대로
    echo되는 필드는 포함하지 않고, B·D 어디에서도 오지 않아 C가 직접 생성해야
    하는 항목만 담는다("B가 준 것은 그대로, 나머지는 Gemini로").

    "예상 문답"은 별도 칸이 아니라, 공식 양식이 실제로 갖고 있는 각 학습내용
    행의 교사/학생 열(StageActivity.teacher / .student)에 교사의 발화·행동과
    학생의 예상 반응이 각각 들어가는 형태로 반영한다.
    """

    lesson_time: str
    topic: str
    ai_digital_tool: str
    learning_objectives: list[str] = Field(min_length=1)
    materials: list[str] = Field(min_length=1)
    lesson_stages: LessonStages
    evaluation_criteria: EvaluationCriteria
    # 학생이 직접 손으로 채우거나 기록해야 하는 실습(카드 분류, 관찰 기록 등)이 있는
    # 수업에서만 채우고, 필요 없는 수업(토론·구두 발표·게임 등)에서는 null로 둔다.
    worksheet: Worksheet | None = None


class LessonOutput(BaseModel):
    """RS-004-2: C가 생성하여 D로 전달하는 출력 스키마.

    첨부된 공식 교수학습과정안 양식(수업지도안)의 모든 항목을 그대로 반영한다.
    """

    lesson_time: str
    school_level: str
    grade: int
    topic: str
    subject: str
    achievement_code: str
    ai_digital_tool: str
    learning_objectives: list[str]
    materials: list[str]
    lesson_stages: LessonStages
    evaluation_criteria: EvaluationCriteria
    worksheet: Worksheet | None = None
