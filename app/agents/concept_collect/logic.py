import logging
import os

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

PROMPT_VERSION = "v1.3"

logger = logging.getLogger(__name__)

# 학년별 key_operations 상한 (D 요청 / WBS 2.3, 측정 전 잠정값)
_ov = os.getenv("A1_KEY_OPS")
KEY_OPS_LIMIT = {1: 1, 2: 1, 3: 1, 4: 1, 5: 2, 6: 3}
if _ov:
    KEY_OPS_LIMIT = {g: int(_ov) for g in range(1, 7)}

# A2 검색 요청 건수. 측정 기록이 top_k=15 기준이라 명시한다
TOP_K = 15

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
                top_k=TOP_K,
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
    except GeminiSchemaError as exc:
        retry_count += 1
        # C와 동일하게 실패 이유를 프롬프트에 덧붙여 같은 실패 반복을 줄인다
        retry_prompt = (
            f"{prompt}\n\n"
            "## 직전 시도 실패\n"
            f"- {exc}\n"
            "- 위 오류를 피해 스키마에 맞는 JSON만 출력하세요.\n"
        )
        try:
            concept = generate_structured(
                retry_prompt,
                StructuredConcept,
                prompt_version=PROMPT_VERSION,
            )
        except GeminiSchemaError as retry_exc:
            logger.error(
                "A1 1단계 구조화 2회 실패: concept=%s, prompt_version=%s, error=%s",
                concept_input.raw_concept_name,
                PROMPT_VERSION,
                retry_exc,
            )
            raise

    # 도메인 범위 검사 (AI 개념이 아니면 즉시 반환)
    if not concept.is_ai_concept:
        return ConceptCollectResult(
            concept=concept,
            search_query=SearchQuery(
                concept_name=concept.concept_name,
                concept_definition="",
                target_grade=context.target_grade,
                top_k=TOP_K,
            ),
            model_version=DEFAULT_MODEL,
            prompt_version=PROMPT_VERSION,
            retry_count=retry_count,
            status="unsupported_concept",
        )

    # 2단계: 검색용 쿼리 재작성
    rewrite_prompt = QUERY_REWRITE_PROMPT.format(
        concept_name=concept.concept_name,
        key_operations=", ".join(concept.key_operations[:KEY_OPS_LIMIT.get(context.target_grade, 3)]), 
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
        top_k=TOP_K,
    )

    return ConceptCollectResult(
        concept=concept,
        search_query=search_query,
        model_version=DEFAULT_MODEL,
        prompt_version=PROMPT_VERSION,
        retry_count=retry_count,
        status="success",
    )