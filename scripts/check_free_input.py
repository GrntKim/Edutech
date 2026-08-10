"""음성 사례 판정 확인 — 9건, 결과는 화면 출력만"""
import csv
from app.lib.types import ConceptInput, PipelineContext
from app.agents.concept_collect.logic import analyze_concept

SOURCE = "data/a1_free_input_source.csv"

with open(SOURCE, encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

for r in rows:
    name, grade = r["ai_개념"], int(r["target_grade"])
    result = analyze_concept(
        ConceptInput(raw_concept_name=name, target_grade=grade, subject_hint=None),
        PipelineContext(target_grade=grade),
    )
    ok = "AI개념" if result.concept.is_ai_concept else "거부됨"
    print(f"[{r['예상']}] {name} → {result.status} / {ok}")
    if result.search_query.concept_definition:
        print(f"    쿼리: {result.search_query.concept_definition}")
