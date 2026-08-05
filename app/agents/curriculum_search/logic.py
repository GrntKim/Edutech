import asyncio
import re

import numpy as np
from pydantic import BaseModel
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from app.lib.db import DatabaseError, get_connection
from app.lib.gemini import GeminiError, generate_structured
from .prompts import build_rerank_prompt
from .schema import GRADE_TO_BANDS, CurriculumChunk, GradeBand, SearchQuery, SearchResult

# ko-sroberta-multitask를 §8.8에서 "최종 확정"했으나, 그 결정은 당시 리랭커(lite 모델
# 대체 상태)를 기준으로 한 것이었다. 리랭커를 gemini-3.6-flash로 정정한 뒤(§9.12) 4개
# 모델을 다시 end-to-end 실측(§9.18)한 결과 KoE5가 Recall 78.57%→83.33%(+4.76%p)로
# 가장 높고 지연도 비슷해(3.10s) 교체 — RS-006 §9.18 참고.
EMBEDDING_MODEL = "nlpai-lab/KoE5"

# E5 계열(KoE5, multilingual-e5-large)은 query:/passage: 프리픽스를 안 붙이면 성능이
# 크게 떨어진다고 알려져 있다(cache_embeddings.py에서도 동일하게 처리). 이 딕셔너리에
# 없는 모델(예: ko-sroberta-multitask, bge-m3)은 프리픽스 없이 원문 그대로 인코딩한다.
_QUERY_PREFIX = {
    "nlpai-lab/KoE5": "query: ",
    "intfloat/multilingual-e5-large": "query: ",
}
_PASSAGE_PREFIX = {
    "nlpai-lab/KoE5": "passage: ",
    "intfloat/multilingual-e5-large": "passage: ",
}
# 팀 표준 모델로 gemini-3.6-flash 고정(alias인 "-latest"는 예고 없이 세대가 바뀌어
# NFR-001-6 재현성 요구와 충돌하므로 사용하지 않는다). Gemini 호출은 전부
# app/lib/gemini.py의 generate_structured() 경유(팀 규약 — google.genai 직접 호출 금지).
RERANK_MODEL = "gemini-3.6-flash"

# gemini-3.6-flash부터 temperature/top_p/top_k가 deprecated되어 무시되고, 대신
# thinking_level(minimal/low/medium/high)로 추론량을 조절한다. 기본값 medium은 지연이 커서
# (실측: 42행 전량 리랭킹 시 평균 6.59s, NFR-002-1의 2초를 42/42 위반, 2026-08-04, 단
# 이 실측은 alias 모델+thinking_budget 기준이라 잠정치) NFR-002-1(2초) 목표를 위해 low로
# 명시한다. thinking_level은 추론 "강도"이지 temperature처럼 무작위성을 제어하지는 않는다 —
# 리랭킹 비결정성 자체는 여전히 남음(§4 리스크 "LLM 리랭킹의 비결정성" 참고).
RERANK_THINKING_LEVEL = "low"

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

# grade_band 누적 구조 때문에 target_grade 5~6 질의는 필터링 효과가 거의 없어(§RS-006 9.5)
# 코퍼스 전체가 무차별 후보가 된다. G1_2 청크와 G5_6 청크가 RRF 융합에서 동등하게 취급되는 걸
# 막기 위해, 질의의 "본 학년군"(target_grade가 실제로 속한 밴드)과 청크 자신의 grade_band가
# 몇 단계 떨어져 있는지에 따라 융합 스코어에 가중치를 곱한다.
# 팀 제안값(1.0/0.6/0.3)을 포함해 4세트를 골든셋 42행 end-to-end로 스윕 실측한 결과
# (sweep_grade_weights.py, §RS-006 9.8), 1.0/0.8/0.6(완만한 페널티)이 Recall 64.29%로 최고 —
# 더 세게 누른 1.0/0.4/0.15(57.14%)보다도 낮은 페널티가 더 나았다. 골든셋 정답 중 하위
# 학년군(G3_4) 청크가 실제 정답인 경우가 꽤 있어, 페널티가 강할수록 그 진짜 정답까지
# 같이 밀어내는 역효과가 더 컸던 것으로 보인다("세게 누를수록 좋다"는 가정은 틀렸음).
_GRADE_BAND_ORDER = [GradeBand.G1_2, GradeBand.G3_4, GradeBand.G5_6]
_GRADE_DISTANCE_WEIGHT = {0: 1.0, 1: 0.8, 2: 0.6}


def _grade_band_weight(chunk_band: GradeBand, query_band: GradeBand) -> float:
    distance = _GRADE_BAND_ORDER.index(query_band) - _GRADE_BAND_ORDER.index(chunk_band)
    return _GRADE_DISTANCE_WEIGHT.get(distance, _GRADE_DISTANCE_WEIGHT[2])

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


async def embed_text(text: str, *, is_query: bool = False) -> list[float]:
    """is_query=False(기본)면 passage 프리픽스를 적용한다 — ingest_curriculum.py의
    embed_chunks()는 청크(passage) 임베딩이라 기본값을 그대로 쓴다. 질의 임베딩은
    search_within_chunks()에서 is_query=True로 명시적으로 호출한다."""
    prefix = (_QUERY_PREFIX if is_query else _PASSAGE_PREFIX).get(EMBEDDING_MODEL, "")
    try:
        model = await asyncio.to_thread(_get_model)
        embedding = await asyncio.to_thread(model.encode, prefix + text)
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


async def _llm_rerank(query: SearchQuery, candidates: list[CurriculumChunk], top_k: int) -> _RerankResponse:
    prompt = build_rerank_prompt(query, candidates, top_k)
    try:
        # generate_structured()가 API 레벨 실패(타임아웃/429/5xx) 재시도를 자체적으로 수행하는
        # 유일한 재시도 계층이다(app/lib/gemini.py 모듈 docstring 참고) — 이 함수 바깥에서
        # 다시 재시도를 걸면 이중 재시도로 지연 예산이 무너지므로 여기서는 추가하지 않는다.
        # 타임아웃 미설정 시 네트워크 hang이 예외 없이 무한 대기로 이어지는 걸 실측으로 확인함
        # (2026-08-04, sweep_grade_weights.py 1행에서 3분+ 무응답) — 60초로 명시.
        return await asyncio.to_thread(
            generate_structured,
            prompt,
            _RerankResponse,
            model=RERANK_MODEL,
            thinking_level=RERANK_THINKING_LEVEL,
            timeout_s=60.0,
        )
    except GeminiError as exc:
        raise CurriculumSearchError(f"LLM 리랭킹 실패: {exc}") from exc


def _fetch_band_rows(grade_bands: list[GradeBand]) -> list[tuple]:
    """lib.db.get_connection()은 동기 함수라(팀 공용 계층, psycopg 직접 import 금지 —
    REQ-006 NFR-006-4), 블로킹 호출을 asyncio.to_thread로 감싸 쓴다. embedding 컬럼은
    lib.db의 공용 조회 함수(get_chunks_by_scope 등)에 일부러 없음(CurriculumChunk에
    embedding 필드가 없어서) — 벡터가 필요한 A2는 get_connection()으로 직접 커서를 얻어
    자체 SQL(_FETCH_BAND_SQL)을 쓴다(lib/db.py docstring에 이 용례가 명시되어 있음).
    """
    params = {"grade_bands": [band.value for band in grade_bands]}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_FETCH_BAND_SQL, params)
            return cur.fetchall()


async def hybrid_search(query: SearchQuery) -> list[SearchResult]:
    grade_bands = resolve_grade_bands(query.target_grade)

    try:
        rows = await asyncio.to_thread(_fetch_band_rows, grade_bands)
    except DatabaseError as exc:
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

    query_embedding = await embed_text(query.concept_definition, is_query=True)
    dense_scores = _cosine_similarities(query_embedding, embeddings)
    chunk_texts = [embedding_source_text(chunk) for chunk in chunks]
    query_text = f"{query.concept_name} {query.concept_definition}"
    sparse_scores = _sparse_scores(query_text, chunk_texts)

    fused_scores = _reciprocal_rank_fusion(dense_scores, sparse_scores)
    query_band = resolve_grade_bands(query.target_grade)[-1]
    weighted_scores = [
        score * _grade_band_weight(chunk.grade_band, query_band)
        for chunk, score in zip(chunks, fused_scores)
    ]
    pool_size = min(CANDIDATE_POOL_SIZE, len(chunks))
    top_indices = sorted(range(len(chunks)), key=lambda i: weighted_scores[i], reverse=True)[:pool_size]

    candidates = [chunks[i] for i in top_indices]
    dense_by_chunk_id = {chunks[i].chunk_id: dense_scores[i] for i in top_indices}
    candidate_by_id = {chunk.chunk_id: chunk for chunk in candidates}

    rerank = await _llm_rerank(query, candidates, query.top_k)

    valid_matches = [match for match in rerank.matches if match.chunk_id in candidate_by_id]

    results: list[SearchResult] = []
    for rank, match in enumerate(valid_matches[: query.top_k], start=1):
        chunk = candidate_by_id[match.chunk_id]
        results.append(
            SearchResult(
                chunk=chunk,
                similarity_score=dense_by_chunk_id[chunk.chunk_id],
                rank=rank,
                reasoning=match.reasoning,
            )
        )
    return results
