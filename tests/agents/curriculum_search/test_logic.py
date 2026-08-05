import numpy as np
import pytest

from agents.curriculum_search.logic import (
    CurriculumSearchError,
    _bigram_tokens,
    _cosine_similarities,
    _reciprocal_rank_fusion,
    _row_to_chunk,
    _sparse_scores,
    embedding_source_text,
    resolve_grade_bands,
)
from agents.curriculum_search.schema import CurriculumChunk, GradeBand, Subject


class TestResolveGradeBands:
    def test_grade_1_maps_to_first_band_only(self):
        assert resolve_grade_bands(1) == [GradeBand.G1_2]

    def test_grade_6_accumulates_all_bands(self):
        assert resolve_grade_bands(6) == [GradeBand.G1_2, GradeBand.G3_4, GradeBand.G5_6]

    def test_unsupported_grade_raises(self):
        with pytest.raises(CurriculumSearchError):
            resolve_grade_bands(7)


class TestBigramTokens:
    def test_two_char_word_becomes_single_bigram(self):
        assert _bigram_tokens("분류") == ["분류"]

    def test_three_char_word_becomes_two_bigrams(self):
        assert _bigram_tokens("학생들") == ["학생", "생들"]

    def test_single_char_word_kept_as_is(self):
        assert _bigram_tokens("가") == ["가"]

    def test_mixed_korean_english_number_tokens(self):
        assert _bigram_tokens("AI 개념 3개") == ["AI", "개념", "3개"]

    def test_empty_string_yields_no_tokens(self):
        assert _bigram_tokens("") == []


class TestCosineSimilarities:
    def test_identical_vector_scores_one(self):
        result = _cosine_similarities([1, 0, 0], [np.array([1, 0, 0])])
        assert result[0] == pytest.approx(1.0)

    def test_orthogonal_vector_scores_zero(self):
        result = _cosine_similarities([1, 0, 0], [np.array([0, 1, 0])])
        assert result[0] == pytest.approx(0.0)

    def test_zero_embedding_does_not_raise_division_error(self):
        result = _cosine_similarities([1, 0, 0], [np.array([0, 0, 0])])
        assert result[0] == pytest.approx(0.0)

    def test_preserves_order_of_input_embeddings(self):
        result = _cosine_similarities(
            [1, 0, 0], [np.array([1, 0, 0]), np.array([0, 1, 0]), np.array([-1, 0, 0])]
        )
        assert result == [pytest.approx(1.0), pytest.approx(0.0), pytest.approx(-1.0)]


class TestReciprocalRankFusion:
    def test_item_ranked_first_in_both_lists_gets_highest_fused_score(self):
        fused = _reciprocal_rank_fusion([3, 2, 1], [3, 1, 2])
        assert fused[0] > fused[1]
        assert fused[0] > fused[2]

    def test_item_ranked_last_in_both_lists_gets_lowest_fused_score(self):
        fused = _reciprocal_rank_fusion([1, 2, 3], [1, 3, 2])
        assert fused[0] < fused[1]
        assert fused[0] < fused[2]

    def test_tied_ranks_produce_equal_fused_scores(self):
        fused = _reciprocal_rank_fusion([0.9, 0.1, 0.5], [0.2, 0.8, 0.3])
        assert fused[0] == pytest.approx(fused[1])


class TestSparseScores:
    def test_matching_document_scores_higher_than_unrelated_ones(self):
        scores = _sparse_scores(
            "삼각형의 넓이",
            [
                "삼각형의 넓이를 구하는 방법을 배운다",
                "전혀 관련 없는 문서 내용입니다",
                "오늘 날씨는 맑고 기온이 높다",
            ],
        )
        assert scores[0] > scores[1]
        assert scores[0] > scores[2]


class TestEmbeddingSourceText:
    def _make_chunk(self, **overrides):
        defaults = dict(
            chunk_id="2수03-05",
            subject=Subject.MATH,
            grade_band=GradeBand.G1_2,
            unit_name="도형과 측정",
            domain="도형",
            core_idea="핵심 아이디어",
            achievement_code="[2수03-05]",
            achievement_text="성취기준 텍스트",
            explanation="설명",
            inquiry_activities=[],
            source_page=12,
        )
        defaults.update(overrides)
        return CurriculumChunk(**defaults)

    def test_concatenates_unit_core_idea_achievement_and_explanation(self):
        chunk = self._make_chunk()
        assert (
            embedding_source_text(chunk)
            == "도형과 측정 핵심 아이디어 성취기준 텍스트 설명"
        )

    def test_strips_trailing_whitespace_when_explanation_is_empty(self):
        chunk = self._make_chunk(explanation="")
        assert embedding_source_text(chunk) == "도형과 측정 핵심 아이디어 성취기준 텍스트"


class TestRowToChunk:
    def _make_row(self, inquiry_activities=None):
        return (
            "2수03-05",
            "MATH",
            "G1_2",
            "도형과 측정",
            "도형",
            "핵심 아이디어",
            "[2수03-05]",
            "성취기준 텍스트",
            "설명",
            inquiry_activities,
            12,
            [0.1, 0.2, 0.3],
        )

    def test_converts_row_into_chunk_and_embedding(self):
        chunk, embedding = _row_to_chunk(self._make_row(["활동1", "활동2"]))
        assert chunk == CurriculumChunk(
            chunk_id="2수03-05",
            subject=Subject.MATH,
            grade_band=GradeBand.G1_2,
            unit_name="도형과 측정",
            domain="도형",
            core_idea="핵심 아이디어",
            achievement_code="[2수03-05]",
            achievement_text="성취기준 텍스트",
            explanation="설명",
            inquiry_activities=["활동1", "활동2"],
            source_page=12,
        )
        assert isinstance(embedding, np.ndarray)
        assert embedding.tolist() == pytest.approx([0.1, 0.2, 0.3])

    def test_null_inquiry_activities_becomes_empty_list(self):
        chunk, _ = _row_to_chunk(self._make_row(inquiry_activities=None))
        assert chunk.inquiry_activities == []
