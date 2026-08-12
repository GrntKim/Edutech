"""A1 쿼리 생성 — 골든셋 기반, 이어하기 지원.

사용법:
    python -m scripts.build_queries              # 4건 처리 (기본)
    python -m scripts.build_queries --limit 8    # 8건 처리
    python -m scripts.build_queries --status     # 진행 상황만 확인
"""
import csv
import os
import sys
import time

from app.lib.types import ConceptInput, PipelineContext
from app.agents.concept_collect.logic import analyze_concept

SOURCE = "curriculum-search-engine/RS-005_골든셋.csv"
OUTPUT = "app/data/a1_queries.csv"
FIELDS = [
    "ai_개념", "target_grade", "chunk_id", "개념_정의_초안",
    "a1_쿼리", "status", "is_ai_concept", "소요시간", "글자수",
]


def load_source():
    with open(SOURCE, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_done():
    if not os.path.exists(OUTPUT):
        return {}
    with open(OUTPUT, encoding="utf-8-sig") as f:
        return {(r["ai_개념"], r["target_grade"]): r for r in csv.DictReader(f)}


def save(rows):
    with open(OUTPUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def main():
    limit = 4
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    source = load_source()
    done = load_done()
    rows = list(done.values())

    todo = [r for r in source if (r["ai_개념"], r["target_grade"]) not in done]
    print(f"전체 {len(source)}건 / 완료 {len(done)}건 / 남음 {len(todo)}건")

    if "--status" in sys.argv:
        return

    if not todo:
        print("모두 처리되었습니다.")
        return

    batch = todo[:limit]
    print(f"이번 실행: {len(batch)}건 (Gemini 최대 {len(batch) * 2}회)\n")

    for i, src in enumerate(batch, 1):
        name = src["ai_개념"]
        grade = int(src["target_grade"])
        print(f"[{i}/{len(batch)}] {name} / {grade}학년 ... ", end="", flush=True)

        try:
            start = time.perf_counter()
            result = analyze_concept(
                ConceptInput(raw_concept_name=name, target_grade=grade, subject_hint=None),
                PipelineContext(target_grade=grade),
            )
            elapsed = time.perf_counter() - start
        except Exception as exc:
            print(f"실패: {exc}")
            print("\n중단합니다. 지금까지 결과는 저장되었습니다.")
            break

        query = result.search_query.concept_definition
        rows.append({
            "ai_개념": name,
            "target_grade": grade,
            "chunk_id": src["chunk_id"],
            "개념_정의_초안": src["개념_정의_초안"],
            "a1_쿼리": query,
            "status": result.status,
            "is_ai_concept": result.concept.is_ai_concept,
            "소요시간": f"{elapsed:.2f}",
            "글자수": len(query),
        })
        save(rows)
        print(f"{result.status} ({elapsed:.2f}초, {len(query)}자)")

    print(f"\n저장: {OUTPUT}")
    print(f"누적 {len(rows)}건 / 전체 {len(source)}건")


main()