from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field
from typing import Literal

class Subject(str, Enum):
    MATH = "MATH"
    SCIENCE = "SCIENCE"
    DOMESTIC_SCIENCE = "DOMESTIC_SCIENCE"
    ART = "ART"
    SOCIAL = "SOCIAL"
    KOREAN = "KOREAN"


class GradeBand(str, Enum):
    G1_2 = "G1_2"
    G3_4 = "G3_4"
    G5_6 = "G5_6"


GRADE_TO_BANDS: dict[int, tuple[GradeBand, ...]] = {
    1: (GradeBand.G1_2,),
    2: (GradeBand.G1_2,),
    3: (GradeBand.G1_2, GradeBand.G3_4),
    4: (GradeBand.G1_2, GradeBand.G3_4),
    5: (GradeBand.G1_2, GradeBand.G3_4, GradeBand.G5_6),
    6: (GradeBand.G1_2, GradeBand.G3_4, GradeBand.G5_6),
}


class CurriculumChunk(BaseModel):
    chunk_id: str
    subject: Subject
    grade_band: GradeBand
    unit_name: str
    domain: str
    core_idea: str
    achievement_code: str
    achievement_text: str
    explanation: str
    inquiry_activities: list[str] = Field(default_factory=list)
    source_page: int


class SearchQuery(BaseModel):
    concept_name: str
    concept_definition: str
    target_grade: int = Field(ge=1, le=6)
    top_k: int = Field(default=15, ge=1)


class SearchResult(BaseModel):
    chunk: CurriculumChunk
    similarity_score: float
    rank: int
    reasoning: str | None = None



def grade_to_bands(target_grade: int) -> set[GradeBand]:
    """대상 학년 → 누적 학습 범위 학년군 집합.
    target_grade 3과 4가 동일 결과인 것은 NCIC 원문 학년군제 반영, 버그 아님."""
    if target_grade not in GRADE_TO_BANDS:
        raise ValueError(f"target_grade must be 1-6, got {target_grade}")
    return set(GRADE_TO_BANDS[target_grade])


# 소유: A1(REQ-001)
class ConceptInput(BaseModel):
    raw_concept_name: str
    target_grade: int = Field(ge=1, le=6)
    subject_hint: Subject | None = None


# 소유: A1(REQ-001)
class StructuredConcept(BaseModel):
    # is_ai_concept=False: 입력이 AI 개념으로 인식 안 됨(예: "김치찌개").
    # A1이 ConceptCollectResult.status="unsupported_concept"로 반환하는 근거.
    # False인 경우 나머지 필드는 신뢰하지 않는다 — 모델이 형식상 채워도 환각이므로
    # 소비자(A2·B·C·D)는 사용 금지.
    # 파이프라인 분기는 이 필드가 아니라 ConceptCollectResult.status로 판단한다
    # (status가 오케스트레이터의 공식 계약).
    is_ai_concept: bool
    concept_name: str
    one_line_definition: str
    core_mechanism: str
    key_operations: list[str]
    prerequisite_ideas: list[str]
    everyday_examples: list[str]
    caution_terms: list[str]


# 소유: A1(REQ-001)
class ConceptCollectResult(BaseModel):
    concept: StructuredConcept
    search_query: SearchQuery
    model_version: str
    prompt_version: str
    retry_count: int
    status: Literal["success", "unsupported_concept", "ambiguous_input"]


# 소유: B(REQ-003)
class MappingResult(BaseModel):
    chunk_id: str
    achievement_code: str
    subject: Subject
    unit_name: str
    mapping_reason: str
    analogy: str
    confidence: float
    criteria_scores: dict[str, float]
    flags: list[str]
    concept_name: str
    inquiry_activities: list[str] = Field(default_factory=list)


# 소유: D(REQ-005)
class PipelineContext(BaseModel):
    target_grade: int = Field(ge=1, le=6)
    subject_hint: Subject | None = None


# 소유: D(REQ-005)
class ValidationResult(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)
    retry_feedback: str = ""


# 소유: D(REQ-005)
class PipelineStatus(str, Enum):
    """파이프라인이 어떤 경로로 끝났는지를 나타내는 구조화된 값.

    warning 문구는 사용자 안내용 자유 문자열이라 호출부가 분기 근거로 쓸 수
    없다(문구가 바뀌면 조용히 깨진다). 소비자는 이 값으로 판단한다:
    - main.py 레이트리밋: ambiguous_input만 카운트에서 제외한다(A1이 LLM
      호출 전에 규칙으로 걸러내는 경로라 API 비용이 0이다).
    - lesson_requests.validation_status: 이 값을 그대로 저장해 실패 사유를
      사후에 집계할 수 있게 한다.

    unsupported_concept/ambiguous_input은 ConceptCollectResult.status와 문자열
    값이 일치한다 — 오케스트레이터가 변환 없이 그대로 승격시킬 수 있도록.
    """

    SUCCESS = "success"
    # 재시도를 다 썼지만 위반이 남은 채로 마지막 결과를 반환한 경우.
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    # 재시도할 때마다 완전히 다른 위반이 나와(수렴 불가) 중단한 경우.
    VALIDATION_DIVERGED = "validation_diverged"
    UNSUPPORTED_CONCEPT = "unsupported_concept"
    AMBIGUOUS_INPUT = "ambiguous_input"
    NO_CURRICULUM_MATCH = "no_curriculum_match"
    # run_pipeline이 잡아낸 에이전트 실패(_AGENT_ERRORS).
    AGENT_ERROR = "agent_error"
    # run_pipeline 밖으로 새어나간 예외 — main.py가 직접 기록한다.
    PIPELINE_ERROR = "pipeline_error"


# 소유: D(REQ-005)
class PipelineResult(BaseModel):
    lesson_plan: dict
    validation: ValidationResult
    # 기본값을 두지 않는다 — 새 조기 종료 경로를 추가하면서 status를 빠뜨리면
    # 실패가 조용히 success로 기록되므로, 그럴 바엔 즉시 터지는 편이 낫다.
    status: PipelineStatus
    warning: str | None = None


# 소유: E(REQ-006)
class User(BaseModel):
    id: UUID
    email: str
    password_hash: str
    name: str
    role: Literal["user", "admin"]
    created_at: datetime
    # 마이페이지에서 설정하는 담당 학년(초등 1~6). 선택 항목이라 기본은 "설정 안 함".
    # 생성 화면의 학년 기본 선택에만 쓰이며, 생성 시 다른 학년을 골라도 이 값은 바뀌지 않는다.
    default_grade: int | None = None


# 소유: E(REQ-006)
class Session(BaseModel):
    id: str
    user_id: UUID
    created_at: datetime
    expires_at: datetime


# 소유: E(REQ-006)
class LessonRequest(BaseModel):
    id: UUID
    user_id: UUID
    concept_name: str
    target_grade: int
    subject_hint: str | None = None
    mapped_curriculum_code: str | None = None
    # 기존 DOCX 생성 함수(render_lesson_docx)가 바로 소비할 수 있는 완전한
    # LessonOutput 구조를 그대로 저장한다 — 재출력 시 파이프라인 재실행 없이
    # 이 dict를 LessonOutput(**lesson_output)에 넣기만 하면 된다.
    lesson_output: dict
    validation_status: str
    created_at: datetime


# 소유: E(REQ-006)
class RateLimitStatus(BaseModel):
    allowed: bool
    daily_used: int
    daily_limit: int
    weekly_used: int
    weekly_limit: int
    # 다음 1회가 회복되는 시각(UTC). 롤링 윈도우라 캘린더 자정 리셋이 아니라
    # 윈도우 안 가장 오래된 요청이 빠져나가는 시각이다. 사용량이 0이거나
    # admin(무제한)이면 리셋할 것이 없어 None.
    daily_reset_at: datetime | None = None
    weekly_reset_at: datetime | None = None
