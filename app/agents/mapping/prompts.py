from app.lib.types import StructuredConcept, SearchResult, PipelineContext


SYSTEM_PROMPT = """
당신은 초·중학교 AI 교육 전문가입니다.

주어진 AI 개념과 교육과정 후보 중 가장 적절한 단원 하나를 선택하세요.

규칙
1. 반드시 후보 목록 안에서만 선택합니다.
2. 의미 유사성, 교육적 적합성, 학생 이해도, 비유 가능성, 성취기준 정합성을 종합적으로 평가합니다.
3. 대상 학년(target_grade)에 맞는 설명과 비유를 생성합니다.
4. JSON 이외의 문장은 출력하지 않습니다.
"""


def build_prompt(
    concept: StructuredConcept,
    candidates: list[SearchResult],
    context: PipelineContext,
) -> str:
    """LLM에 전달할 User Prompt 생성"""

    candidate_text = []

    for candidate in candidates:
        chunk = candidate.chunk

        candidate_text.append(
            f"""
[후보 {candidate.rank}]
chunk_id: {chunk.chunk_id}
과목: {chunk.subject.value}
학년군: {chunk.grade_band.value}
단원명: {chunk.unit_name}
영역: {chunk.domain}

핵심 아이디어:
{chunk.core_idea}

성취기준:
{chunk.achievement_text}

설명:
{chunk.explanation}

탐구활동:
{", ".join(chunk.inquiry_activities)}

검색 근거:
{candidate.reasoning or "없음"}
"""
        )

    return f"""
{SYSTEM_PROMPT}

========================

AI 개념

이름:
{concept.concept_name}

카테고리:
{concept.category.value}

한 줄 정의:
{concept.one_line_definition}

핵심 원리:
{concept.core_mechanism}

핵심 동작:
{", ".join(concept.key_operations)}

선행 개념:
{", ".join(concept.prerequisite_ideas)}

생활 예시:
{", ".join(concept.everyday_examples)}

학생에게 노출하면 안 되는 용어:
{", ".join(concept.caution_terms)}

========================

대상 학년:
{context.target_grade}

과목 힌트:
{context.subject_hint.value if context.subject_hint else "없음"}

========================

교육과정 후보

{''.join(candidate_text)}

========================

반드시 아래 JSON 형식으로만 응답하세요.

{{
    "chunk_id": "...",
    "mapping_reason": "...",
    "analogy": "...",
    "criteria_scores": {{
        "semantic_similarity": 0.0,
        "educational_fit": 0.0,
        "student_level": 0.0,
        "analogy": 0.0,
        "achievement_alignment": 0.0
    }}
}}
"""