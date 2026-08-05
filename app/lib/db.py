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

from app.lib.types import CurriculumChunk, GradeBand, Subject, grade_to_bands

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
