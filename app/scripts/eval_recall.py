"""골든셋(RS-005) 기준 Recall@k를 로컬 캐시로 계산한다. DB 불필요."""

import csv
import json
import sys
from pathlib import Path

import numpy as np

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
sys.path.insert(0, str(APP_ROOT))

from agents.curriculum_search.logic import _cosine_similarities, resolve_grade_bands  # noqa: E402
from agents.curriculum_search.schema import CurriculumChunk  # noqa: E402

GOLDEN_PATH = REPO_ROOT / "curriculum-search-engine" / "RS-005_골든셋_라벨링_보정.csv"
CHUNKS_PATH = APP_ROOT / "data" / "curriculum_units.json"
CACHE_DIR = APP_ROOT / "data" / "embeddings_cache"
ANSWER_COL = "정답_chunk_id(직접입력, 없으면 '없음')"

MODELS = [
    "jhgan/ko-sroberta-multitask",
    "BAAI/bge-m3",
    "nlpai-lab/KoE5",
    "intfloat/multilingual-e5-large",
]
# E5 계열은 query: 프리픽스가 없으면 성능이 크게 떨어짐 (캐싱 시 passage: 프리픽스를 candidate 쪽에 이미 적용함).
QUERY_PREFIX = {
    "nlpai-lab/KoE5": "query: ",
    "intfloat/multilingual-e5-large": "query: ",
}
K_VALUES = [1, 3, 5, 10]


def load_answered_rows() -> list[dict]:
    with open(GOLDEN_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    answered = []
    for r in rows:
        answer = r[ANSWER_COL].strip()
        if answer and answer != "없음" and "제외" not in answer:
            answered.append(r)
    return answered


def load_chunks() -> list[CurriculumChunk]:
    raw = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    return [CurriculumChunk(**item) for item in raw]


def recall_at_k(model_name: str, rows: list[dict], chunks: list[CurriculumChunk]) -> dict:
    slug = model_name.replace("/", "__")
    cache = np.load(CACHE_DIR / f"{slug}.npz")
    id_to_emb = dict(zip(cache["chunk_ids"], cache["embeddings"]))

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)

    hits = {k: 0 for k in K_VALUES}
    misses = []
    for row in rows:
        grade = int(row["target_grade"])
        bands = {b.value for b in resolve_grade_bands(grade)}
        candidate_chunks = [c for c in chunks if c.grade_band.value in bands]
        embeddings = [id_to_emb[c.chunk_id] for c in candidate_chunks]

        gold_ids = {g.strip() for g in row[ANSWER_COL].split(",")}

        query_text = QUERY_PREFIX.get(model_name, "") + row["개념_정의_초안"]
        q_emb = model.encode(query_text)
        sims = _cosine_similarities(q_emb.tolist(), embeddings)
        ranked = sorted(zip(candidate_chunks, sims), key=lambda x: -x[1])

        for k in K_VALUES:
            top_ids = {c.chunk_id for c, _ in ranked[:k]}
            if gold_ids & top_ids:
                hits[k] += 1

        rank_of_gold = next(
            (i + 1 for i, (c, _) in enumerate(ranked) if c.chunk_id in gold_ids), None
        )
        misses.append((row["no"], row["ai_개념"], rank_of_gold))

    n = len(rows)
    result = {k: hits[k] / n for k in K_VALUES}
    return result, misses


def main() -> None:
    rows = load_answered_rows()
    chunks = load_chunks()
    print(f"평가 대상(있음) 행: {len(rows)}개\n")

    for model_name in MODELS:
        print(f"=== {model_name} ===")
        result, misses = recall_at_k(model_name, rows, chunks)
        for k in K_VALUES:
            print(f"  Recall@{k}: {result[k]:.2%}")
        print("  행별 정답 순위:")
        for no, concept, rank in misses:
            print(f"    no.{no} {concept}: {'순위 ' + str(rank) if rank else '못 찾음(순위 없음)'}")
        print()


if __name__ == "__main__":
    main()
