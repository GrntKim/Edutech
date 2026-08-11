from enum import Enum
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
class PipelineResult(BaseModel):
    lesson_plan: dict
    validation: ValidationResult
    warning: str | None = None
