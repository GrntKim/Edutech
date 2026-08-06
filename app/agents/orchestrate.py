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

from app.agents.concept_collect.logic import analyze_concept as collect_concept
from app.agents.curriculum_search.logic import search_curriculum
from app.agents.lesson_generate.logic import generate_lesson as _generate_lesson
from app.agents.mapping.logic import map_curriculum as map_concept
from app.lib.db import DatabaseError
from app.lib.gemini import GeminiError
from app.lib.types import (
    ConceptInput,
    MappingResult,
    PipelineContext,
    PipelineResult,
    Subject,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# NFR-005-2: 검증 실패 시 최대 재시도 횟수. 초과하면 마지막 결과 + 경고로 강제 반환한다.
MAX_RETRIES = 3

# 사용자 노출 문구. E가 템플릿에서 그대로 쓰거나 조정할 수 있도록 상수로 모아둔다.
MSG_UNSUPPORTED_CONCEPT = "입력하신 내용은 AI 개념으로 인식되지 않았습니다. 다른 개념을 입력해 주세요."
MSG_AMBIGUOUS_INPUT = "입력하신 개념이 너무 넓어 특정할 수 없습니다. 더 구체적인 AI 개념을 입력해 주세요."
MSG_NO_CURRICULUM_MATCH = "해당 학년에서 연결 가능한 교육과정 성취기준을 찾지 못했습니다. 다른 학년이나 개념으로 다시 시도해 주세요."
MSG_MAX_RETRIES_EXCEEDED = "검증 기준을 완전히 통과하지 못해 최선의 결과로 제공합니다. 내용을 확인한 뒤 사용해 주세요."
MSG_AGENT_FAILURE = "일시적인 오류로 교안 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."


def _elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000


# ── 미구현 에이전트 스텁 ──────────────────────────────
# 각 담당자의 구현이 완료되면 아래 스텁을 삭제하고 실제 import로 교체한다.
#   A1: 완료 — app.agents.concept_collect.logic.analyze_concept (파일 상단 import 참고)
#   A2: 완료 — app.agents.curriculum_search.logic.search_curriculum (파일 상단 import 참고)
#   B:  완료 — app.agents.mapping.logic.map_curriculum (파일 상단 import 참고)
#   C:  완료 — app.agents.lesson_generate.logic.generate_lesson (파일 상단 import 참고)
#   D:  from app.agents.validate.logic import validate   ← 본인 구현 예정
#
# 각 스텁은 타입이 맞는 더미 객체를 반환한다 — 파이프라인 전체를 실제로
# 한 번 돌려볼 수 있어야 하기 때문이다.


def generate_lesson(
    mapping: MappingResult,
    context: PipelineContext,
    retry_feedback: ValidationResult | None = None,
) -> dict:
    """C의 generate_lesson()이 반환하는 LessonOutput(Pydantic)을 dict로 변환한다.

    LessonOutput은 C 소유(app/agents/lesson_generate/)라 공통 계층(orchestrate.py)이
    그 타입을 직접 참조하면 의존 방향이 뒤집힌다. PipelineResult.lesson_plan도 같은
    이유로 dict 계약이다 — model_dump()로 변환해서 그 계약을 지킨다.
    """
    return _generate_lesson(mapping, context, retry_feedback).model_dump()


def validate(
    lesson_plan: dict,
    context: PipelineContext,
    *,
    subject: Subject,
    caution_terms: list[str],
) -> ValidationResult:
    """[스텁] D(본인) 미구현 — agents/validate/logic.py 작성 후 교체 예정.

    교체 대상: app.agents.validate.logic.validate(
        lesson_plan: dict, context: PipelineContext, *, subject: Subject, caution_terms: list[str]
    ) -> ValidationResult
    """
    logger.info("stub_call agent=D fn=validate")
    return ValidationResult(passed=True)


# ── 오케스트레이션 본체 ──────────────────────────────


def run_pipeline(user_input: ConceptInput) -> PipelineResult:
    """사용자 입력을 받아 교안 생성까지 전체 파이프라인을 실행한다.

    A1의 unsupported_concept/ambiguous_input, A2의 검색 0건은 오류가 아닌
    정상 종료로 취급하고 warning 문구를 담아 반환한다. 검증(D) 실패 시 C를
    최대 MAX_RETRIES회까지 재호출하며, 초과해도 마지막 결과는 반드시 반환한다.
    에이전트 호출 실패(GeminiError/DatabaseError)는 사용자를 무응답으로 두지
    않도록 안내 문구가 담긴 PipelineResult로 변환해 반환한다.
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
        logger.info(f"stage_start stage=A1 concept={user_input.raw_concept_name!r}")
        concept_result = collect_concept(user_input, context)
        logger.info(
            f"stage_end stage=A1 status={concept_result.status} "
            f"elapsed_ms={_elapsed_ms(stage_start):.1f}"
        )

        if concept_result.status != "success":
            message = (
                MSG_UNSUPPORTED_CONCEPT
                if concept_result.status == "unsupported_concept"
                else MSG_AMBIGUOUS_INPUT
            )
            logger.info(
                f"pipeline_early_exit reason={concept_result.status} "
                f"elapsed_ms={_elapsed_ms(pipeline_start):.1f}"
            )
            return PipelineResult(
                lesson_plan={},
                validation=ValidationResult(passed=False),
                warning=message,
            )

        # ② A2: 교육과정 검색
        stage_start = time.monotonic()
        logger.info("stage_start stage=A2")
        search_results = search_curriculum(concept_result.search_query)
        logger.info(
            f"stage_end stage=A2 results={len(search_results)} "
            f"elapsed_ms={_elapsed_ms(stage_start):.1f}"
        )

        if not search_results:
            logger.info(
                f"pipeline_early_exit reason=no_curriculum_match "
                f"elapsed_ms={_elapsed_ms(pipeline_start):.1f}"
            )
            return PipelineResult(
                lesson_plan={},
                validation=ValidationResult(passed=False),
                warning=MSG_NO_CURRICULUM_MATCH,
            )

        # ③ B: 매핑 (context 동반 전달 — 학생 이해도·비유 가능성 평가가 학년 의존적)
        stage_start = time.monotonic()
        logger.info("stage_start stage=B")
        mapping = map_concept(concept_result.concept, search_results, context)
        logger.info(f"stage_end stage=B elapsed_ms={_elapsed_ms(stage_start):.1f}")

        # ④ C + D: 교안 생성 · 검증 재시도 루프
        # 재시도 카운터는 반복문 지역 변수로 관리한다(ValidationResult에 상태값을 두지 않음).
        retry_count = 0
        retry_feedback: ValidationResult | None = None
        lesson_plan: dict | None = None
        validation: ValidationResult | None = None

        while True:
            try:
                stage_start = time.monotonic()
                logger.info(f"stage_start stage=C retry_count={retry_count}")
                new_lesson_plan = generate_lesson(mapping, context, retry_feedback)
                logger.info(f"stage_end stage=C elapsed_ms={_elapsed_ms(stage_start):.1f}")

                stage_start = time.monotonic()
                logger.info(f"stage_start stage=D retry_count={retry_count}")
                new_validation = validate(
                    new_lesson_plan,
                    context,
                    subject=mapping.subject,
                    caution_terms=concept_result.concept.caution_terms,
                )
                logger.info(
                    f"stage_end stage=D passed={new_validation.passed} "
                    f"elapsed_ms={_elapsed_ms(stage_start):.1f}"
                )
            except (GeminiError, DatabaseError):
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
                        warning=MSG_AGENT_FAILURE,
                    )
                raise

            lesson_plan, validation = new_lesson_plan, new_validation

            if validation.passed:
                break

            if retry_count >= MAX_RETRIES:
                logger.info(
                    f"pipeline_retry_exhausted retry_count={retry_count} "
                    f"elapsed_ms={_elapsed_ms(pipeline_start):.1f}"
                )
                return PipelineResult(
                    lesson_plan=lesson_plan,
                    validation=validation,
                    warning=MSG_MAX_RETRIES_EXCEEDED,
                )

            retry_feedback = validation
            retry_count += 1

        logger.info(f"pipeline_end elapsed_ms={_elapsed_ms(pipeline_start):.1f}")
        return PipelineResult(lesson_plan=lesson_plan, validation=validation, warning=None)

    except (GeminiError, DatabaseError):
        logger.exception(
            f"pipeline_agent_failure elapsed_ms={_elapsed_ms(pipeline_start):.1f}"
        )
        return PipelineResult(
            lesson_plan={},
            validation=ValidationResult(passed=False),
            warning=MSG_AGENT_FAILURE,
        )
