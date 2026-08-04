import json
import sys
import time
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_ROOT))

from agents.curriculum_search.logic import embedding_source_text  # noqa: E402
from agents.curriculum_search.schema import CurriculumChunk  # noqa: E402

DATA_PATH = APP_ROOT / "data" / "curriculum_units.json"
CACHE_DIR = APP_ROOT / "data" / "embeddings_cache"

CANDIDATE_MODELS = [
    "jhgan/ko-sroberta-multitask",
    "BAAI/bge-m3",
    "nlpai-lab/KoE5",
    "intfloat/multilingual-e5-large",
]

# E5 계열은 query: / passage: 프리픽스를 안 붙이면 성능이 크게 떨어진다고 알려져 있음.
# 이 딕셔너리에 없는 모델(ko-sroberta, bge-m3)은 프리픽스 없이 원문 그대로 인코딩한다.
PASSAGE_PREFIX = {
    "nlpai-lab/KoE5": "passage: ",
    "intfloat/multilingual-e5-large": "passage: ",
}
QUERY_PREFIX = {
    "nlpai-lab/KoE5": "query: ",
    "intfloat/multilingual-e5-large": "query: ",
}


def _slug(model_name: str) -> str:
    return model_name.replace("/", "__")


def cache_path(model_name: str) -> Path:
    return CACHE_DIR / f"{_slug(model_name)}.npz"


def load_chunks() -> list[CurriculumChunk]:
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return [CurriculumChunk(**item) for item in raw]


def compute_and_cache(model_name: str, chunks: list[CurriculumChunk]) -> None:
    prefix = PASSAGE_PREFIX.get(model_name, "")
    texts = [prefix + embedding_source_text(c) for c in chunks]
    chunk_ids = [c.chunk_id for c in chunks]

    print(f"\n=== {model_name} ===")
    t0 = time.time()
    model = SentenceTransformer(model_name)
    print(f"모델 로드: {time.time() - t0:.2f}s")

    t0 = time.time()
    embeddings = model.encode(texts, show_progress_bar=True, convert_to_numpy=True)
    elapsed = time.time() - t0
    print(f"임베딩 {len(texts)}개: {elapsed:.2f}s ({elapsed / len(texts) * 1000:.1f}ms/개), dim={embeddings.shape[1]}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_path(model_name), chunk_ids=np.array(chunk_ids), embeddings=embeddings)
    print(f"저장: {cache_path(model_name)}")


def load_cache(model_name: str) -> tuple[list[str], np.ndarray]:
    data = np.load(cache_path(model_name))
    return list(data["chunk_ids"]), data["embeddings"]


def main() -> None:
    chunks = load_chunks()
    print(f"{len(chunks)}개 청크 로드 완료.")
    for model_name in CANDIDATE_MODELS:
        compute_and_cache(model_name, chunks)


if __name__ == "__main__":
    main()
