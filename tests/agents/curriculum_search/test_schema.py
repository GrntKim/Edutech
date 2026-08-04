import pytest
from pydantic import ValidationError

from agents.curriculum_search.schema import (
    GRADE_TO_BANDS,
    CurriculumChunk,
    GradeBand,
    SearchQuery,
    SearchResult,
    Subject,
)


class TestGradeToBands:
    def test_covers_every_elementary_grade(self):
        assert set(GRADE_TO_BANDS.keys()) == {1, 2, 3, 4, 5, 6}

    def test_bands_accumulate_as_grade_increases(self):
        assert GRADE_TO_BANDS[1] == (GradeBand.G1_2,)
        assert GRADE_TO_BANDS[3] == (GradeBand.G1_2, GradeBand.G3_4)
        assert GRADE_TO_BANDS[6] == (GradeBand.G1_2, GradeBand.G3_4, GradeBand.G5_6)


class TestCurriculumChunk:
    def _base_kwargs(self, **overrides):
        defaults = dict(
            chunk_id="6실05-01",
            subject=Subject.DOMESTIC_SCIENCE,
            grade_band=GradeBand.G5_6,
            unit_name="디지털 사회와 인공지능",
            domain="디지털 사회와 인공지능",
            core_idea="핵심 아이디어",
            achievement_code="[6실05-01]",
            achievement_text="알고리즘을 다양한 방법으로 표현한다.",
            explanation="",
            source_page=16,
        )
        defaults.update(overrides)
        return defaults

    def test_inquiry_activities_defaults_to_empty_list(self):
        chunk = CurriculumChunk(**self._base_kwargs())
        assert chunk.inquiry_activities == []

    def test_rejects_unknown_subject(self):
        with pytest.raises(ValidationError):
            CurriculumChunk(**self._base_kwargs(subject="ENGLISH"))

    def test_rejects_unknown_grade_band(self):
        with pytest.raises(ValidationError):
            CurriculumChunk(**self._base_kwargs(grade_band="G7_8"))

    def test_missing_required_field_raises(self):
        kwargs = self._base_kwargs()
        del kwargs["achievement_text"]
        with pytest.raises(ValidationError):
            CurriculumChunk(**kwargs)


class TestSearchQuery:
    def test_top_k_defaults_to_fifteen(self):
        query = SearchQuery(concept_name="분류", concept_definition="정의", target_grade=4)
        assert query.top_k == 15

    def test_top_k_can_be_overridden(self):
        query = SearchQuery(
            concept_name="분류", concept_definition="정의", target_grade=4, top_k=3
        )
        assert query.top_k == 3


class TestSearchResult:
    def test_reasoning_is_optional(self):
        chunk = CurriculumChunk(
            chunk_id="6실05-01",
            subject=Subject.DOMESTIC_SCIENCE,
            grade_band=GradeBand.G5_6,
            unit_name="디지털 사회와 인공지능",
            domain="디지털 사회와 인공지능",
            core_idea="핵심 아이디어",
            achievement_code="[6실05-01]",
            achievement_text="알고리즘을 다양한 방법으로 표현한다.",
            explanation="",
            source_page=16,
        )
        result = SearchResult(chunk=chunk, similarity_score=0.9, rank=1)
        assert result.reasoning is None
