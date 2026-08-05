from pydantic import BaseModel, Field


class MappingLLMResponse(BaseModel):
    """
    Gemini가 반환해야 하는 구조화(JSON) 응답 스키마.
    """

    chunk_id: str
    mapping_reason: str
    analogy: str
    criteria_scores: dict[str, float] = Field(default_factory=dict)