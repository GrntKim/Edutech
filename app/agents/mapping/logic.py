"""
REQ-003(B) Mapping Agent

Business Logic

A1이 생성한 StructuredConcept와 A2가 검색한 SearchResult[] 후보를 입력받아
가장 적합한 교육과정 단원 1개를 선택한다.

LLM은 mapping_reason/analogy/criteria_scores만 생성한다. confidence는 LLM이
절대 생성하지 않고, 서버(이 모듈)가 criteria_scores와 mapping_weights.yaml의
가중치로 계산한다.
"""

import logging
from pathlib import Path

import yaml

from lib.gemini import GeminiSchemaError, generate_structured
from lib.types import MappingResult, PipelineContext, SearchResult, StructuredConcept

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .schema import MappingLLMResponse

logger = logging.getLogger(__name__)

# Gemini 호출 파라미터. 모델은 lib/gemini.py의 DEFAULT_MODEL(gemini-3.6-flash)을
# 그대로 쓰므로 여기서는 넘기지 않는다(팀 규약 — Gemini 호출은 generate_structured()
# 경유, 이 모듈에서 google.genai 직접 호출 금지).
MAPPING_THINKING_LEVEL = "low"
MAPPING_PROMPT_VERSION = "mapping_v3"

# config/mapping_weights.yaml 경로. __file__ 기준 상대 경로로 계산해 cwd에 의존하지 않는다.
# app/agents/mapping/logic.py -> parents[3] == 레포 루트.
_WEIGHTS_PATH = Path(__file__).resolve().parents[3] / "config" / "mapping_weights.yaml"

# criteria_scores/가중치 5개 항목 이름. schema.CriteriaScores 필드와 반드시 일치해야 한다.
_CRITERIA_KEYS = (
    "semantic_similarity",
    "educational_fit",
    "student_level",
    "analogy",
    "achievement_alignment",
)


class MappingError(RuntimeError):
    """매핑 에이전트 처리 중 발생하는 오류(설정 오류, 후보 없음 등)."""


def _load_config(path: Path) -> tuple[dict[str, float], dict[str, float]]:
    """mapping_weights.yaml을 읽어 (weights, thresholds)를 반환한다."""
    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    weights = (config or {}).get("weights")
    if not weights:
        raise MappingError(f"{path}에 'weights' 설정이 없습니다.")

    missing_weights = set(_CRITERIA_KEYS) - set(weights)
    if missing_weights:
        raise MappingError(f"mapping_weights.yaml에 가중치가 누락되었습니다: {sorted(missing_weights)}")

    weight_sum = sum(weights[key] for key in _CRITERIA_KEYS)
    if abs(weight_sum - 1.0) > 1e-6:
        raise MappingError(f"mapping_weights.yaml 가중치 합이 1.0이 아닙니다: {weight_sum}")

    thresholds = (config or {}).get("thresholds")
    if not thresholds:
        raise MappingError(f"{path}에 'thresholds' 설정이 없습니다.")

    missing_thresholds = {"normal", "low"} - set(thresholds)
    if missing_thresholds:
        raise MappingError(f"mapping_weights.yaml에 임계값이 누락되었습니다: {sorted(missing_thresholds)}")

    return weights, thresholds


WEIGHTS, THRESHOLDS = _load_config(_WEIGHTS_PATH)


def calculate_confidence(criteria_scores: dict[str, float]) -> float:
    """criteria_scores(LLM 생성)와 WEIGHTS(mapping_weights.yaml)로 confidence를 계산한다.

    SRS 규칙: confidence는 LLM이 생성하지 않고 서버에서만 계산한다.
    """
    confidence = sum(criteria_scores[key] * WEIGHTS[key] for key in _CRITERIA_KEYS)
    return round(confidence, 4)


def determine_flags(confidence: float) -> list[str]:
    """confidence 값을 SRS 임계값에 따라 flags로 변환한다.

    confidence >= 0.75: 정상(빈 리스트)
    0.5 <= confidence < 0.75: low_confidence
    confidence < 0.5: remap_recommended
    """
    if confidence < THRESHOLDS["low"]:
        return ["remap_recommended"]
    if confidence < THRESHOLDS["normal"]:
        return ["low_confidence"]
    return []


def _fallback_criteria_scores() -> dict[str, float]:
    """LLM 응답 자체가 없는 완전 실패(GeminiSchemaError 재생성까지 실패) 시 사용하는
    보수적 기본값. 모두 0.0으로 두어 confidence가 0이 되고 remap_recommended가
    자동으로 붙게 한다."""
    return {key: 0.0 for key in _CRITERIA_KEYS}


def _generate_mapping_response(prompt: str) -> MappingLLMResponse | None:
    """Gemini를 호출해 MappingLLMResponse를 받는다.

    GeminiSchemaError(응답을 스키마로 파싱하지 못함)가 발생하면 B가 직접 1회
    재생성을 시도한다. 그래도 실패하면 None을 반환해 호출자가 Fallback하도록 한다.
    API 레벨 실패(타임아웃/429/5xx) 재시도는 generate_structured()가 이미 수행하므로
    여기서는 재시도하지 않는다.
    """
    try:
        return generate_structured(
            prompt,
            MappingLLMResponse,
            thinking_level=MAPPING_THINKING_LEVEL,
            prompt_version=MAPPING_PROMPT_VERSION,
        )
    except GeminiSchemaError as exc:
        logger.warning(f"mapping_schema_error_retry error={exc}")
        try:
            return generate_structured(
                prompt,
                MappingLLMResponse,
                thinking_level=MAPPING_THINKING_LEVEL,
                prompt_version=MAPPING_PROMPT_VERSION,
            )
        except GeminiSchemaError as exc2:
            logger.warning(f"mapping_schema_error_fallback error={exc2}")
            return None


def map_curriculum(
    concept: StructuredConcept,
    search_results: list[SearchResult],
    context: PipelineContext,
) -> MappingResult:
    """StructuredConcept와 SearchResult[] 후보 중 가장 적합한 단원 1개를 선택한다.

    LLM은 새로운 검색을 하지 않고 후보 목록 안에서만 chunk_id를 고른다. LLM이
    후보 밖 chunk_id를 반환하거나 재생성까지 실패하면 SearchResult[0]으로
    Fallback한다.

    Args:
        concept: A1이 생성한 구조화된 AI 개념.
        search_results: A2가 검색한 교육과정 후보 목록(비어 있으면 안 됨).
        context: 대상 학년 등 파이프라인 공통 컨텍스트.

    Returns:
        선택된 단원과 confidence/flags가 채워진 MappingResult.

    Raises:
        MappingError: search_results가 비어 있어 매핑할 후보가 없는 경우.
    """
    if not search_results:
        raise MappingError("검색 결과가 없어 매핑을 수행할 수 없습니다.")

    candidate_ids = {result.chunk.chunk_id for result in search_results}
    prompt = f"{SYSTEM_PROMPT}\n\n{build_user_prompt(concept, search_results, context.target_grade)}"

    llm_response = _generate_mapping_response(prompt)
    chosen_in_candidates = llm_response is not None and llm_response.chunk_id in candidate_ids

    if chosen_in_candidates:
        chosen = next(r for r in search_results if r.chunk.chunk_id == llm_response.chunk_id)
        mapping_reason = llm_response.mapping_reason
        analogy = llm_response.analogy
        criteria_scores = llm_response.criteria_scores.model_dump()
    else:
        chosen = search_results[0]
        if llm_response is not None:
            logger.warning(
                f"mapping_chunk_id_not_in_candidates concept={concept.concept_name} "
                f"chunk_id={llm_response.chunk_id}"
            )
            mapping_reason = llm_response.mapping_reason
            analogy = llm_response.analogy
            criteria_scores = llm_response.criteria_scores.model_dump()
        else:
            mapping_reason = (
                "Gemini 응답 생성에 실패하여(재생성 1회 포함) 검색 결과 1순위로 자동 매핑되었습니다."
            )
            analogy = "자동 Fallback으로 인해 비유를 생성하지 못했습니다."
            criteria_scores = _fallback_criteria_scores()

    confidence = calculate_confidence(criteria_scores)
    flags = determine_flags(confidence)

    logger.info(
        f"mapping_result concept={concept.concept_name} chunk_id={chosen.chunk.chunk_id} "
        f"confidence={confidence} flags={flags}"
    )

    return MappingResult(
        chunk_id=chosen.chunk.chunk_id,
        achievement_code=chosen.chunk.achievement_code,
        subject=chosen.chunk.subject,
        unit_name=chosen.chunk.unit_name,
        mapping_reason=mapping_reason,
        analogy=analogy,
        confidence=confidence,
        criteria_scores=criteria_scores,
        flags=flags,
        concept_name=concept.concept_name,
        inquiry_activities=chosen.chunk.inquiry_activities,
    )
