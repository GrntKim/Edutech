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
        pool = logic._build_forbidden_term_pool(3, Subject.SCIENCE, ["특징"], "이미지 인식")
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
            concept_name="이미지 인식",
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
            concept_name="이미지 인식",
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

    def test_grade6_has_no_band_derived_terms(self):
        """6학년은 학년군 차집합이 비므로 전 밴드 고정 금지어만 남는다."""
        assert logic._curriculum_forbidden_terms(6) == logic._ALWAYS_FORBIDDEN


class TestUnionOfSources:
    def test_curriculum_derived_violation_detected(self):
        plan = _make_lesson_plan()
        plan["lesson_stages"]["intro"][0]["teacher"] = "백분율을 계산해 봅시다."
        result = logic.validate(
            plan,
            _make_context(target_grade=4),
            subject=Subject.MATH,
            caution_terms=[],
            concept_name="이미지 인식",
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
            concept_name="이미지 인식",
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
            concept_name="이미지 인식",
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
            concept_name="이미지 인식",
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
            concept_name="이미지 인식",
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
            concept_name="이미지 인식",
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
            concept_name="이미지 인식",
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
            concept_name="이미지 인식",
        )
        assert result_g4.passed is False
        assert "백분율" in result_g4.violations


class TestSelfReferenceFilter:
    """#50 — 개념명 자체·접두 파생어가 caution_terms에 섞여 상시 반려되던 문제."""

    def test_prefix_derivative_excluded(self):
        """'분류' 교안에서 '분류기'는 금지어가 될 수 없다(실측: 분류/5학년)."""
        pool = logic._build_forbidden_term_pool(
            5, Subject.SCIENCE, ["지도 학습", "레이블", "분류기", "특징 공간"], "분류"
        )
        assert "분류기" not in pool
        # 같은 실행의 나머지 항목은 개념명과 겹치지 않으므로 전부 금지어로 남는다.
        # "레이블"은 개념명이 "분류"일 때 자기참조가 아니므로 계속 금지 대상이다
        # (초등 교육과정 424청크에 0건 — _ALWAYS_FORBIDDEN 주석 참고).
        assert {"지도 학습", "레이블", "특징 공간"} <= pool

    def test_exact_match_excluded_with_and_without_space(self):
        """'패턴 인식' 교안에서 개념명 그대로는 물론 공백만 다른 표기도 제외한다."""
        pool = logic._build_forbidden_term_pool(
            5, Subject.SCIENCE, ["패턴 인식", "패턴인식", "특징 벡터"], "패턴 인식"
        )
        assert "패턴 인식" not in pool
        assert "패턴인식" not in pool

    def test_unrelated_technical_terms_still_forbidden(self):
        """무관한 전문 용어는 개념명이 뭐든 그대로 금지어로 남는다."""
        pool = logic._build_forbidden_term_pool(
            5, Subject.SCIENCE, ["지도 학습", "특징 공간", "템플릿 매칭"], "분류"
        )
        assert {"지도 학습", "특징 공간", "템플릿 매칭"} <= pool

    def test_multi_word_derivative_not_excluded(self):
        """접두가 겹쳐도 2음절 이상 덧붙으면 독립된 전문 용어로 보고 남긴다."""
        pool = logic._build_forbidden_term_pool(
            5, Subject.SCIENCE, ["분류 알고리즘", "분류 모델"], "분류"
        )
        assert "분류 알고리즘" in pool
        assert "분류 모델" in pool

    def test_discipline_suffix_derivative_not_excluded(self):
        """'분류학'(생물 분류학)은 1음절 차이여도 다른 학문 분야라 금지어로 남는다."""
        pool = logic._build_forbidden_term_pool(
            4, Subject.SCIENCE, ["분류학", "분류론", "지도 학습"], "분류"
        )
        assert {"분류학", "분류론", "지도 학습"} <= pool

    def test_discipline_suffix_concept_still_frees_its_stem(self):
        """반대 방향은 그대로 제외한다 — '분류학'을 가르치며 '분류'를 안 쓸 수 없다."""
        assert logic._is_self_reference("분류", "분류학") is True

    def test_compound_judgement_ignores_spacing(self):
        """공백 유무로 결과가 갈리면 안 된다 — 정규화가 공백을 지우기 때문.

        어절 수로 복합어를 가려내면 "분류 모델"은 남고 "분류모델"만 빠져나가
        같은 말이 표기에 따라 다르게 판정된다.
        """
        pool = logic._build_forbidden_term_pool(
            5, Subject.SCIENCE, ["분류 모델", "분류모델"], "분류"
        )
        assert {"분류 모델", "분류모델"} <= pool

    def test_same_word_count_derivative_excluded(self):
        """어절 수가 같은 파생어는 제외한다("패턴 인식" → "패턴 인식기")."""
        pool = logic._build_forbidden_term_pool(
            5, Subject.SCIENCE, ["패턴 인식기", "패턴인식기"], "패턴 인식"
        )
        assert pool.isdisjoint({"패턴 인식기", "패턴인식기"})

    def test_short_concept_name_disables_filter(self):
        """1음절 개념명으로 접두 매칭하면 무관한 용어가 대량으로 빠져나간다."""
        pool = logic._build_forbidden_term_pool(5, Subject.SCIENCE, ["수학적 귀납"], "수")
        assert "수학적 귀납" in pool

    def test_self_reference_term_not_reported_as_violation(self):
        """풀에서 빠졌으므로 교안 본문에 등장해도 위반이 아니다."""
        plan = _make_lesson_plan()
        plan["lesson_stages"]["intro"][0]["teacher"] = "오늘은 패턴 인식을 배웁니다."
        result = logic.validate(
            plan,
            _make_context(target_grade=5),
            subject=Subject.SCIENCE,
            caution_terms=["패턴 인식", "특징 벡터"],
            concept_name="패턴 인식",
        )
        assert result.passed is True
        assert result.violations == []


class TestAlwaysForbiddenTerms:
    """학년군과 무관하게 전 밴드 금지("레이블"/"라벨", 교육과정 424청크 0건)."""

    def test_label_detected_at_highest_grade(self):
        """6학년은 학년군 차집합이 비어도 이 용어만은 잡혀야 한다."""
        plan = _make_lesson_plan()
        plan["learning_objectives"] = ["정답 레이블 없이 비슷한 것끼리 묶을 수 있다."]
        result = logic.validate(
            plan,
            _make_context(target_grade=6),
            subject=Subject.SCIENCE,
            caution_terms=[],
            concept_name="군집화",
        )
        assert result.passed is False
        assert "레이블" in result.violations

    def test_always_forbidden_yields_to_self_reference(self):
        """개념명이 고정 금지어 자체면 #50과 같은 교착이 되므로 이때만 풀어준다."""
        pool = logic._build_forbidden_term_pool(4, Subject.SCIENCE, [], "레이블")
        assert "레이블" not in pool
        assert "라벨" in pool  # 개념명과 무관한 쪽은 그대로 금지

    def test_always_forbidden_yields_to_longer_concept_name(self):
        """개념명이 금지어보다 긴 파생어여도 교착이다 — "레이블링" 수업의 "레이블"."""
        pool = logic._build_forbidden_term_pool(4, Subject.SCIENCE, [], "레이블링")
        assert "레이블" not in pool
        assert "라벨" in pool

    def test_curated_term_yields_to_self_reference(self):
        """①에도 "프로그래밍"처럼 개념명으로 입력될 수 있는 용어가 있다.

        3학년은 G5_6 목록 전체가 금지어라 "프로그래밍"이 포함되는데, 그
        개념을 가르치는 교안에서 그 단어를 뺄 수는 없다(#50과 같은 교착).
        """
        pool = logic._build_forbidden_term_pool(3, Subject.SCIENCE, [], "프로그래밍")
        assert "프로그래밍" not in pool
        assert "백분율" in pool  # 같은 학년군의 무관한 용어는 그대로 금지

    def test_curated_term_kept_for_unrelated_concept(self):
        pool = logic._build_forbidden_term_pool(3, Subject.SCIENCE, [], "군집화")
        assert "프로그래밍" in pool

    def test_always_forbidden_kept_for_unrelated_concept(self):
        """개념명과 글자가 겹치지 않으면 학년과 무관하게 계속 금지어다."""
        pool = logic._build_forbidden_term_pool(4, Subject.SCIENCE, [], "군집화")
        assert {"레이블", "라벨"} <= pool

    def test_label_variant_detected_at_low_grade(self):
        plan = _make_lesson_plan()
        plan["lesson_stages"]["intro"][0]["student"] = "라벨을 붙여 봅니다."
        result = logic.validate(
            plan,
            _make_context(target_grade=4),
            subject=Subject.SCIENCE,
            caution_terms=[],
            concept_name="군집화",
        )
        assert result.passed is False
        assert "라벨" in result.violations


class TestPrincipleCountValidation:
    """학년별 AI 원리 개수 검증 — 금지어 매칭이 아니라 배열 길이 비교다.

    기준값은 4학년 1개 / 5학년 2개 / 6학년 3개이고, 1~3학년은 잠정 1개다
    (logic._PRINCIPLE_COUNT_BY_GRADE).
    """

    def _validate(self, target_grade, ai_principles, **kwargs):
        plan = _make_lesson_plan(ai_principles=ai_principles)
        return logic.validate(
            plan,
            _make_context(target_grade=target_grade),
            subject=Subject.SCIENCE,
            caution_terms=kwargs.get("caution_terms", []),
            concept_name=kwargs.get("concept_name", "군집화"),
        )

    def test_grade6_with_three_principles_passes(self):
        result = self._validate(6, ["원리1", "원리2", "원리3"])
        assert result.passed is True
        assert result.violations == []

    def test_grade6_with_two_principles_violates(self):
        """2026-08-11 실측 사례(군집화 6학년, 2개)."""
        result = self._validate(6, ["원리1", "원리2"])
        assert result.passed is False
        assert result.violations == [f"{logic.PRINCIPLE_COUNT_VIOLATION_PREFIX}: 3개 필요, 2개 생성"]
        assert "3개" in result.retry_feedback

    def test_grade5_boundary(self):
        assert self._validate(5, ["원리1", "원리2"]).passed is True
        assert self._validate(5, ["원리1"]).passed is False
        assert self._validate(5, ["원리1", "원리2", "원리3"]).passed is False

    def test_grade4_boundary(self):
        assert self._validate(4, ["원리1"]).passed is True
        assert self._validate(4, ["원리1", "원리2"]).passed is False

    def test_lower_grades_expect_one(self):
        for grade in (1, 2, 3):
            assert self._validate(grade, ["원리1"]).passed is True
            assert self._validate(grade, ["원리1", "원리2"]).passed is False

    def test_over_count_is_violation(self):
        """초과도 위반이다 — 학년별 난이도 차등이 무너진 경우이기 때문."""
        result = self._validate(4, ["원리1", "원리2", "원리3"])
        assert result.passed is False
        assert result.violations == [f"{logic.PRINCIPLE_COUNT_VIOLATION_PREFIX}: 1개 필요, 3개 생성"]

    def test_none_skips_validation(self):
        """C가 못 채웠거나 기존 히스토리 데이터면 이 검증 때문에 반려하지 않는다."""
        result = self._validate(6, None)
        assert result.passed is True
        assert result.violations == []

    def test_empty_list_skips_validation(self):
        result = self._validate(6, [])
        assert result.passed is True

    def test_missing_key_skips_validation(self):
        """`ai_principles` 키 자체가 없는 dict(기존 교안)도 통과한다."""
        result = logic.validate(
            _make_lesson_plan(),
            _make_context(target_grade=6),
            subject=Subject.SCIENCE,
            caution_terms=[],
            concept_name="군집화",
        )
        assert result.passed is True

    def test_skipping_count_does_not_disable_term_check(self):
        """개수 검증을 건너뛰어도 금지어 검증은 정상 동작한다."""
        plan = _make_lesson_plan(ai_principles=None)
        plan["learning_objectives"] = ["정답 레이블 없이 묶을 수 있다."]
        result = logic.validate(
            plan,
            _make_context(target_grade=6),
            subject=Subject.SCIENCE,
            caution_terms=[],
            concept_name="군집화",
        )
        assert result.passed is False
        assert result.violations == ["레이블"]

    def test_both_violations_reported_together(self):
        plan = _make_lesson_plan(ai_principles=["원리1", "원리2"])
        plan["learning_objectives"] = ["정답 레이블 없이 묶을 수 있다."]
        result = logic.validate(
            plan,
            _make_context(target_grade=6),
            subject=Subject.SCIENCE,
            caution_terms=[],
            concept_name="군집화",
        )
        assert result.passed is False
        assert "레이블" in result.violations
        assert f"{logic.PRINCIPLE_COUNT_VIOLATION_PREFIX}: 3개 필요, 2개 생성" in result.violations
        # 피드백은 두 종류를 문단으로 나눠 담는다 — 고쳐야 할 것이 서로 다르다.
        assert "'레이블'" in result.retry_feedback
        assert "ai_principles" in result.retry_feedback

    def test_violation_label_is_stable_across_retries(self):
        """접두사가 고정이라 오케스트레이터가 개수 위반을 식별할 수 있다."""
        for count in (1, 2, 4):
            result = self._validate(6, ["원리"] * count)
            assert result.violations[0].startswith(logic.PRINCIPLE_COUNT_VIOLATION_PREFIX)
