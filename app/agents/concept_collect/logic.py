from app.lib.types import (
    ConceptInput,
    StructuredConcept,
    ConceptCollectResult,
    SearchQuery,
    PipelineContext,
    ACTIVE_CATEGORIES,
)
from app.lib.gemini import generate_structured, generate_text, GeminiSchemaError
from app.lib.types import (
    ConceptInput,
    StructuredConcept,
    ConceptCollectResult,
    SearchQuery,
    PipelineContext,
)
from app.agents.concept_collect.prompts import (
    CONCEPT_ANALYSIS_PROMPT,
    QUERY_REWRITE_PROMPT,
)

PROMPT_VERSION = "v1.0"


def analyze_concept(
    concept_input: ConceptInput,
    context: PipelineContext,
) -> ConceptCollectResult:
    """AI 개념을 구조화하고 교육과정 검색용 쿼리로 재작성한다."""

    retry_count = 0

    # 1단계: 개념 구조화
    prompt = CONCEPT_ANALYSIS_PROMPT.format(
        concept_name=concept_input.raw_concept_name
    )
    try:
        concept = generate_structured(
            prompt,
            StructuredConcept,
            prompt_version=PROMPT_VERSION,
        )
    except GeminiSchemaError:
        retry_count += 1
        concept = generate_structured(
            prompt,
            StructuredConcept,
            prompt_version=PROMPT_VERSION,
        )

    # 지원 범위 검사 (1단계 활성 카테고리만 통과)
    if concept.category not in ACTIVE_CATEGORIES:
        return ConceptCollectResult(
            concept=concept,
            search_query=SearchQuery(
                concept_name=concept.concept_name,
                concept_definition="",
                target_grade=context.target_grade,
                top_k=15,
            ),
            model_version="gemini-3.6-flash",
            prompt_version=PROMPT_VERSION,
            retry_count=retry_count,
            status="unsupported_concept",
        )

    # 2단계: 검색용 쿼리 재작성
    rewrite_prompt = QUERY_REWRITE_PROMPT.format(
        concept_name=concept.concept_name,
        key_operations=", ".join(concept.key_operations),
        prerequisite_ideas=", ".join(concept.prerequisite_ideas),
        target_grade=context.target_grade,
    )
    definition = generate_text(rewrite_prompt)

    search_query = SearchQuery(
        concept_name=concept.concept_name,
        concept_definition=definition.strip(),
        target_grade=context.target_grade,
        top_k=15,
    )

    return ConceptCollectResult(
        concept=concept,
        search_query=search_query,
        model_version="gemini-3.6-flash",
        prompt_version=PROMPT_VERSION,
        retry_count=retry_count,
        status="success",
    )