"""prefilter(dense+sparse RRF) 방식과, 필터 없이 학년군 후보 전체를 바로 Gemini에 넘기는
LLM-only 방식을 golden set '있음' 16개 행으로 비교한다. Recall뿐 아니라 실제 Gemini 호출
지연시간(초)과 후보 텍스트 크기(문자 수, 토큰 예산 프록시)도 함께 측정한다.
"""

import asyncio
import csv
import json
import sys
import time
from pathlib import Path

import google.api_core.exceptions
import numpy as np
from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
REPO_ROOT = APP_ROOT.parent
sys.path.insert(0, str(APP_ROOT))

from agents.curriculum_search import logic as _logic  # noqa: E402
from agents.curriculum_search.logic import (  # noqa: E402
    _llm_rerank,
    embedding_source_text,
    resolve_grade_bands,
    search_within_chunks,
)
from agents.curriculum_search.schema import CurriculumChunk, SearchQuery  # noqa: E402
from eval_cache_utils import corpus_fingerprint, load_cached_results, save_results  # noqa: E402

# gemini-flash-latest(gemini-3.6-flash)가 무료 티어 일일 한도(20건)를 오늘 이미 소진해서
# 이 벤치마크 한정으로 별도 쿼터를 가진 gemini-flash-lite-latest로 임시 교체한다.
# 프로덕션 RERANK_MODEL 설정은 건드리지 않는다.
_logic.RERANK_MODEL = "gemini-flash-lite-latest"

GOLDEN_PATH = REPO_ROOT / "curriculum-search-engine" / "RS-005_골든셋.csv"
CHUNKS_PATH = APP_ROOT / "data" / "curriculum_units.json"
EMBEDDING_CACHE = APP_ROOT / "data" / "embeddings_cache" / "jhgan__ko-sroberta-multitask.npz"
ANSWER_COL = "chunk_id"


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


RESULTS_PATH = APP_ROOT / "data" / "eval_prefilter_vs_llm_only_results.json"


async def _with_retry(coro_fn, *args, max_retries: int = 10):
    for attempt in range(max_retries):
        try:
            return await coro_fn(*args)
        except (google.api_core.exceptions.ResourceExhausted, _logic.CurriculumSearchError) as exc:
            if "429" not in str(exc) and "RESOURCE_EXHAUSTED" not in str(exc):
                raise
            wait = 30 * (attempt + 1)
            print(f"    (429 rate limit, {wait}s 대기 후 재시도 {attempt + 1}/{max_retries}) {str(exc)[:80]}")
            await asyncio.sleep(wait)
    raise RuntimeError("재시도 초과")


async def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    rows = load_answered_rows()
    all_chunks, emb_by_id = load_chunks_and_embeddings()
    print(f"평가 대상(있음) 행: {len(rows)}개\n")

    fingerprint = corpus_fingerprint(CHUNKS_PATH, GOLDEN_PATH)
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
        candidate_chars = sum(len(embedding_source_text(c)) for c in candidate_chunks)

        query = SearchQuery(
            concept_name=concept, concept_definition=row["개념_정의_초안"], target_grade=grade, top_k=15
        )

        # 1) 현재 방식: dense+sparse RRF top20 -> Gemini
        t0 = time.monotonic()
        hybrid_results = await _with_retry(
            search_within_chunks, query, candidate_chunks, candidate_embeddings
        )
        hybrid_elapsed = time.monotonic() - t0
        hybrid_hit = bool({r.chunk.chunk_id for r in hybrid_results} & gold_ids)

        # 2) LLM-only: 학년군 필터만, 후보 전량을 바로 Gemini에
        t0 = time.monotonic()
        rerank = await _with_retry(_llm_rerank, query, candidate_chunks, query.top_k)
        llm_only_elapsed = time.monotonic() - t0
        llm_only_hit = bool({m.chunk_id for m in rerank.matches} & gold_ids)

        print(
            f"no.{no} {concept}(g{grade}, 후보 {len(candidate_chunks)}개/{candidate_chars}자) | "
            f"hybrid: {'O' if hybrid_hit else 'X'} ({hybrid_elapsed:.2f}s) | "
            f"llm_only: {'O' if llm_only_hit else 'X'} ({llm_only_elapsed:.2f}s)"
        )

        all_results.append(
            {
                "no": no,
                "concept": concept,
                "grade": grade,
                "candidate_count": len(candidate_chunks),
                "candidate_chars": candidate_chars,
                "hybrid_hit": hybrid_hit,
                "hybrid_elapsed": hybrid_elapsed,
                "llm_only_hit": llm_only_hit,
                "llm_only_elapsed": llm_only_elapsed,
            }
        )
        save_results(RESULTS_PATH, fingerprint, all_results)

    n = len(all_results)
    hybrid_hits = sum(r["hybrid_hit"] for r in all_results)
    llm_only_hits = sum(r["llm_only_hit"] for r in all_results)
    hybrid_latencies = [r["hybrid_elapsed"] for r in all_results]
    llm_only_latencies = [r["llm_only_elapsed"] for r in all_results]

    print(f"\n=== 요약 ({n}개 행, 모델: {_logic.RERANK_MODEL}) ===")
    print(f"hybrid   Recall: {hybrid_hits/n:.2%} | 평균 지연: {sum(hybrid_latencies)/n:.2f}s | 최대: {max(hybrid_latencies):.2f}s")
    print(f"llm_only Recall: {llm_only_hits/n:.2%} | 평균 지연: {sum(llm_only_latencies)/n:.2f}s | 최대: {max(llm_only_latencies):.2f}s")
    over_sla_hybrid = sum(1 for t in hybrid_latencies if t > 2.0)
    over_sla_llm_only = sum(1 for t in llm_only_latencies if t > 2.0)
    print(f"NFR-002-1(2초) 초과: hybrid {over_sla_hybrid}/{n} | llm_only {over_sla_llm_only}/{n}")


if __name__ == "__main__":
    asyncio.run(main())
