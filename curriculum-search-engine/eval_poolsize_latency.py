"""CANDIDATE_POOL_SIZE를 20에서 25로 올린 뒤, 실제 hybrid_search(search_within_chunks)를
골든셋 '있음' 행 전체에 돌려 Recall과 Gemini 호출 지연을 함께 측정한다. DB 불필요(로컬 캐시)."""

import asyncio
import csv
import json
import sys
import time
from pathlib import Path

import google.api_core.exceptions
from dotenv import load_dotenv
import numpy as np

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
REPO_ROOT = APP_ROOT.parent
sys.path.insert(0, str(APP_ROOT))

from agents.curriculum_search import logic as _logic  # noqa: E402
from agents.curriculum_search.logic import resolve_grade_bands, search_within_chunks  # noqa: E402
from agents.curriculum_search.schema import CurriculumChunk, SearchQuery  # noqa: E402
from eval_cache_utils import corpus_fingerprint, load_cached_results, save_results  # noqa: E402

_logic.RERANK_MODEL = "gemini-3.6-flash"  # 프로덕션과 동일 모델(RS-007 §9.14 KoE5 재검증용)

POOL_SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else _logic.CANDIDATE_POOL_SIZE
_logic.CANDIDATE_POOL_SIZE = POOL_SIZE

THINKING_LEVEL = sys.argv[2] if len(sys.argv) > 2 else _logic.RERANK_THINKING_LEVEL
_logic.RERANK_THINKING_LEVEL = THINKING_LEVEL

GOLDEN_PATH = REPO_ROOT / "curriculum-search-engine" / "RS-005_골든셋.csv"
CHUNKS_PATH = APP_ROOT / "data" / "curriculum_units.json"
EMBEDDING_CACHE = APP_ROOT / "data" / "embeddings_cache" / "nlpai-lab__KoE5.npz"
RESULTS_PATH = APP_ROOT / "data" / f"eval_poolsize_latency_results_pool{POOL_SIZE}_koe5_{THINKING_LEVEL}.json"
ANSWER_COL = "chunk_id"
# _logic.EMBEDDING_MODEL 기본값이 이미 "nlpai-lab/KoE5"라 embed_text()의 query:/passage:
# 프리픽스 처리를 그대로 재사용한다(별도 패치 불필요).


def load_answered_rows() -> list[dict]:
    with open(GOLDEN_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    answered = []
    for i, r in enumerate(rows, start=2):
        if r[ANSWER_COL].strip():
            r["no"] = str(i)
            answered.append(r)
    return answered


def load_chunks_and_embeddings() -> tuple[list[CurriculumChunk], dict[str, np.ndarray]]:
    raw = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    chunks = [CurriculumChunk(**item) for item in raw]
    cache = np.load(EMBEDDING_CACHE)
    by_id = dict(zip(cache["chunk_ids"], cache["embeddings"]))
    return chunks, by_id


async def _with_retry(coro_fn, *args, max_retries: int = 10):
    for attempt in range(max_retries):
        try:
            return await coro_fn(*args)
        except (google.api_core.exceptions.ResourceExhausted, _logic.CurriculumSearchError) as exc:
            if "429" not in str(exc) and "RESOURCE_EXHAUSTED" not in str(exc):
                raise
            wait = 30 * (attempt + 1)
            print(f"    (429 rate limit, {wait}s 대기 후 재시도 {attempt + 1}/{max_retries})")
            await asyncio.sleep(wait)
    raise RuntimeError("재시도 초과")


async def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    rows = load_answered_rows()
    all_chunks, emb_by_id = load_chunks_and_embeddings()
    print(f"CANDIDATE_POOL_SIZE={_logic.CANDIDATE_POOL_SIZE}, 평가 대상(있음) 행: {len(rows)}개\n")

    fingerprint = corpus_fingerprint(CHUNKS_PATH)
    done = load_cached_results(RESULTS_PATH, fingerprint)
    if done:
        print(f"이전 결과 {len(done)}개 재사용(코퍼스 지문 일치 확인됨)\n")

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

        print(f"no.{no} {concept}(g{grade}, 후보 {len(candidate_chunks)}개) | hit: {'O' if hit else 'X'} ({elapsed:.2f}s)")

        all_results.append({"no": no, "concept": concept, "grade": grade, "hit": hit, "elapsed": elapsed})
        save_results(RESULTS_PATH, fingerprint, all_results)

    n = len(all_results)
    hits = sum(r["hit"] for r in all_results)
    latencies = [r["elapsed"] for r in all_results]
    over_sla = sum(1 for t in latencies if t > 2.0)

    print(f"\n=== 요약 (pool={_logic.CANDIDATE_POOL_SIZE}, {n}개 행, 모델: {_logic.RERANK_MODEL}) ===")
    print(f"Recall: {hits/n:.2%} | 평균 지연: {sum(latencies)/n:.2f}s | 최대: {max(latencies):.2f}s")
    print(f"NFR-002-1(2초) 초과: {over_sla}/{n}")


if __name__ == "__main__":
    asyncio.run(main())
