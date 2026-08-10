import asyncio
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from agents.curriculum_search import logic
from agents.curriculum_search.logic import (
    CurriculumSearchError,
    _assemble_results,
    _bigram_tokens,
    _cosine_similarities,
    _fetch_band_rows,
    _grade_band_weight,
    _normalize_chunk_id,
    _reciprocal_rank_fusion,
    _RerankMatch,
    _RerankResponse,
    _row_to_chunk,
    _sparse_scores,
    embedding_source_text,
    hybrid_search,
    resolve_grade_bands,
    search_within_chunks,
)
from agents.curriculum_search.schema import CurriculumChunk, GradeBand, SearchQuery, Subject


def _make_chunk(chunk_id="c1", grade_band=GradeBand.G3_4, **overrides):
    defaults = dict(
        chunk_id=chunk_id,
        subject=Subject.MATH,
        grade_band=grade_band,
        unit_name="단원",
        domain="영역",
        core_idea="핵심 아이디어",
        achievement_code=f"[{chunk_id}]",
        achievement_text="성취기준 텍스트",
        explanation="설명",
        inquiry_activities=[],
        source_page=1,
    )
    defaults.update(overrides)
    return CurriculumChunk(**defaults)


def _make_query(**overrides):
    defaults = dict(
        concept_name="군집화",
        concept_definition="비슷한 것끼리 묶는다",
        target_grade=4,
        top_k=15,
    )
    defaults.update(overrides)
    return SearchQuery(**defaults)


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


class TestGradeBandWeight:
    """RS-006 §9.8에서 골든셋 42행 스윕으로 확정된 가중치(1.0/0.8/0.6)를 검증한다."""

    def test_same_band_weight_is_one(self):
        assert _grade_band_weight(GradeBand.G3_4, GradeBand.G3_4) == pytest.approx(1.0)

    def test_distance_one_weight_is_zero_point_eight(self):
        assert _grade_band_weight(GradeBand.G1_2, GradeBand.G3_4) == pytest.approx(0.8)
        assert _grade_band_weight(GradeBand.G3_4, GradeBand.G5_6) == pytest.approx(0.8)

    def test_distance_two_weight_is_zero_point_six(self):
        assert _grade_band_weight(GradeBand.G1_2, GradeBand.G5_6) == pytest.approx(0.6)

    def test_chunk_band_above_query_band_raises_value_error(self):
        """chunk_band가 query_band보다 상위 학년군이면(distance<0) 호출부가 학년군
        필터링을 빠뜨렸다는 신호이므로, 조용히 fallback하지 않고 명시적으로 실패한다."""
        with pytest.raises(ValueError, match="grade_band 불변식 위반"):
            _grade_band_weight(GradeBand.G5_6, GradeBand.G1_2)
        with pytest.raises(ValueError, match="grade_band 불변식 위반"):
            _grade_band_weight(GradeBand.G3_4, GradeBand.G1_2)


class TestNormalizeChunkId:
    def test_strips_surrounding_brackets(self):
        assert _normalize_chunk_id("[6실05-04]") == "6실05-04"

    def test_leaves_unbracketed_id_unchanged(self):
        assert _normalize_chunk_id("6실05-04") == "6실05-04"

    def test_strips_surrounding_whitespace(self):
        assert _normalize_chunk_id("  6실05-04  ") == "6실05-04"


class TestAssembleResults:
    """2026-08-07 LangSmith 로그(a2-hybrid-search-acc4b991, 골든셋 42건 중 1건)에서
    확인된 회귀: LLM이 achievement_code 표기 관례(대괄호)를 따라 chunk_id에 대괄호를
    붙여 답하면, 후보 목록의 실제 chunk_id(대괄호 없음)와 매칭에 실패해 정답을 골랐음에도
    조용히 0건으로 소멸했다(2026-08-10 5회 재현 시도로 상시 재현되지는 않는 저빈도
    현상임을 확인, _assemble_results 정규화로 방어)."""

    def test_bracketed_chunk_id_is_normalized_and_matched(self):
        chunk = _make_chunk("6실05-04")
        rerank = _RerankResponse(matches=[_RerankMatch(chunk_id="[6실05-04]", reasoning="근거")])

        results = _assemble_results(rerank, [chunk], {"6실05-04": 0.9}, top_k=15)

        assert [r.chunk.chunk_id for r in results] == ["6실05-04"]
        assert results[0].rank == 1


class TestSearchWithinChunks:
    def _run(self, query, chunks, embeddings):
        return asyncio.run(search_within_chunks(query, chunks, embeddings))

    def test_empty_chunks_returns_empty_list_without_calling_embed_or_rerank(self, monkeypatch):
        embed_mock = AsyncMock()
        rerank_mock = AsyncMock()
        monkeypatch.setattr(logic, "embed_text", embed_mock)
        monkeypatch.setattr(logic, "_llm_rerank", rerank_mock)

        result = self._run(_make_query(), [], [])

        assert result == []
        embed_mock.assert_not_called()
        rerank_mock.assert_not_called()

    def test_normal_case_returns_results_matching_llm_order_and_dense_score(self, monkeypatch):
        chunks = [_make_chunk("c1"), _make_chunk("c2")]
        embeddings = [np.array([1.0, 0.0]), np.array([0.0, 1.0])]
        monkeypatch.setattr(logic, "embed_text", AsyncMock(return_value=[1.0, 0.0]))
        monkeypatch.setattr(
            logic,
            "_llm_rerank",
            AsyncMock(
                return_value=_RerankResponse(
                    matches=[
                        _RerankMatch(chunk_id="c2", reasoning="r2"),
                        _RerankMatch(chunk_id="c1", reasoning="r1"),
                    ]
                )
            ),
        )

        results = self._run(_make_query(top_k=5), chunks, embeddings)

        assert [r.chunk.chunk_id for r in results] == ["c2", "c1"]
        assert [r.rank for r in results] == [1, 2]
        assert results[0].reasoning == "r2"
        # c2=[0,1]은 질의([1,0])와 직교 -> 0.0, c1=[1,0]은 질의와 동일 -> 1.0
        assert results[0].similarity_score == pytest.approx(0.0)
        assert results[1].similarity_score == pytest.approx(1.0)

    def test_llm_matches_with_unknown_chunk_id_are_filtered_and_rank_stays_contiguous(
        self, monkeypatch
    ):
        """방금 고친 rank 버그의 회귀 테스트: 후보 목록에 없는 chunk_id는 걸러내고,
        남은 매치의 rank는 걸러낸 뒤 순서로 1부터 연속으로 매겨져야 한다."""
        chunks = [_make_chunk("c1"), _make_chunk("c2"), _make_chunk("c3")]
        embeddings = [np.array([1.0, 0.0]), np.array([0.0, 1.0]), np.array([1.0, 1.0])]
        monkeypatch.setattr(logic, "embed_text", AsyncMock(return_value=[1.0, 0.0]))
        monkeypatch.setattr(
            logic,
            "_llm_rerank",
            AsyncMock(
                return_value=_RerankResponse(
                    matches=[
                        _RerankMatch(chunk_id="nonexistent", reasoning="유령"),
                        _RerankMatch(chunk_id="c1", reasoning="r1"),
                        _RerankMatch(chunk_id="c3", reasoning="r3"),
                    ]
                )
            ),
        )

        results = self._run(_make_query(top_k=5), chunks, embeddings)

        assert [r.chunk.chunk_id for r in results] == ["c1", "c3"]
        assert [r.rank for r in results] == [1, 2]

    def test_top_k_limits_results_after_unknown_chunk_ids_are_filtered(self, monkeypatch):
        chunks = [_make_chunk(f"c{i}") for i in range(1, 4)]
        embeddings = [np.array([1.0, 0.0]) for _ in chunks]
        monkeypatch.setattr(logic, "embed_text", AsyncMock(return_value=[1.0, 0.0]))
        monkeypatch.setattr(
            logic,
            "_llm_rerank",
            AsyncMock(
                return_value=_RerankResponse(
                    matches=[
                        _RerankMatch(chunk_id="nonexistent", reasoning="유령"),
                        _RerankMatch(chunk_id="c1", reasoning="r1"),
                        _RerankMatch(chunk_id="c2", reasoning="r2"),
                        _RerankMatch(chunk_id="c3", reasoning="r3"),
                    ]
                )
            ),
        )

        results = self._run(_make_query(top_k=2), chunks, embeddings)

        assert len(results) == 2
        assert [r.chunk.chunk_id for r in results] == ["c1", "c2"]
        assert [r.rank for r in results] == [1, 2]

    def test_llm_empty_matches_returns_empty_results(self, monkeypatch):
        chunks = [_make_chunk("c1")]
        embeddings = [np.array([1.0, 0.0])]
        monkeypatch.setattr(logic, "embed_text", AsyncMock(return_value=[1.0, 0.0]))
        monkeypatch.setattr(
            logic, "_llm_rerank", AsyncMock(return_value=_RerankResponse(matches=[]))
        )

        results = self._run(_make_query(), chunks, embeddings)

        assert results == []


class TestSearchWithinChunksSync:
    """search_within_chunks(async)의 동기 버전. 시나리오는 동일하게 미러링하되
    _embed_text_sync/_llm_rerank_sync를 Mock으로 대체한다(AsyncMock 아님)."""

    def _run(self, query, chunks, embeddings):
        return logic.search_within_chunks_sync(query, chunks, embeddings)

    def test_empty_chunks_returns_empty_list_without_calling_embed_or_rerank(self, monkeypatch):
        embed_mock = Mock()
        rerank_mock = Mock()
        monkeypatch.setattr(logic, "_embed_text_sync", embed_mock)
        monkeypatch.setattr(logic, "_llm_rerank_sync", rerank_mock)

        result = self._run(_make_query(), [], [])

        assert result == []
        embed_mock.assert_not_called()
        rerank_mock.assert_not_called()

    def test_normal_case_returns_results_matching_llm_order_and_dense_score(self, monkeypatch):
        chunks = [_make_chunk("c1"), _make_chunk("c2")]
        embeddings = [np.array([1.0, 0.0]), np.array([0.0, 1.0])]
        monkeypatch.setattr(logic, "_embed_text_sync", Mock(return_value=[1.0, 0.0]))
        monkeypatch.setattr(
            logic,
            "_llm_rerank_sync",
            Mock(
                return_value=_RerankResponse(
                    matches=[
                        _RerankMatch(chunk_id="c2", reasoning="r2"),
                        _RerankMatch(chunk_id="c1", reasoning="r1"),
                    ]
                )
            ),
        )

        results = self._run(_make_query(top_k=5), chunks, embeddings)

        assert [r.chunk.chunk_id for r in results] == ["c2", "c1"]
        assert [r.rank for r in results] == [1, 2]
        assert results[0].similarity_score == pytest.approx(0.0)
        assert results[1].similarity_score == pytest.approx(1.0)

    def test_llm_matches_with_unknown_chunk_id_are_filtered_and_rank_stays_contiguous(
        self, monkeypatch
    ):
        chunks = [_make_chunk("c1"), _make_chunk("c2"), _make_chunk("c3")]
        embeddings = [np.array([1.0, 0.0]), np.array([0.0, 1.0]), np.array([1.0, 1.0])]
        monkeypatch.setattr(logic, "_embed_text_sync", Mock(return_value=[1.0, 0.0]))
        monkeypatch.setattr(
            logic,
            "_llm_rerank_sync",
            Mock(
                return_value=_RerankResponse(
                    matches=[
                        _RerankMatch(chunk_id="nonexistent", reasoning="유령"),
                        _RerankMatch(chunk_id="c1", reasoning="r1"),
                        _RerankMatch(chunk_id="c3", reasoning="r3"),
                    ]
                )
            ),
        )

        results = self._run(_make_query(top_k=5), chunks, embeddings)

        assert [r.chunk.chunk_id for r in results] == ["c1", "c3"]
        assert [r.rank for r in results] == [1, 2]

    def test_llm_empty_matches_returns_empty_results(self, monkeypatch):
        chunks = [_make_chunk("c1")]
        embeddings = [np.array([1.0, 0.0])]
        monkeypatch.setattr(logic, "_embed_text_sync", Mock(return_value=[1.0, 0.0]))
        monkeypatch.setattr(
            logic, "_llm_rerank_sync", Mock(return_value=_RerankResponse(matches=[]))
        )

        results = self._run(_make_query(), chunks, embeddings)

        assert results == []


class TestLlmRerankExceptionPropagation:
    """D 요청: GeminiError를 CurriculumSearchError로 다시 감싸지 않고 그대로 올려야
    오케스트레이터의 (GeminiError, DatabaseError) 전역 핸들러가 잡을 수 있다."""

    def test_llm_rerank_sync_propagates_gemini_error_unwrapped(self, monkeypatch):
        def _raise(prompt, schema, **kwargs):
            raise logic.GeminiError("쿼터 초과")

        monkeypatch.setattr(logic, "generate_structured", _raise)

        with pytest.raises(logic.GeminiError):
            logic._llm_rerank_sync(_make_query(), [_make_chunk("c1")], top_k=5)

    def test_llm_rerank_async_propagates_gemini_error_unwrapped(self, monkeypatch):
        def _raise(prompt, schema, **kwargs):
            raise logic.GeminiError("쿼터 초과")

        monkeypatch.setattr(logic, "generate_structured", _raise)

        with pytest.raises(logic.GeminiError):
            asyncio.run(logic._llm_rerank(_make_query(), [_make_chunk("c1")], top_k=5))


class TestSearchCurriculum:
    """동기 코어(search_curriculum). D 요청(orchestrate.py 통합 이슈)으로 추가된
    동기 진입점 — hybrid_search는 이 위에 asyncio.to_thread 래퍼일 뿐이다."""

    def test_empty_rows_returns_empty_list_without_calling_search_within_chunks_sync(
        self, monkeypatch
    ):
        monkeypatch.setattr(logic, "_fetch_band_rows", lambda grade_bands: [])
        search_mock = Mock()
        monkeypatch.setattr(logic, "search_within_chunks_sync", search_mock)

        result = logic.search_curriculum(_make_query())

        assert result == []
        search_mock.assert_not_called()

    def test_database_error_propagates_unwrapped(self, monkeypatch):
        """D 요청: CurriculumSearchError로 다시 감싸면 오케스트레이터의
        (GeminiError, DatabaseError) 전역 핸들러를 못 타므로, 원본 DatabaseError를
        그대로 올려야 한다."""

        def _raise(grade_bands):
            raise logic.DatabaseError("연결 실패")

        monkeypatch.setattr(logic, "_fetch_band_rows", _raise)

        with pytest.raises(logic.DatabaseError):
            logic.search_curriculum(_make_query())

    def test_rows_are_parsed_into_chunks_and_delegated_to_search_within_chunks_sync(
        self, monkeypatch
    ):
        row = (
            "c1",
            "MATH",
            "G3_4",
            "단원",
            "영역",
            "핵심 아이디어",
            "[c1]",
            "성취기준",
            "설명",
            [],
            1,
            [0.1, 0.2],
        )
        monkeypatch.setattr(logic, "_fetch_band_rows", lambda grade_bands: [row])

        captured = {}

        def _fake_search_within_chunks_sync(query, chunks, embeddings):
            captured["query"] = query
            captured["chunks"] = chunks
            captured["embeddings"] = embeddings
            return ["dummy-result"]

        monkeypatch.setattr(logic, "search_within_chunks_sync", _fake_search_within_chunks_sync)

        query = _make_query()
        result = logic.search_curriculum(query)

        assert result == ["dummy-result"]
        assert captured["query"] is query
        assert len(captured["chunks"]) == 1
        assert captured["chunks"][0].chunk_id == "c1"
        assert isinstance(captured["embeddings"][0], np.ndarray)


class TestHybridSearch:
    """hybrid_search는 search_curriculum을 asyncio.to_thread로 감싸는 async
    호환 래퍼일 뿐이라, 여기서는 그 위임 자체만 검증한다."""

    def test_delegates_to_search_curriculum_via_to_thread(self, monkeypatch):
        captured = {}

        def _fake_search_curriculum(query):
            captured["query"] = query
            return ["dummy-result"]

        monkeypatch.setattr(logic, "search_curriculum", _fake_search_curriculum)

        query = _make_query()
        result = asyncio.run(hybrid_search(query))

        assert result == ["dummy-result"]
        assert captured["query"] is query


class TestFetchBandRows:
    class _FakeCursor:
        def __init__(self, fetchall_result):
            self.executed: list[tuple] = []
            self._fetchall_result = fetchall_result

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            self.executed.append((query, params))

        def fetchall(self):
            return self._fetchall_result

    class _FakeConnection:
        def __init__(self, cursor):
            self._cursor = cursor

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return self._cursor

    def _patch_connection(self, monkeypatch, cursor):
        from contextlib import contextmanager

        fake_conn = self._FakeConnection(cursor)

        @contextmanager
        def fake_get_connection():
            yield fake_conn

        monkeypatch.setattr(logic, "get_connection", fake_get_connection)
        return cursor

    def test_sql_params_use_grade_band_string_values(self, monkeypatch):
        cursor = self._patch_connection(monkeypatch, self._FakeCursor(fetchall_result=[]))

        _fetch_band_rows([GradeBand.G1_2, GradeBand.G3_4])

        query, params = cursor.executed[0]
        assert params == {"grade_bands": ["G1_2", "G3_4"]}
        assert "grade_band = ANY(%(grade_bands)s)" in query

    def test_returns_fetchall_result(self, monkeypatch):
        rows = [("row1",), ("row2",)]
        cursor = self._patch_connection(monkeypatch, self._FakeCursor(fetchall_result=rows))

        result = _fetch_band_rows([GradeBand.G1_2])

        assert result == rows
