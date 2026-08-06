import sys
import asyncio
import time
from app.lib.types import ConceptInput, PipelineContext, SearchQuery
from app.agents.concept_collect.logic import analyze_concept
from app.agents.curriculum_search.logic import hybrid_search

name = sys.argv[1] if len(sys.argv) > 1 else "분류"
grade = int(sys.argv[2]) if len(sys.argv) > 2 else 4
answer = sys.argv[3] if len(sys.argv) > 3 else None


async def main():
    # A1
    start = time.perf_counter()
    result = analyze_concept(
        ConceptInput(raw_concept_name=name, target_grade=grade, subject_hint=None),
        PipelineContext(target_grade=grade),
    )
    a1_time = time.perf_counter() - start

    print(f"=== A1 ({a1_time:.2f}초, {result.status}) ===")
    print(f"쿼리: {result.search_query.concept_definition}\n")

    if result.status != "success":
        return

    # A2 — A1 생성 쿼리
    start = time.perf_counter()
    results = await hybrid_search(result.search_query)
    a2_time = time.perf_counter() - start

    print(f"=== A2 ({a2_time:.2f}초, {len(results)}건) ===")
    for r in results[:5]:
        mark = ""
        if answer and answer in str(r.chunk.achievement_code):
            mark = "  ★정답"
        print(f"{r.rank}. {r.chunk.achievement_code} ({r.similarity_score:.3f}){mark}")
        print(f"   {r.chunk.unit_name}")


asyncio.run(main())