# 소유: E(REQ-006)
"""Cloud SQL(PostgreSQL) 공통 접근 계층.

DB가 필요한 모든 에이전트는 이 모듈을 경유해서만 접속한다
(팀 규약 — psycopg 직접 import 금지).

커넥션은 요청마다 획득·반납한다(모듈 전역 캐싱 금지) — Cloud Run 인스턴스가
유휴 상태로 오래 있으면 커넥션이 끊기기 때문이다. 커넥션 풀은 1단계 스코프에서
과하므로 쓰지 않되, 나중에 풀로 바꿔도 호출부가 안 깨지도록 get_connection()의
시그니처(컨텍스트 매니저, Connection 반환)는 그대로 유지되게 설계했다.

이 모듈은 범용 조회만 담당한다. "무엇을 금지어로 볼지"(D), "원문에서 무엇을
학습목표로 뽑을지"(C) 같은 판정·가공 로직은 절대 넣지 않는다 — 그건 각
에이전트의 정책이다.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector
from psycopg import Connection, Cursor
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from datetime import datetime, timedelta
from uuid import UUID

from app.lib.types import (
    CurriculumChunk,
    GradeBand,
    LessonRequest,
    Session,
    Subject,
    User,
    grade_to_bands,
)

logger = logging.getLogger(__name__)

TABLE_NAME = "curriculum_chunks"

# CurriculumChunk(Pydantic)에는 embedding 필드가 없다 — 벡터를 전달 객체에 실으면
# 직렬화 비용만 발생하므로 조회 컬럼에서 항상 제외한다. 벡터가 필요한 A2는 별도 조회한다.
_CHUNK_COLUMNS = (
    "chunk_id, subject, grade_band, unit_name, domain, core_idea, "
    "achievement_code, achievement_text, explanation, inquiry_activities, source_page"
)


class DatabaseError(Exception):
    """DB 접근 관련 모든 예외의 기반 클래스."""


class DatabaseConfigError(DatabaseError):
    """환경변수 누락 등 설정 문제."""


class DatabaseConnectionError(DatabaseError):
    """접속 실패(접속 자체 또는 pgvector 확장 등록 실패)."""


class EmailAlreadyExistsError(DatabaseError):
    """이미 가입된 이메일로 회원가입 시도."""


def _resolve_conninfo() -> dict[str, str | int]:
    """.env → 환경변수 순으로 접속 정보를 조회한다.

    Cloud Run(K_SERVICE 존재)이면 유닉스 소켓(/cloudsql/<INSTANCE_CONNECTION_NAME>),
    아니면 로컬 TCP(Cloud SQL Proxy 경유)로 분기한다. 필수 값이 없으면 어떤
    변수가 빠졌는지 메시지에 명시해 DatabaseConfigError를 던진다.
    """
    # 함수 안에서 import하는 이유: gemini.py의 _resolve_api_key와 동일하게, 테스트가
    # dotenv.load_dotenv를 monkeypatch할 수 있도록 매 호출 시점에 현재 바인딩을 조회한다.
    from dotenv import load_dotenv

    load_dotenv()

    is_cloud_run = bool(os.environ.get("K_SERVICE"))

    required_keys = ["DB_NAME", "DB_USER", "DB_PASSWORD"]
    required_keys.append("INSTANCE_CONNECTION_NAME" if is_cloud_run else "DB_HOST")

    values = {key: os.environ.get(key) for key in required_keys}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise DatabaseConfigError(f"필수 환경변수가 없습니다: {', '.join(missing)}")

    conninfo: dict[str, str | int] = {
        "dbname": values["DB_NAME"],
        "user": values["DB_USER"],
        "password": values["DB_PASSWORD"],
    }

    if is_cloud_run:
        instance_connection_name = values["INSTANCE_CONNECTION_NAME"]
        conninfo["host"] = f"/cloudsql/{instance_connection_name}"
        logger.info(f"db_connect_mode=unix_socket instance={instance_connection_name}")
    else:
        conninfo["host"] = values["DB_HOST"]
        conninfo["port"] = int(os.environ.get("DB_PORT", "5432"))
        logger.info(f"db_connect_mode=tcp host={conninfo['host']} port={conninfo['port']}")

    return conninfo


@contextmanager
def get_connection() -> Iterator[Connection]:
    """커넥션 획득·반납. 정상 종료 시 commit, 예외 시 rollback, 항상 close.

    psycopg 3의 `with psycopg.connect(...) as conn:`은 psycopg2와 달리 블록
    종료 시 (예외 없으면 commit / 있으면 rollback)을 자동 수행한 뒤, 풀에
    속하지 않은 커넥션이면 close까지 한다 — "정상 시 commit, 예외 시 rollback,
    항상 close" 계약과 정확히 일치한다. 그래서 별도 try/finally 없이 이
    동작에 그대로 위임한다(직접 제어로 재구현하지 않기로 명시적으로 선택).
    """
    conninfo = _resolve_conninfo()
    try:
        conn = psycopg.connect(**conninfo)
    except psycopg.Error as exc:
        raise DatabaseConnectionError(f"DB 접속 실패: {exc}") from exc

    with conn:
        try:
            register_vector(conn)
        except Exception as exc:
            raise DatabaseConnectionError(
                "pgvector 확장 등록 실패 — DB에 CREATE EXTENSION vector가 "
                f"설치되어 있는지 확인하세요: {exc}"
            ) from exc
        yield conn


@contextmanager
def get_cursor(dict_rows: bool = False) -> Iterator[Cursor]:
    """커넥션+커서를 한 번에 관리한다. dict_rows=True면 dict_row row_factory를 쓴다."""
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row if dict_rows else None) as cur:
            yield cur


def _normalize_achievement_code(achievement_code: str) -> str:
    """조회용 achievement_code를 저장 형식(`[4과02-01]`)으로 정규화한다.

    호출부가 대괄호 없이(`4과02-01`) 넘길 수 있는데, 그 상태로 조회하면
    에러 없이 조용히 0건(None)이 되어 디버깅이 매우 어려워진다. 그래서
    대괄호가 없으면 붙이고, 이미 있으면 그대로 둔다.
    """
    code = achievement_code.strip()
    if not code.startswith("["):
        code = f"[{code}"
    if not code.endswith("]"):
        code = f"{code}]"
    return code


def get_chunk_by_code(achievement_code: str) -> CurriculumChunk | None:
    """성취기준 코드로 단건 조회. 없으면 None. C의 원문·해설 확보용."""
    normalized = _normalize_achievement_code(achievement_code)
    query = f"SELECT {_CHUNK_COLUMNS} FROM {TABLE_NAME} WHERE achievement_code = %s"

    start = time.monotonic()
    with get_cursor(dict_rows=True) as cur:
        cur.execute(query, (normalized,))
        row = cur.fetchone()
    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        f"db_query table={TABLE_NAME} rows={0 if row is None else 1} "
        f"elapsed_ms={elapsed_ms:.1f}"
    )

    return CurriculumChunk(**row) if row is not None else None


def get_chunks_by_scope(
    target_grade: int,
    subject: Subject | None = None,
) -> list[CurriculumChunk]:
    """대상 학년까지 누적 학습 범위의 청크 조회.

    grade_to_bands()로 학년군 집합을 구해 grade_band = ANY(...)로 필터링한다.
    subject가 None이면 전 과목. 과목별로 존재하는 학년군이 달라(예: 실과는
    5~6학년군만) 특정 조합은 0건일 수 있는데, 이는 정상 동작이므로 예외를
    던지지 않고 빈 리스트를 반환한다. D의 금지 용어 파생, A2의 검색 대상
    조회에 공용으로 쓰인다.
    """
    bands = [band.value for band in grade_to_bands(target_grade)]

    query = f"SELECT {_CHUNK_COLUMNS} FROM {TABLE_NAME} WHERE grade_band = ANY(%s)"
    params: list[object] = [bands]
    if subject is not None:
        query += " AND subject = %s"
        params.append(subject.value)

    start = time.monotonic()
    with get_cursor(dict_rows=True) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(f"db_query table={TABLE_NAME} rows={len(rows)} elapsed_ms={elapsed_ms:.1f}")

    return [CurriculumChunk(**row) for row in rows]


def get_scope_summary() -> list[tuple[Subject, GradeBand, int]]:
    """과목×학년군별 청크 수. 적재 검증·디버깅용.

    과목마다 존재하는 학년군이 달라 특정 조합이 0건일 수 있어, 분포를
    즉시 확인하기 위함이다.
    """
    query = f"""
        SELECT subject, grade_band, COUNT(*) AS chunk_count
        FROM {TABLE_NAME}
        GROUP BY subject, grade_band
        ORDER BY subject, grade_band
    """

    start = time.monotonic()
    with get_cursor(dict_rows=True) as cur:
        cur.execute(query)
        rows = cur.fetchall()
    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(f"db_query table={TABLE_NAME} rows={len(rows)} elapsed_ms={elapsed_ms:.1f}")

    return [
        (Subject(row["subject"]), GradeBand(row["grade_band"]), row["chunk_count"])
        for row in rows
    ]


def _escape_like(value: str) -> str:
    """ILIKE 패턴에서 %/_는 와일드카드로 해석된다 — 검색어에 그 문자가 그대로
    들어있어도 리터럴로 매칭되도록 이스케이프한다(기본 이스케이프 문자는
    백슬래시). 안 하면 단원명에 `_`가 들어간 코드를 검색할 때 한 글자
    와일드카드로 해석돼 무관한 행까지 걸린다."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _chunk_filter(query: str | None, subject: Subject | None) -> tuple[str, list[object]]:
    """search_chunks/count_chunks가 공유하는 WHERE절 구성. 필터를 한쪽에만
    추가하면 목록과 총건수(페이지네이션 분모)가 조용히 어긋나므로 반드시
    이 헬퍼를 함께 쓴다."""
    conditions: list[str] = []
    params: list[object] = []
    if query:
        conditions.append(
            "(achievement_code ILIKE %s OR unit_name ILIKE %s OR achievement_text ILIKE %s)"
        )
        like = f"%{_escape_like(query)}%"
        params.extend([like, like, like])
    if subject is not None:
        conditions.append("subject = %s")
        params.append(subject.value)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, params


def search_chunks(
    query: str | None = None,
    subject: Subject | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[CurriculumChunk]:
    """관리자 화면의 성취기준 검색·목록 조회(REQ-002 관리자용 데이터 수정 API).

    query는 achievement_code/unit_name/achievement_text 부분일치(ILIKE)로 찾는다 —
    관리자가 정확한 코드를 모르고 단원명이나 원문 일부만 기억하는 경우가 많아서다.
    """
    where, params = _chunk_filter(query, subject)
    sql = f"SELECT {_CHUNK_COLUMNS} FROM {TABLE_NAME} {where} ORDER BY chunk_id LIMIT %s OFFSET %s"
    with get_cursor(dict_rows=True) as cur:
        cur.execute(sql, [*params, limit, offset])
        rows = cur.fetchall()
    return [CurriculumChunk(**row) for row in rows]


def count_chunks(query: str | None = None, subject: Subject | None = None) -> int:
    """search_chunks와 동일한 필터의 총 개수(관리자 화면 페이지네이션용)."""
    where, params = _chunk_filter(query, subject)
    sql = f"SELECT count(*) AS n FROM {TABLE_NAME} {where}"
    with get_cursor(dict_rows=True) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row["n"]


def get_chunk_by_id(chunk_id: str) -> CurriculumChunk | None:
    """chunk_id 컬럼으로 단건 조회. update_chunk와 동일한 키(chunk_id)로 조회해
    관리자 수정 폼(조회)과 저장(갱신)이 항상 같은 행을 가리키도록 보장한다 —
    get_chunk_by_code(achievement_code 컬럼 기준)를 대신 썼다면 현재는
    chunk_id와 1:1 대응해 우연히 맞지만, 그 대응은 ingest_curriculum.py의
    구현 세부사항이지 이 함수의 계약이 아니다."""
    query = f"SELECT {_CHUNK_COLUMNS} FROM {TABLE_NAME} WHERE chunk_id = %s"
    with get_cursor(dict_rows=True) as cur:
        cur.execute(query, (chunk_id,))
        row = cur.fetchone()
    return CurriculumChunk(**row) if row is not None else None


def update_chunk(
    chunk_id: str,
    *,
    unit_name: str,
    domain: str,
    core_idea: str,
    achievement_text: str,
    explanation: str,
) -> CurriculumChunk | None:
    """관리자가 파싱 오류·오탈자 등 콘텐츠를 직접 수정하는 경로. 존재하지 않는
    chunk_id면 None.

    chunk_id/subject/grade_band/achievement_code는 여기서 바꾸지 않는다 —
    chunk_id는 임베딩 캐시(app/data/embeddings_cache/*.npz)와 골든셋
    (RS-005_골든셋.csv)이 참조하는 키라 여기서 바뀌면 두 곳이 조용히 깨진다.
    구조적 정정(코드·학년군 자체가 틀린 경우)은 관리자 API 범위 밖 —
    ingest_curriculum.py 재실행 대상이다.

    주의(재임베딩 미포함): achievement_text/explanation을 바꾸면 Cloud SQL에
    저장된 dense 임베딩 벡터가 새 텍스트와 더 이상 일치하지 않게 된다. 이 함수는
    텍스트만 갱신하고 재임베딩은 하지 않는다 — 임베딩 모델 로드가 무거워
    요청-응답 경로에 넣기 부적절하기 때문이다(별도 배치 재적재로 후속 처리 필요,
    RS-006 후속 과제).
    """
    sql = f"""
        UPDATE {TABLE_NAME}
        SET unit_name = %s, domain = %s, core_idea = %s,
            achievement_text = %s, explanation = %s
        WHERE chunk_id = %s
        RETURNING {_CHUNK_COLUMNS}
    """
    params = (unit_name, domain, core_idea, achievement_text, explanation, chunk_id)

    with get_cursor(dict_rows=True) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return CurriculumChunk(**row) if row is not None else None


# ── 계정 · 세션 · 히스토리 (E, REQ-006) ──────────────────────────


def create_user(email: str, password_hash: str, name: str, role: str = "user") -> User:
    """계정 생성. email UNIQUE 위반 시 psycopg.errors.UniqueViolation이 그대로 올라간다
    (호출부가 "이미 가입된 이메일" 문구로 변환)."""
    query = (
        "INSERT INTO users (email, password_hash, name, role) "
        "VALUES (%s, %s, %s, %s) "
        "RETURNING id, email, password_hash, name, role, created_at"
    )
    try:
        with get_cursor(dict_rows=True) as cur:
            cur.execute(query, (email, password_hash, name, role))
            row = cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise EmailAlreadyExistsError(f"이미 가입된 이메일입니다: {email}") from exc
    return User(**row)


def get_user_by_email(email: str) -> User | None:
    query = "SELECT id, email, password_hash, name, role, created_at FROM users WHERE email = %s"
    with get_cursor(dict_rows=True) as cur:
        cur.execute(query, (email,))
        row = cur.fetchone()
    return User(**row) if row is not None else None


def get_user_by_id(user_id: UUID) -> User | None:
    query = "SELECT id, email, password_hash, name, role, created_at FROM users WHERE id = %s"
    with get_cursor(dict_rows=True) as cur:
        cur.execute(query, (user_id,))
        row = cur.fetchone()
    return User(**row) if row is not None else None


def create_session(session_id: str, user_id: UUID, expires_at: datetime) -> None:
    query = "INSERT INTO sessions (id, user_id, expires_at) VALUES (%s, %s, %s)"
    with get_cursor() as cur:
        cur.execute(query, (session_id, user_id, expires_at))


def get_session(session_id: str) -> Session | None:
    query = "SELECT id, user_id, created_at, expires_at FROM sessions WHERE id = %s"
    with get_cursor(dict_rows=True) as cur:
        cur.execute(query, (session_id,))
        row = cur.fetchone()
    return Session(**row) if row is not None else None


def delete_session(session_id: str) -> None:
    with get_cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))


def count_lesson_requests_since(user_id: UUID, window: timedelta) -> int:
    """롤링 윈도우 카운트. 캘린더 자정 리셋이 아니라 now() - window 기준
    (레이트리밋 경계 우회 방지). 삭제된(soft-delete) 행도 일부러 그대로 센다 —
    안 그러면 사용자가 삭제→재생성을 반복해 레이트리밋을 무력화할 수 있다."""
    query = (
        "SELECT count(*) AS n FROM lesson_requests "
        "WHERE user_id = %s AND created_at > now() - %s"
    )
    with get_cursor(dict_rows=True) as cur:
        cur.execute(query, (user_id, window))
        row = cur.fetchone()
    return row["n"]


def create_lesson_request(
    user_id: UUID,
    concept_name: str,
    target_grade: int,
    subject_hint: str | None,
    mapped_curriculum_code: str | None,
    lesson_output: dict,
    validation_status: str,
) -> LessonRequest:
    """이 INSERT 자체가 레이트리밋 카운트 대상이다(count_lesson_requests_since가
    이 테이블을 그대로 세므로 별도 카운터 컬럼 불필요). 파이프라인을 실행한
    요청은 결과와 무관하게 1행씩 남긴다 — 조기 종료(unsupported_concept,
    검색 0건 등)도 Gemini 호출 비용이 이미 발생했으므로 카운트해야 한다.

    실패한 시도는 lesson_output이 빈 dict({})로 저장된다(NULL 아님). 재출력
    가능 여부는 이 값이 비었는지로 판별한다 — validation_status에는 실패
    사유 문구가 그대로 들어가는 자유 문자열이라 판별 기준이 못 된다."""
    query = (
        "INSERT INTO lesson_requests "
        "(user_id, concept_name, target_grade, subject_hint, mapped_curriculum_code, "
        "lesson_output, validation_status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, user_id, concept_name, target_grade, subject_hint, "
        "mapped_curriculum_code, lesson_output, validation_status, created_at"
    )
    with get_cursor(dict_rows=True) as cur:
        cur.execute(
            query,
            (
                user_id,
                concept_name,
                target_grade,
                subject_hint,
                mapped_curriculum_code,
                Jsonb(lesson_output),
                validation_status,
            ),
        )
        row = cur.fetchone()
    return LessonRequest(**row)


def list_lesson_requests(user_id: UUID, limit: int, offset: int) -> list[LessonRequest]:
    query = (
        "SELECT id, user_id, concept_name, target_grade, subject_hint, "
        "mapped_curriculum_code, lesson_output, validation_status, created_at "
        "FROM lesson_requests WHERE user_id = %s AND deleted_at IS NULL "
        "ORDER BY created_at DESC LIMIT %s OFFSET %s"
    )
    with get_cursor(dict_rows=True) as cur:
        cur.execute(query, (user_id, limit, offset))
        rows = cur.fetchall()
    return [LessonRequest(**row) for row in rows]


def count_lesson_requests(user_id: UUID) -> int:
    """마이페이지 목록 페이지네이션 총 개수(레이트리밋 윈도우 카운트와는 별개, 삭제 제외 전체 기간)."""
    with get_cursor(dict_rows=True) as cur:
        cur.execute(
            "SELECT count(*) AS n FROM lesson_requests WHERE user_id = %s AND deleted_at IS NULL",
            (user_id,),
        )
        row = cur.fetchone()
    return row["n"]


def get_lesson_request_by_id(request_id: UUID) -> LessonRequest | None:
    query = (
        "SELECT id, user_id, concept_name, target_grade, subject_hint, "
        "mapped_curriculum_code, lesson_output, validation_status, created_at "
        "FROM lesson_requests WHERE id = %s AND deleted_at IS NULL"
    )
    with get_cursor(dict_rows=True) as cur:
        cur.execute(query, (request_id,))
        row = cur.fetchone()
    return LessonRequest(**row) if row is not None else None


def delete_lesson_request(request_id: UUID, user_id: UUID) -> bool:
    """소유자가 아니면 아무 행도 안 지운다 — SQL WHERE에 user_id를 직접 걸어
    호출부의 사전 소유권 체크에만 의존하지 않는다(defense in depth).

    실제로는 soft-delete(deleted_at 세팅)만 한다 — 하드 DELETE를 하면
    count_lesson_requests_since(레이트리밋 카운트)가 그 행을 못 세게 되어
    "삭제 후 재생성"으로 일/주 한도를 무력화할 수 있기 때문이다. deleted_at이
    찍힌 행은 목록/상세/카운트 조회에서는 빠지지만 레이트리밋 카운트에는 계속 잡힌다.

    반환값은 실제로 지워진(소유자였던) 행이 있었는지 여부.
    """
    query = (
        "UPDATE lesson_requests SET deleted_at = now() "
        "WHERE id = %s AND user_id = %s AND deleted_at IS NULL"
    )
    with get_cursor() as cur:
        cur.execute(query, (request_id, user_id))
        return cur.rowcount > 0
