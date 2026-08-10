import sys, csv, asyncio
from app.lib.types import SearchQuery
from app.agents.curriculum_search.logic import hybrid_search

CSV_PATH = "app/agents/concept_collect/a1_queries.csv"

async def main():
    name, grade = sys.argv[1], sys.argv[2]
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    row = next(r for r in rows if r["ai_개념"] == name and r["target_grade"] == grade)
    answer = row["chunk_id"]
    q = SearchQuery(concept_name=name, concept_definition=row["a1_쿼리"],
                    target_grade=int(grade), top_k=15)
    print(f"\n{name} {grade}학년 | 정답 {answer}\n")
    for r in await hybrid_search(q):
        mark = "  <== 정답" if answer in str(r.chunk.achievement_code) else ""
        print(f"{r.rank:2}. {r.chunk.achievement_code} ({r.similarity_score:.3f}) {r.chunk.unit_name}{mark}")

asyncio.run(main())
