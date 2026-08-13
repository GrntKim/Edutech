"""a1_queries_v2.csv 전체를 원본 정의와 비교한다. 주의: A2 검색은 결정론적이나 A1 쿼리 생성은 실행마다 달라진다.
이 CSV는 1회 생성 표본이므로 승패 결과를 성능 근거로 쓰지 말 것."""
import csv, asyncio
from app.lib.types import SearchQuery
from app.agents.curriculum_search.logic import hybrid_search

CSV_PATH = "app/data/a1_queries_v5.csv"
MISS = 99

def _norm(code):
    return code.strip().strip("[]").replace(" ", "")


def rank_of(results, answer):
    # 정답이 쉼표로 여러 개일 수 있다(골든셋 다대다). 하나라도 맞으면 정답으로 본다.
    wanted = {_norm(a) for a in str(answer).split(",") if a.strip()}
    for r in results:
        if _norm(str(r.chunk.achievement_code)) in wanted:
            return r.rank
    return MISS


async def rank(name, grade, answer, text):
    q = SearchQuery(concept_name=name, concept_definition=text,
                    target_grade=int(grade), top_k=15)
    return rank_of(await hybrid_search(q), answer)


async def main():
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    runs = sorted({r["회차"] for r in rows})
    base = {}
    bare = {}   # 개념명만 넣은 경우   # 원본 쿼리는 회차와 무관하므로 1회만 검색
    a1 = {}     # (회차, 키) -> 순위
    keys = []
    for r in rows:
        k = (r["ai_개념"], r["target_grade"], r["chunk_id"])
        if k not in base:
            base[k] = await rank(k[0], k[1], k[2], r["개념_정의_초안"])
            bare[k] = await rank(k[0], k[1], k[2], k[0])
            keys.append(k)
            print(f"[원본 {len(keys)}/{len(rows)//len(runs)}] {k[0]} {k[1]} -> {base[k]}")
        a1[(r["회차"], k)] = await rank(k[0], k[1], k[2], r["a1_쿼리"])
        print(f"  [{r['회차']}회] {k[0]} {k[1]} -> {a1[(r['회차'], k)]}")

    print("\n" + "=" * 78)
    print(f"{'개념':<24}{'학년':<5}{'개념명':>5}{'원본':>5}" + "".join(f"{r+'회':>6}" for r in runs))
    for k in keys:
        row = f"{k[0]:<24}{k[1]:<5}{bare[k]:>5}{base[k]:>5}"
        row += "".join(f"{a1[(r, k)]:>6}" for r in runs)
        print(row)

    print("\n회차별 승패")
    for r in runs:
        w = sum(1 for k in keys if a1[(r, k)] < base[k])
        l = sum(1 for k in keys if base[k] < a1[(r, k)])
        t = len(keys) - w - l
        print(f"  {r}회차 vs 원본정의문: A1 {w} / 원본 {l} / 동률 {t}")
        w2 = sum(1 for k in keys if a1[(r, k)] < bare[k])
        l2 = sum(1 for k in keys if bare[k] < a1[(r, k)])
        print(f"  {r}회차 vs 개념명단독: A1 {w2} / 개념명 {l2} / 동률 {len(keys)-w2-l2}")

    print("\n회차 간 A1 순위가 흔들린 항목")
    n = 0
    for k in keys:
        v = [a1[(r, k)] for r in runs]
        if len(set(v)) > 1:
            n += 1
            print(f"  {k[0]} {k[1]}학년: {v}")
    print(f"  총 {n}/{len(keys)}건")


if __name__ == "__main__":
    asyncio.run(main())
