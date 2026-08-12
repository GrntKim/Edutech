"""app/agents/orchestrate.py 흐름 제어 테스트.

각 에이전트는 monkeypatch로 대체한다 — 판단 로직이 아니라 오케스트레이터의
결정론적 흐름(호출 순서, 조기 종료, 재시도 루프, 인자 전달)만 검증한다.
Gemini·DB 실호출 없음.
"""

import inspect

import pytest

from app.agents import orchestrate
from app.agents.concept_collect.logic import analyze_concept
from app.agents.curriculum_search.logic import CurriculumSearchError
from app.agents.curriculum_search.logic import search_curriculum as real_search_curriculum
from app.agents.mapping.logic import MappingError, map_curriculum
from app.agents.validate.logic import validate as real_validate
from app.lib.gemini import GeminiError
from app.lib.types import (
    ConceptCollectResult,
    ConceptInput,
    CurriculumChunk,
    GradeBand,
    MappingResult,
    PipelineContext,
    SearchQuery,
    SearchResult,
    StructuredConcept,
    Subject,
    ValidationResult,
)


def _make_concept_result(status="success", caution_terms=None, concept_name="분류"):
    concept = StructuredConcept(
        is_ai_concept=True,
        concept_name=concept_name,
        one_line_definition="정의",
        core_mechanism="메커니즘",
        key_operations=["연산"],
        prerequisite_ideas=["선행"],
        everyday_examples=["예시"],
        caution_terms=caution_terms or [],
    )
    query = SearchQuery(concept_name=concept_name, concept_definition="정의", target_grade=4)
    return ConceptCollectResult(
        concept=concept,
        search_query=query,
        model_version="v1",
        prompt_version="v1",
        retry_count=0,
        status=status,
    )


def _make_chunk(subject=Subject.SCIENCE):
    return CurriculumChunk(
        chunk_id="chunk-1",
        subject=subject,
        grade_band=GradeBand.G3_4,
        unit_name="단원",
        domain="영역",
        core_idea="핵심 아이디어",
        achievement_code="[4과02-01]",
        achievement_text="성취기준",
        explanation="해설",
        inquiry_activities=["활동1"],
        source_page=1,
    )


def _make_search_results(subject=Subject.SCIENCE):
    return [SearchResult(chunk=_make_chunk(subject), similarity_score=0.9, rank=1)]


def _make_mapping(subject=Subject.SCIENCE, concept_name="분류"):
    chunk = _make_chunk(subject)
    return MappingResult(
        chunk_id=chunk.chunk_id,
        achievement_code=chunk.achievement_code,
        subject=chunk.subject,
        unit_name=chunk.unit_name,
        mapping_reason="사유",
        analogy="비유",
        confidence=0.9,
        criteria_scores={},
        flags=[],
        concept_name=concept_name,
        inquiry_activities=chunk.inquiry_activities,
    )


def _fail_if_called(*args, **kwargs):
    raise AssertionError("호출되면 안 되는 스텁이 호출됨")


def _signature_shape(fn) -> list[tuple[str, str, bool]]:
    """이름/종류(positional-or-keyword vs keyword-only)/기본값 유무만 비교한다.

    타입 애너테이션까지 비교하면 monkeypatch fake(보통 애너테이션 없음)와
    항상 불일치하므로 제외한다 — 여기서 잡으려는 건 "인자 개수·이름·필수
    여부가 몰래 바뀌는 것"이지 타입 힌트 누락이 아니다.
    """
    return [
        (name, str(param.kind), param.default is not inspect.Parameter.empty)
        for name, param in inspect.signature(fn).parameters.items()
    ]


class TestAgentContractSignatures:
    """#30 — monkeypatch로 갈아끼우는 A1~D 함수들의 실제 시그니처가 바뀌면 이 테스트가
    깨져야 한다. 다른 테스트들은 fake 함수로 대체하므로 실제 시그니처가 바뀌어도
    통과해버리는 사각지대였다.
    """

    def test_collect_concept_signature(self):
        assert _signature_shape(orchestrate.collect_concept) == _signature_shape(analyze_concept)
        assert _signature_shape(analyze_concept) == [
            ("concept_input", "POSITIONAL_OR_KEYWORD", False),
            ("context", "POSITIONAL_OR_KEYWORD", False),
        ]

    def test_search_curriculum_signature(self):
        assert _signature_shape(orchestrate.search_curriculum) == _signature_shape(
            real_search_curriculum
        )
        assert _signature_shape(real_search_curriculum) == [
            ("query", "POSITIONAL_OR_KEYWORD", False),
        ]

    def test_map_concept_signature(self):
        assert _signature_shape(orchestrate.map_concept) == _signature_shape(map_curriculum)
        assert _signature_shape(map_curriculum) == [
            ("concept", "POSITIONAL_OR_KEYWORD", False),
            ("search_results", "POSITIONAL_OR_KEYWORD", False),
            ("context", "POSITIONAL_OR_KEYWORD", False),
        ]

    def test_validate_signature(self):
        assert _signature_shape(orchestrate.validate) == _signature_shape(real_validate)
        assert _signature_shape(real_validate) == [
            ("lesson_plan", "POSITIONAL_OR_KEYWORD", False),
            ("context", "POSITIONAL_OR_KEYWORD", False),
            ("subject", "KEYWORD_ONLY", False),
            ("caution_terms", "KEYWORD_ONLY", False),
            ("concept_name", "KEYWORD_ONLY", False),
        ]

    def test_generate_lesson_signature(self):
        # orchestrate.generate_lesson은 C의 실제 함수를 감싸는 자체 래퍼라
        # (LessonOutput → dict 변환), 비교 대상은 이 래퍼 자신의 선언이다.
        assert _signature_shape(orchestrate.generate_lesson) == [
            ("mapping", "POSITIONAL_OR_KEYWORD", False),
            ("context", "POSITIONAL_OR_KEYWORD", False),
            ("retry_feedback", "POSITIONAL_OR_KEYWORD", True),
        ]


def test_normal_flow_returns_pipeline_result(monkeypatch):
    """정상 흐름 — 전 단계가 순서대로 호출되고 warning 없는 PipelineResult를 반환한다."""
    calls = []
    concept_result = _make_concept_result()
    search_results = _make_search_results()
    mapping = _make_mapping()

    def fake_collect_concept(user_input, context):
        calls.append("A1")
        return concept_result

    def fake_search_curriculum(query):
        calls.append("A2")
        return search_results

    def fake_map_concept(concept, results, context):
        calls.append("B")
        return mapping

    def fake_generate_lesson(m, ctx, retry_feedback=None):
        calls.append("C")
        return {"title": "완성 교안"}

    def fake_validate(lesson_plan, ctx, *, subject, caution_terms, concept_name):
        calls.append("D")
        return ValidationResult(passed=True)

    monkeypatch.setattr(orchestrate, "collect_concept", fake_collect_concept)
    monkeypatch.setattr(orchestrate, "search_curriculum", fake_search_curriculum)
    monkeypatch.setattr(orchestrate, "map_concept", fake_map_concept)
    monkeypatch.setattr(orchestrate, "generate_lesson", fake_generate_lesson)
    monkeypatch.setattr(orchestrate, "validate", fake_validate)

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None)
    )

    assert calls == ["A1", "A2", "B", "C", "D"]
    assert result.warning is None
    assert result.lesson_plan == {"title": "완성 교안"}
    assert result.validation.passed is True


def test_a1_unsupported_concept_short_circuits(monkeypatch):
    """A1이 unsupported_concept 반환 → 이후 단계 호출 안 되고 정상 종료, 안내 문구 포함."""
    calls = []
    concept_result = _make_concept_result(status="unsupported_concept")

    def fake_collect_concept(user_input, context):
        calls.append("A1")
        return concept_result

    monkeypatch.setattr(orchestrate, "collect_concept", fake_collect_concept)
    monkeypatch.setattr(orchestrate, "search_curriculum", _fail_if_called)
    monkeypatch.setattr(orchestrate, "map_concept", _fail_if_called)
    monkeypatch.setattr(orchestrate, "generate_lesson", _fail_if_called)
    monkeypatch.setattr(orchestrate, "validate", _fail_if_called)

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="김치찌개", target_grade=4, subject_hint=None)
    )

    assert calls == ["A1"]
    assert result.warning == orchestrate.MSG_UNSUPPORTED_CONCEPT


def test_a1_ambiguous_input_short_circuits(monkeypatch):
    """A1이 ambiguous_input 반환 → 동일하게 조기 종료."""
    concept_result = _make_concept_result(status="ambiguous_input")

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", _fail_if_called)
    monkeypatch.setattr(orchestrate, "map_concept", _fail_if_called)
    monkeypatch.setattr(orchestrate, "generate_lesson", _fail_if_called)
    monkeypatch.setattr(orchestrate, "validate", _fail_if_called)

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="AI", target_grade=4, subject_hint=None)
    )

    assert result.warning == orchestrate.MSG_AMBIGUOUS_INPUT


def test_a2_empty_results_short_circuits(monkeypatch):
    """A2가 빈 리스트 반환 → B 호출 안 되고 정상 종료."""
    concept_result = _make_concept_result()

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: [])
    monkeypatch.setattr(orchestrate, "map_concept", _fail_if_called)
    monkeypatch.setattr(orchestrate, "generate_lesson", _fail_if_called)
    monkeypatch.setattr(orchestrate, "validate", _fail_if_called)

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="분류", target_grade=1, subject_hint=None)
    )

    assert result.warning == orchestrate.MSG_NO_CURRICULUM_MATCH


def test_map_concept_receives_context(monkeypatch):
    """B 호출 시 PipelineContext가 인자로 전달된다."""
    concept_result = _make_concept_result()
    search_results = _make_search_results()
    mapping = _make_mapping()
    received = {}

    def fake_map_concept(concept, results, context):
        received["context"] = context
        return mapping

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", fake_map_concept)
    monkeypatch.setattr(orchestrate, "generate_lesson", lambda m, ctx, retry_feedback=None: {"title": "t"})
    monkeypatch.setattr(
        orchestrate, "validate", lambda lp, ctx, *, subject, caution_terms, concept_name: ValidationResult(passed=True)
    )

    orchestrate.run_pipeline(ConceptInput(raw_concept_name="분류", target_grade=5, subject_hint=None))

    assert isinstance(received["context"], PipelineContext)
    assert received["context"].target_grade == 5


def test_validation_retries_once_then_passes(monkeypatch):
    """검증 1회 실패 후 통과 → C가 2번 호출되고 warning은 None."""
    concept_result = _make_concept_result()
    search_results = _make_search_results()
    mapping = _make_mapping()
    c_feedbacks = []

    first_validation = ValidationResult(passed=False, violations=["금지어"], retry_feedback="다시 생성")
    second_validation = ValidationResult(passed=True)
    validation_queue = [first_validation, second_validation]

    def fake_generate_lesson(m, ctx, retry_feedback=None):
        c_feedbacks.append(retry_feedback)
        return {"attempt": len(c_feedbacks)}

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", lambda c, r, ctx: mapping)
    monkeypatch.setattr(orchestrate, "generate_lesson", fake_generate_lesson)
    monkeypatch.setattr(
        orchestrate, "validate", lambda lp, ctx, *, subject, caution_terms, concept_name: validation_queue.pop(0)
    )

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None)
    )

    assert len(c_feedbacks) == 2
    assert c_feedbacks[0] is None
    assert c_feedbacks[1] is first_validation  # 변환 없이 그대로 재전달
    assert result.warning is None
    assert result.validation.passed is True


def test_validation_exhausts_retries(monkeypatch):
    """검증이 계속 실패 → C가 MAX_RETRIES + 1번 호출되고, 마지막 결과 + 경고 반환."""
    concept_result = _make_concept_result()
    search_results = _make_search_results()
    mapping = _make_mapping()
    call_count = {"c": 0, "d": 0}

    def fake_generate_lesson(m, ctx, retry_feedback=None):
        call_count["c"] += 1
        return {"attempt": call_count["c"]}

    def fake_validate(lp, ctx, *, subject, caution_terms, concept_name):
        call_count["d"] += 1
        return ValidationResult(passed=False, violations=["금지어"], retry_feedback="다시 생성")

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", lambda c, r, ctx: mapping)
    monkeypatch.setattr(orchestrate, "generate_lesson", fake_generate_lesson)
    monkeypatch.setattr(orchestrate, "validate", fake_validate)

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None)
    )

    assert call_count["c"] == orchestrate.MAX_RETRIES + 1
    assert call_count["d"] == orchestrate.MAX_RETRIES + 1
    assert result.warning == orchestrate.MSG_MAX_RETRIES_EXCEEDED
    assert result.lesson_plan == {"attempt": orchestrate.MAX_RETRIES + 1}


def test_validation_diverges_stops_before_max_retries(monkeypatch):
    """재시도마다 위반이 완전히 다르면 2회차에서 중단 — C가 MAX_RETRIES+1번 호출되지 않는다."""
    concept_result = _make_concept_result()
    search_results = _make_search_results()
    mapping = _make_mapping()
    call_count = {"c": 0, "d": 0}

    validation_queue = [
        ValidationResult(passed=False, violations=["만들어"], retry_feedback="a"),
        ValidationResult(passed=False, violations=["모서리"], retry_feedback="b"),  # 완전히 다른 위반
    ]

    def fake_generate_lesson(m, ctx, retry_feedback=None):
        call_count["c"] += 1
        return {"attempt": call_count["c"]}

    def fake_validate(lp, ctx, *, subject, caution_terms, concept_name):
        call_count["d"] += 1
        return validation_queue.pop(0)

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", lambda c, r, ctx: mapping)
    monkeypatch.setattr(orchestrate, "generate_lesson", fake_generate_lesson)
    monkeypatch.setattr(orchestrate, "validate", fake_validate)

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None)
    )

    assert call_count["c"] == 2  # MAX_RETRIES + 1(=4)까지 가지 않음
    assert call_count["d"] == 2
    assert result.warning == orchestrate.MSG_VALIDATION_DIVERGED
    assert result.validation.violations == ["모서리"]


def test_validation_partial_overlap_continues(monkeypatch):
    """위반이 부분적으로 겹치면(개선 중) 중단하지 않고 계속 진행한다."""
    concept_result = _make_concept_result()
    search_results = _make_search_results()
    mapping = _make_mapping()
    call_count = {"c": 0}

    validation_queue = [
        ValidationResult(passed=False, violations=["a", "b"], retry_feedback="x"),
        ValidationResult(passed=False, violations=["b", "c"], retry_feedback="y"),  # "b" 겹침
        ValidationResult(passed=True),
    ]

    def fake_generate_lesson(m, ctx, retry_feedback=None):
        call_count["c"] += 1
        return {"attempt": call_count["c"]}

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", lambda c, r, ctx: mapping)
    monkeypatch.setattr(orchestrate, "generate_lesson", fake_generate_lesson)
    monkeypatch.setattr(
        orchestrate, "validate", lambda lp, ctx, *, subject, caution_terms, concept_name: validation_queue.pop(0)
    )

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None)
    )

    assert call_count["c"] == 3  # 중단 없이 세 번째(통과)까지 진행
    assert result.warning is None
    assert result.validation.passed is True


def test_first_validation_failure_alone_does_not_trigger_divergence(monkeypatch):
    """첫 검증(previous_violations 없음)에서는 수렴 판정을 하지 않고 정상 재시도한다."""
    concept_result = _make_concept_result()
    search_results = _make_search_results()
    mapping = _make_mapping()
    call_count = {"c": 0}

    validation_queue = [
        ValidationResult(passed=False, violations=["첫위반"], retry_feedback="x"),
        ValidationResult(passed=True),
    ]

    def fake_generate_lesson(m, ctx, retry_feedback=None):
        call_count["c"] += 1
        return {"attempt": call_count["c"]}

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", lambda c, r, ctx: mapping)
    monkeypatch.setattr(orchestrate, "generate_lesson", fake_generate_lesson)
    monkeypatch.setattr(
        orchestrate, "validate", lambda lp, ctx, *, subject, caution_terms, concept_name: validation_queue.pop(0)
    )

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None)
    )

    assert call_count["c"] == 2
    assert result.warning is None
    assert result.validation.passed is True


def test_max_retries_and_diverged_warnings_differ():
    assert orchestrate.MSG_MAX_RETRIES_EXCEEDED != orchestrate.MSG_VALIDATION_DIVERGED


def test_validate_receives_caution_terms_from_a1(monkeypatch):
    """검증 호출 시 caution_terms가 A1 산출물(StructuredConcept)에서 전달된다."""
    concept_result = _make_concept_result(caution_terms=["과적합", "오버피팅"])
    search_results = _make_search_results()
    mapping = _make_mapping()
    received = {}

    def fake_validate(lp, ctx, *, subject, caution_terms, concept_name):
        received["caution_terms"] = caution_terms
        return ValidationResult(passed=True)

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", lambda c, r, ctx: mapping)
    monkeypatch.setattr(orchestrate, "generate_lesson", lambda m, ctx, retry_feedback=None: {"title": "t"})
    monkeypatch.setattr(orchestrate, "validate", fake_validate)

    orchestrate.run_pipeline(ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None))

    assert received["caution_terms"] == ["과적합", "오버피팅"]


def test_validate_receives_concept_name_from_a1(monkeypatch):
    """자기참조 금지어 제외(#50)에 쓸 concept_name은 A1이 구조화한 값이어야 한다.

    사용자 원문(raw_concept_name)이 아니라 StructuredConcept.concept_name을 넘긴다.
    """
    concept_result = _make_concept_result(concept_name="패턴 인식")
    search_results = _make_search_results()
    mapping = _make_mapping()
    received = {}

    def fake_validate(lp, ctx, *, subject, caution_terms, concept_name):
        received["concept_name"] = concept_name
        return ValidationResult(passed=True)

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", lambda c, r, ctx: mapping)
    monkeypatch.setattr(orchestrate, "generate_lesson", lambda m, ctx, retry_feedback=None: {"title": "t"})
    monkeypatch.setattr(orchestrate, "validate", fake_validate)

    orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="패턴인식 알려줘", target_grade=5, subject_hint=None)
    )

    assert received["concept_name"] == "패턴 인식"


def test_validate_receives_subject_from_mapping_not_context(monkeypatch):
    """검증 호출 시 subject는 MappingResult.subject에서 오며 context.subject_hint가 아니다."""
    concept_result = _make_concept_result()
    search_results = _make_search_results(subject=Subject.ART)
    mapping = _make_mapping(subject=Subject.ART)
    received = {}

    def fake_validate(lp, ctx, *, subject, caution_terms, concept_name):
        received["subject"] = subject
        received["context_subject_hint"] = ctx.subject_hint
        return ValidationResult(passed=True)

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", lambda c, r, ctx: mapping)
    monkeypatch.setattr(orchestrate, "generate_lesson", lambda m, ctx, retry_feedback=None: {"title": "t"})
    monkeypatch.setattr(orchestrate, "validate", fake_validate)

    orchestrate.run_pipeline(ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None))

    assert received["subject"] == Subject.ART
    assert received["context_subject_hint"] is None


def test_gemini_error_during_retry_falls_back_to_last_success(monkeypatch):
    """재시도 중 GeminiError 발생 → 즉시 중단, 마지막 성공 결과로 폴백."""
    concept_result = _make_concept_result()
    search_results = _make_search_results()
    mapping = _make_mapping()
    call_count = {"c": 0}

    def fake_generate_lesson(m, ctx, retry_feedback=None):
        call_count["c"] += 1
        if call_count["c"] == 2:
            raise GeminiError("일시적 오류")
        return {"attempt": call_count["c"]}

    def fake_validate(lp, ctx, *, subject, caution_terms, concept_name):
        return ValidationResult(passed=False, violations=["금지어"], retry_feedback="다시 생성")

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", lambda c, r, ctx: mapping)
    monkeypatch.setattr(orchestrate, "generate_lesson", fake_generate_lesson)
    monkeypatch.setattr(orchestrate, "validate", fake_validate)

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None)
    )

    assert call_count["c"] == 2
    assert result.lesson_plan == {"attempt": 1}
    assert result.warning == orchestrate.MSG_AGENT_FAILURE


def test_curriculum_search_error_does_not_escape_pipeline(monkeypatch):
    """#30 회귀 방지 — A2의 CurriculumSearchError(RuntimeError 직속)가 처리되지 않은
    채 run_pipeline() 밖으로 새어나가지 않고 MSG_AGENT_FAILURE로 반환돼야 한다.
    """
    concept_result = _make_concept_result()

    def fake_search_curriculum(query):
        raise CurriculumSearchError("지원하지 않는 학년입니다: 9")

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", fake_search_curriculum)
    monkeypatch.setattr(orchestrate, "map_concept", _fail_if_called)
    monkeypatch.setattr(orchestrate, "generate_lesson", _fail_if_called)
    monkeypatch.setattr(orchestrate, "validate", _fail_if_called)

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None)
    )

    assert result.warning == orchestrate.MSG_AGENT_FAILURE
    assert result.lesson_plan == {}


def test_mapping_error_does_not_escape_pipeline(monkeypatch):
    """#30 회귀 방지 — B의 MappingError(RuntimeError 직속)도 동일하게 처리돼야 한다."""
    concept_result = _make_concept_result()
    search_results = _make_search_results()

    def fake_map_concept(concept, results, context):
        raise MappingError("검색 결과가 없어 매핑을 수행할 수 없습니다.")

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", fake_map_concept)
    monkeypatch.setattr(orchestrate, "generate_lesson", _fail_if_called)
    monkeypatch.setattr(orchestrate, "validate", _fail_if_called)

    result = orchestrate.run_pipeline(
        ConceptInput(raw_concept_name="분류", target_grade=4, subject_hint=None)
    )

    assert result.warning == orchestrate.MSG_AGENT_FAILURE
    assert result.lesson_plan == {}


def test_pipeline_context_subject_hint_is_none(monkeypatch):
    """PipelineContext가 subject_hint=None으로 생성된다(1단계 팀 합의)."""
    concept_result = _make_concept_result()
    search_results = _make_search_results()
    mapping = _make_mapping()
    received = {}

    def fake_map_concept(concept, results, context):
        received["context"] = context
        return mapping

    monkeypatch.setattr(orchestrate, "collect_concept", lambda ui, ctx: concept_result)
    monkeypatch.setattr(orchestrate, "search_curriculum", lambda q: search_results)
    monkeypatch.setattr(orchestrate, "map_concept", fake_map_concept)
    monkeypatch.setattr(orchestrate, "generate_lesson", lambda m, ctx, retry_feedback=None: {"title": "t"})
    monkeypatch.setattr(
        orchestrate, "validate", lambda lp, ctx, *, subject, caution_terms, concept_name: ValidationResult(passed=True)
    )

    orchestrate.run_pipeline(ConceptInput(raw_concept_name="분류", target_grade=3, subject_hint=None))

    assert received["context"].subject_hint is None
