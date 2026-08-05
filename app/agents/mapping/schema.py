"""
REQ-003 Mapping Agent

LLM Output Schema
"""

from pydantic import BaseModel, Field


class CriteriaScores(BaseModel):
    """
    LLM이 생성하는 5개 평가 점수

    모든 점수는 0.0 ~ 1.0
    """

    semantic_similarity: float = Field(
        ge=0.0,
        le=1.0,
        description="AI 개념과 단원의 의미적 유사성"
    )

    educational_fit: float = Field(
        ge=0.0,
        le=1.0,
        description="교육과정 적합성"
    )

    student_level: float = Field(
        ge=0.0,
        le=1.0,
        description="학생 수준 적합성"
    )

    analogy: float = Field(
        ge=0.0,
        le=1.0,
        description="비유 적절성"
    )

    achievement_alignment: float = Field(
        ge=0.0,
        le=1.0,
        description="성취기준 정합성"
    )


class MappingLLMResponse(BaseModel):
    """
    Gemini가 반환하는 JSON

    주의
    ----
    confidence는 생성하지 않는다.
    """

    chunk_id: str = Field(
        description="선택한 CurriculumChunk ID"
    )

    mapping_reason: str = Field(
        min_length=20,
        description="단원을 선택한 이유"
    )

    analogy: str = Field(
        min_length=10,
        description="학생 눈높이 비유"
    )

    criteria_scores: CriteriaScores