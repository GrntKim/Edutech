"""A1 1단계 재시도 경로 주입 테스트 — Gemini 호출 0회, 쿼터 미소모"""
import logging
import app.agents.concept_collect.logic as L
from app.lib.gemini import GeminiSchemaError
from app.lib.types import ConceptInput, PipelineContext, StructuredConcept

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

CONCEPT = ConceptInput(raw_concept_name="분류", target_grade=4)
CTX = PipelineContext(target_grade=4)
CALLS = []


def _dummy_concept():
    return StructuredConcept(
        is_ai_concept=True,
        concept_name="분류",
        one_line_definition="d",
        core_mechanism="m",
        key_operations=["a"],
        prerequisite_ideas=["b"],
        everyday_examples=["c"],
        caution_terms=["e"],
    )


def _fake(fail_times):
    def inner(prompt, schema, **kw):
        CALLS.append(prompt)
        if len(CALLS) <= fail_times:
            raise GeminiSchemaError("필수 항목 key_operations 누락")
        return _dummy_concept()
    return inner


def run(name, fail_times):
    CALLS.clear()
    L.generate_structured = _fake(fail_times)
    L.generate_text = lambda p, **kw: "재작성된 쿼리"
    print(f"\n=== {name} ===")
    try:
        r = L.analyze_concept(CONCEPT, CTX)
        print("status:", r.status, "| retry_count:", r.retry_count)
        print("top_k:", r.search_query.top_k)
    except GeminiSchemaError as e:
        print("예외 올라옴(의도대로):", e)
    print("호출 횟수:", len(CALLS))
    if len(CALLS) >= 2:
        print("2차 프롬프트에 실패 이유 포함:", "직전 시도 실패" in CALLS[1])


run("정상", 0)
run("1차 실패 후 성공", 1)
run("2회 모두 실패", 2)
