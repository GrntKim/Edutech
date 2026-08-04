import asyncio
import os
import re

import google.generativeai as genai
import numpy as np
import psycopg
from pgvector.psycopg import register_vector_async
from pydantic import BaseModel
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from .prompts import build_rerank_prompt
from .schema import GRADE_TO_BANDS, CurriculumChunk, GradeBand, SearchQuery, SearchResult

EMBEDDING_MODEL = "jhgan/ko-sroberta-multitask"
# "gemini-2.5-flash" 고정 버전은 신규 발급 API 키에서 404로 막혀있어(2026-08 기준),
# 모델 세대가 바뀌어도 계속 최신 flash 모델을 가리키는 alias를 사용한다.
RERANK_MODEL = "gemini-flash-latest"

# grade_band 필터만으로는 최대 262개 청크가 그대로 통과한다(GRADE_TO_BANDS가 누적 구조라
# 5~6학년 질의는 전체 코퍼스와 동일). 청크 하나당 평균 900자 안팎이라 전부 LLM에 넘기면
# 입력 토큰이 10만 개 수준까지 올라가 NFR-002-1(2초 이내 응답)을 지키기 어렵다.
# dense+sparse RRF 융합 결과 상위 이 개수만 LLM 리랭킹에 넘긴다.
# 20→25 상향을 실측(2026-08-04, eval_poolsize_latency.py, 골든셋 18행, 동일 모델)했으나
# Recall이 오히려 61.11%→50.00%로 하락, 평균 지연도 2.75s→4.87s로 악화되어 20으로 되돌림.
# 원인: 풀을 늘리면 dense+sparse RRF 융합에서 정답이 후보에 들어올 확률(top-N 생존율)은
# 15/18→16/18로 개선되지만, LLM 리랭커가 늘어난 후보(distractor) 사이에서 정답을 고르는
# 정확도가 같이 떨어져 순효과가 마이너스였음. "후보 풀 생존율"만으로 크기를 늘리는 결정을
# 하면 안 되고, LLM-in-the-loop 실측이 필요하다는 교훈. 상세: RS-006 §8.7.
CANDIDATE_POOL_SIZE = 20
RRF_K = 60

_model: SentenceTransformer | None = None

_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")

_FETCH_BAND_SQL = """
    SELECT
        chunk_id, subject, grade_band, unit_name, domain, core_idea,
        achievement_code, achievement_text, explanation, inquiry_activities,
        source_page, embedding
    FROM curriculum_chunks
    WHERE grade_band = ANY(%(grade_bands)s)
"""


class CurriculumSearchError(RuntimeError):
    pass


class _RerankMatch(BaseModel):
    chunk_id: str
    reasoning: str


class _RerankResponse(BaseModel):
    matches: list[_RerankMatch]


def resolve_grade_bands(target_grade: int) -> list[GradeBand]:
    try:
        return list(GRADE_TO_BANDS[target_grade])
    except KeyError:
        raise CurriculumSearchError(f"지원하지 않는 학년입니다: {target_grade}")


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


async def embed_text(text: str) -> list[float]:
    try:
        model = await asyncio.to_thread(_get_model)
        embedding = await asyncio.to_thread(model.encode, text)
    except Exception as exc:
        raise CurriculumSearchError(f"임베딩 생성 실패: {exc}") from exc
    return embedding.tolist()


def embedding_source_text(chunk: CurriculumChunk) -> str:
    return f"{chunk.unit_name} {chunk.core_idea} {chunk.achievement_text} {chunk.explanation}".strip()


def _row_to_chunk(row: tuple) -> tuple[CurriculumChunk, np.ndarray]:
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
        embedding,
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
    return chunk, np.asarray(embedding, dtype=float)


def _cosine_similarities(query_embedding: list[float], chunk_embeddings: list[np.ndarray]) -> list[float]:
    query = np.asarray(query_embedding, dtype=float)
    query_norm = query / (np.linalg.norm(query) or 1.0)
    similarities = []
    for embedding in chunk_embeddings:
        embedding_norm = embedding / (np.linalg.norm(embedding) or 1.0)
        similarities.append(float(np.dot(query_norm, embedding_norm)))
    return similarities


def _bigram_tokens(text: str) -> list[str]:
    """형태소 분석기 없이 한국어를 다루기 위한 음절 bigram 토크나이저."""
    tokens: list[str] = []
    for word in _TOKEN_RE.findall(text):
        if len(word) < 2:
            tokens.append(word)
        else:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


def _sparse_scores(query_text: str, chunk_texts: list[str]) -> list[float]:
    corpus = [_bigram_tokens(text) for text in chunk_texts]
    bm25 = BM25Okapi(corpus)
    return list(bm25.get_scores(_bigram_tokens(query_text)))


def _reciprocal_rank_fusion(*score_lists: list[float], k: int = RRF_K) -> list[float]:
    n = len(score_lists[0])
    fused = [0.0] * n
    for scores in score_lists:
        order = sorted(range(n), key=lambda i: scores[i], reverse=True)
        for rank, idx in enumerate(order):
            fused[idx] += 1.0 / (k + rank + 1)
    return fused


def _get_gemini_model() -> genai.GenerativeModel:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    return genai.GenerativeModel(RERANK_MODEL)


async def _llm_rerank(query: SearchQuery, candidates: list[CurriculumChunk], top_k: int) -> _RerankResponse:
    prompt = build_rerank_prompt(query, candidates, top_k)
    try:
        model = await asyncio.to_thread(_get_gemini_model)
        response = await asyncio.to_thread(
            model.generate_content,
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=_RerankResponse,
            ),
        )
        return _RerankResponse.model_validate_json(response.text)
    except Exception as exc:
        raise CurriculumSearchError(f"LLM 리랭킹 실패: {exc}") from exc


async def hybrid_search(query: SearchQuery) -> list[SearchResult]:
    grade_bands = resolve_grade_bands(query.target_grade)

    params = {"grade_bands": [band.value for band in grade_bands]}
    try:
        async with await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"]) as conn:
            await register_vector_async(conn)
            async with conn.cursor() as cur:
                await cur.execute(_FETCH_BAND_SQL, params)
                rows = await cur.fetchall()
    except psycopg.OperationalError as exc:
        raise CurriculumSearchError(f"Cloud SQL 연결 실패: {exc}") from exc

    if not rows:
        return []

    chunks: list[CurriculumChunk] = []
    embeddings: list[np.ndarray] = []
    for row in rows:
        chunk, embedding = _row_to_chunk(row)
        chunks.append(chunk)
        embeddings.append(embedding)

    return await search_within_chunks(query, chunks, embeddings)


async def search_within_chunks(
    query: SearchQuery, chunks: list[CurriculumChunk], embeddings: list[np.ndarray]
) -> list[SearchResult]:
    """DB에서 이미 가져온(또는 로컬 캐시에서 불러온) chunks/embeddings에 대해
    dense+sparse RRF 융합과 LLM 리랭킹을 수행한다. DB 연결이 없는 오프라인 평가에서 재사용한다."""
    if not chunks:
        return []

    query_embedding = await embed_text(query.concept_definition)
    dense_scores = _cosine_similarities(query_embedding, embeddings)
    chunk_texts = [embedding_source_text(chunk) for chunk in chunks]
    query_text = f"{query.concept_name} {query.concept_definition}"
    sparse_scores = _sparse_scores(query_text, chunk_texts)

    fused_scores = _reciprocal_rank_fusion(dense_scores, sparse_scores)
    pool_size = min(CANDIDATE_POOL_SIZE, len(chunks))
    top_indices = sorted(range(len(chunks)), key=lambda i: fused_scores[i], reverse=True)[:pool_size]

    candidates = [chunks[i] for i in top_indices]
    dense_by_chunk_id = {chunks[i].chunk_id: dense_scores[i] for i in top_indices}
    candidate_by_id = {chunk.chunk_id: chunk for chunk in candidates}

    rerank = await _llm_rerank(query, candidates, query.top_k)

    results: list[SearchResult] = []
    for rank, match in enumerate(rerank.matches[: query.top_k], start=1):
        chunk = candidate_by_id.get(match.chunk_id)
        if chunk is None:
            continue
        results.append(
            SearchResult(
                chunk=chunk,
                similarity_score=dense_by_chunk_id[chunk.chunk_id],
                rank=rank,
                reasoning=match.reasoning,
            )
        )
    return results
