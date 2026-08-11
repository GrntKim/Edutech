"""RS-005 골든셋을 LangSmith Dataset으로 업로드한다.

eval_recall.py와 동일한 로딩 규칙(utf-8-sig, chunk_id 빈 행 제외)을 따른다 —
두 스크립트가 같은 42행을 기준으로 삼아야 recall 수치를 서로 비교할 수 있다.

업로드 후에는 LangSmith UI(smith.langchain.com)에서 이 Dataset을 대상으로
_llm_rerank_sync에 걸린 @traceable 트레이스를 experiment로 비교할 수 있다.
DB/Gemini 호출 없이 CSV → Dataset 변환만 수행하므로 실행 비용은 없다.
"""

import csv
import sys
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")
GOLDEN_PATH = REPO_ROOT / "curriculum-search-engine" / "RS-005_골든셋.csv"
ANSWER_COL = "chunk_id"

DATASET_NAME = "curriculum-search-golden-set"
DATASET_DESCRIPTION = (
    "RS-005 골든셋(42행) — AI 개념 → 대응 성취기준 코드. "
    "A2 curriculum-search-engine 리랭킹 품질 실험용(개인 프로젝트, 팀 공용 아님)."
)


def load_answered_rows() -> list[dict]:
    with open(GOLDEN_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r[ANSWER_COL].strip()]


def main() -> None:
    rows = load_answered_rows()
    if not rows:
        print(f"업로드할 행이 없습니다: {GOLDEN_PATH}", file=sys.stderr)
        sys.exit(1)

    client = Client()

    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
        print(f"기존 Dataset 재사용: {dataset.id} ({DATASET_NAME})")
    else:
        dataset = client.create_dataset(dataset_name=DATASET_NAME, description=DATASET_DESCRIPTION)
        print(f"Dataset 생성: {dataset.id} ({DATASET_NAME})")

    inputs = [
        {
            "ai_개념": r["ai_개념"],
            "개념_정의_초안": r["개념_정의_초안"],
            "target_grade": int(r["target_grade"]),
        }
        for r in rows
    ]
    outputs = [
        {
            "chunk_id": r["chunk_id"].strip(),
            "성취기준_원문": r["성취기준_원문"],
        }
        for r in rows
    ]

    client.create_examples(inputs=inputs, outputs=outputs, dataset_id=dataset.id)
    print(f"{len(rows)}개 example 업로드 완료 → {dataset.url}")


if __name__ == "__main__":
    main()
