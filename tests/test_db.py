"""app/lib/db.py 유닛 테스트.

실제 DB에 붙지 않고 psycopg.connect를 가짜 Connection/Cursor로 대체해 검증한다.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from app.lib import db
from app.lib.types import Subject


class _FakeCursor:
    """psycopg 3 Cursor의 최소 동작(컨텍스트 매니저 + execute/fetch)을 흉내낸다."""

    def __init__(self, fetchone_result=None, fetchall_result=None, rowcount=0):
        self.executed: list[tuple[str, object]] = []
        self._fetchone_result = fetchone_result
        self._fetchall_result = [] if fetchall_result is None else fetchall_result
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return self._fetchall_result


class _FakeConnection:
    """psycopg 3 Connection의 실제 컨텍스트 매니저 동작(commit/rollback/close)을 흉내낸다.

    db.get_connection()이 psycopg 3의 `with conn:` 자동 동작에 그대로 위임하므로,
    그 위임이 실제로 되는지 검증하려면 가짜도 동일한 계약(예외 없으면 commit,
    있으면 rollback, 항상 close)을 지켜야 의미가 있다.
    """

    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True

    def cursor(self, row_factory=None):
        return self._cursor


def _set_valid_env(monkeypatch):
    monkeypatch.setenv("DB_NAME", "edutech")
    monkeypatch.setenv("DB_USER", "u")
    monkeypatch.setenv("DB_PASSWORD", "p")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")


def _patch_connect(monkeypatch, fake_conn: _FakeConnection) -> list[dict]:
    """psycopg.connect·register_vector를 가짜로 교체하고, connect 호출 인자를 기록해 반환한다."""
    calls: list[dict] = []

    def fake_connect(**kwargs):
        calls.append(kwargs)
        return fake_conn

    monkeypatch.setattr(db.psycopg, "connect", fake_connect)
    monkeypatch.setattr(db, "register_vector", lambda conn: None)
    return calls


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """DB 관련 환경변수를 매 테스트마다 비운다. 로컬 .env에 실접속 정보가 있을 수
    있으므로 load_dotenv도 무력화해 결정적으로 만든다."""
    for key in (
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "DB_HOST",
        "DB_PORT",
        "K_SERVICE",
        "INSTANCE_CONNECTION_NAME",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: False)


# ---------- 설정/접속 분기 ----------


def test_missing_required_env_lists_each_missing_key(monkeypatch):
    monkeypatch.setenv("DB_NAME", "edutech")  # 나머지는 autouse 픽스처가 비운 상태 유지

    with pytest.raises(db.DatabaseConfigError) as exc_info:
        db._resolve_conninfo()

    message = str(exc_info.value)
    assert "DB_USER" in message
    assert "DB_PASSWORD" in message
    assert "DB_HOST" in message
    assert "DB_NAME" not in message  # 있는 건 메시지에 안 나와야 함


def test_import_succeeds_but_first_call_fails_without_env():
    # 이 시점까지 도달했다는 것 자체가 "import는 성공"의 증거.
    with pytest.raises(db.DatabaseConfigError):
        with db.get_connection():
            pass


def test_cloud_run_uses_unix_socket(monkeypatch):
    _set_valid_env(monkeypatch)
    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.setenv("K_SERVICE", "edutech-svc")
    monkeypatch.setenv("INSTANCE_CONNECTION_NAME", "proj:region:inst")
    connect_calls = _patch_connect(monkeypatch, _FakeConnection(_FakeCursor()))

    with db.get_connection():
        pass

    assert connect_calls[0]["host"] == "/cloudsql/proj:region:inst"


def test_local_uses_tcp(monkeypatch):
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("DB_PORT", "5433")
    connect_calls = _patch_connect(monkeypatch, _FakeConnection(_FakeCursor()))

    with db.get_connection():
        pass

    assert connect_calls[0]["host"] == "127.0.0.1"
    assert connect_calls[0]["port"] == 5433


# ---------- 커넥션 commit/rollback/close 계약 ----------


def test_exception_in_block_rolls_back_and_closes(monkeypatch):
    _set_valid_env(monkeypatch)
    fake_conn = _FakeConnection(_FakeCursor())
    _patch_connect(monkeypatch, fake_conn)

    with pytest.raises(RuntimeError):
        with db.get_connection():
            raise RuntimeError("boom")

    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False
    assert fake_conn.closed is True


def test_normal_exit_commits_and_closes(monkeypatch):
    _set_valid_env(monkeypatch)
    fake_conn = _FakeConnection(_FakeCursor())
    _patch_connect(monkeypatch, fake_conn)

    with db.get_connection():
        pass

    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert fake_conn.closed is True


# ---------- get_chunks_by_scope ----------


def test_scope_query_includes_expected_grade_bands(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchall_result=[])
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    db.get_chunks_by_scope(4)

    _, params = cursor.executed[0]
    bands = params[0]
    assert "G1_2" in bands
    assert "G3_4" in bands
    assert "G5_6" not in bands


def test_scope_out_of_range_grade_raises_value_error(monkeypatch):
    _set_valid_env(monkeypatch)

    with pytest.raises(ValueError):
        db.get_chunks_by_scope(7)


def test_scope_query_adds_subject_filter(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchall_result=[])
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    db.get_chunks_by_scope(4, Subject.SCIENCE)

    query, params = cursor.executed[0]
    assert "subject = %s" in query
    assert params[-1] == "SCIENCE"


def test_scope_empty_result_returns_empty_list_without_error(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchall_result=[])
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    result = db.get_chunks_by_scope(4)

    assert result == []


# ---------- get_chunk_by_code ----------


def test_chunk_by_code_returns_none_when_not_found(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result=None)
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    assert db.get_chunk_by_code("[4과02-01]") is None


def test_chunk_by_code_normalizes_brackets(monkeypatch):
    _set_valid_env(monkeypatch)

    cursor_with_brackets = _FakeCursor(fetchone_result=None)
    _patch_connect(monkeypatch, _FakeConnection(cursor_with_brackets))
    db.get_chunk_by_code("[4과02-01]")

    cursor_without_brackets = _FakeCursor(fetchone_result=None)
    _patch_connect(monkeypatch, _FakeConnection(cursor_without_brackets))
    db.get_chunk_by_code("4과02-01")

    params_with = cursor_with_brackets.executed[0][1]
    params_without = cursor_without_brackets.executed[0][1]
    assert params_with == params_without == ("[4과02-01]",)


# ---------- search_chunks / count_chunks / update_chunk (관리자 데이터 수정 API) ----------


def _chunk_row(**overrides):
    row = {
        "chunk_id": "4과02-01",
        "subject": "SCIENCE",
        "grade_band": "G3_4",
        "unit_name": "동물의 생활",
        "domain": "생명",
        "core_idea": "동물 분류",
        "achievement_code": "[4과02-01]",
        "achievement_text": "동물을 분류할 수 있다.",
        "explanation": "관찰 가능한 특징을 기준으로 분류한다.",
        "inquiry_activities": [],
        "source_page": 12,
    }
    row.update(overrides)
    return row


def test_search_chunks_without_filters_has_no_where_clause(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchall_result=[])
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    db.search_chunks()

    query, params = cursor.executed[0]
    assert "WHERE" not in query
    assert params == [50, 0]


def test_search_chunks_query_uses_ilike_on_three_columns(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchall_result=[_chunk_row()])
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    result = db.search_chunks(query="분류")

    query, params = cursor.executed[0]
    assert "achievement_code ILIKE %s" in query
    assert "unit_name ILIKE %s" in query
    assert "achievement_text ILIKE %s" in query
    assert params[:3] == ["%분류%", "%분류%", "%분류%"]
    assert result[0].chunk_id == "4과02-01"


def test_search_chunks_adds_subject_filter(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchall_result=[])
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    db.search_chunks(subject=Subject.KOREAN)

    query, params = cursor.executed[0]
    assert "subject = %s" in query
    assert "KOREAN" in params


def test_search_chunks_paginates(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchall_result=[])
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    db.search_chunks(limit=10, offset=20)

    _, params = cursor.executed[0]
    assert params[-2:] == [10, 20]


def test_count_chunks_mirrors_search_filters(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result={"n": 3})
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    result = db.count_chunks(query="분류", subject=Subject.SCIENCE)

    query, params = cursor.executed[0]
    assert "count(*)" in query
    assert "ILIKE" in query
    assert "subject = %s" in query
    assert result == 3


def test_update_chunk_returns_updated_chunk(monkeypatch):
    _set_valid_env(monkeypatch)
    updated_row = _chunk_row(achievement_text="정정된 원문")
    cursor = _FakeCursor(fetchone_result=updated_row)
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    result = db.update_chunk(
        "4과02-01",
        unit_name="동물의 생활",
        domain="생명",
        core_idea="동물 분류",
        achievement_text="정정된 원문",
        explanation="관찰 가능한 특징을 기준으로 분류한다.",
    )

    assert result is not None
    assert result.achievement_text == "정정된 원문"
    query, params = cursor.executed[0]
    assert "UPDATE curriculum_chunks" in query
    assert "RETURNING" in query
    assert params[-1] == "4과02-01"


def test_update_chunk_returns_none_when_not_found(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result=None)
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    result = db.update_chunk(
        "없는코드",
        unit_name="x",
        domain="x",
        core_idea="x",
        achievement_text="x",
        explanation="x",
    )

    assert result is None


def test_update_chunk_does_not_modify_identity_fields(monkeypatch):
    """chunk_id/subject/grade_band/achievement_code는 SET 절에 없어야 한다 —
    임베딩 캐시·골든셋이 chunk_id를 키로 참조하므로 여기서 바뀌면 안 된다."""
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result=_chunk_row())
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    db.update_chunk(
        "4과02-01",
        unit_name="x",
        domain="x",
        core_idea="x",
        achievement_text="x",
        explanation="x",
    )

    query, _ = cursor.executed[0]
    set_clause = query.split("SET", 1)[1].split("WHERE", 1)[0]
    assert "chunk_id =" not in set_clause
    assert "subject =" not in set_clause
    assert "grade_band =" not in set_clause
    assert "achievement_code =" not in set_clause


def test_search_chunks_escapes_like_metacharacters(monkeypatch):
    """검색어에 %나 _가 그대로 들어있으면 와일드카드가 아니라 리터럴로
    매칭돼야 한다 — 안 그러면 '4_2' 검색이 '4a2' 같은 무관한 행까지 잡는다."""
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchall_result=[])
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    db.search_chunks(query="4_2%off")

    _, params = cursor.executed[0]
    assert params[0] == "%4\\_2\\%off%"


def test_get_chunk_by_id_queries_by_chunk_id_column(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result=_chunk_row())
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    result = db.get_chunk_by_id("4과02-01")

    query, params = cursor.executed[0]
    assert "WHERE chunk_id = %s" in query
    assert params == ("4과02-01",)
    assert result.chunk_id == "4과02-01"


def test_get_chunk_by_id_returns_none_when_missing(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result=None)
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    assert db.get_chunk_by_id("없는코드") is None


# ---------- 계정 · 세션 · 히스토리 (E, REQ-006) ----------


class _RaisingCursor(_FakeCursor):
    """execute()가 지정된 예외를 던지는 가짜 커서. UniqueViolation 같은 DB 제약
    위반 경로를 실제 접속 없이 재현하기 위함."""

    def __init__(self, exc: Exception):
        super().__init__()
        self._exc = exc

    def execute(self, query, params=None):
        raise self._exc


def _user_row(**overrides):
    row = {
        "id": uuid4(),
        "email": "a@b.com",
        "password_hash": "hashed",
        "name": "테스트",
        "role": "user",
        "created_at": datetime.now(timezone.utc),
    }
    row.update(overrides)
    return row


def test_create_user_returns_user(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result=_user_row())
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    user = db.create_user("a@b.com", "hashed", "테스트")

    assert user.email == "a@b.com"
    assert user.role == "user"


def test_create_user_duplicate_email_raises_domain_error(monkeypatch):
    _set_valid_env(monkeypatch)
    unique_violation = psycopg.errors.UniqueViolation("duplicate key")
    cursor = _RaisingCursor(unique_violation)
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    with pytest.raises(db.EmailAlreadyExistsError):
        db.create_user("dup@b.com", "hashed", "테스트")


def test_get_user_by_email_returns_none_when_missing(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result=None)
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    assert db.get_user_by_email("nobody@b.com") is None


def test_count_lesson_requests_since_uses_rolling_window(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result={"n": 3})
    _patch_connect(monkeypatch, _FakeConnection(cursor))
    user_id = uuid4()

    result = db.count_lesson_requests_since(user_id, timedelta(days=1))

    query, params = cursor.executed[0]
    assert "now() - %s" in query
    assert "created_at >" in query
    assert params == (user_id, timedelta(days=1))
    assert result == 3


def test_list_lesson_requests_orders_desc_and_paginates(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchall_result=[])
    _patch_connect(monkeypatch, _FakeConnection(cursor))
    user_id = uuid4()

    db.list_lesson_requests(user_id, limit=10, offset=20)

    query, params = cursor.executed[0]
    assert "ORDER BY created_at DESC" in query
    assert params == (user_id, 10, 20)


def test_get_lesson_request_by_id_returns_none_when_missing(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result=None)
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    assert db.get_lesson_request_by_id(uuid4()) is None


def test_delete_lesson_request_soft_deletes_scoped_to_owner(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(rowcount=1)
    _patch_connect(monkeypatch, _FakeConnection(cursor))
    request_id = uuid4()
    user_id = uuid4()

    result = db.delete_lesson_request(request_id, user_id)

    query, params = cursor.executed[0]
    assert "UPDATE lesson_requests" in query
    assert "SET deleted_at = now()" in query
    assert "user_id = %s" in query
    assert params == (request_id, user_id)
    assert result is True


def test_delete_lesson_request_returns_false_when_not_owner_or_missing(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(rowcount=0)
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    assert db.delete_lesson_request(uuid4(), uuid4()) is False


def test_count_lesson_requests_since_does_not_filter_deleted_at(monkeypatch):
    """레이트리밋 카운트는 삭제된(soft-delete) 행도 세야 한다 — 안 그러면
    삭제→재생성으로 일/주 한도를 무력화할 수 있다."""
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result={"n": 3})
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    db.count_lesson_requests_since(uuid4(), timedelta(days=1))

    query, _ = cursor.executed[0]
    assert "deleted_at" not in query


def test_list_lesson_requests_excludes_deleted(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchall_result=[])
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    db.list_lesson_requests(uuid4(), limit=10, offset=0)

    query, _ = cursor.executed[0]
    assert "deleted_at IS NULL" in query


def test_get_lesson_request_by_id_excludes_deleted(monkeypatch):
    _set_valid_env(monkeypatch)
    cursor = _FakeCursor(fetchone_result=None)
    _patch_connect(monkeypatch, _FakeConnection(cursor))

    db.get_lesson_request_by_id(uuid4())

    query, _ = cursor.executed[0]
    assert "deleted_at IS NULL" in query


# ---------- 공통: embedding 컬럼 제외 ----------


def test_select_columns_exclude_embedding():
    assert "embedding" not in db._CHUNK_COLUMNS
