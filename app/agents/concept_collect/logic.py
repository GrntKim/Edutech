from app.lib.types import (
    ConceptInput,
    StructuredConcept,
    ConceptCollectResult,
    SearchQuery,
    PipelineContext,
)
from app.lib.gemini import (
    generate_structured,
    generate_text,
    GeminiSchemaError,
    DEFAULT_MODEL,
)
from app.agents.concept_collect.prompts import (
    CONCEPT_ANALYSIS_PROMPT,
    QUERY_REWRITE_PROMPT,
)

PROMPT_VERSION = "v1.1"

# ambiguous_input 판정용 (LLM 호출 전, 쿼터 미소모)
BROAD_TERMS = frozenset({
    "AI",
    "A.I.",
    "에이아이",
    "인공지능",
    "컴퓨터",
})
MIN_LENGTH = 2
MAX_LENGTH = 50


def _is_ambiguous(raw_name: str) -> bool:
    """규칙 기반 모호 입력 판정. True면 LLM 호출 없이 ambiguous_input 반환."""
    name = raw_name.strip()
    if not name:
        return True
    if len(name) < MIN_LENGTH or len(name) > MAX_LENGTH:
        return True
    if not any(ch.isalpha() for ch in name):
        return True
    if name.upper().replace(" ", "") in {t.upper().replace(" ", "") for t in BROAD_TERMS}:
        return True
    return False

def analyze_concept(
    concept_input: ConceptInput,
    context: PipelineContext,
) -> ConceptCollectResult:
    """AI 개념을 구조화하고 교육과정 검색용 쿼리로 재작성한다."""

    retry_count = 0
    # 0단계: 모호 입력 규칙 판정 (Gemini 호출 없음)
    if _is_ambiguous(concept_input.raw_concept_name):
        return ConceptCollectResult(
            concept=StructuredConcept(
                is_ai_concept=False,
                concept_name=concept_input.raw_concept_name.strip(),
                one_line_definition="",
                core_mechanism="",
                key_operations=[],
                prerequisite_ideas=[],
                everyday_examples=[],
                caution_terms=[],
            ),
            search_query=SearchQuery(
                concept_name=concept_input.raw_concept_name.strip(),
                concept_definition="",
                target_grade=context.target_grade,
                top_k=15,
            ),
            model_version=DEFAULT_MODEL,
            prompt_version=PROMPT_VERSION,
            retry_count=0,
            status="ambiguous_input",
        )

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

    # 도메인 범위 검사 (AI 개념이 아니면 즉시 반환)
    if not concept.is_ai_concept:
        return ConceptCollectResult(
            concept=concept,
            search_query=SearchQuery(
                concept_name=concept.concept_name,
                concept_definition="",
                target_grade=context.target_grade,
                top_k=15,
            ),
            model_version=DEFAULT_MODEL,
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
    definition = generate_text(rewrite_prompt).strip()
    if not definition:
        retry_count += 1
        definition = generate_text(rewrite_prompt).strip()
    if not definition:
        # 빈 쿼리는 A2 검색이 성립하지 않으므로 개념명으로 대체
        definition = concept.concept_name

    search_query = SearchQuery(
        concept_name=concept.concept_name,
        concept_definition=definition,
        target_grade=context.target_grade,
        top_k=15,
    )

    return ConceptCollectResult(
        concept=concept,
        search_query=search_query,
        model_version=DEFAULT_MODEL,
        prompt_version=PROMPT_VERSION,
        retry_count=retry_count,
        status="success",
    )