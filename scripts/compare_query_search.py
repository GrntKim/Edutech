import sys, csv, asyncio, time
from app.lib.types import SearchQuery
from app.agents.curriculum_search.logic import hybrid_search

CSV_PATH = "app/agents/concept_collect/a1_queries.csv"

def find_rank(results, answer):
    for r in results:
        if answer in str(r.chunk.achievement_code):
            return r.rank, r.similarity_score
    return None, None

async def run(row):
    name, grade, answer = row["ai_개념"], int(row["target_grade"]), row["chunk_id"]
    print(f"\n=== {name} ({grade}학년, 정답 {answer}) ===")
    for label, text in (("원본", row["개념_정의_초안"]), ("A1", row["a1_쿼리"])):
        q = SearchQuery(concept_name=name, concept_definition=text,
                        target_grade=grade, top_k=15)
        rank, score = find_rank(await hybrid_search(q), answer)
        pos = "15위 밖" if rank is None else f"{rank}위 ({score:.3f})"
        print(f"[{label}] {pos}  {len(text)}자")
        await asyncio.sleep(4)

async def main():
    target = " ".join(sys.argv[1:]) or "분류"
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    picked = rows if target == "all" else [r for r in rows if r["ai_개념"] == target]
    if not picked:
        print(f"'{target}' 없음")
        return
    for row in picked:
        await run(row)

asyncio.run(main())
