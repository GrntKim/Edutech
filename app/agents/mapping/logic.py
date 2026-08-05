from app.lib.gemini import GeminiSchemaError, generate_structured
from app.lib.types import (
    MappingResult,
    PipelineContext,
    SearchResult,
    StructuredConcept,
)

from .prompts import build_prompt
from .schema import MappingLLMResponse


def _calculate_confidence(criteria_scores: dict[str, float]) -> float:
    """
    criteria_scores 평균값으로 confidence 계산.
    (1차 프로토타입에서는 단순 평균 사용)
    """

    if not criteria_scores:
        return 0.0

    return round(
        sum(criteria_scores.values()) / len(criteria_scores),
        3,
    )


def _make_flags(confidence: float) -> list[str]:
    """
    Confidence 기준으로 Flag 생성
    """

    flags: list[str] = []

    if confidence < 0.5:
        flags.append("retry_recommended")

    elif confidence < 0.75:
        flags.append("low_confidence")

    return flags


def map_curriculum(
    concept: StructuredConcept,
    candidates: list[SearchResult],
    context: PipelineContext,
    excluded_chunk_ids: list[str] | None = None,
) -> MappingResult:
    """
    REQ-003
    Mapping Agent

    StructuredConcept
        +
    SearchResult[]
        +
    PipelineContext

            ↓

    MappingResult 반환
    """

    # -----------------------------------------
    # 후보 필터링
    # -----------------------------------------

    if excluded_chunk_ids:

        candidates = [
            candidate
            for candidate in candidates
            if candidate.chunk.chunk_id not in excluded_chunk_ids
        ]

    if not candidates:
        raise ValueError("매핑 가능한 후보가 없습니다.")

    # -----------------------------------------
    # Prompt 생성
    # -----------------------------------------

    prompt = build_prompt(
        concept=concept,
        candidates=candidates,
        context=context,
    )

    # -----------------------------------------
    # Gemini 호출
    # -----------------------------------------

    try:

        response = generate_structured(
            prompt=prompt,
            response_schema=MappingLLMResponse,
            thinking_level="low",
            prompt_version="REQ003-v2.0",
        )

        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.chunk.chunk_id == response.chunk_id
            ),
            None,
        )

        # LLM이 후보 밖 chunk_id 반환 시
        if selected is None:
            selected = candidates[0]

        mapping_reason = response.mapping_reason
        analogy = response.analogy
        criteria_scores = response.criteria_scores

    except GeminiSchemaError:

        # JSON 파싱 실패 시 Fallback
        selected = candidates[0]

        mapping_reason = "Gemini 응답 검증 실패로 SearchResult 1순위를 선택했습니다."

        analogy = ""

        criteria_scores = {}

    # -----------------------------------------
    # Confidence 계산
    # -----------------------------------------

    confidence = _calculate_confidence(criteria_scores)

    flags = _make_flags(confidence)

    # -----------------------------------------
    # MappingResult 반환
    # -----------------------------------------

    return MappingResult(
        chunk_id=selected.chunk.chunk_id,
        achievement_code=selected.chunk.achievement_code,
        subject=selected.chunk.subject,
        unit_name=selected.chunk.unit_name,
        mapping_reason=mapping_reason,
        analogy=analogy,
        confidence=confidence,
        criteria_scores=criteria_scores,
        flags=flags,
        concept_name=concept.concept_name,
        inquiry_activities=selected.chunk.inquiry_activities,
    )