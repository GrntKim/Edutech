"""dense 임베딩 모델을 바꿔가며 실제 hybrid_search(dense+sparse RRF+LLM 리랭커) 전체를
골든셋에 돌려 end-to-end Recall/지연을 비교한다. eval_recall.py는 dense 단독 순위만 보므로
그 결과가 최종 파이프라인 성능과 일치하는지 검증하는 목적. DB 불필요(로컬 캐시)."""

import asyncio
import csv
import json
import sys
import time
from pathlib import Path

import google.api_core.exceptions
from dotenv import load_dotenv
import numpy as np

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
sys.path.insert(0, str(APP_ROOT))

from agents.curriculum_search import logic as _logic  # noqa: E402
from agents.curriculum_search.logic import resolve_grade_bands, search_within_chunks  # noqa: E402
from agents.curriculum_search.schema import CurriculumChunk, SearchQuery  # noqa: E402

_logic.RERANK_MODEL = "gemini-flash-lite-latest"  # 무료 티어 쿼터 제약, 기존 eval들과 동일 사유

MODEL_NAME = sys.argv[1] if len(sys.argv) > 1 else _logic.EMBEDDING_MODEL
QUERY_PREFIX = {
    "nlpai-lab/KoE5": "query: ",
    "intfloat/multilingual-e5-large": "query: ",
}.get(MODEL_NAME, "")

_logic.EMBEDDING_MODEL = MODEL_NAME
_logic._model = None  # 새 모델 강제 재로드


async def _patched_embed_text(text: str) -> list[float]:
    model = await asyncio.to_thread(_logic._get_model)
    embedding = await asyncio.to_thread(model.encode, QUERY_PREFIX + text)
    return embedding.tolist()


_logic.embed_text = _patched_embed_text  # search_within_chunks가 참조하는 모듈 전역을 교체

GOLDEN_PATH = REPO_ROOT / "curriculum-search-engine" / "RS-005_골든셋_라벨링_보정.csv"
CHUNKS_PATH = APP_ROOT / "data" / "curriculum_units.json"
EMBEDDING_CACHE = APP_ROOT / "data" / "embeddings_cache" / f"{MODEL_NAME.replace('/', '__')}.npz"
RESULTS_PATH = APP_ROOT / "data" / f"eval_embedding_e2e_results_{MODEL_NAME.replace('/', '__')}.json"
ANSWER_COL = "정답_chunk_id(직접입력, 없으면 '없음')"


def load_answered_rows() -> list[dict]:
    with open(GOLDEN_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [
        r for r in rows
        if r[ANSWER_COL].strip() and r[ANSWER_COL].strip() != "없음" and "제외" not in r[ANSWER_COL]
    ]


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
    print(f"embedding_model={MODEL_NAME}, pool={_logic.CANDIDATE_POOL_SIZE}, 평가 대상(있음) 행: {len(rows)}개\n")

    done = {}
    if RESULTS_PATH.exists():
        done = {r["no"]: r for r in json.loads(RESULTS_PATH.read_text(encoding="utf-8"))}
        print(f"이전 결과 {len(done)}개 재사용\n")

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
        RESULTS_PATH.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    n = len(all_results)
    hits = sum(r["hit"] for r in all_results)
    latencies = [r["elapsed"] for r in all_results]
    over_sla = sum(1 for t in latencies if t > 2.0)

    print(f"\n=== 요약 (embedding={MODEL_NAME}, pool={_logic.CANDIDATE_POOL_SIZE}, {n}개 행) ===")
    print(f"Recall: {hits/n:.2%} | 평균 지연: {sum(latencies)/n:.2f}s | 최대: {max(latencies):.2f}s")
    print(f"NFR-002-1(2초) 초과: {over_sla}/{n}")


if __name__ == "__main__":
    asyncio.run(main())
