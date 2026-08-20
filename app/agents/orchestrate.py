# 소유: D(REQ-005)
"""파이프라인 오케스트레이터 (REQ-005 ORCH-001/ORCH-002).

A1(개념 수집) → A2(교육과정 검색) → B(매핑) → C(교안 생성) → D(검증) 순으로
에이전트를 호출하고, 단계 간 데이터 전달과 검증 실패 시 재시도 루프를 관리한다.

Deterministic Shell, Probabilistic Core — 이 모듈은 결정론적 흐름 제어만
담당한다. 개념 해석·매핑 판정·용어 판별 같은 도메인 로직은 각 에이전트에
위임하며 여기에 넣지 않는다.

`app/main.py`(E 소유)의 라우트는 run_pipeline()만 호출한다.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from app.agents.concept_collect.logic import analyze_concept as collect_concept
from app.agents.curriculum_search.logic import CurriculumSearchError, search_curriculum
from app.agents.lesson_generate.logic import generate_lesson as _generate_lesson
from app.agents.mapping.logic import MappingError, map_curriculum as map_concept
from app.agents.validate.logic import PRINCIPLE_COUNT_VIOLATION_PREFIX, validate
from app.lib.db import DatabaseError
from app.lib.gemini import GeminiError
from app.lib.types import (
    ConceptInput,
    MappingResult,
    PipelineContext,
    PipelineResult,
    PipelineStatus,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# 각 에이전트가 던지는 예외를 한곳에 모은다. GeminiError/DatabaseError만 잡던
# 기존 튜플이 CurriculumSearchError(A2)·MappingError(B)를 놓쳐 A2/B 예외가
# 처리되지 않은 채 파이프라인 밖으로 새어나갔다(#30). A2가 GeminiError만
# 재포장하지 않고 그대로 올리는 부분 우회를 해뒀던 이유이기도 하다
# (app/agents/curriculum_search/logic.py의 관련 주석 참고) — 이제 오케스트레이터가
# 직접 잡으므로 그 우회는 더 이상 필요하지 않지만, A2 쪽 정리는 담당자 몫이라
# 여기서는 건드리지 않는다.
_AGENT_ERRORS = (GeminiError, DatabaseError, CurriculumSearchError, MappingError)

# NFR-005-2: 검증 실패 시 최대 재시도 횟수. 초과하면 마지막 결과 + 경고로 강제 반환한다.
MAX_RETRIES = 3

# 사용자 노출 문구. E가 템플릿에서 그대로 쓰거나 조정할 수 있도록 상수로 모아둔다.
MSG_UNSUPPORTED_CONCEPT = "입력하신 내용은 AI 개념으로 인식되지 않았습니다. 다른 개념을 입력해 주세요."
MSG_AMBIGUOUS_INPUT = "입력하신 개념이 너무 넓어 특정할 수 없습니다. 더 구체적인 AI 개념을 입력해 주세요."
MSG_NO_CURRICULUM_MATCH = "해당 학년에서 연결 가능한 교육과정 성취기준을 찾지 못했습니다. 다른 학년이나 개념으로 다시 시도해 주세요."
MSG_MAX_RETRIES_EXCEEDED = "검증 기준을 완전히 통과하지 못해 최선의 결과로 제공합니다. 내용을 확인한 뒤 사용해 주세요."
MSG_VALIDATION_DIVERGED = "검증 결과가 수렴하지 않아 재시도를 중단했습니다"
MSG_AGENT_FAILURE = "일시적인 오류로 교안 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."


def _elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000


# 화면에 진행 상황을 보여주기 위한 단계 전환 알림. (stage, phase, retry_count)를
# 받으며 stage는 아래 STAGES의 키, phase는 "start" 또는 "end"다.
# E(main.py)가 이 콜백으로 job 상태를 갱신하고, 화면은 그 상태를 폴링한다.
StageCallback = Callable[[str, str, int], None]

# 화면에 보여줄 단계 순서와 이름. 파이프라인 호출 순서와 일치해야 한다 —
# 여기가 어긋나면 사용자에게 실제와 다른 진행 상황을 보여주게 된다.
STAGES: tuple[tuple[str, str], ...] = (
    ("A1", "AI 개념 분석"),
    ("A2", "성취기준 검색"),
    ("B", "교육과정 매핑"),
    ("C", "교수학습과정안 작성"),
    ("D", "검증"),
)


def _notify(on_stage: StageCallback | None, stage: str, phase: str, retry_count: int = 0) -> None:
    """단계 전환을 호출부에 알린다.

    콜백은 화면 표시용 부가 기능이므로 실패해도 파이프라인을 죽이면 안 된다 —
    교안은 이미 만들어졌는데 진행 표시 코드의 버그로 결과를 잃는 상황을 막는다.
    """
    if on_stage is None:
        return
    try:
        on_stage(stage, phase, retry_count)
    except Exception:
        logger.exception(f"stage_callback_failed stage={stage} phase={phase}")


# ── 미구현 에이전트 스텁 ──────────────────────────────
# 각 담당자의 구현이 완료되면 아래 스텁을 삭제하고 실제 import로 교체한다.
#   A1: 완료 — app.agents.concept_collect.logic.analyze_concept (파일 상단 import 참고)
#   A2: 완료 — app.agents.curriculum_search.logic.search_curriculum (파일 상단 import 참고)
#   B:  완료 — app.agents.mapping.logic.map_curriculum (파일 상단 import 참고)
#   C:  완료 — app.agents.lesson_generate.logic.generate_lesson (파일 상단 import 참고)
#   D:  완료 — app.agents.validate.logic.validate (파일 상단 import 참고)
#
# 각 스텁은 타입이 맞는 더미 객체를 반환한다 — 파이프라인 전체를 실제로
# 한 번 돌려볼 수 있어야 하기 때문이다.


def generate_lesson(
    mapping: MappingResult,
    context: PipelineContext,
    retry_feedback: ValidationResult | None = None,
    caution_terms: list[str] | None = None,
) -> dict:
    """C의 generate_lesson()이 반환하는 LessonOutput(Pydantic)을 dict로 변환한다.

    LessonOutput은 C 소유(app/agents/lesson_generate/)라 공통 계층(orchestrate.py)이
    그 타입을 직접 참조하면 의존 방향이 뒤집힌다. PipelineResult.lesson_plan도 같은
    이유로 dict 계약이다 — model_dump()로 변환해서 그 계약을 지킨다.

    caution_terms는 A1의 `StructuredConcept.caution_terms`를 그대로 C에 넘긴다.
    D의 사후 검증에만 쓰던 값인데, 생성 시점에 목록을 주면 C가 애초에 그 용어를
    쓰지 않아 재시도 자체가 줄어든다(같은 개념인데 4학년 교안에만 "레이블"이
    노출된 2026-08-11 실측 건). C 쪽 기본값이 None이라 인자를 넘기지 않는
    호출부도 그대로 동작한다.
    """
    return _generate_lesson(mapping, context, retry_feedback, caution_terms).model_dump()


def _forbidden_term_violations(violations: list[str]) -> set[str]:
    """수렴 불가 판정 대상인 금지어 위반만 남긴다(원리 개수 위반은 제외).

    수렴 불가 판정은 "지적한 용어를 고치면 다른 용어가 걸리는" 금지어 특유의
    두더지잡기를 겨냥한 장치다. 원리 개수 위반은 고칠 대상이 명확하고 개수만
    맞추면 끝나므로 같은 취급을 하면 안 된다 — 섞어서 비교하면 1회차 금지어
    위반 / 2회차 개수 위반이 "완전히 다른 위반"으로 보여 고칠 수 있는 문제인데도
    조기 종료해 버린다.
    """
    return {v for v in violations if not v.startswith(PRINCIPLE_COUNT_VIOLATION_PREFIX)}


# ── 오케스트레이션 본체 ──────────────────────────────


def run_pipeline(
    user_input: ConceptInput, on_stage: StageCallback | None = None
) -> PipelineResult:
    """사용자 입력을 받아 교안 생성까지 전체 파이프라인을 실행한다.

    A1의 unsupported_concept/ambiguous_input, A2의 검색 0건은 오류가 아닌
    정상 종료로 취급하고 warning 문구를 담아 반환한다. 검증(D) 실패 시 C를
    최대 MAX_RETRIES회까지 재호출하며, 초과해도 마지막 결과는 반드시 반환한다.
    에이전트 호출 실패(GeminiError/DatabaseError)는 사용자를 무응답으로 두지
    않도록 안내 문구가 담긴 PipelineResult로 변환해 반환한다.

    모든 반환 지점은 warning 문구와 함께 PipelineStatus를 채운다 — 문구는
    사용자 안내용이고, 호출부(main.py의 레이트리밋 판정·기록)는 status로만
    분기한다.

    on_stage는 선택적 단계 전환 알림 콜백이다. 화면의 진행 표시에만 쓰이며
    파이프라인 판단에는 관여하지 않는다. 넘기지 않으면 아무 일도 하지 않는다.
    """
    pipeline_start = time.monotonic()

    # subject_hint는 1단계에서 UI에 노출하지 않는다(팀 합의 2026-08-04).
    # 생략이 아니라 명시적 None — 2단계에서 과목 선택 폼 값으로 교체하면 된다.
    context = PipelineContext(
        target_grade=user_input.target_grade,
        subject_hint=None,
    )

    try:
        # ① A1: 개념 수집
        stage_start = time.monotonic()
        _notify(on_stage, "A1", "start")
        logger.info(f"stage_start stage=A1 concept={user_input.raw_concept_name!r}")
        concept_result = collect_concept(user_input, context)
        logger.info(
            f"stage_end stage=A1 status={concept_result.status} "
            f"elapsed_ms={_elapsed_ms(stage_start):.1f}"
        )
        _notify(on_stage, "A1", "end")

        if concept_result.status != "success":
            # PipelineStatus의 문자열 값이 A1의 status와 일치하도록 정의돼 있어
            # 변환 없이 그대로 승격시킨다(문구 분기와 이중 관리하지 않는다).
            status = PipelineStatus(concept_result.status)
            message = (
                MSG_UNSUPPORTED_CONCEPT
                if status is PipelineStatus.UNSUPPORTED_CONCEPT
                else MSG_AMBIGUOUS_INPUT
            )
            logger.info(
                f"pipeline_early_exit reason={concept_result.status} "
                f"elapsed_ms={_elapsed_ms(pipeline_start):.1f}"
            )
            return PipelineResult(
                lesson_plan={},
                validation=ValidationResult(passed=False),
                status=status,
                warning=message,
            )

        # ② A2: 교육과정 검색
        stage_start = time.monotonic()
        _notify(on_stage, "A2", "start")
        logger.info("stage_start stage=A2")
        search_results = search_curriculum(concept_result.search_query)
        logger.info(
            f"stage_end stage=A2 results={len(search_results)} "
            f"elapsed_ms={_elapsed_ms(stage_start):.1f}"
        )
        _notify(on_stage, "A2", "end")

        if not search_results:
            logger.info(
                f"pipeline_early_exit reason=no_curriculum_match "
                f"elapsed_ms={_elapsed_ms(pipeline_start):.1f}"
            )
            return PipelineResult(
                lesson_plan={},
                validation=ValidationResult(passed=False),
                status=PipelineStatus.NO_CURRICULUM_MATCH,
                warning=MSG_NO_CURRICULUM_MATCH,
            )

        # ③ B: 매핑 (context 동반 전달 — 학생 이해도·비유 가능성 평가가 학년 의존적)
        stage_start = time.monotonic()
        _notify(on_stage, "B", "start")
        logger.info("stage_start stage=B")
        mapping = map_concept(concept_result.concept, search_results, context)
        logger.info(f"stage_end stage=B elapsed_ms={_elapsed_ms(stage_start):.1f}")
        _notify(on_stage, "B", "end")

        # ④ C + D: 교안 생성 · 검증 재시도 루프
        # 재시도 카운터는 반복문 지역 변수로 관리한다(ValidationResult에 상태값을 두지 않음).
        retry_count = 0
        retry_feedback: ValidationResult | None = None
        lesson_plan: dict | None = None
        validation: ValidationResult | None = None
        # 직전 회차 위반 목록. 재시도해도 완전히 다른 위반이 나오면(수렴 불가)
        # C가 해결할 수 있는 문제가 아니므로 조기 종료한다(아래 참고).
        previous_violations: list[str] | None = None

        while True:
            try:
                stage_start = time.monotonic()
                _notify(on_stage, "C", "start", retry_count)
                logger.info(f"stage_start stage=C retry_count={retry_count}")
                new_lesson_plan = generate_lesson(
                    mapping,
                    context,
                    retry_feedback,
                    # 사후 검증(D)뿐 아니라 생성 프롬프트(C)에도 매 회차 넘긴다 —
                    # 재시도 때도 목록이 빠지지 않아야 순화가 유지된다.
                    caution_terms=concept_result.concept.caution_terms,
                )
                logger.info(f"stage_end stage=C elapsed_ms={_elapsed_ms(stage_start):.1f}")
                _notify(on_stage, "C", "end", retry_count)

                stage_start = time.monotonic()
                _notify(on_stage, "D", "start", retry_count)
                logger.info(f"stage_start stage=D retry_count={retry_count}")
                new_validation = validate(
                    new_lesson_plan,
                    context,
                    subject=mapping.subject,
                    caution_terms=concept_result.concept.caution_terms,
                    # D의 자기참조 금지어 제외(#50)에 필요하다. PipelineContext에
                    # 없는 값이라 A1 결과에서 직접 꺼내 넘긴다.
                    concept_name=concept_result.concept.concept_name,
                )
                logger.info(
                    f"stage_end stage=D passed={new_validation.passed} "
                    f"elapsed_ms={_elapsed_ms(stage_start):.1f}"
                )
                _notify(on_stage, "D", "end", retry_count)
            except _AGENT_ERRORS:
                # 재시도가 오히려 장애를 키우지 않도록 즉시 중단한다. 직전 회차
                # 결과가 있으면 그걸로 폴백하고, 첫 시도부터 실패했으면 바깥
                # except로 넘겨 일반 실패 응답을 반환한다.
                logger.exception(
                    f"pipeline_retry_agent_failure retry_count={retry_count} "
                    f"elapsed_ms={_elapsed_ms(pipeline_start):.1f}"
                )
                if lesson_plan is not None and validation is not None:
                    return PipelineResult(
                        lesson_plan=lesson_plan,
                        validation=validation,
                        status=PipelineStatus.AGENT_ERROR,
                        warning=MSG_AGENT_FAILURE,
                    )
                raise

            lesson_plan, validation = new_lesson_plan, new_validation

            if validation.passed:
                break

            # 이전 위반과 완전히 다른 위반이 나오면 수렴 불가로 판단한다 — C가
            # 지적받은 용어를 고치면 다른 용어가 걸리는 상태로, C가 해결할 수
            # 있는 문제가 아니다. 첫 검증(previous_violations 없음)은 판정하지
            # 않는다. 일부만 겹치면(isdisjoint=False) 개선 중이므로 계속 진행한다.
            # 원리 개수 위반은 양쪽에서 걸러낸 뒤 비교한다(_forbidden_term_violations
            # 참고). 걸러낸 결과가 한쪽이라도 비면 비교할 대상이 없으므로 판정하지
            # 않고 재시도를 계속한다.
            current_terms = _forbidden_term_violations(validation.violations)
            previous_terms = _forbidden_term_violations(previous_violations or [])
            if previous_terms and current_terms and current_terms.isdisjoint(previous_terms):
                logger.warning(
                    f"pipeline_retry_diverged retry_count={retry_count} "
                    f"elapsed_ms={_elapsed_ms(pipeline_start):.1f}"
                )
                return PipelineResult(
                    lesson_plan=lesson_plan,
                    validation=validation,
                    status=PipelineStatus.VALIDATION_DIVERGED,
                    warning=MSG_VALIDATION_DIVERGED,
                )

            if retry_count >= MAX_RETRIES:
                logger.info(
                    f"pipeline_retry_exhausted retry_count={retry_count} "
                    f"elapsed_ms={_elapsed_ms(pipeline_start):.1f}"
                )
                return PipelineResult(
                    lesson_plan=lesson_plan,
                    validation=validation,
                    status=PipelineStatus.MAX_RETRIES_EXCEEDED,
                    warning=MSG_MAX_RETRIES_EXCEEDED,
                )

            previous_violations = validation.violations
            retry_feedback = validation
            retry_count += 1

        logger.info(f"pipeline_end elapsed_ms={_elapsed_ms(pipeline_start):.1f}")
        return PipelineResult(
            lesson_plan=lesson_plan,
            validation=validation,
            status=PipelineStatus.SUCCESS,
            warning=None,
        )

    except _AGENT_ERRORS:
        logger.exception(
            f"pipeline_agent_failure elapsed_ms={_elapsed_ms(pipeline_start):.1f}"
        )
        return PipelineResult(
            lesson_plan={},
            validation=ValidationResult(passed=False),
            status=PipelineStatus.AGENT_ERROR,
            warning=MSG_AGENT_FAILURE,
        )
