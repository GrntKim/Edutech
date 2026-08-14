# 소유: E(REQ-006)
"""비밀번호 해싱 · 세션 발급/검증 · 인증 의존성 · 레이트리밋 판정.

세션은 JWT를 쓰지 않는다(팀 결정). 세션 id는 secrets.token_urlsafe로 생성한
opaque random token이며, 그 자체가 256비트 엔트로피라 추측 불가능하다 — 이
토큰을 sessions 테이블에 저장하고 쿠키 값으로 그대로 내려준다. Cloud Run이
다중 인스턴스로 뜨므로 세션 데이터는 항상 DB(sessions 테이블)에만 있고
앱 프로세스 메모리에는 두지 않는다(db.py 경유).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Response

from app.lib import db
from app.lib.types import RateLimitStatus, User

SESSION_COOKIE_NAME = "session_id"
SESSION_TTL = timedelta(days=7)  # 고정 만료 — 슬라이딩 갱신 없음(단순화, 팀 결정)
BCRYPT_ROUNDS = 12

DAILY_LIMIT = 5
WEEKLY_LIMIT = 15
# 롤링 윈도우 폭. 리셋 시각 계산도 같은 값을 써야 카운트와 어긋나지 않는다.
DAILY_WINDOW = timedelta(days=1)
WEEKLY_WINDOW = timedelta(days=7)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode(
        "utf-8"
    )


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def issue_session(response: Response, user_id) -> None:
    """세션 row를 만들고 응답에 쿠키를 심는다. 로그인·회원가입 성공 시 호출."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + SESSION_TTL
    db.create_session(token, user_id, expires_at)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()),
    )


def clear_session(response: Response, session_id: str | None) -> None:
    """로그아웃: 세션 row 삭제 + 쿠키 만료."""
    if session_id:
        db.delete_session(session_id)
    response.delete_cookie(SESSION_COOKIE_NAME)


def _is_expired(expires_at: datetime) -> bool:
    """sessions.expires_at은 DDL상 TIMESTAMPTZ라 psycopg가 tz-aware datetime을
    돌려줘야 하지만, 마이그레이션 툴 없이 SQL Studio로 수작업 생성하는 테이블이라
    실수로 TIMESTAMP(WITHOUT TIME ZONE)로 만들어질 여지가 있다 — 그 경우 naive
    datetime과 tz-aware now()를 비교하면 TypeError로 전체 인증 라우트가 죽는다.
    naive로 들어오면 UTC로 간주해 보정한다(방어적, 근본 대책은 DDL을 맞게 유지하는 것)."""
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def get_current_user(
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> User:
    """로그인 필수 라우트의 의존성. 세션이 없거나 만료됐으면 401."""
    if session_id is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    session = db.get_session(session_id)
    if session is None or _is_expired(session.expires_at):
        raise HTTPException(status_code=401, detail="세션이 만료되었습니다. 다시 로그인해 주세요.")

    user = db.get_user_by_id(session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


def get_optional_user(
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> User | None:
    """비로그인도 허용하는 라우트(예: "/")에서 로그인 상태만 확인할 때 쓴다.
    get_current_user와 달리 세션이 없거나 만료돼도 401을 던지지 않고 None."""
    if session_id is None:
        return None
    session = db.get_session(session_id)
    if session is None or _is_expired(session.expires_at):
        return None
    return db.get_user_by_id(session.user_id)


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return user


def check_rate_limit(user: User) -> RateLimitStatus:
    """admin은 무제한. user는 일 5회/주 15회 롤링 윈도우, 하나라도 초과하면 차단.

    파이프라인을 실행한 요청은 실패해도(A1 조기 종료·검색 0건·에이전트 예외)
    lesson_requests에 1행 남으므로 이 카운트에 잡힌다 — 실패해도 Gemini 호출
    비용은 이미 발생하기 때문이다.

    재출력(redownload)은 파이프라인을 실행하지 않으므로 lesson_requests에
    INSERT되지 않고, 따라서 이 카운트에도 잡히지 않는다.
    """
    if user.role == "admin":
        return RateLimitStatus(
            allowed=True,
            daily_used=0,
            daily_limit=DAILY_LIMIT,
            weekly_used=0,
            weekly_limit=WEEKLY_LIMIT,
        )

    daily_used = db.count_lesson_requests_since(user.id, DAILY_WINDOW)
    weekly_used = db.count_lesson_requests_since(user.id, WEEKLY_WINDOW)
    allowed = daily_used < DAILY_LIMIT and weekly_used < WEEKLY_LIMIT
    return RateLimitStatus(
        allowed=allowed,
        daily_used=daily_used,
        daily_limit=DAILY_LIMIT,
        weekly_used=weekly_used,
        weekly_limit=WEEKLY_LIMIT,
        daily_reset_at=_next_reset_at(user, DAILY_WINDOW, daily_used),
        weekly_reset_at=_next_reset_at(user, WEEKLY_WINDOW, weekly_used),
    )


def _next_reset_at(user: User, window: timedelta, used: int) -> datetime | None:
    """윈도우 안 가장 오래된 요청이 빠져나가는 시각 = 다음 1회가 회복되는 시각.

    사용량이 0이면 조회 자체를 건너뛴다 — 회복될 것이 없고, 배너에도 표시하지
    않으므로 불필요한 DB 왕복이다.
    """
    if used == 0:
        return None
    oldest = db.oldest_lesson_request_since(user.id, window)
    return oldest + window if oldest is not None else None
