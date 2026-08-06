# 소유: D(REQ-005)
"""D 2단계(비유-개념 논리 검증) 프롬프트 골격. logic.py에서 아직 호출하지 않는다.

향후 사용 지점: `app.lib.gemini.generate_structured(prompt=..., response_schema=LogicCheckResult,
thinking_level="low")`. `temperature`는 gemini.py에서 deprecated이므로 쓰지 않는다.
"""

from __future__ import annotations

from app.agents.validate.schema import LogicCheckResult  # noqa: F401  (향후 generate_structured 호출부에서 사용)

SYSTEM_INSTRUCTION_STAGE2 = """\
당신은 초등 AI 교육과정 감수자입니다. 주어진 교안의 비유(analogy)가 원래 AI 개념의 \
핵심 동작 원리(core_mechanism)를 왜곡하지 않는지 판단합니다. 학년 수준에 맞게 단순화한 \
것은 문제가 아니지만, 개념 자체를 다른 것으로 바꿔치기하거나 핵심 원리와 모순되는 \
설명은 문제입니다.
"""


def build_logic_check_prompt(core_mechanism: str, lesson_plan: dict) -> str:
    """비유-개념 대응 관계 검증용 프롬프트를 만든다(현재 미사용, 골격만).

    core_mechanism: A1(StructuredConcept)이 만든 AI 개념의 핵심 동작 원리 원문.
    lesson_plan: C가 생성한 교안(dict). 비유가 등장하는 도입/전개 단계 위주로
    발췌해 넘기는 형태로 다듬을 예정(현재는 전체를 그대로 넘기는 자리표시).
    """
    return (
        f"{SYSTEM_INSTRUCTION_STAGE2}\n\n"
        f"[AI 개념 핵심 원리]\n{core_mechanism}\n\n"
        f"[교안]\n{lesson_plan}\n\n"
        "이 교안의 비유가 핵심 원리를 왜곡하는지 판단해 주세요."
    )
