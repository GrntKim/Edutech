"""REQ-003(B) Mapping Agent 유닛 테스트.

Gemini API는 실제로 호출하지 않고 generate_structured를 가짜 함수로 대체한다.
"""

import pytest

from app.agents.mapping import logic
from app.agents.mapping.schema import CriteriaScores, MappingLLMResponse
from app.lib.gemini import GeminiSchemaError
from app.lib.types import (
    CurriculumChunk,
    GradeBand,
    PipelineContext,
    SearchResult,
    StructuredConcept,
    Subject,
)


def _make_concept(**overrides) -> StructuredConcept:
    defaults = dict(
        is_ai_concept=True,
        concept_name="분류",
        one_line_definition="여러 대상을 기준에 따라 나누는 것",
        core_mechanism="정해진 기준으로 대상을 비교해 무리를 나눈다.",
        key_operations=["기준 정하기", "비교하기"],
        prerequisite_ideas=["분류 기준"],
        everyday_examples=["빨래 분류하기"],
        caution_terms=["Feature"],
    )
    defaults.update(overrides)
    return StructuredConcept(**defaults)


def _make_chunk(chunk_id: str, **overrides) -> CurriculumChunk:
    defaults = dict(
        chunk_id=chunk_id,
        subject=Subject.MATH,
        grade_band=GradeBand.G3_4,
        unit_name="분류와 정리",
        domain="자료와 가능성",
        core_idea="기준에 따라 자료를 분류한다.",
        achievement_code=f"[{chunk_id}]",
        achievement_text="자료를 기준에 따라 분류할 수 있다.",
        explanation="설명",
        inquiry_activities=["활동1", "활동2"],
        source_page=10,
    )
    defaults.update(overrides)
    return CurriculumChunk(**defaults)


def _make_search_results(*chunk_ids: str) -> list[SearchResult]:
    return [
        SearchResult(chunk=_make_chunk(cid), similarity_score=0.9 - i * 0.1, rank=i + 1)
        for i, cid in enumerate(chunk_ids)
    ]


def _make_llm_response(chunk_id: str, **score_overrides) -> MappingLLMResponse:
    scores = dict(
        semantic_similarity=0.8,
        educational_fit=0.7,
        student_level=0.6,
        analogy=0.9,
        achievement_alignment=0.5,
    )
    scores.update(score_overrides)
    return MappingLLMResponse(
        chunk_id=chunk_id,
        mapping_reason="이 단원의 분류 활동이 AI 개념과 직접 연결되기 때문입니다.",
        analogy="마치 장난감을 색깔별로 나누는 것과 같아요.",
        criteria_scores=CriteriaScores(**scores),
    )


def _context(target_grade: int = 4) -> PipelineContext:
    return PipelineContext(target_grade=target_grade)


class TestLoadConfig:
    def test_weights_loaded_from_yaml_match_srs(self):
        assert logic.WEIGHTS == {
            "semantic_similarity": 0.25,
            "educational_fit": 0.30,
            "student_level": 0.20,
            "analogy": 0.15,
            "achievement_alignment": 0.10,
        }

    def test_thresholds_loaded_from_yaml_match_srs(self):
        assert logic.THRESHOLDS == {"normal": 0.75, "low": 0.5}

    def test_missing_weights_key_raises(self, tmp_path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("thresholds:\n  normal: 0.75\n  low: 0.5\n", encoding="utf-8")
        with pytest.raises(logic.MappingError):
            logic._load_config(bad_file)

    def test_weights_not_summing_to_one_raises(self, tmp_path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text(
            "weights:\n"
            "  semantic_similarity: 0.5\n"
            "  educational_fit: 0.5\n"
            "  student_level: 0.5\n"
            "  analogy: 0.5\n"
            "  achievement_alignment: 0.5\n"
            "thresholds:\n  normal: 0.75\n  low: 0.5\n",
            encoding="utf-8",
        )
        with pytest.raises(logic.MappingError):
            logic._load_config(bad_file)


class TestCalculateConfidence:
    def test_all_perfect_scores_yield_confidence_one(self):
        scores = {key: 1.0 for key in logic._CRITERIA_KEYS}
        assert logic.calculate_confidence(scores) == pytest.approx(1.0)

    def test_all_zero_scores_yield_confidence_zero(self):
        scores = {key: 0.0 for key in logic._CRITERIA_KEYS}
        assert logic.calculate_confidence(scores) == pytest.approx(0.0)

    def test_weighted_average_matches_manual_calculation(self):
        scores = {
            "semantic_similarity": 0.8,
            "educational_fit": 0.7,
            "student_level": 0.6,
            "analogy": 0.9,
            "achievement_alignment": 0.5,
        }
        expected = (
            0.8 * 0.25 + 0.7 * 0.30 + 0.6 * 0.20 + 0.9 * 0.15 + 0.5 * 0.10
        )
        assert logic.calculate_confidence(scores) == pytest.approx(expected)


class TestDetermineFlags:
    def test_confidence_at_or_above_normal_threshold_has_no_flags(self):
        assert logic.determine_flags(0.75) == []
        assert logic.determine_flags(0.9) == []

    def test_confidence_between_low_and_normal_is_low_confidence(self):
        assert logic.determine_flags(0.5) == ["low_confidence"]
        assert logic.determine_flags(0.74) == ["low_confidence"]

    def test_confidence_below_low_threshold_is_remap_recommended(self):
        assert logic.determine_flags(0.49) == ["remap_recommended"]
        assert logic.determine_flags(0.0) == ["remap_recommended"]


class TestMapCurriculum:
    def test_raises_when_no_search_results(self):
        with pytest.raises(logic.MappingError):
            logic.map_curriculum(_make_concept(), [], _context())

    def test_success_path_uses_llm_chosen_chunk(self, monkeypatch):
        results = _make_search_results("4수05-01", "4수05-02")
        response = _make_llm_response("4수05-02")
        monkeypatch.setattr(logic, "generate_structured", lambda *a, **kw: response)

        result = logic.map_curriculum(_make_concept(), results, _context())

        assert result.chunk_id == "4수05-02"
        assert result.mapping_reason == response.mapping_reason
        assert result.analogy == response.analogy
        assert result.criteria_scores == response.criteria_scores.model_dump()
        assert result.confidence == logic.calculate_confidence(response.criteria_scores.model_dump())
        assert result.concept_name == "분류"
        assert result.inquiry_activities == ["활동1", "활동2"]

    def test_llm_never_generates_confidence_field(self, monkeypatch):
        results = _make_search_results("4수05-01")
        response = _make_llm_response("4수05-01")
        monkeypatch.setattr(logic, "generate_structured", lambda *a, **kw: response)

        assert not hasattr(response, "confidence")
        result = logic.map_curriculum(_make_concept(), results, _context())
        assert isinstance(result.confidence, float)

    def test_chunk_id_outside_candidates_falls_back_to_first_result(self, monkeypatch):
        results = _make_search_results("4수05-01", "4수05-02")
        response = _make_llm_response("존재하지-않는-청크")
        monkeypatch.setattr(logic, "generate_structured", lambda *a, **kw: response)

        result = logic.map_curriculum(_make_concept(), results, _context())

        assert result.chunk_id == "4수05-01"
        # 후보 밖 chunk_id라도 LLM이 생성한 reasoning/점수는 그대로 재사용한다.
        assert result.mapping_reason == response.mapping_reason
        assert result.criteria_scores == response.criteria_scores.model_dump()

    def test_schema_error_retries_once_then_succeeds(self, monkeypatch):
        results = _make_search_results("4수05-01")
        response = _make_llm_response("4수05-01")
        calls = {"count": 0}

        def fake_generate(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise GeminiSchemaError("파싱 실패")
            return response

        monkeypatch.setattr(logic, "generate_structured", fake_generate)

        result = logic.map_curriculum(_make_concept(), results, _context())

        assert calls["count"] == 2
        assert result.chunk_id == "4수05-01"
        assert result.mapping_reason == response.mapping_reason

    def test_schema_error_persists_falls_back_with_zero_scores(self, monkeypatch):
        results = _make_search_results("4수05-01", "4수05-02")

        def always_fail(*args, **kwargs):
            raise GeminiSchemaError("파싱 실패")

        monkeypatch.setattr(logic, "generate_structured", always_fail)

        result = logic.map_curriculum(_make_concept(), results, _context())

        assert result.chunk_id == "4수05-01"
        assert result.criteria_scores == {key: 0.0 for key in logic._CRITERIA_KEYS}
        assert result.confidence == 0.0
        assert result.flags == ["remap_recommended"]

    def test_other_gemini_errors_are_not_swallowed(self, monkeypatch):
        from app.lib.gemini import GeminiTimeoutError
        results = _make_search_results("4수05-01")

        def raise_timeout(*args, **kwargs):
            raise GeminiTimeoutError("타임아웃")

        monkeypatch.setattr(logic, "generate_structured", raise_timeout)

        with pytest.raises(GeminiTimeoutError):
            logic.map_curriculum(_make_concept(), results, _context())
