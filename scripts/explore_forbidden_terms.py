"""D(REQ-005) 금지 용어 큐레이션 지원 스크립트. 커밋용 아님, 판단 근거 확보용.

학년군 차집합으로 나온 후보를 필터링(길이·한글·용언/부사·빈도)해도 수백 개
규모라 사람이 그대로 훑기 어렵다. 학년군별로 상위 80개(빈도 내림차순)만
예문과 함께 뽑아 O/X로 확정 목록을 만들 수 있게 출력한다.

- G3_4 전용: G3_4에는 등장하지만 G1_2에는 없는 어휘
- G5_6 전용: G5_6에는 등장하지만 G1_2·G3_4에는 없는 어휘

D는 이제 이 차집합을 실시간으로 파생하지 않는다(`app/agents/validate/logic.py`
"판정 기준" 절 참고) — 결과를 사람이 검수해 `logic._CURATED_FORBIDDEN`에
확정 반영한다. 그래서 어절 분리·조사 제거·빈도 집계는 이 스크립트 안에서만
쓰는 후보 생성 전용 로직으로 자체 보유한다. 다만 "용언/부사가 아닌지"
판정(`_is_valid_forbidden_term`)은 ②(caution_terms) 필터로 `logic.py`에
여전히 남아있으므로 그대로 가져다 쓴다 — 후보 품질 기준이 실제 운영
필터와 어긋나지 않게 하기 위함이다.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from app.agents.validate import logic
from app.lib.db import get_chunks_by_scope
from app.lib.types import CurriculumChunk, GradeBand

_TOP_N = 80
_SNIPPET_LEN = 40
_OUTPUT_PATH = Path(__file__).resolve().parent / "forbidden_candidates.txt"

# 상위 학년군에서 이 횟수 미만으로 등장하면 후보에서 제외한다. 1~2회는
# 활용형 표면 차이가 우연히 하위 학년군과 안 겹친 것이거나, 288건 규모의
# 작은 말뭉치 특유의 우연일 가능성이 커서 3회를 하한으로 잡았다.
_MIN_TERM_FREQUENCY = 3

# 어절 분리용 구두점/기호. 큰따옴표류(‘’“”)까지 포함해 인용부호 잔재를 제거한다.
_SPLIT_RE = re.compile(r"[\s,.·()\[\]{}\"'‘’“”/\\~`!?;:%△]+")

# logic.py의 _JOSA_SUFFIXES와 동일한 근거(형태소 분석기 대체용 근사치).
_JOSA_SUFFIXES = sorted(
    [
        "으로써", "로써", "이라는", "라는", "에서는", "에게서", "부터는",
        "까지는", "이지만", "지만", "이라고", "라고", "이며", "며",
        "으로", "에서", "부터", "까지", "에게", "한테", "이다", "이나",
        "이란", "란", "이랑", "랑", "만큼", "처럼", "보다",
        "은", "는", "이", "가", "을", "를", "의", "와", "과",
        "도", "만", "로", "에", "께", "나",
    ],
    key=len,
    reverse=True,
)


def _strip_trailing_josa(token: str) -> str:
    for josa in _JOSA_SUFFIXES:
        if token.endswith(josa) and len(token) > len(josa):
            return token[: -len(josa)]
    return token


def _tokenize(text: str) -> list[str]:
    raw_tokens = _SPLIT_RE.split(text)
    return [_strip_trailing_josa(t) for t in raw_tokens if t]


def _collect_band_only_candidates(
    scope_with_band: int,
    scope_accessible: int,
    target_band: GradeBand,
) -> list[tuple[str, int, str]]:
    """target_band에만 등장하는(scope_accessible 어휘에는 없는) 후보를 빈도·예문과 함께 뽑는다.

    scope_with_band: target_band를 포함하는 누적 scope(예: G3_4면 4, G5_6면 6).
    scope_accessible: target_band 이전까지의 누적 scope(예: G3_4면 2, G5_6면 4).
    """
    band_chunks = [c for c in get_chunks_by_scope(scope_with_band) if c.grade_band == target_band]
    accessible_chunks = get_chunks_by_scope(scope_accessible)

    accessible_vocab: set[str] = set()
    for chunk in accessible_chunks:
        accessible_vocab.update(_tokenize(chunk.achievement_text))
        accessible_vocab.update(_tokenize(chunk.explanation))

    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}

    def _record(chunk: CurriculumChunk, text: str) -> None:
        for token in _tokenize(text):
            counts[token] += 1
            if token not in examples:
                idx = text.find(token)
                idx = max(idx, 0)
                snippet = text[idx : idx + _SNIPPET_LEN].replace("\n", " ").strip()
                examples[token] = f"[{chunk.unit_name}] {snippet}"

    for chunk in band_chunks:
        _record(chunk, chunk.achievement_text)
        _record(chunk, chunk.explanation)

    candidates = [
        (term, count, examples.get(term, ""))
        for term, count in counts.items()
        if term not in accessible_vocab
        and count >= _MIN_TERM_FREQUENCY
        and logic._is_valid_forbidden_term(term)
    ]
    candidates.sort(key=lambda row: (-row[1], row[0]))
    return candidates[:_TOP_N]


def _format_table(title: str, rows: list[tuple[str, int, str]]) -> str:
    lines = [title, "-" * len(title)]
    lines.append(f"{'':3s}{'용어':<14s}{'빈도':>4s}   실제 등장 문장(앞 {_SNIPPET_LEN}자)")
    for term, count, example in rows:
        lines.append(f"[ ] {term:<14s}{count:>4d}   {example}")
    lines.append(f"\n총 {len(rows)}개 (상위 {_TOP_N}개 제한)")
    return "\n".join(lines)


def main() -> None:
    g34_only = _collect_band_only_candidates(4, 2, GradeBand.G3_4)
    g56_only = _collect_band_only_candidates(6, 4, GradeBand.G5_6)

    g34_table = _format_table("G3_4 전용 어휘 (G1_2에 없음)", g34_only)
    g56_table = _format_table("G5_6 전용 어휘 (G1_2·G3_4에 없음)", g56_only)

    report = f"{g34_table}\n\n\n{g56_table}\n"
    print(report)

    _OUTPUT_PATH.write_text(report, encoding="utf-8")
    print(f"\n저장됨: {_OUTPUT_PATH} — 열어서 각 줄 [ ]에 O/X 표시하며 확정 목록 만들 것")


if __name__ == "__main__":
    main()
