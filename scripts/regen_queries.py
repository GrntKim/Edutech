"""골든셋 기준으로 A1 쿼리를 재생성한다. 중단되면 다시 실행하면 이어서 진행."""
import csv, os, time
from app.lib.types import ConceptInput, PipelineContext
from app.agents.concept_collect.logic import analyze_concept

SRC = "curriculum-search-engine/RS-005_골든셋.csv"
DST = "app/data/a1_queries_v5.csv"
RUNS = 3
COLS = ["회차", "ai_개념", "target_grade", "chunk_id", "개념_정의_초안",
        "a1_쿼리", "status", "is_ai_concept", "소요시간", "글자수"]


def done_keys():
    if not os.path.exists(DST):
        return set()
    with open(DST, encoding="utf-8-sig") as f:
        return {(r["회차"], r["ai_개념"], r["target_grade"]) for r in csv.DictReader(f)}


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
    done = done_keys()
    new = not os.path.exists(DST)
    f = open(DST, "a", encoding="utf-8-sig", newline="")
    w = csv.DictWriter(f, fieldnames=COLS)
    if new:
        w.writeheader()
    for run in range(1, RUNS + 1):
      for i, row in enumerate(rows, 1):
        name, grade = row["ai_개념"], row["target_grade"]
        if (str(run), name, grade) in done:
            print(f"[{run}회 {i}/{len(rows)}] {name} {grade} 건너뜀")
            continue
        t0 = time.time()
        r = analyze_concept(
            ConceptInput(raw_concept_name=name, target_grade=int(grade)),
            PipelineContext(target_grade=int(grade)),
        )
        q = r.search_query.concept_definition
        w.writerow({
            "회차": run,
            "ai_개념": name, "target_grade": grade, "chunk_id": row["chunk_id"],
            "개념_정의_초안": row["개념_정의_초안"], "a1_쿼리": q,
            "status": r.status, "is_ai_concept": r.concept.is_ai_concept,
            "소요시간": round(time.time() - t0, 2), "글자수": len(q),
        })
        f.flush()
        print(f"[{run}회 {i}/{len(rows)}] {name} {grade} | {r.status} | {len(q)}자")
        time.sleep(2)
    f.close()




if __name__ == "__main__":
    main()
