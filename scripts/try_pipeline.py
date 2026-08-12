"""파이프라인 통합 확인 스크립트.
run_pipeline()과 동일한 순서로 A1→A2→B→(C+D 재시도 루프)를 직접 호출한다.
run_pipeline() 자체를 호출하지 않는 이유: PipelineResult에는 최종 교안만
담기고 A2의 중간 산출물(SearchResult 목록)이 없다. 검색 품질을 눈으로
확인하는 게 이번 실행의 주 목적이라, 같은 흐름을 여기서 한 번 더 조립해
중간 결과를 전부 출력한다(로직 재구현이 아니라 orchestrate 모듈의 실제
스테이지 함수를 그대로 호출 — run_pipeline() 안 부르는 대신 Gemini/DB를
두 번 소모하지도 않는다).

재시도 루프는 run_pipeline()의 종료 조건 두 가지(최대 횟수 초과, 수렴 불가)를
모두 반영한다. 하나라도 빠지면 실제 파이프라인보다 시도 횟수가 많아져
소요 시간 측정이 어긋난다.

사전 준비:
    1. Cloud SQL Proxy 실행 (docs/infra/REQ006-DB접속안내.md)
       cloud-sql-proxy <INSTANCE_CONNECTION_NAME> --port 5432 &
    2. .env에 GEMINI_API_KEY, DB_* 값 채워둘 것

사용법:
    python scripts/try_pipeline.py ["개념명" [학년]] [--no-json]
    python scripts/try_pipeline.py "분류" 4
    python scripts/try_pipeline.py "분류" 4 --no-json   # 교안 JSON 덤프 생략
    python scripts/try_pipeline.py                       # 기본값: 분류, 4학년
"""

import json
import sys
import time
from pathlib import Path

# `python scripts/try_pipeline.py`로 직접 실행하면 Python이 scripts/만
# sys.path에 넣고 레포 루트는 안 넣는다 — app 패키지가 안 보여서 ModuleNotFoundError.
# PYTHONPATH=. 없이도 누구나 그대로 실행되도록 루트를 직접 꽂는다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import orchestrate  # noqa: E402
from app.agents.validate.logic import expected_principle_count  # noqa: E402
from app.lib.types import ConceptInput, PipelineContext  # noqa: E402


def _ms(start: float) -> str:
    return f"{(time.monotonic() - start) * 1000:.0f}ms"


def _total(start: float) -> str:
    """조기 종료·정상 종료 모두 같은 형식으로 찍는다.
    결과 파일에서 grep으로 뽑아 비교할 때 형식이 갈리면 걸린다."""
    return f"\n=== 총 소요 {_ms(start)} (목표: 60초) ==="


def _format_principles(lesson_plan: dict, target_grade: int) -> str:
    """C의 `ai_principles`를 `  ai_principles: ` 한 줄로 요약한다.

    `--no-json`으로 여러 조합을 일괄 실행한 뒤 이 줄만 grep해 비교하는 게 주
    용도라, 교안 JSON 덤프와 무관하게 항상 찍는다. 배열 길이만 찍으면 학년별
    기준값(4학년 1개/5학년 2개/6학년 3개)을 외우고 있어야 판단이 되므로 기준과
    실제를 나란히 놓고 판정("일치"/"미달"/"초과")까지 붙인다. 기준값은
    validate/logic.py의 것을 그대로 참조한다 — 여기에 따로 적으면 어긋난다.

    None(C가 필드를 못 채움)과 빈 배열은 구분해서 찍는다. 둘 다 D의 개수
    검증이 건너뛰어지는 경우인데 원인이 달라 구분이 필요하다.

    재시도 회차마다 찍는다. 개수 위반으로 재시도가 돌면 회차별로 개수가
    어떻게 움직였는지가 이 스크립트로 확인하려는 것 자체이고, 재시도는
    MAX_RETRIES로 막혀 있어 줄 수가 몇 줄 늘지 않는다.
    """
    prefix = "  ai_principles:"
    expected = expected_principle_count(target_grade)
    expected_text = f"기준 {expected}개" if expected is not None else "기준 없음"

    principles = lesson_plan.get("ai_principles")
    if principles is None:
        return f"{prefix} 없음(None) / {expected_text} — 개수 검증 건너뜀"
    if not principles:
        return f"{prefix} 0개(빈 배열) / {expected_text} — 개수 검증 건너뜀"

    actual = len(principles)
    if expected is None:
        verdict = "판정 없음"
    elif actual == expected:
        verdict = "일치"
    else:
        verdict = "미달" if actual < expected else "초과"

    # 내용까지 봐야 학년이 올라갈수록 난이도 계단이 서는지 확인할 수 있다.
    return f"{prefix} {actual}개 / {expected_text} ({verdict}) {principles}"


def _parse_args() -> tuple[str, int, bool]:
    args = [a for a in sys.argv[1:] if a != "--no-json"]
    show_json = "--no-json" not in sys.argv

    concept_name = args[0] if args else "분류"
    raw_grade = args[1] if len(args) > 1 else "4"
    try:
        target_grade = int(raw_grade)
    except ValueError:
        print(
            f"학년은 1~6 사이 정수여야 합니다. 받은 값: {raw_grade!r}\n"
            f'사용법: python scripts/try_pipeline.py "분류" 4'
        )
        sys.exit(1)
    return concept_name, target_grade, show_json


def main() -> None:
    concept_name, target_grade, show_json = _parse_args()

    user_input = ConceptInput(
        raw_concept_name=concept_name, target_grade=target_grade, subject_hint=None
    )
    context = PipelineContext(target_grade=target_grade, subject_hint=None)

    print(f"=== 개념='{concept_name}' 학년={target_grade} ===\n")
    pipeline_start = time.monotonic()

    # ── A1 ──
    t = time.monotonic()
    concept_result = orchestrate.collect_concept(user_input, context)
    print(f"[A1 개념 수집] status={concept_result.status} ({_ms(t)})")
    # 어느 프롬프트 버전으로 돌았는지 결과 파일만 보고도 알 수 있어야
    # 브랜치 간 비교가 성립한다(REQ-001 NFR-001-6 재현성).
    print(
        f"  prompt_version: {concept_result.prompt_version} / "
        f"model: {concept_result.model_version} / "
        f"retry_count: {concept_result.retry_count}"
    )
    if concept_result.status != "success":
        print(f"  -> 조기 종료 (정상 동작): {concept_result.status}")
        print(_total(pipeline_start))
        return

    concept = concept_result.concept
    print(f"  concept_name  : {concept.concept_name}")
    print(f"  is_ai_concept : {concept.is_ai_concept}")
    print(f"  definition    : {concept.one_line_definition}")
    # core_mechanism / key_operations는 A1 프롬프트 개선의 직접 대상이다.
    # core_mechanism이 교과 조작으로 환원됐는지, key_operations가 난이도순으로
    # 정렬됐는지(앞쪽일수록 저학년)를 눈으로 확인한다.
    print(f"  core_mechanism: {concept.core_mechanism}")
    print(f"  key_operations: {concept.key_operations}")
    print(f"  prerequisite  : {concept.prerequisite_ideas}")
    print(f"  everyday_ex   : {concept.everyday_examples}")
    print(f"  caution_terms : {concept.caution_terms}")
    # A1 2단계 산출물. A2가 왜 그 단원을 찾았는지 이해하려면 이 문장이 필요하고,
    # 빈 값 폴백(concept_name으로 대체)이 발동했는지도 여기서 드러난다.
    print(f"  search_query  : {concept_result.search_query.concept_definition}")

    # ── A2 ── (검색 품질 확인이 이 스크립트의 주 목적)
    t = time.monotonic()
    search_results = orchestrate.search_curriculum(concept_result.search_query)
    print(f"\n[A2 교육과정 검색] {len(search_results)}건 ({_ms(t)})")
    if not search_results:
        print("  -> 조기 종료 (정상 동작): 검색 결과 없음")
        print(_total(pipeline_start))
        return

    for r in search_results:
        print(
            f"  #{r.rank} [{r.chunk.achievement_code}] {r.chunk.unit_name} "
            f"({r.chunk.subject.value}, 유사도={r.similarity_score:.3f})"
        )
        if r.reasoning:
            print(f"       reasoning: {r.reasoning}")

    # ── B ──
    t = time.monotonic()
    mapping = orchestrate.map_concept(concept, search_results, context)
    print(
        f"\n[B 매핑] [{mapping.achievement_code}] {mapping.unit_name} "
        f"(confidence={mapping.confidence:.2f}, flags={mapping.flags}) ({_ms(t)})"
    )
    print(f"  criteria      : {mapping.criteria_scores}")
    # mapping_reason은 C 프롬프트로 전달되어 교안의 논리적 뼈대가 된다.
    print(f"  mapping_reason: {mapping.mapping_reason}")
    print(f"  analogy       : {mapping.analogy}")

    # ── C + D 재시도 루프 (run_pipeline()과 동일 로직) ──
    retry_count = 0
    retry_feedback = None
    previous_violations: list[str] = []
    lesson_plan: dict = {}
    validation = None
    stop_reason = "통과"

    while True:
        t = time.monotonic()
        lesson_plan = orchestrate.generate_lesson(
            mapping, context, retry_feedback, caution_terms=concept.caution_terms
        )
        print(f"\n[C 교안 생성] retry={retry_count} ({_ms(t)})")

        t = time.monotonic()
        validation = orchestrate.validate(
            lesson_plan,
            context,
            subject=mapping.subject,
            caution_terms=concept.caution_terms,
            concept_name=concept.concept_name,
        )
        print(f"[D 검증] passed={validation.passed} ({_ms(t)})")
        print(_format_principles(lesson_plan, target_grade))
        if not validation.passed:
            print(f"  violations: {validation.violations}")

        if validation.passed:
            break

        # 재시도 위반이 이전과 완전히 다르면 C가 해결할 수 있는 문제가 아니다
        # (ORCH-002 수렴 불가 조기 종료). 이 조건이 빠지면 실제 파이프라인보다
        # 시도 횟수가 많아져 소요 시간이 실제와 어긋난다. run_pipeline()과 똑같이
        # 원리 개수 위반은 비교에서 제외한다.
        current_terms = orchestrate._forbidden_term_violations(validation.violations)
        previous_terms = orchestrate._forbidden_term_violations(previous_violations)
        if previous_terms and current_terms and current_terms.isdisjoint(previous_terms):
            stop_reason = "수렴 불가로 재시도 중단"
            print(f"  -> {stop_reason}")
            break

        if retry_count >= orchestrate.MAX_RETRIES:
            stop_reason = "최대 재시도 횟수 초과"
            print(f"  -> {stop_reason}")
            break

        previous_violations = validation.violations
        retry_feedback = validation
        retry_count += 1

    print(f"\n[요약] 재시도 {retry_count}회 / 종료 사유: {stop_reason}")
    print(_total(pipeline_start))
    if show_json:
        print(json.dumps(lesson_plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()