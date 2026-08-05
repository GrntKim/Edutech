"""app/lib/db.py 유닛 테스트.

실제 DB에 붙지 않고 psycopg.connect를 가짜 Connection/Cursor로 대체해 검증한다.
"""

import pytest

from app.lib import db
from app.lib.types import Subject


class _FakeCursor:
    """psycopg 3 Cursor의 최소 동작(컨텍스트 매니저 + execute/fetch)을 흉내낸다."""

    def __init__(self, fetchone_result=None, fetchall_result=None):
        self.executed: list[tuple[str, object]] = []
        self._fetchone_result = fetchone_result
        self._fetchall_result = [] if fetchall_result is None else fetchall_result

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


# ---------- 공통: embedding 컬럼 제외 ----------


def test_select_columns_exclude_embedding():
    assert "embedding" not in db._CHUNK_COLUMNS
