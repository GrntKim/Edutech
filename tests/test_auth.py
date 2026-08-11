"""app/lib/auth.py 유닛 테스트. DB는 항상 monkeypatch로 대체한다."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.lib import auth, db
from app.lib.types import Session, User


def _user(role="user") -> User:
    return User(
        id=uuid4(),
        email="a@b.com",
        password_hash=auth.hash_password("password123"),
        name="테스트",
        role=role,
        created_at=datetime.now(timezone.utc),
    )


# ---------- 비밀번호 해싱 ----------


def test_hash_password_verifies_correct_password():
    hashed = auth.hash_password("password123")
    assert auth.verify_password("password123", hashed)


def test_hash_password_rejects_wrong_password():
    hashed = auth.hash_password("password123")
    assert not auth.verify_password("wrong", hashed)


def test_hash_password_never_stores_plaintext():
    hashed = auth.hash_password("password123")
    assert "password123" not in hashed
    assert hashed.startswith("$2b$")


# ---------- get_current_user ----------


def test_get_current_user_raises_401_without_cookie():
    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(session_id=None)
    assert exc_info.value.status_code == 401


def test_get_current_user_raises_401_when_session_missing(monkeypatch):
    monkeypatch.setattr(db, "get_session", lambda sid: None)
    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(session_id="nope")
    assert exc_info.value.status_code == 401


def test_get_current_user_raises_401_when_session_expired(monkeypatch):
    user = _user()
    expired = Session(
        id="tok",
        user_id=user.id,
        created_at=datetime.now(timezone.utc) - timedelta(days=8),
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    monkeypatch.setattr(db, "get_session", lambda sid: expired)
    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(session_id="tok")
    assert exc_info.value.status_code == 401


def test_get_current_user_returns_user_for_valid_session(monkeypatch):
    user = _user()
    valid = Session(
        id="tok",
        user_id=user.id,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    monkeypatch.setattr(db, "get_session", lambda sid: valid)
    monkeypatch.setattr(db, "get_user_by_id", lambda uid: user)

    result = auth.get_current_user(session_id="tok")

    assert result.id == user.id


# ---------- require_admin ----------


def test_require_admin_rejects_normal_user():
    with pytest.raises(HTTPException) as exc_info:
        auth.require_admin(user=_user(role="user"))
    assert exc_info.value.status_code == 403


def test_require_admin_allows_admin():
    admin = _user(role="admin")
    assert auth.require_admin(user=admin) is admin


# ---------- check_rate_limit ----------


def test_check_rate_limit_admin_always_allowed(monkeypatch):
    monkeypatch.setattr(
        db, "count_lesson_requests_since", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("admin은 카운트 쿼리도 스킵해야 함"))
    )
    status = auth.check_rate_limit(_user(role="admin"))
    assert status.allowed is True


def test_check_rate_limit_blocks_when_daily_limit_hit(monkeypatch):
    monkeypatch.setattr(
        db, "count_lesson_requests_since", lambda user_id, window: 5 if window == timedelta(days=1) else 5
    )
    status = auth.check_rate_limit(_user())
    assert status.allowed is False
    assert status.daily_used == 5


def test_check_rate_limit_blocks_when_weekly_limit_hit_even_if_daily_ok(monkeypatch):
    monkeypatch.setattr(
        db, "count_lesson_requests_since", lambda user_id, window: 1 if window == timedelta(days=1) else 15
    )
    status = auth.check_rate_limit(_user())
    assert status.allowed is False
    assert status.weekly_used == 15


def test_check_rate_limit_allows_under_both_limits(monkeypatch):
    monkeypatch.setattr(
        db, "count_lesson_requests_since", lambda user_id, window: 1 if window == timedelta(days=1) else 3
    )
    status = auth.check_rate_limit(_user())
    assert status.allowed is True
