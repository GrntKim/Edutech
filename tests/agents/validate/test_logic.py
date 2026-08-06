"""REQ-005(D) VALID-001 검증 에이전트 유닛 테스트.

① 금지 용어는 사람이 검수한 확정 목록(`logic._CURATED_FORBIDDEN`)이라 DB
mock이 필요 없다(2026-08-06 큐레이션 전환). ② `caution_terms`는 여전히
런타임 필터를 거치므로 그 경로만 테스트 대상이다.
"""

from app.agents.validate import logic
from app.lib.types import PipelineContext, Subject


def _make_context(target_grade: int = 3) -> PipelineContext:
    return PipelineContext(target_grade=target_grade)


def _make_lesson_plan(**overrides) -> dict:
    plan = {
        "topic": "주제",
        "learning_objectives": ["목표1"],
        "materials": [],
        "lesson_stages": {
            "intro": [
                {
                    "content_label": "전시학습 상기",
                    "time": "5분",
                    "teacher": "지난 시간 내용을 물어본다.",
                    "student": "대답한다.",
                    "tools": [],
                    "notes": [],
                }
            ],
            "development": [],
            "wrap_up": [],
        },
        "evaluation_criteria": {"high": "", "mid": "", "low": ""},
        "worksheet": None,
    }
    plan.update(overrides)
    return plan


class TestTermMatching:
    def test_short_term_excluded_by_length_filter(self):
        pool = logic._build_forbidden_term_pool(3, Subject.SCIENCE, ["특징"])
        assert "특징" not in pool
        assert logic._find_violations(["동물의 특징을 관찰한다"], pool) == []

    def test_multi_word_term_with_josa_detected(self):
        assert logic._term_matches("특징 벡터를 추출한다", "특징 벡터") is True

    def test_term_with_josa_detected_even_when_short(self):
        assert logic._term_matches("분류를 한다", "분류") is True

    def test_no_partial_substring_match(self):
        assert logic._term_matches("미분류 상태이다", "분류") is False
        assert logic._term_matches("분류학을 배운다", "분류") is False


class TestResultAssembly:
    def test_no_violations_passes(self):
        result = logic.validate(
            _make_lesson_plan(),
            _make_context(target_grade=6),  # 6학년: ① 금지어 0개
            subject=Subject.SCIENCE,
            caution_terms=[],
        )
        assert result.passed is True
        assert result.violations == []
        assert result.retry_feedback == ""

    def test_violations_fail_with_feedback(self):
        plan = _make_lesson_plan()
        plan["lesson_stages"]["intro"][0]["teacher"] = "오늘은 머신러닝을 배웁니다."
        result = logic.validate(
            plan,
            _make_context(target_grade=6),
            subject=Subject.SCIENCE,
            caution_terms=["머신러닝"],
        )
        assert result.passed is False
        assert result.violations == ["머신러닝"]
        assert result.retry_feedback != ""


class TestGradeBandCuration:
    """grade_to_bands() 연동 — 큐레이션 목록이 누적 학년군 규칙대로 합쳐지는지."""

    def test_grade4_forbidden_only_includes_g56(self):
        pool = logic._curriculum_forbidden_terms(4)
        assert "백분율" in pool  # G5_6 전용 → 4학년엔 금지어
        assert "직사각형" not in pool  # G3_4 전용 → 4학년까지 누적 범위라 금지어 아님

    def test_grade2_forbidden_includes_both_bands(self):
        pool = logic._curriculum_forbidden_terms(2)
        assert "직사각형" in pool  # G3_4
        assert "백분율" in pool  # G5_6

    def test_grade6_forbidden_is_empty(self):
        assert logic._curriculum_forbidden_terms(6) == frozenset()


class TestUnionOfSources:
    def test_curriculum_derived_violation_detected(self):
        plan = _make_lesson_plan()
        plan["lesson_stages"]["intro"][0]["teacher"] = "백분율을 계산해 봅시다."
        result = logic.validate(
            plan,
            _make_context(target_grade=4),
            subject=Subject.MATH,
            caution_terms=[],
        )
        assert result.passed is False
        assert "백분율" in result.violations

    def test_caution_terms_only_violation_detected(self):
        plan = _make_lesson_plan()
        plan["lesson_stages"]["intro"][0]["teacher"] = "딥러닝을 활용해 봅시다."
        result = logic.validate(
            plan,
            _make_context(target_grade=6),
            subject=Subject.SCIENCE,
            caution_terms=["딥러닝"],
        )
        assert result.passed is False
        assert result.violations == ["딥러닝"]

    def test_empty_caution_terms_still_validates_via_curriculum(self):
        plan = _make_lesson_plan()
        plan["lesson_stages"]["intro"][0]["teacher"] = "백분율을 계산해 봅시다."
        result = logic.validate(
            plan,
            _make_context(target_grade=4),
            subject=Subject.MATH,
            caution_terms=[],
        )
        assert result.passed is False
        assert "백분율" in result.violations


class TestFieldScope:
    def test_teacher_speech_violation_detected(self):
        plan = _make_lesson_plan()
        plan["lesson_stages"]["intro"][0]["teacher"] = "인공신경망을 설명합니다."
        result = logic.validate(
            plan,
            _make_context(target_grade=6),
            subject=Subject.SCIENCE,
            caution_terms=["인공신경망"],
        )
        assert "인공신경망" in result.violations

    def test_notes_only_term_not_detected(self):
        plan = _make_lesson_plan()
        plan["lesson_stages"]["intro"][0]["notes"] = ["교사 참고: 인공신경망 개념 주의"]
        result = logic.validate(
            plan,
            _make_context(target_grade=6),
            subject=Subject.SCIENCE,
            caution_terms=["인공신경망"],
        )
        assert result.passed is True
        assert result.violations == []


class TestPredicateAndAllowlistFilters:
    """2026-08-06 실측 오탐(만들어/명확하게/탐구하여/구별하/모서리 등) 재발 방지."""

    def test_predicate_forms_excluded_from_pool(self):
        for term in ("탐구하여", "구별하", "명확하게"):
            assert logic._is_valid_forbidden_term(term) is False

    def test_always_allowed_terms_never_detected_via_caution_terms(self):
        plan = _make_lesson_plan()
        plan["lesson_stages"]["intro"][0]["teacher"] = (
            "있는지 확인하기 위해 새로운 친구들과 모서리를 만들어 명확하게 탐구하여 봅시다."
        )
        result = logic.validate(
            plan,
            _make_context(target_grade=6),
            subject=Subject.MATH,
            caution_terms=[
                "있는지", "확인하기", "새로운", "친구들",
                "모서리", "만들어", "명확하게", "탐구하여",
            ],
        )
        assert result.passed is True
        assert result.violations == []

    def test_real_noun_term_still_detected_regression(self):
        """진짜 금지어(교과 용어)는 위 필터에 걸리지 않고 정상 검출돼야 한다."""
        plan_g2 = _make_lesson_plan()
        plan_g2["lesson_stages"]["intro"][0]["teacher"] = "직사각형을 배워봅시다."
        result_g2 = logic.validate(
            plan_g2,
            _make_context(target_grade=2),
            subject=Subject.MATH,
            caution_terms=[],
        )
        assert result_g2.passed is False
        assert "직사각형" in result_g2.violations

        plan_g4 = _make_lesson_plan()
        plan_g4["lesson_stages"]["intro"][0]["teacher"] = "백분율을 배워봅시다."
        result_g4 = logic.validate(
            plan_g4,
            _make_context(target_grade=4),
            subject=Subject.MATH,
            caution_terms=[],
        )
        assert result_g4.passed is False
        assert "백분율" in result_g4.violations
