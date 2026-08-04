from .schema import CurriculumChunk, SearchQuery

RERANK_SYSTEM_PROMPT = """당신은 초등학교 교육과정 전문가입니다.
주어진 AI 개념을 특정 학년 학생에게 설명할 때, 그 학생이 이미 배운 교육과정 성취기준 중
어떤 것을 비유나 예시로 연결할 수 있는지 판단하는 것이 당신의 역할입니다.

같은 단어가 후보 텍스트에 등장한다고 해서 관련 있다고 판단하지 마세요.
반대로 단어가 겹치지 않아도 개념적으로 진짜 연결되면 선택하세요.
후보 중 어느 것도 진짜로 관련이 없으면 억지로 채우지 말고 빈 리스트를 반환하세요."""


def build_rerank_prompt(query: SearchQuery, candidates: list[CurriculumChunk], top_k: int) -> str:
    candidate_lines = []
    for chunk in candidates:
        inquiry = ", ".join(chunk.inquiry_activities) if chunk.inquiry_activities else "-"
        candidate_lines.append(
            f"[{chunk.chunk_id}]\n"
            f"  과목/학년군: {chunk.subject.value} / {chunk.grade_band.value}\n"
            f"  단원: {chunk.unit_name} ({chunk.domain})\n"
            f"  핵심 아이디어: {chunk.core_idea}\n"
            f"  성취기준: {chunk.achievement_text}\n"
            f"  해설: {chunk.explanation}\n"
            f"  탐구활동: {inquiry}"
        )
    candidates_block = "\n\n".join(candidate_lines)

    return f"""{RERANK_SYSTEM_PROMPT}

## AI 개념
- 이름: {query.concept_name}
- 정의: {query.concept_definition}
- 대상 학년: {query.target_grade}학년

## 후보 성취기준 (chunk_id 기준)
{candidates_block}

## 지시사항
1. 위 후보 중 이 AI 개념의 비유/설명 근거로 실제 사용할 수 있는 성취기준만 골라주세요.
2. 관련성이 높은 순서대로 최대 {top_k}개까지 반환하세요. 관련된 후보가 {top_k}개보다 적으면 그만큼만 반환하세요.
3. 각 항목마다 왜 이 성취기준을 골랐는지 한국어 한 문장 근거(reasoning)를 반드시 붙이세요.
4. chunk_id는 반드시 위 후보 목록에 있는 값만 정확히 그대로 사용하세요.
5. 진짜로 관련된 후보가 하나도 없으면 matches를 빈 리스트로 반환하세요.
"""
