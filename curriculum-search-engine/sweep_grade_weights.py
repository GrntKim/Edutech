"""RS-006 §9.7에서 프로토타입한 grade-distance 가중치(_GRADE_DISTANCE_WEIGHT)를
여러 후보 세트로 바꿔가며 골든셋 42행 전체(eval_poolsize_latency.py와 동일 파이프라인,
pool=20)에 돌려 Recall/지연을 비교한다. 세트별 결과는 app/data/grade_weight_sweep/에
개별 저장(재시작 캐시 포함)하고, 마지막에 summary.json으로 종합한다."""

import asyncio
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "app"))

from agents.curriculum_search import logic as _logic  # noqa: E402
from agents.curriculum_search.logic import resolve_grade_bands, search_within_chunks  # noqa: E402
from agents.curriculum_search.schema import SearchQuery  # noqa: E402
from eval_poolsize_latency import (  # noqa: E402
    REPO_ROOT,
    _with_retry,
    load_answered_rows,
    load_chunks_and_embeddings,
)

RESULTS_DIR = REPO_ROOT / "app" / "data" / "grade_weight_sweep"
ANSWER_COL = "chunk_id"

# distance=0(같은 학년군)은 항상 1.0으로 고정. 1/2는 후보 세트별로 바꿔가며 비교.
WEIGHT_CANDIDATES = {
    "baseline_1.0_1.0_1.0": {0: 1.0, 1: 1.0, 2: 1.0},  # 가중치 없음 (기존 로직과 동일)
    "team_1.0_0.6_0.3": {0: 1.0, 1: 0.6, 2: 0.3},  # 팀 제안값, 이미 1회 실측(52.38%)
    "mild_1.0_0.8_0.6": {0: 1.0, 1: 0.8, 2: 0.6},  # 완만한 페널티
    "strong_1.0_0.4_0.15": {0: 1.0, 1: 0.4, 2: 0.15},  # 강한 페널티
}


async def run_one(label: str, weights: dict[int, float], rows, all_chunks, emb_by_id) -> dict:
    _logic._GRADE_DISTANCE_WEIGHT = weights

    results_path = RESULTS_DIR / f"{label}.json"
    done = {}
    if results_path.exists():
        done = {r["no"]: r for r in json.loads(results_path.read_text(encoding="utf-8"))}
        print(f"  이전 결과 {len(done)}개 재사용")

    all_results = list(done.values())
    for row in rows:
        no, concept, grade = row["no"], row["ai_개념"], int(row["target_grade"])
        if no in done:
            continue
        gold_ids = {g.strip() for g in row[ANSWER_COL].split(",")}
        bands = {b.value for b in resolve_grade_bands(grade)}
        candidate_chunks = [c for c in all_chunks if c.grade_band.value in bands]
        candidate_embeddings = [emb_by_id[c.chunk_id] for c in candidate_chunks]

        query = SearchQuery(
            concept_name=concept, concept_definition=row["개념_정의_초안"], target_grade=grade, top_k=15
        )

        t0 = time.monotonic()
        results = await _with_retry(search_within_chunks, query, candidate_chunks, candidate_embeddings)
        elapsed = time.monotonic() - t0
        hit = bool({r.chunk.chunk_id for r in results} & gold_ids)

        print(f"  no.{no} {concept}(g{grade}) | hit: {'O' if hit else 'X'} ({elapsed:.2f}s)")
        all_results.append({"no": no, "concept": concept, "grade": grade, "hit": hit, "elapsed": elapsed})
        results_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    n = len(all_results)
    hits = sum(r["hit"] for r in all_results)
    latencies = [r["elapsed"] for r in all_results]
    return {
        "label": label,
        "weights": weights,
        "n": n,
        "recall": hits / n,
        "avg_latency": sum(latencies) / n,
        "max_latency": max(latencies),
    }


async def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    rows = load_answered_rows()
    all_chunks, emb_by_id = load_chunks_and_embeddings()
    print(f"평가 대상: {len(rows)}개 행, 가중치 후보 {len(WEIGHT_CANDIDATES)}세트\n")

    summary = []
    for label, weights in WEIGHT_CANDIDATES.items():
        print(f"\n=== {label} {weights} ===")
        summary.append(await run_one(label, weights, rows, all_chunks, emb_by_id))

    print("\n=== 스윕 요약 ===")
    for s in summary:
        print(f"{s['label']:<25} recall={s['recall']:.2%} avg_latency={s['avg_latency']:.2f}s max={s['max_latency']:.2f}s")

    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{RESULTS_DIR / 'summary.json'}에 저장 완료.")


if __name__ == "__main__":
    asyncio.run(main())
