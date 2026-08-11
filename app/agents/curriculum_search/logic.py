import asyncio
import logging
import re
import threading

import numpy as np
from langsmith import traceable
from pydantic import BaseModel
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from app.lib.db import DatabaseError, get_connection
from app.lib.gemini import GeminiError, generate_structured
from .prompts import build_rerank_prompt
from .schema import GRADE_TO_BANDS, CurriculumChunk, GradeBand, SearchQuery, SearchResult

logger = logging.getLogger(__name__)

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

# 2026-08-10: 동일 입력에 대해 실행마다 리랭킹 결과가 달라지는 문제(L1-a/L2-a)의
# 완화 시도로 고정 seed를 도입. SDK 문서상 "최선 노력" 재현이라 완전한 결정성은
# 보장되지 않지만, 매번 임의 시드를 쓰던 기존 상태보다는 변동을 줄일 것으로 기대.
# 값 자체엔 의미 없음 — 그냥 고정된 정수.
RERANK_SEED = 42

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
    """chunk_band는 query_band보다 상위 학년군일 수 없다는 불변식을 전제한다 —
    GRADE_TO_BANDS가 누적 구조라 target_grade의 후보는 항상 query_band 이하의
    학년군에서만 온다(RS-006 §9.5). 이 불변식이 깨졌다면(distance<0) 호출부가
    학년군 필터링을 빠뜨린 신호이므로, 조용히 fallback하지 않고 즉시 실패시켜
    잘못된 가중치가 결과에 섞여 들어가는 걸 막는다."""
    distance = _GRADE_BAND_ORDER.index(query_band) - _GRADE_BAND_ORDER.index(chunk_band)
    if distance < 0:
        raise ValueError(
            f"grade_band 불변식 위반: chunk_band={chunk_band.value}가 "
            f"query_band={query_band.value}보다 상위 학년군입니다. "
            "호출부가 학년군 필터링을 빠뜨렸을 가능성이 있습니다."
        )
    return _GRADE_DISTANCE_WEIGHT.get(distance, _GRADE_DISTANCE_WEIGHT[2])

_model: SentenceTransformer | None = None
# _get_model()은 asyncio.to_thread로 실제 워커 스레드에서 실행되므로(embed_text 참고),
# 동시 요청 시 check-then-set이 스레드 안전하지 않다. asyncio.Lock이 아니라
# threading.Lock을 쓰는 이유도 이 함수가 이벤트 루프가 아닌 워커 스레드에서 돈다는
# 점과 같은 이유다.
_model_lock = threading.Lock()

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
        with _model_lock:
            if _model is None:
                _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


async def embed_text(text: str, *, is_query: bool = False) -> list[float]:
    """is_query=False(기본)면 passage 프리픽스를 적용한다. 질의 임베딩은
    search_within_chunks()에서 is_query=True로 명시적으로 호출한다. 청크를 여러 개
    한 번에 임베딩할 때(ingest_curriculum.py)는 단건 반복 대신 embed_texts()를 쓴다."""
    prefix = (_QUERY_PREFIX if is_query else _PASSAGE_PREFIX).get(EMBEDDING_MODEL, "")
    try:
        model = await asyncio.to_thread(_get_model)
        embedding = await asyncio.to_thread(model.encode, prefix + text)
    except Exception as exc:
        raise CurriculumSearchError(f"임베딩 생성 실패: {exc}") from exc
    return embedding.tolist()


async def embed_texts(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    """embed_text의 배치 버전. SentenceTransformer.encode는 리스트 입력을 한 번의
    forward pass로 처리하므로, 청크마다 별도 asyncio.to_thread 디스패치를 반복하던
    ingest_curriculum.py의 embed_chunks()가 이걸 쓴다."""
    if not texts:
        return []
    prefix = (_QUERY_PREFIX if is_query else _PASSAGE_PREFIX).get(EMBEDDING_MODEL, "")
    try:
        model = await asyncio.to_thread(_get_model)
        embeddings = await asyncio.to_thread(model.encode, [prefix + text for text in texts])
    except Exception as exc:
        raise CurriculumSearchError(f"임베딩 생성 실패: {exc}") from exc
    return [embedding.tolist() for embedding in embeddings]


def _embed_text_sync(text: str, *, is_query: bool = False) -> list[float]:
    """embed_text의 동기 버전 — search_curriculum()의 동기 코어가 쓴다."""
    prefix = (_QUERY_PREFIX if is_query else _PASSAGE_PREFIX).get(EMBEDDING_MODEL, "")
    try:
        model = _get_model()
        embedding = model.encode(prefix + text)
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


# generate_structured()가 API 레벨 실패(타임아웃/429/5xx) 재시도를 자체적으로 수행하는
# 유일한 재시도 계층이다(app/lib/gemini.py 모듈 docstring 참고) — 이 함수 바깥에서
# 다시 재시도를 걸면 이중 재시도로 지연 예산이 무너지므로 여기서는 추가하지 않는다.
# 타임아웃 미설정 시 네트워크 hang이 예외 없이 무한 대기로 이어지는 걸 실측으로 확인함
# (2026-08-04, sweep_grade_weights.py 1행에서 3분+ 무응답) — 60초로 명시.
#
# GeminiError는 CurriculumSearchError로 다시 감싸지 않고 그대로 올린다 — 오케스트레이터의
# 전역 예외 핸들러가 (GeminiError, DatabaseError)를 잡는데, CurriculumSearchError(RuntimeError
# 직속)로 감싸면 이 핸들러를 못 타고 500으로 새어나간다(D 요청, orchestrate.py 통합 이슈).
#
# @traceable은 A2 개인 관찰가능성/eval 실험용이다(LANGSMITH_API_KEY 미설정 시 no-op —
# 팀 공용 app/lib/gemini.py는 건드리지 않음, REQ-006 계약 범위 밖 개인 계측).
@traceable(name="a2_llm_rerank", run_type="chain")
def _llm_rerank_sync(query: SearchQuery, candidates: list[CurriculumChunk], top_k: int) -> _RerankResponse:
    prompt = build_rerank_prompt(query, candidates, top_k)
    try:
        return generate_structured(
            prompt,
            _RerankResponse,
            model=RERANK_MODEL,
            thinking_level=RERANK_THINKING_LEVEL,
            timeout_s=60.0,
            seed=RERANK_SEED,
        )
    except GeminiError:
        logger.warning(f"llm_rerank_failed concept_name={query.concept_name!r}")
        raise


async def _llm_rerank(query: SearchQuery, candidates: list[CurriculumChunk], top_k: int) -> _RerankResponse:
    return await asyncio.to_thread(_llm_rerank_sync, query, candidates, top_k)


def _fetch_band_rows(grade_bands: list[GradeBand]) -> list[tuple]:
    """lib.db.get_connection()은 동기 함수다(팀 공용 계층, psycopg 직접 import 금지 —
    REQ-006 NFR-006-4). 이 함수 자체는 동기이고, search_curriculum()의 동기 코어에서
    직접 호출된다 — async 호출자(hybrid_search)는 search_curriculum 전체를
    asyncio.to_thread로 감싸는 바깥쪽 한 겹으로 블로킹을 처리한다. embedding 컬럼은
    lib.db의 공용 조회 함수(get_chunks_by_scope 등)에 일부러 없음(CurriculumChunk에
    embedding 필드가 없어서) — 벡터가 필요한 A2는 get_connection()으로 직접 커서를 얻어
    자체 SQL(_FETCH_BAND_SQL)을 쓴다(lib/db.py docstring에 이 용례가 명시되어 있음).
    """
    params = {"grade_bands": [band.value for band in grade_bands]}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_FETCH_BAND_SQL, params)
            return cur.fetchall()


def _rank_candidates(
    query: SearchQuery,
    chunks: list[CurriculumChunk],
    embeddings: list[np.ndarray],
    query_embedding: list[float],
) -> tuple[list[CurriculumChunk], dict[str, float]]:
    """dense+sparse+RRF+학년 가중치까지의 순수 계산(I/O 없음). sync/async 양쪽
    (search_within_chunks / search_within_chunks_sync)이 공유한다."""
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
    return candidates, dense_by_chunk_id


def _normalize_chunk_id(chunk_id: str) -> str:
    """실제 chunk_id는 대괄호가 없지만("6실05-04"), 프롬프트가 후보를 보여줄 때
    achievement_code 관례(대괄호 표기, "[6실05-04]")를 연상시키는 형태로 나열하다 보니
    LLM이 드물게 대괄호를 붙여 답한다(2026-08-07 LangSmith 로그 골든셋 42건 중 1건 재현,
    2026-08-10 반복 확인 시 5/5는 정상 — 상시 버그가 아니라 저빈도 샘플링 변동). 그 결과
    실제로는 맞는 답을 골랐는데도 후속 조회가 실패해 조용히 0건으로 소멸했다. 비교 전에
    양쪽을 정규화해 이 케이스를 흡수한다."""
    return chunk_id.strip().strip("[]")


def _assemble_results(
    rerank: _RerankResponse,
    candidates: list[CurriculumChunk],
    dense_by_chunk_id: dict[str, float],
    top_k: int,
) -> list[SearchResult]:
    candidate_by_normalized_id = {_normalize_chunk_id(chunk.chunk_id): chunk for chunk in candidates}
    valid_matches = [
        (candidate_by_normalized_id[normalized], match)
        for match in rerank.matches
        if (normalized := _normalize_chunk_id(match.chunk_id)) in candidate_by_normalized_id
    ]

    results: list[SearchResult] = []
    for rank, (chunk, match) in enumerate(valid_matches[:top_k], start=1):
        results.append(
            SearchResult(
                chunk=chunk,
                similarity_score=dense_by_chunk_id[chunk.chunk_id],
                rank=rank,
                reasoning=match.reasoning,
            )
        )
    return results


def _rows_to_chunks(rows: list[tuple]) -> tuple[list[CurriculumChunk], list[np.ndarray]]:
    chunks: list[CurriculumChunk] = []
    embeddings: list[np.ndarray] = []
    for row in rows:
        chunk, embedding = _row_to_chunk(row)
        chunks.append(chunk)
        embeddings.append(embedding)
    return chunks, embeddings


def search_within_chunks_sync(
    query: SearchQuery, chunks: list[CurriculumChunk], embeddings: list[np.ndarray]
) -> list[SearchResult]:
    """search_within_chunks의 동기 버전. search_curriculum()의 동기 코어가 쓴다."""
    if not chunks:
        return []
    query_embedding = _embed_text_sync(query.concept_definition, is_query=True)
    candidates, dense_by_chunk_id = _rank_candidates(query, chunks, embeddings, query_embedding)
    rerank = _llm_rerank_sync(query, candidates, query.top_k)
    return _assemble_results(rerank, candidates, dense_by_chunk_id, query.top_k)


async def search_within_chunks(
    query: SearchQuery, chunks: list[CurriculumChunk], embeddings: list[np.ndarray]
) -> list[SearchResult]:
    """DB에서 이미 가져온(또는 로컬 캐시에서 불러온) chunks/embeddings에 대해
    dense+sparse RRF 융합과 LLM 리랭킹을 수행한다. DB 연결이 없는 오프라인 평가(eval
    스크립트)에서 재사용하므로, 이 이름·시그니처·async는 그대로 유지한다."""
    if not chunks:
        return []
    query_embedding = await embed_text(query.concept_definition, is_query=True)
    candidates, dense_by_chunk_id = _rank_candidates(query, chunks, embeddings, query_embedding)
    rerank = await _llm_rerank(query, candidates, query.top_k)
    return _assemble_results(rerank, candidates, dense_by_chunk_id, query.top_k)


# DatabaseError는 CurriculumSearchError로 다시 감싸지 않고 그대로 올린다 — 이유는
# _llm_rerank_sync 위 주석과 동일(오케스트레이터의 (GeminiError, DatabaseError) 전역
# 핸들러가 CurriculumSearchError는 못 잡는다, D 요청, orchestrate.py 통합 이슈).
def search_curriculum(query: SearchQuery) -> list[SearchResult]:
    """동기 진입점. hybrid_search 안의 to_thread 호출이 전부 블로킹 작업(모델 인코딩,
    DB 커서, Gemini 호출)을 스레드로 넘기는 용도일 뿐 실제 동시성(gather/create_task)이
    없어서, 동기 호출자(오케스트레이터의 run_pipeline 등)는 이걸 직접 쓰는 게 이벤트 루프
    생성 왕복 오버헤드가 없다(D 요청, orchestrate.py 통합 이슈)."""
    grade_bands = resolve_grade_bands(query.target_grade)

    try:
        rows = _fetch_band_rows(grade_bands)
    except DatabaseError:
        logger.warning(f"curriculum_search_db_failed target_grade={query.target_grade}")
        raise

    if not rows:
        return []

    chunks, embeddings = _rows_to_chunks(rows)
    return search_within_chunks_sync(query, chunks, embeddings)


async def hybrid_search(query: SearchQuery) -> list[SearchResult]:
    """기존 async 호출자 호환용 래퍼. 실제 작업은 동기 코어(search_curriculum)에서
    수행하고, 여기서는 asyncio.to_thread로 감싸기만 한다(D 요청, orchestrate.py 통합
    이슈 — 자세한 배경은 search_curriculum() docstring 참고)."""
    return await asyncio.to_thread(search_curriculum, query)
