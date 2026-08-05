"""RS-006 TODO: RS-005 골든셋 "없음" 행을 비고 기반으로 재검색해 실제 정답이 있는지 확인한다.

DB 없이 로컬 curriculum_units.json + 캐싱된 임베딩으로 실제 hybrid_search 파이프라인
(dense+sparse RRF + Gemini 리랭킹)을 그대로 재현해서 돌린다. 결과는 자동 반영하지 않고
사람이 비고와 대조해 판단할 수 있도록 출력만 한다.
"""

import asyncio
import csv
import json
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
REPO_ROOT = APP_ROOT.parent
sys.path.insert(0, str(APP_ROOT))

from agents.curriculum_search.logic import resolve_grade_bands, search_within_chunks  # noqa: E402
from agents.curriculum_search.schema import CurriculumChunk, SearchQuery  # noqa: E402

GOLDEN_PATH = REPO_ROOT / "curriculum-search-engine" / "RS-005_골든셋_라벨링_보정.csv"
CHUNKS_PATH = APP_ROOT / "data" / "curriculum_units.json"
EMBEDDING_CACHE = APP_ROOT / "data" / "embeddings_cache" / "jhgan__ko-sroberta-multitask.npz"
ANSWER_COL = "정답_chunk_id(직접입력, 없으면 '없음')"
NOTE_COL = "비고 (왜 안 맞는지도 적어주시면 감사하겠습니다 :) )"


def load_none_rows() -> list[dict]:
    with open(GOLDEN_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r[ANSWER_COL].strip() == "없음"]


def load_chunks_and_embeddings() -> tuple[list[CurriculumChunk], dict[str, np.ndarray]]:
    raw = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    chunks = [CurriculumChunk(**item) for item in raw]
    cache = np.load(EMBEDDING_CACHE)
    by_id = dict(zip(cache["chunk_ids"], cache["embeddings"]))
    return chunks, by_id


async def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    none_rows = load_none_rows()
    all_chunks, emb_by_id = load_chunks_and_embeddings()
    print(f"'없음' 행 {len(none_rows)}개 재검색 시작 (코퍼스 {len(all_chunks)}개 청크)\n")

    for row in none_rows:
        grade = int(row["target_grade"])
        bands = {b.value for b in resolve_grade_bands(grade)}
        candidate_chunks = [c for c in all_chunks if c.grade_band.value in bands]
        candidate_embeddings = [emb_by_id[c.chunk_id] for c in candidate_chunks]

        query = SearchQuery(
            concept_name=row["ai_개념"],
            concept_definition=row["개념_정의_초안"],
            target_grade=grade,
            top_k=5,
        )

        try:
            results = await search_within_chunks(query, candidate_chunks, candidate_embeddings)
        except Exception as exc:  # noqa: BLE001
            print(f"no.{row['no']} {row['ai_개념']}(g{grade}) -- 에러: {exc}\n")
            continue

        print(f"no.{row['no']} {row['ai_개념']}(g{grade})")
        print(f"  기존 비고: {row[NOTE_COL].strip()[:120]}")
        if not results:
            print("  LLM 재검색 결과: 없음 (기존 라벨과 일치)")
        else:
            print("  LLM 재검색 결과:")
            for r in results:
                print(f"    [{r.chunk.chunk_id}] {r.chunk.achievement_text[:60]}")
                print(f"        근거: {r.reasoning}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
