"""curriculum-search-golden-set Dataset(RS-005, 42행)에 대해 실제 hybrid_search()를
돌리고 LangSmith evaluate()로 recall을 측정해 대시보드에 실험(Experiment)으로 기록한다.

프로덕션 설정 그대로(KoE5 + dense/sparse RRF + gemini-3.6-flash, thinking_level=low)
사용하므로 42행 전량에 실제 Gemini 호출 + Cloud SQL 조회가 발생한다 — 쿼터/비용이
드는 작업이다. cloud-sql-proxy가 로컬 5433 포트에서 떠 있어야 하고, 순차 실행
(max_concurrency=1)으로 eval_poolsize_latency.py와 동일하게 레이트리밋을 피한다.
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from langsmith.evaluation import evaluate

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(REPO_ROOT))
load_dotenv(REPO_ROOT / ".env")

from agents.curriculum_search.logic import hybrid_search  # noqa: E402
from agents.curriculum_search.schema import SearchQuery  # noqa: E402

DATASET_NAME = "curriculum-search-golden-set"


def target(inputs: dict) -> dict:
    query = SearchQuery(
        concept_name=inputs["ai_개념"],
        concept_definition=inputs["개념_정의_초안"],
        target_grade=inputs["target_grade"],
        top_k=15,
    )
    results = asyncio.run(hybrid_search(query))
    return {"predicted_chunk_ids": [r.chunk.chunk_id for r in results]}


def recall_at_15(outputs: dict, reference_outputs: dict) -> bool:
    """target_grade까지 top_k=15 안에 정답 chunk_id가 있는지."""
    return reference_outputs["chunk_id"] in outputs["predicted_chunk_ids"]


def rank1_match(outputs: dict, reference_outputs: dict) -> bool:
    """LLM 리랭킹 1위가 정답과 일치하는지(더 엄격한 precision 신호)."""
    predicted = outputs["predicted_chunk_ids"]
    return bool(predicted) and predicted[0] == reference_outputs["chunk_id"]


def main() -> None:
    results = evaluate(
        target,
        data=DATASET_NAME,
        evaluators=[recall_at_15, rank1_match],
        experiment_prefix="a2-hybrid-search",
        max_concurrency=1,
    )
    print(results)


if __name__ == "__main__":
    main()
