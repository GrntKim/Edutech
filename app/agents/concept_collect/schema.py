from enum import Enum
from pydantic import BaseModel, Field

class ConceptCategory(str, Enum):
    CLASSIFICATION = "분류"
    PREDICTION = "예측"
    PATTERN_RECOGNITION = "패턴인식"
    CLUSTERING = "군집화"

class ConceptInput(BaseModel):
    raw_concept_name: str
    target_grade: int = Field(ge=1, le=6)

class StructuredConcept(BaseModel):
    concept_name: str
    category: ConceptCategory
    one_line_definition: str
    key_operations: list[str]
    everyday_examples: list[str]
    core_mechanism: str
    prerequisite_ideas: list[str]
    caution_terms: list[str]
    
class SearchQuery(BaseModel):
    concept_name: str
    concept_definition: str
    target_grade: int
    top_k: int = 15

