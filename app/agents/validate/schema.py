# 소유: D(REQ-005)
"""D 2단계(비유-개념 논리 검증) 응답 스키마 골격. 아직 호출되지 않는다.

1단계(logic.py의 금지 용어 매칭)만으로도 REQ-005 VALID-001 기본 요구는
충족한다. 2단계는 "시간 여유 시" 조건이며, C가 만든 비유(analogy)가
`core_mechanism`(A1)이 설명하는 AI 개념을 왜곡하지 않는지 LLM으로
판단하는 용도다. `app/lib/gemini.py`의 `generate_structured()`를
경유하는 것을 전제로 스키마만 먼저 정의해 둔다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LogicCheckResult(BaseModel):
    """2단계 LLM 논리 검증 결과. generate_structured()의 response_schema로 쓰일 예정."""

    is_consistent: bool
    # core_mechanism과 어긋나는 부분이 있으면 구체적으로 무엇이 왜 문제인지 적는다.
    issues: list[str] = Field(default_factory=list)
    reasoning: str
