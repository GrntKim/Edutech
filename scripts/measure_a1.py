import sys
import time
from app.lib.types import ConceptInput, PipelineContext
from app.agents.concept_collect.logic import analyze_concept

name = sys.argv[1] if len(sys.argv) > 1 else "분류"
grade = int(sys.argv[2]) if len(sys.argv) > 2 else 3

concept_input = ConceptInput(raw_concept_name=name, target_grade=grade, subject_hint=None)
context = PipelineContext(target_grade=grade)

start = time.perf_counter()
result = analyze_concept(concept_input, context)
elapsed = time.perf_counter() - start

print(f"입력: {name} / {grade}학년")
print(f"소요 시간: {elapsed:.2f}초")
print(f"status: {result.status}")
print(f"is_ai_concept: {result.concept.is_ai_concept}")
print(f"concept_name: {result.concept.concept_name}")
print(f"retry_count: {result.retry_count}")
print(f"쿼리({len(result.search_query.concept_definition)}자): {result.search_query.concept_definition}")