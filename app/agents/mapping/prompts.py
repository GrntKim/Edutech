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
11. Write mapping_reason and analogy in Korean. All other output stays as specified below.
12. mapping_reason is passed downstream to the lesson-generation agent, which uses it as the
    logical backbone of the entire lesson. Do not write a generic justification like "similarity
    score is high." State concretely which activity in this chunk (inquiry_activities or
    achievement_text) corresponds to which part of the AI concept (core_mechanism or
    key_operations).
13. analogy must contain three parts, in order, and each part must be identifiable in the text:
    (1) what the student concretely does in the curriculum activity,
    (2) the principle that activity reveals,
    (3) HOW the AI mechanically performs that same principle — not just "AI does this too."
    Part 3 is the point of the analogy: describe the AI's mechanical processing at a level an
    elementary student can grasp (e.g. "computers have no eyes, so they must receive the criteria as numbers").
    An analogy that ends at "AI also classifies things" without explaining the mechanism fails this rule.
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

Return JSON only.

"analogy" must follow Rule 10:
student activity -> discovered principle -> AI's mechanical process,
in Korean, as one natural flowing explanation.

"mapping_reason" must follow Rule 12:
explain concretely which curriculum activity corresponds to which part
of the AI concept, rather than giving a generic similarity-based reason.

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