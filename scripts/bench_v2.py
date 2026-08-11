"""a1_queries_v2.csv 전체를 원본 정의와 비교한다. 비결정성 해소로 1회 측정."""
import csv, asyncio
from app.lib.types import SearchQuery
from app.agents.curriculum_search.logic import hybrid_search

CSV_PATH = "app/agents/concept_collect/a1_queries_v2.csv"
MISS = 99

def rank_of(results, answer):
    for r in results:
        if answer in str(r.chunk.achievement_code):
            return r.rank
    return MISS


async def rank(name, grade, answer, text):
    q = SearchQuery(concept_name=name, concept_definition=text,
                    target_grade=int(grade), top_k=15)
    return rank_of(await hybrid_search(q), answer)


async def main():
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    win = lose = tie = 0
    lines = []
    for i, r in enumerate(rows, 1):
        n, g, ans = r["ai_개념"], r["target_grade"], r["chunk_id"]
        o = await rank(n, g, ans, r["개념_정의_초안"])
        a = await rank(n, g, ans, r["a1_쿼리"])
        mark = "A1승" if a < o else ("원본승" if o < a else "=")
        if a < o: win += 1
        elif o < a: lose += 1
        else: tie += 1
        lines.append(f"{n:<22} {g}학년  원본 {o:>3}  A1 {a:>3}  {mark}")
        print(f"[{i}/{len(rows)}] {lines[-1]}")
    print("\n" + "=" * 60)
    for l in lines:
        print(l)
    print(f"\nA1 우세 {win} / 원본 우세 {lose} / 동률 {tie}")

asyncio.run(main())
