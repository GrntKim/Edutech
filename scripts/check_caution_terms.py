"""골든셋 42건 caution_terms 전수 확인 — 음역어/영어 잔존 여부 육안 점검용.
사용법:
    python -m scripts.check_caution_terms
"""
import csv
import time

from app.lib.types import ConceptInput, PipelineContext
from app.agents.concept_collect.logic import analyze_concept

SOURCE = "curriculum-search-engine/RS-005_골든셋.csv"


def load_source():
    with open(SOURCE, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    source = load_source()
    print(f"전체 {len(source)}건 확인 시작\n")

    for i, src in enumerate(source, 1):
        name = src["ai_개념"]
        grade = int(src["target_grade"])
        print(f"[{i}/{len(source)}] {name} / {grade}학년 ... ", end="", flush=True)

        try:
            start = time.perf_counter()
            result = analyze_concept(
                ConceptInput(raw_concept_name=name, target_grade=grade, subject_hint=None),
                PipelineContext(target_grade=grade),
            )
            elapsed = time.perf_counter() - start
        except Exception as exc:
            print(f"실패: {exc}")
            continue

        if result.status != "success":
            print(f"status={result.status}, 건너뜀")
            continue

        terms = result.concept.caution_terms
        print(f"({elapsed:.2f}초)")
        print(f"    {terms}")


main()
