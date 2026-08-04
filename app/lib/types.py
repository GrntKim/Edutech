from enum import Enum
from pydantic import BaseModel, Field
from typing import Literal

class Subject(str, Enum):
    MATH = "MATH"
    SCIENCE = "SCIENCE"
    DOMESTIC_SCIENCE = "DOMESTIC_SCIENCE"
    ART = "ART"


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
    target_grade: int
    top_k: int = 15


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
class ConceptCategory(str, Enum):
    CLASSIFICATION = "CLASSIFICATION"
    PREDICTION = "PREDICTION"
    PATTERN_RECOGNITION = "PATTERN_RECOGNITION"
    CLUSTERING = "CLUSTERING"
    GENERATION = "GENERATION"
    RECOMMENDATION = "RECOMMENDATION"


ACTIVE_CATEGORIES: frozenset[ConceptCategory] = frozenset(
    {
        ConceptCategory.CLASSIFICATION,
        ConceptCategory.PREDICTION,
        ConceptCategory.PATTERN_RECOGNITION,
        ConceptCategory.CLUSTERING,
    }
)


# 소유: A1(REQ-001)
class ConceptInput(BaseModel):
    raw_concept_name: str
    target_grade: int = Field(ge=1, le=6)
    subject_hint: Subject | None = None


# 소유: A1(REQ-001)
class StructuredConcept(BaseModel):
    concept_name: str
    category: ConceptCategory
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
