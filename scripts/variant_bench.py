import csv, asyncio, statistics
from app.lib.types import SearchQuery
from app.agents.curriculum_search.logic import hybrid_search

CSV_PATH = "app/data/a1_queries.csv"
REPEAT = 3
MISS = 99
SAMPLE = [("분류","4"),("분류","3"),("패턴 인식","2"),("패턴 인식","4"),
          ("특징 추출","3"),("예측","5"),("데이터 수집","4"),
          ("의사결정트리","4"),("데이터 시각화","4"),("확률","6")]

def rank_of(results, answer):
    for r in results:
        if answer in str(r.chunk.achievement_code):
            return r.rank
    return MISS

async def measure(name, grade, answer, text):
    ranks = []
    for _ in range(REPEAT):
        q = SearchQuery(concept_name=name, concept_definition=text,
                        target_grade=int(grade), top_k=15)
        ranks.append(rank_of(await hybrid_search(q), answer))
    return ranks

async def main():
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    picked = [r for r in rows if (r["ai_개념"], r["target_grade"]) in SAMPLE]
    print(f"표본 {len(picked)}건 x 2쿼리 x {REPEAT}회\n")
    out = []
    for i, r in enumerate(picked, 1):
        n, g, a = r["ai_개념"], r["target_grade"], r["chunk_id"]
        print(f"[{i}/{len(picked)}] {n} {g}학년", flush=True)
        o = await measure(n, g, a, r["개념_정의_초안"])
        w = await measure(n, g, a, r["a1_쿼리"])
        print(f"    원본 {o} 평균 {statistics.mean(o):.1f}")
        print(f"    A1   {w} 평균 {statistics.mean(w):.1f}", flush=True)
        out.append((n, g, statistics.mean(o), statistics.mean(w)))
    print("\n" + "=" * 60)
    win = sum(1 for _, _, o, w in out if w < o)
    lose = sum(1 for _, _, o, w in out if w > o)
    for n, g, o, w in out:
        mark = "A1승" if w < o else ("원본승" if w > o else "=")
        print(f"{n:<16}{g}학년  원본 {o:5.1f}  A1 {w:5.1f}  {mark}")
    print(f"\nA1 우세 {win} / 원본 우세 {lose} / 동률 {len(out)-win-lose}")

asyncio.run(main())
