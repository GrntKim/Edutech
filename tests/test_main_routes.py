"""app/main.py 라우트 유닛 테스트. DB·파이프라인·DOCX 렌더는 항상 monkeypatch로 대체한다.

주 관심사는 두 가지 회귀 방지다.
1. 파이프라인을 실행한 요청은 결과와 무관하게 lesson_requests에 1행 남는다
   (그 INSERT가 곧 레이트리밋 카운트라, 실패가 무료가 되면 안 된다).
2. DOCX는 디스크를 거치지 않고 바이트로 내려간다(Cloud Run 다중 인스턴스 안전).
"""

from datetime import datetime, timezone
from urllib.parse import unquote
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main
from app.lib import auth, db
from app.lib.types import (
    ConceptInput,
    LessonRequest,
    PipelineResult,
    User,
    ValidationResult,
)

# result.html이 실제로 참조하는 필드를 모두 채운 최소 교안 — 성공 경로에서
# 템플릿이 렌더까지 되는지 함께 확인하기 위함이다.
LESSON_PLAN = {
    "lesson_time": "1차시(40분)",
    "school_level": "초등학교",
    "grade": 5,
    "topic": "인공지능 이미지 인식",
    "subject": "실과",
    "achievement_code": "[6실05-05]",
    "achievement_statement": "인공지능이 만들어지는 과정을 이해한다.",
    "ai_digital_tool": "티처블 머신",
    "learning_objectives": ["이미지 인식 원리를 설명할 수 있다."],
    "materials": ["태블릿", "학습지"],
    "lesson_stages": {
        "intro": [{"content_label": "동기 유발", "teacher": "질문한다", "student": "답한다"}],
        "development": [{"content_label": "활동1", "teacher": "안내한다", "student": "수행한다"}],
        "wrap_up": [{"content_label": "정리하기", "teacher": "정리한다", "student": "발표한다"}],
    },
    "evaluation_criteria": {"high": "상", "mid": "중", "low": "하"},
    "worksheet": None,
}


def _user(role="user") -> User:
    return User(
        id=uuid4(),
        email="a@b.com",
        password_hash="x",
        name="테스트",
        role=role,
        created_at=datetime.now(timezone.utc),
    )


def _lesson_request(user_id, lesson_output: dict, validation_status="passed") -> LessonRequest:
    return LessonRequest(
        id=uuid4(),
        user_id=user_id,
        concept_name="이미지 인식",
        target_grade=5,
        subject_hint=None,
        mapped_curriculum_code=lesson_output.get("achievement_code"),
        lesson_output=lesson_output,
        validation_status=validation_status,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def user():
    return _user()


@pytest.fixture
def client(user):
    """로그인 의존성만 우회한다 — 라우트 본문의 DB 호출은 각 테스트가 개별 monkeypatch."""
    main.app.dependency_overrides[auth.get_current_user] = lambda: user
    main.app.dependency_overrides[auth.get_optional_user] = lambda: user
    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()


@pytest.fixture
def saved_rows(monkeypatch, user):
    """create_lesson_request 호출을 가로채 기록한다(= 레이트리밋 카운트 대상 행)."""
    rows = []

    def fake_create(**kwargs):
        rows.append(kwargs)
        return _lesson_request(
            user.id, kwargs["lesson_output"], kwargs["validation_status"]
        )

    monkeypatch.setattr(db, "create_lesson_request", fake_create)
    # 한도 판정은 항상 통과시킨다 — 여기서 검증하려는 건 "카운트되는가"이지 차단이 아니다.
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 0)
    return rows


# ---------- 파이프라인 실행 = 카운트 ----------


def test_generate_records_attempt_when_search_returns_nothing(client, monkeypatch, saved_rows):
    """A2 검색 0건으로 조기 종료해도 A1·A2 Gemini 비용은 이미 났다 — 카운트되어야 한다."""
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input: PipelineResult(
            lesson_plan={},
            validation=ValidationResult(passed=False),
            warning="해당 학년에서 연결 가능한 교육과정 성취기준을 찾지 못했습니다.",
        ),
    )

    response = client.post("/generate", data={"concept": "이미지 인식", "grade": 5})

    assert response.status_code == 200
    assert len(saved_rows) == 1
    assert saved_rows[0]["lesson_output"] == {}
    assert saved_rows[0]["validation_status"].startswith("해당 학년에서")
    assert saved_rows[0]["mapped_curriculum_code"] is None


def test_generate_records_attempt_when_concept_unsupported(client, monkeypatch, saved_rows):
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input: PipelineResult(
            lesson_plan={},
            validation=ValidationResult(passed=False),
            warning="입력하신 내용은 AI 개념으로 인식되지 않았습니다.",
        ),
    )

    client.post("/generate", data={"concept": "김치찌개", "grade": 3})

    assert len(saved_rows) == 1
    assert saved_rows[0]["lesson_output"] == {}


def test_generate_records_attempt_when_pipeline_raises(client, monkeypatch, saved_rows):
    """파이프라인이 예외로 죽어도 이미 쓴 비용은 카운트한다(그리고 500은 그대로 난다)."""

    def boom(concept_input):
        raise RuntimeError("gemini down")

    monkeypatch.setattr(main, "run_pipeline", boom)

    response = client.post("/generate", data={"concept": "이미지 인식", "grade": 5})

    assert response.status_code == 500
    assert len(saved_rows) == 1
    assert saved_rows[0]["validation_status"] == "pipeline_error"
    assert saved_rows[0]["lesson_output"] == {}


def test_generate_records_success_once(client, monkeypatch, saved_rows):
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input: PipelineResult(
            lesson_plan=LESSON_PLAN,
            validation=ValidationResult(passed=True),
            warning=None,
        ),
    )

    response = client.post("/generate", data={"concept": "이미지 인식", "grade": 5})

    assert response.status_code == 200
    assert len(saved_rows) == 1
    assert saved_rows[0]["validation_status"] == "passed"
    assert saved_rows[0]["lesson_output"] == LESSON_PLAN
    # 다운로드 링크는 저장된 행을 가리킨다 — 임시파일 경로(/download/...)가 아니다.
    assert "/redownload" in response.text
    assert "/download/" not in response.text


def test_generate_passes_form_values_to_pipeline(client, monkeypatch, saved_rows):
    captured = {}

    def capture(concept_input: ConceptInput):
        captured["input"] = concept_input
        return PipelineResult(
            lesson_plan=LESSON_PLAN, validation=ValidationResult(passed=True), warning=None
        )

    monkeypatch.setattr(main, "run_pipeline", capture)

    client.post("/generate", data={"concept": "이미지 인식", "grade": 5})

    assert captured["input"].raw_concept_name == "이미지 인식"
    assert captured["input"].target_grade == 5
    assert captured["input"].subject_hint is None


def test_generate_blocked_when_rate_limit_exceeded(client, monkeypatch, saved_rows):
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 99)
    monkeypatch.setattr(
        main, "run_pipeline", lambda concept_input: pytest.fail("한도 초과인데 파이프라인이 실행됨")
    )

    response = client.post("/generate", data={"concept": "이미지 인식", "grade": 5})

    assert response.status_code == 429
    assert saved_rows == []


# ---------- 재출력: 무저장 스트리밍 · 카운트 제외 ----------


@pytest.fixture
def fake_docx(monkeypatch):
    monkeypatch.setattr(main, "LessonOutput", lambda **kwargs: kwargs)
    monkeypatch.setattr(main, "render_lesson_docx", lambda lesson: b"PK\x03\x04docx")


def test_redownload_streams_bytes_without_touching_disk(
    client, monkeypatch, saved_rows, fake_docx, user
):
    item = _lesson_request(user.id, LESSON_PLAN)
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: item)

    response = client.get(f"/mypage/requests/{item.id}/redownload")

    assert response.status_code == 200
    assert response.content == b"PK\x03\x04docx"
    assert response.headers["content-type"] == main.DOCX_MEDIA_TYPE
    # 재출력은 LLM을 부르지 않으므로 레이트리밋에 잡히면 안 된다.
    assert saved_rows == []


def test_redownload_encodes_korean_filename(client, monkeypatch, fake_docx, user):
    item = _lesson_request(user.id, LESSON_PLAN)
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: item)

    disposition = client.get(f"/mypage/requests/{item.id}/redownload").headers[
        "content-disposition"
    ]

    encoded = disposition.split("filename*=UTF-8''")[1]
    assert unquote(encoded) == "인공지능 이미지 인식.docx"
    assert disposition.startswith("attachment;")


def test_redownload_strips_path_separators_from_filename(client, monkeypatch, fake_docx, user):
    item = _lesson_request(user.id, {**LESSON_PLAN, "topic": 'a/b\\c:d*e?f"g<h>i|j'})
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: item)

    disposition = client.get(f"/mypage/requests/{item.id}/redownload").headers[
        "content-disposition"
    ]

    assert unquote(disposition.split("filename*=UTF-8''")[1]) == "abcdefghij.docx"


def test_redownload_rejects_failed_attempt_row(client, monkeypatch, fake_docx, user):
    """실패 기록(lesson_output={})은 내려받을 교안이 없다 — 500이 아니라 400 안내."""
    item = _lesson_request(user.id, {}, validation_status="pipeline_error")
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: item)

    response = client.get(f"/mypage/requests/{item.id}/redownload")

    assert response.status_code == 400


def test_redownload_rejects_stale_schema(client, monkeypatch, user):
    """옛 저장분이 최신 LessonOutput 스키마와 안 맞을 때의 400 처리는 유지된다."""
    item = _lesson_request(user.id, LESSON_PLAN)
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: item)

    def boom(**kwargs):
        raise ValueError("missing field")

    monkeypatch.setattr(main, "LessonOutput", boom)

    response = client.get(f"/mypage/requests/{item.id}/redownload")

    assert response.status_code == 400


def test_redownload_forbids_other_users_request(client, monkeypatch, fake_docx):
    item = _lesson_request(uuid4(), LESSON_PLAN)  # 다른 사용자 소유
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: item)

    assert client.get(f"/mypage/requests/{item.id}/redownload").status_code == 403


def test_redownload_404_when_missing(client, monkeypatch, fake_docx):
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: None)

    assert client.get(f"/mypage/requests/{uuid4()}/redownload").status_code == 404


def test_download_route_is_gone(client):
    """임시파일 서빙 라우트는 제거됐다(인스턴스 로컬 디스크 의존 제거)."""
    assert client.get("/download/" + "0" * 32 + ".docx").status_code == 404


# ---------- 남은 횟수 배너 ----------


def test_index_shows_remaining_counts(client, monkeypatch):
    monkeypatch.setattr(
        db, "count_lesson_requests_since", lambda user_id, window: 2 if window.days == 1 else 4
    )

    body = " ".join(client.get("/").text.split())

    # 일 5회 중 2회 사용 → 3회 남음, 주 15회 중 4회 사용 → 11회 남음
    assert "오늘 <strong>3</strong>/5회" in body
    assert "이번 주 <strong>11</strong>/15회" in body


def test_index_shows_unlimited_for_admin(client, monkeypatch):
    admin = _user(role="admin")
    main.app.dependency_overrides[auth.get_optional_user] = lambda: admin

    body = client.get("/").text

    assert "무제한" in body


def test_index_skips_rate_lookup_when_anonymous(client, monkeypatch):
    """비로그인은 폼이 없으므로 배너도, 한도 조회 DB 왕복도 없어야 한다."""
    main.app.dependency_overrides[auth.get_optional_user] = lambda: None
    monkeypatch.setattr(
        db,
        "count_lesson_requests_since",
        lambda user_id, window: pytest.fail("비로그인인데 한도를 조회함"),
    )

    body = client.get("/").text

    assert 'id="rate-limit"' not in body
    assert "로그인</a>이 필요합니다" in body


def test_generate_refreshes_banner_out_of_band(client, monkeypatch, saved_rows):
    """htmx는 #result만 갈아끼우므로, 폼 옆 배너는 OOB swap으로만 갱신된다."""
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input: PipelineResult(
            lesson_plan={}, validation=ValidationResult(passed=False), warning="AI 개념이 아닙니다"
        ),
    )

    body = client.post("/generate", data={"concept": "김치찌개", "grade": 5}).text

    assert 'id="rate-limit"' in body
    assert 'hx-swap-oob="true"' in body


def test_mypage_detail_has_no_rate_banner(client, monkeypatch, user):
    """result.html을 include하는 상세 페이지에는 rate_status가 없어 배너가 안 뜬다."""
    item = _lesson_request(user.id, LESSON_PLAN)
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: item)

    assert 'id="rate-limit"' not in client.get(f"/mypage/requests/{item.id}").text


# ---------- 마이페이지: 실패 행 표시 ----------


def test_mypage_marks_failed_rows(client, monkeypatch, user):
    rows = [
        _lesson_request(user.id, LESSON_PLAN, validation_status="passed"),
        _lesson_request(user.id, {}, validation_status="검색 결과가 없습니다"),
    ]
    monkeypatch.setattr(db, "list_lesson_requests", lambda user_id, limit, offset: rows)
    monkeypatch.setattr(db, "count_lesson_requests", lambda user_id: len(rows))

    body = client.get("/mypage").text

    assert "생성 실패" in body
    assert "검색 결과가 없습니다" in body


def test_mypage_detail_hides_download_for_failed_row(client, monkeypatch, user):
    item = _lesson_request(user.id, {}, validation_status="pipeline_error")
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: item)

    body = client.get(f"/mypage/requests/{item.id}").text

    assert "DOCX 재출력" not in body
    assert "생성에 실패한 요청입니다" in body
