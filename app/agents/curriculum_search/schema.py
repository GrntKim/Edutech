from enum import Enum

from pydantic import BaseModel, Field


class Subject(str, Enum):
    MATH = "MATH"
    SCIENCE = "SCIENCE"
    SOCIAL = "SOCIAL"


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
