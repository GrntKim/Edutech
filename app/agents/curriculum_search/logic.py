import asyncio
import os

import google.generativeai as genai
import psycopg
from pgvector.psycopg import register_vector_async

from .schema import GRADE_TO_BANDS, CurriculumChunk, GradeBand, SearchQuery, SearchResult

EMBEDDING_MODEL = "models/text-embedding-004"

_SEARCH_SQL = """
    SELECT
        chunk_id, subject, grade_band, unit_name, domain, core_idea,
        achievement_code, achievement_text, explanation, inquiry_activities,
        source_page,
        embedding <=> %(query_embedding)s AS distance
    FROM curriculum_chunks
    WHERE grade_band = ANY(%(grade_bands)s)
    ORDER BY distance
    LIMIT %(top_k)s
"""


class CurriculumSearchError(RuntimeError):
    pass


def resolve_grade_bands(target_grade: int) -> list[GradeBand]:
    try:
        return list(GRADE_TO_BANDS[target_grade])
    except KeyError:
        raise CurriculumSearchError(f"지원하지 않는 학년입니다: {target_grade}")


async def embed_text(text: str) -> list[float]:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    try:
        result = await asyncio.to_thread(
            genai.embed_content, model=EMBEDDING_MODEL, content=text
        )
    except Exception as exc:
        raise CurriculumSearchError(f"임베딩 생성 실패: {exc}") from exc
    return result["embedding"]


def _row_to_result(row: tuple, rank: int) -> SearchResult:
    (
        chunk_id,
        subject,
        grade_band,
        unit_name,
        domain,
        core_idea,
        achievement_code,
        achievement_text,
        explanation,
        inquiry_activities,
        source_page,
        distance,
    ) = row
    chunk = CurriculumChunk(
        chunk_id=chunk_id,
        subject=subject,
        grade_band=grade_band,
        unit_name=unit_name,
        domain=domain,
        core_idea=core_idea,
        achievement_code=achievement_code,
        achievement_text=achievement_text,
        explanation=explanation,
        inquiry_activities=inquiry_activities or [],
        source_page=source_page,
    )
    return SearchResult(chunk=chunk, similarity_score=1 - distance, rank=rank)


async def hybrid_search(query: SearchQuery) -> list[SearchResult]:
    grade_bands = resolve_grade_bands(query.target_grade)
    query_embedding = await embed_text(query.concept_definition)

    params = {
        "query_embedding": query_embedding,
        "grade_bands": [band.value for band in grade_bands],
        "top_k": query.top_k,
    }

    try:
        async with await psycopg.AsyncConnection.connect(
            os.environ["DATABASE_URL"]
        ) as conn:
            await register_vector_async(conn)
            async with conn.cursor() as cur:
                await cur.execute(_SEARCH_SQL, params)
                rows = await cur.fetchall()
    except psycopg.OperationalError as exc:
        raise CurriculumSearchError(f"Cloud SQL 연결 실패: {exc}") from exc

    return [_row_to_result(row, rank) for rank, row in enumerate(rows, start=1)]
