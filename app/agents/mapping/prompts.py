"""
REQ-003 Mapping Agent Prompt

LLM Prompt Builder
"""

from app.lib.types import StructuredConcept, SearchResult

SYSTEM_PROMPT = """
You are an expert in mapping AI concepts to the Korean elementary curriculum.

Your task is to choose ONLY ONE curriculum chunk from the given candidates.

Rules:

1. Never create a new chunk_id.
2. You MUST choose one of the provided candidates.
3. Do NOT perform additional search.
4. Use A2 reasoning only as reference.
5. Select the curriculum chunk that best explains the AI concept.
6. Generate:
   - chunk_id
   - mapping_reason
   - analogy
   - criteria_scores
7. Do NOT generate confidence.
8. Every criteria score must be between 0.0 and 1.0.
9. Return JSON only.
10. Write the "analogy" field in Korean, as ONE natural flowing explanation (not
    labeled/bulleted) that connects exactly three parts in this order:
    (1) what the student actually does in a real activity of the chosen curriculum
        unit, (2) the principle the student discovers through that activity, and
        (3) how AI carries out the same principle mechanically. For part (3), do not
        stop at a vague statement like "AI does this too" — concretely describe what
        input AI receives and what feature/criterion/calculation it uses to make its
        judgment, at a level an elementary student can understand.
"""


def format_candidate(result: SearchResult) -> str:
    chunk = result.chunk

    return f"""
Chunk ID: {chunk.chunk_id}

Subject: {chunk.subject}
Grade Band: {chunk.grade_band}

Unit Name:
{chunk.unit_name}

Core Idea:
{chunk.core_idea}

Achievement Code:
{chunk.achievement_code}

Achievement Text:
{chunk.achievement_text}

Explanation:
{chunk.explanation}

Inquiry Activities:
{", ".join(chunk.inquiry_activities)}

Similarity Score:
{result.similarity_score:.3f}

Search Reasoning:
{result.reasoning or "N/A"}
"""


def build_user_prompt(
    concept: StructuredConcept,
    search_results: list[SearchResult],
    target_grade: int,
) -> str:

    candidates = "\n\n".join(
        [
            f"========== Candidate {i+1} ==========\n{format_candidate(r)}"
            for i, r in enumerate(search_results)
        ]
    )

    return f"""
# Target Grade

{target_grade}

# AI Concept

Concept Name:
{concept.concept_name}

Definition:
{concept.one_line_definition}

Core Mechanism:
{concept.core_mechanism}

Key Operations:
{", ".join(concept.key_operations)}

Prerequisite Ideas:
{", ".join(concept.prerequisite_ideas)}

Everyday Examples:
{", ".join(concept.everyday_examples)}

# Candidate Curriculum Chunks

{candidates}

# Evaluation Criteria

Evaluate candidates using:

1. semantic_similarity
2. educational_fit
3. student_level
4. analogy
5. achievement_alignment

# Output Format

Return JSON only. "analogy" must follow Rule 10 (student activity -> discovered
principle -> AI's mechanical process, in Korean, one flowing explanation), for example:

  "학생이 동물을 다리 개수와 사는 곳 같은 특징으로 나누어 무리를 짓다 보면, 비슷한 특징을 \
가진 대상끼리 묶인다는 분류 원리를 발견하게 됩니다. AI도 이와 똑같이, 데이터마다 특징값을 \
입력받아 정해진 기준(예: 특징값 사이의 거리)으로 얼마나 비슷한지 계산한 뒤, 가장 가까운 \
것끼리 자동으로 묶는 방식으로 분류를 수행합니다."

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