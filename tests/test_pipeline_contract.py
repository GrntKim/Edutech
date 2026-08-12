"""app/lib/types.py 데이터 계약 검증.

에이전트 구현과 무관하게 데이터 계약만 검증한다. 실패하면 타입 정의나
팀 합의 중 하나가 어긋난 것이다.

app/agents/ 하위는 import하지 않는다 — 더미 객체로 필드 흐름만 확인한다.
Gemini·DB 호출 없음, 외부 의존 없음.
"""

import pytest
from pydantic import ValidationError

from app.lib.types import (
    ConceptCollectResult,
    ConceptInput,
    CurriculumChunk,
    GradeBand,
    MappingResult,
    PipelineContext,
    PipelineResult,
    PipelineStatus,
    SearchQuery,
    SearchResult,
    StructuredConcept,
    Subject,
    ValidationResult,
    grade_to_bands,
)


@pytest.fixture
def sample_chunk():
    return CurriculumChunk(
        chunk_id="chunk-001",
        subject=Subject.SCIENCE,
        grade_band=GradeBand.G3_4,
        unit_name="동물의 생활",
        domain="생명과학",
        core_idea="동물은 특징에 따라 분류할 수 있다",
        achievement_code="[4과02-01]",
        achievement_text="동물을 특징에 따라 분류할 수 있다.",
        explanation="동물의 겉모습 특징을 관찰하여 분류 기준을 세운다.",
        inquiry_activities=["동물 사진 분류하기", "분류 기준 세워보기"],
        source_page=42,
    )


@pytest.fixture
def sample_concept():
    return StructuredConcept(
        is_ai_concept=True,
        concept_name="분류",
        one_line_definition="특징에 따라 대상을 나누는 것",
        core_mechanism="특징을 비교해 기준에 따라 그룹으로 나눈다",
        key_operations=["특징 추출", "기준 적용"],
        prerequisite_ideas=["특징 관찰"],
        everyday_examples=["빨래 분류하기"],
        caution_terms=["알고리즘"],
    )


@pytest.fixture
def sample_context():
    return PipelineContext(target_grade=4, subject_hint=None)


@pytest.fixture
def sample_mapping(sample_chunk, sample_concept):
    return MappingResult(
        chunk_id=sample_chunk.chunk_id,
        achievement_code=sample_chunk.achievement_code,
        subject=sample_chunk.subject,
        unit_name=sample_chunk.unit_name,
        mapping_reason="동물 분류 활동과 AI 분류 개념이 대응된다",
        analogy="동물을 특징별로 나누듯 AI도 데이터를 특징별로 나눈다",
        confidence=0.87,
        criteria_scores={"관련성": 0.9, "학년적합성": 0.85},
        flags=[],
        concept_name=sample_concept.concept_name,
        inquiry_activities=sample_chunk.inquiry_activities,
    )


# ── 1. 파이프라인 체인 ──────────────────────────────────────────────


def test_pipeline_chain_a1_to_a2(sample_concept, sample_context):
    """A1 출력(StructuredConcept)이 A2 입력(SearchQuery) 구성에 필요한 값을 전부 채운다."""
    query = SearchQuery(
        concept_name=sample_concept.concept_name,
        concept_definition=sample_concept.one_line_definition,
        target_grade=sample_context.target_grade,
    )
    assert query.concept_name == sample_concept.concept_name
    assert query.target_grade == sample_context.target_grade


def test_pipeline_chain_a2_output(sample_chunk):
    """A2 출력(SearchResult)이 청크를 그대로 담아 다음 단계로 넘긴다."""
    result = SearchResult(chunk=sample_chunk, similarity_score=0.91, rank=1)
    assert result.chunk.achievement_code == sample_chunk.achievement_code


def test_pipeline_chain_b_output(sample_chunk, sample_concept, sample_context, sample_mapping):
    """B가 StructuredConcept + SearchResult 목록 + PipelineContext로 MappingResult를 조립한다."""
    results = [SearchResult(chunk=sample_chunk, similarity_score=0.91, rank=1)]
    assert sample_mapping.concept_name == sample_concept.concept_name
    assert sample_mapping.chunk_id == results[0].chunk.chunk_id
    assert sample_context.target_grade == 4


def test_pipeline_chain_c_materials_from_mapping_and_context(sample_mapping, sample_context):
    """C의 LessonInput이 요구하는 6개 값이 MappingResult + PipelineContext 두 객체만으로 채워진다."""
    materials = {
        "concept_name": sample_mapping.concept_name,
        "unit_name": sample_mapping.unit_name,
        "achievement_code": sample_mapping.achievement_code,
        "analogy": sample_mapping.analogy,
        "inquiry_activities": sample_mapping.inquiry_activities,
        "target_grade": sample_context.target_grade,
    }
    assert all(v not in (None, "") for v in materials.values())


# ── 2. 팀 합의 검증 ──────────────────────────────────────────────────


def test_target_grade_not_in_mapping_result():
    """target_grade는 PipelineContext로 전역 전달 — MappingResult에는 없어야 한다."""
    assert "target_grade" not in MappingResult.model_fields


def test_inquiry_activities_passthrough(sample_chunk, sample_mapping):
    """inquiry_activities는 B가 그대로 패스스루 — 원본 청크 값과 동일해야 한다."""
    assert sample_mapping.inquiry_activities == sample_chunk.inquiry_activities


def test_concept_name_passthrough(sample_concept, sample_mapping):
    """concept_name은 A1 → B 패스스루."""
    assert sample_mapping.concept_name == sample_concept.concept_name


def test_caution_terms_not_in_mapping_result():
    """caution_terms는 D가 직접 소비 — B·C를 경유하지 않으므로 MappingResult에 없어야 한다."""
    assert "caution_terms" not in MappingResult.model_fields


def test_subject_confirmed_at_b(sample_chunk, sample_mapping):
    """subject 확정 시점은 B — 선택된 청크의 subject와 동일해야 한다."""
    assert sample_mapping.subject == sample_chunk.subject


def test_validation_result_shape():
    """ValidationResult는 C가 기대하는 형식: passed/violations/retry_feedback 3필드, 기본값 []/""."""
    vr = ValidationResult(passed=True)
    assert vr.violations == []
    assert vr.retry_feedback == ""
    assert set(ValidationResult.model_fields) == {"passed", "violations", "retry_feedback"}


def test_category_removed_from_structured_concept():
    """category 필드 제거 합의(2026-08-05) — StructuredConcept에 없어야 한다."""
    assert "category" not in StructuredConcept.model_fields


def test_concept_category_import_error():
    """ConceptCategory 자체가 삭제됐는지 확인 — import 시도 시 ImportError."""
    with pytest.raises(ImportError):
        from app.lib.types import ConceptCategory  # noqa: F401


def test_is_ai_concept_required():
    """is_ai_concept은 StructuredConcept의 필수 필드."""
    assert "is_ai_concept" in StructuredConcept.model_fields
    assert StructuredConcept.model_fields["is_ai_concept"].is_required()


# ── 3. 값 범위 제약 ──────────────────────────────────────────────────


@pytest.mark.parametrize("grade", [0, 7])
def test_concept_input_target_grade_out_of_range(grade):
    with pytest.raises(ValidationError):
        ConceptInput(raw_concept_name="분류", target_grade=grade, subject_hint=None)


def test_pipeline_context_target_grade_out_of_range():
    with pytest.raises(ValidationError):
        PipelineContext(target_grade=9, subject_hint=None)


def test_search_query_target_grade_out_of_range():
    with pytest.raises(ValidationError):
        SearchQuery(concept_name="분류", concept_definition="설명", target_grade=7)


# ── 4. 학년군 매핑 ──────────────────────────────────────────────────


def test_grade_to_bands_3_and_4_equal():
    """target_grade 3, 4는 동일 결과 — NCIC 원문 학년군제 반영, 버그 아님."""
    assert grade_to_bands(3) == grade_to_bands(4)


def test_grade_to_bands_grade1():
    assert grade_to_bands(1) == {GradeBand.G1_2}


def test_grade_to_bands_grade6():
    assert grade_to_bands(6) == {GradeBand.G1_2, GradeBand.G3_4, GradeBand.G5_6}


@pytest.mark.parametrize("grade", [0, 7])
def test_grade_to_bands_out_of_range(grade):
    with pytest.raises(ValueError):
        grade_to_bands(grade)


# ── 5. 파이프라인 종료 상태 ─────────────────────────────────────────


def test_concept_collect_result_status_literal(sample_concept):
    """status는 Literal 3값만 허용 — 그 외 값은 ValidationError."""
    query = SearchQuery(concept_name="분류", concept_definition="설명", target_grade=4)
    with pytest.raises(ValidationError):
        ConceptCollectResult(
            concept=sample_concept,
            search_query=query,
            model_version="v1",
            prompt_version="v1",
            retry_count=0,
            status="foo",
        )


def test_pipeline_result_warning_optional():
    """PipelineResult는 warning=None으로도, 경고 문구를 넣어도 생성된다."""
    vr = ValidationResult(passed=True)
    no_warning = PipelineResult(
        lesson_plan={}, validation=vr, status=PipelineStatus.SUCCESS, warning=None
    )
    with_warning = PipelineResult(
        lesson_plan={},
        validation=vr,
        status=PipelineStatus.MAX_RETRIES_EXCEEDED,
        warning="주의: 학년 범위 초과",
    )
    assert no_warning.warning is None
    assert with_warning.warning == "주의: 학년 범위 초과"


def test_pipeline_result_status_is_required():
    """status에 기본값을 두면 새 조기 종료 경로가 조용히 success로 기록된다."""
    with pytest.raises(ValidationError):
        PipelineResult(lesson_plan={}, validation=ValidationResult(passed=True))


def test_pipeline_status_values_match_concept_collect_status():
    """A1의 status 문자열을 변환 없이 PipelineStatus로 승격시킬 수 있어야 한다."""
    assert PipelineStatus("unsupported_concept") is PipelineStatus.UNSUPPORTED_CONCEPT
    assert PipelineStatus("ambiguous_input") is PipelineStatus.AMBIGUOUS_INPUT
