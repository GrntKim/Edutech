"""app/main.py 라우트 유닛 테스트. DB·파이프라인·DOCX 렌더는 항상 monkeypatch로 대체한다.

주 관심사는 두 가지 회귀 방지다.
1. 파이프라인을 실행한 요청은 결과와 무관하게 lesson_requests에 1행 남는다
   (그 INSERT가 곧 레이트리밋 카운트라, 실패가 무료가 되면 안 된다).
2. DOCX는 디스크를 거치지 않고 바이트로 내려간다(Cloud Run 다중 인스턴스 안전).
"""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agents.concept_collect import logic as concept_logic
from app.lib import auth, db
from app.lib.types import (
    ConceptInput,
    CurriculumChunk,
    GradeBand,
    LessonRequest,
    PipelineResult,
    PipelineStatus,
    Subject,
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


def _user(role="user", default_grade=None) -> User:
    return User(
        id=uuid4(),
        email="a@b.com",
        password_hash="x",
        name="테스트",
        role=role,
        created_at=datetime.now(timezone.utc),
        default_grade=default_grade,
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


@pytest.fixture(autouse=True)
def no_reset_lookup(monkeypatch):
    """레이트리밋 리셋 시각 조회는 기본적으로 막는다(테스트가 실제 DB로 나가지 않게).

    사용량이 0이면 auth가 조회 자체를 건너뛰지만, 사용량을 흉내 내는 테스트는
    이 스텁이 없으면 .env를 타고 진짜 DB에 붙는다.
    """
    monkeypatch.setattr(db, "oldest_lesson_request_since", lambda user_id, window: None)


@pytest.fixture(autouse=True)
def clean_generation_jobs():
    """job store는 모듈 전역이라 테스트 간에 남는다 — 매번 비우고 시작한다."""
    with main._generation_jobs_lock:
        main._generation_jobs.clear()
    yield
    with main._generation_jobs_lock:
        main._generation_jobs.clear()


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


def _generate(client, **form):
    """POST /generate 후 폴링을 한 번 돌려 최종 조각까지 받아온다.

    /generate는 job을 만들고 진행 패널만 즉시 돌려주므로, 결과는
    /generate/progress/{job_id}에서 나온다. TestClient는 백그라운드 작업을
    응답 반환 직후 동기로 실행하므로 POST가 돌아온 시점에 파이프라인은 이미
    끝나 있다.

    반환값은 (POST 응답, 최종 조각 응답)이다. POST가 실패하면 둘 다 그 응답.
    """
    post = client.post("/generate", data=form)
    if post.status_code != 200:
        return post, post
    match = re.search(r"/generate/progress/([0-9a-f]+)", post.text)
    assert match is not None, f"진행 패널에 job_id가 없다: {post.text[:200]}"
    return post, client.get(f"/generate/progress/{match.group(1)}")


# ---------- 파이프라인 실행 = 카운트 ----------


def test_generate_records_attempt_when_search_returns_nothing(client, monkeypatch, saved_rows):
    """A2 검색 0건으로 조기 종료해도 A1·A2 Gemini 비용은 이미 났다 — 카운트되어야 한다."""
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input, on_stage=None: PipelineResult(
            lesson_plan={},
            validation=ValidationResult(passed=False),
            status=PipelineStatus.NO_CURRICULUM_MATCH,
            warning="해당 학년에서 연결 가능한 교육과정 성취기준을 찾지 못했습니다.",
        ),
    )

    _post, response = _generate(client, concept="이미지 인식", grade=5)

    assert response.status_code == 200
    assert len(saved_rows) == 1
    assert saved_rows[0]["lesson_output"] == {}
    assert saved_rows[0]["validation_status"] == "no_curriculum_match"
    assert saved_rows[0]["mapped_curriculum_code"] is None


def test_generate_skips_count_when_input_ambiguous(client, monkeypatch, saved_rows):
    """ambiguous_input은 A1이 LLM 호출 전 규칙으로 걸러낸 경로라 비용이 0이다.

    "인공지능"·빈 값 등 처음 쓰는 교사가 가장 먼저 칠 입력들이 여기 걸리므로,
    안내만 보고 다시 입력하는 흐름에서 일 한도가 깎이면 안 된다.
    """
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input, on_stage=None: PipelineResult(
            lesson_plan={},
            validation=ValidationResult(passed=False),
            status=PipelineStatus.AMBIGUOUS_INPUT,
            warning="입력하신 개념이 너무 넓어 특정할 수 없습니다.",
        ),
    )

    _post, response = _generate(client, concept="인공지능", grade=5)

    assert response.status_code == 200
    assert "너무 넓어" in response.text
    assert saved_rows == []


def test_generate_records_attempt_when_concept_unsupported(client, monkeypatch, saved_rows):
    """unsupported_concept은 A1이 LLM을 1회 호출한 뒤 나오므로 계속 카운트한다."""
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input, on_stage=None: PipelineResult(
            lesson_plan={},
            validation=ValidationResult(passed=False),
            status=PipelineStatus.UNSUPPORTED_CONCEPT,
            warning="입력하신 내용은 AI 개념으로 인식되지 않았습니다.",
        ),
    )

    _generate(client, concept="김치찌개", grade=3)

    assert len(saved_rows) == 1
    assert saved_rows[0]["lesson_output"] == {}
    assert saved_rows[0]["validation_status"] == "unsupported_concept"


def test_generate_records_attempt_when_pipeline_raises(client, monkeypatch, saved_rows):
    """파이프라인이 예외로 죽어도 이미 쓴 비용은 카운트한다(그리고 500은 그대로 난다)."""

    def boom(concept_input, on_stage=None):
        raise RuntimeError("gemini down")

    monkeypatch.setattr(main, "run_pipeline", boom)

    _post, response = _generate(client, concept="이미지 인식", grade=5)

    # 파이프라인이 백그라운드에서 도므로 500이 아니라, 폴링이 받아가는 안내
    # 문구로 실패가 전달된다.
    assert response.status_code == 200
    assert main.MSG_GENERATION_FAILED in response.text
    assert len(saved_rows) == 1
    assert saved_rows[0]["validation_status"] == "pipeline_error"
    assert saved_rows[0]["lesson_output"] == {}


def test_generate_records_success_once(client, monkeypatch, saved_rows):
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input, on_stage=None: PipelineResult(
            lesson_plan=LESSON_PLAN,
            validation=ValidationResult(passed=True),
            status=PipelineStatus.SUCCESS,
            warning=None,
        ),
    )

    _post, response = _generate(client, concept="이미지 인식", grade=5)

    assert response.status_code == 200
    assert len(saved_rows) == 1
    assert saved_rows[0]["validation_status"] == "success"
    assert saved_rows[0]["lesson_output"] == LESSON_PLAN
    # 다운로드 링크는 저장된 행을 가리킨다 — 임시파일 경로(/download/...)가 아니다.
    assert "/redownload" in response.text
    assert "/download/" not in response.text


def test_success_fragment_hides_form_and_offers_reset(client, monkeypatch, saved_rows):
    """교안이 나오면 위쪽 폼을 OOB로 비우고, 상단 액션 바에 새로 생성을 둔다."""
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input, on_stage=None: PipelineResult(
            lesson_plan=LESSON_PLAN,
            validation=ValidationResult(passed=True),
            status=PipelineStatus.SUCCESS,
            warning=None,
        ),
    )

    _post, response = _generate(client, concept="이미지 인식", grade=5)
    body = " ".join(response.text.split())

    assert '<div id="generate-form" hx-swap-oob="true"></div>' in body
    assert 'class="regen-btn"' in body
    # 다운로드는 제목 위 액션 바 안에 있어야 한다.
    assert body.index("download-btn") < body.index("<h1>교수학습과정안</h1>")


def test_failure_fragment_keeps_form(client, monkeypatch, saved_rows):
    """교안 없이 안내만 나오는 경우에는 폼을 남긴다 — 바로 다시 시도해야 한다."""
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input, on_stage=None: PipelineResult(
            lesson_plan={},
            validation=ValidationResult(passed=False),
            status=PipelineStatus.UNSUPPORTED_CONCEPT,
            warning="AI 개념이 아닙니다",
        ),
    )

    _post, response = _generate(client, concept="김치찌개", grade=5)

    assert 'id="generate-form"' not in response.text
    assert "regen-btn" not in response.text


def test_generate_passes_form_values_to_pipeline(client, monkeypatch, saved_rows):
    captured = {}

    def capture(concept_input: ConceptInput, on_stage=None):
        captured["input"] = concept_input
        return PipelineResult(
            lesson_plan=LESSON_PLAN,
            validation=ValidationResult(passed=True),
            status=PipelineStatus.SUCCESS,
            warning=None,
        )

    monkeypatch.setattr(main, "run_pipeline", capture)

    _generate(client, concept="이미지 인식", grade=5)

    assert captured["input"].raw_concept_name == "이미지 인식"
    assert captured["input"].target_grade == 5
    assert captured["input"].subject_hint is None


def test_generate_blocked_when_rate_limit_exceeded(client, monkeypatch, saved_rows):
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 99)
    monkeypatch.setattr(
        main, "run_pipeline", lambda concept_input, on_stage=None: pytest.fail("한도 초과인데 파이프라인이 실행됨")
    )

    response = client.post("/generate", data={"concept": "이미지 인식", "grade": 5})

    assert response.status_code == 429
    assert saved_rows == []


# ---------- 진행 표시 폴링 ----------


def test_generate_returns_polling_panel_before_result(client, monkeypatch, saved_rows):
    """POST는 파이프라인을 기다리지 않고 진행 패널만 즉시 돌려준다."""
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input, on_stage=None: PipelineResult(
            lesson_plan=LESSON_PLAN,
            validation=ValidationResult(passed=True),
            status=PipelineStatus.SUCCESS,
            warning=None,
        ),
    )

    response = client.post("/generate", data={"concept": "이미지 인식", "grade": 5})

    assert response.status_code == 200
    assert 'id="gen-progress"' in response.text
    assert 'hx-trigger="every 1s"' in response.text
    # 첫 단계가 현재 단계로 표시되고, 다섯 단계가 모두 보인다.
    assert '<li class="current">\n            개념 분석' in response.text
    for _code, label in main.STAGES:
        assert label in response.text
    # 결과 조각은 아직 아니다.
    assert "/redownload" not in response.text


def test_generate_progress_tracks_stage_and_retry(client, monkeypatch, saved_rows):
    """on_stage 콜백이 job 상태를 갱신해야 폴링이 실제 진행을 보여준다."""
    observed = []

    def fake_pipeline(concept_input, on_stage=None):
        on_stage("A2", "start", 0)
        observed.append(_job_states())
        # "end"는 무시한다 — 다음 단계의 "start"까지 직전 단계가 그대로 보여야 한다.
        on_stage("A2", "end", 0)
        observed.append(_job_states())
        on_stage("C", "start", 2)
        observed.append(_job_states())
        return PipelineResult(
            lesson_plan=LESSON_PLAN,
            validation=ValidationResult(passed=True),
            status=PipelineStatus.SUCCESS,
            warning=None,
        )

    monkeypatch.setattr(main, "run_pipeline", fake_pipeline)

    client.post("/generate", data={"concept": "이미지 인식", "grade": 5})

    assert observed == [[("A2", 0)], [("A2", 0)], [("C", 2)]]


def _job_states():
    with main._generation_jobs_lock:
        return [(job.stage, job.retry_count) for job in main._generation_jobs.values()]


def _job_progress():
    with main._generation_jobs_lock:
        return [job.progress for job in main._generation_jobs.values()]


def test_progress_bar_fills_by_stage_and_never_goes_backwards(client, monkeypatch, saved_rows):
    """단계마다 20%씩 차오르되, 재시도로 되돌아가도 막대는 줄지 않는다."""
    observed = []

    def fake_pipeline(concept_input, on_stage=None):
        for stage, retry in [("A1", 0), ("A2", 0), ("B", 0), ("C", 0), ("D", 0), ("C", 1), ("D", 1)]:
            on_stage(stage, "start", retry)
            observed.append(_job_progress()[0])
        return PipelineResult(
            lesson_plan=LESSON_PLAN,
            validation=ValidationResult(passed=True),
            status=PipelineStatus.SUCCESS,
            warning=None,
        )

    monkeypatch.setattr(main, "run_pipeline", fake_pipeline)

    client.post("/generate", data={"concept": "이미지 인식", "grade": 5})

    # 마지막 단계는 95%에서 멈춘다 — 결과가 아직 없는데 100%면 다 끝난 것처럼 보인다.
    assert observed == [20, 40, 60, 80, 95, 95, 95]


def test_progress_fragment_carries_previous_value_for_animation(client, monkeypatch, saved_rows):
    """막대가 튀지 않으려면 직전에 내보낸 값이 조각에 같이 실려야 한다.

    패널은 폴링마다 outerHTML로 통째 교체돼 매번 새 DOM 노드가 되므로 CSS
    transition이 걸리지 않는다.
    """
    job_id = "b" * 32
    job = main._GenerationJob(
        user=client.app.dependency_overrides[auth.get_current_user](),
        concept="이미지 인식",
        grade=5,
    )
    job.stage = "B"
    job.progress = 60
    job.reported_progress = 40
    with main._generation_jobs_lock:
        main._generation_jobs[job_id] = job

    body = client.get(f"/generate/progress/{job_id}").text

    assert "--gen-from: 40%" in body
    assert "--gen-to: 60%" in body
    assert 'aria-valuenow="60"' in body
    # 다음 폴링은 60에서 출발해야 한다.
    with main._generation_jobs_lock:
        assert main._generation_jobs[job_id].reported_progress == 60


def test_generate_progress_hides_other_users_job(client, monkeypatch, saved_rows):
    """job_id를 알아내도 남의 생성 결과는 볼 수 없다."""
    other = _user()
    job_id = "f" * 32
    with main._generation_jobs_lock:
        main._generation_jobs[job_id] = main._GenerationJob(
            user=other, concept="이미지 인식", grade=5
        )
    try:
        response = client.get(f"/generate/progress/{job_id}")
    finally:
        with main._generation_jobs_lock:
            main._generation_jobs.pop(job_id, None)

    assert response.status_code == 404


def test_generate_progress_404_for_unknown_job(client, saved_rows):
    assert client.get("/generate/progress/" + "0" * 32).status_code == 404


def test_generate_progress_stops_polling_after_timeout(client, monkeypatch, saved_rows):
    """끝나지 않는 생성이 무한 폴링으로 남지 않는다."""
    job_id = "a" * 32
    job = main._GenerationJob(user=_user(), concept="이미지 인식", grade=5)
    # 소유권 검사를 통과해야 하므로 로그인 사용자로 맞춘다.
    job.user = client.app.dependency_overrides[auth.get_current_user]()
    job.started_at -= main.GENERATION_TIMEOUT_SECONDS + 1
    with main._generation_jobs_lock:
        main._generation_jobs[job_id] = job
    try:
        response = client.get(f"/generate/progress/{job_id}")
    finally:
        with main._generation_jobs_lock:
            main._generation_jobs.pop(job_id, None)

    assert response.status_code == 200
    assert main.MSG_GENERATION_TIMEOUT in response.text
    assert 'id="gen-progress"' not in response.text


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


# ---------- 헤더 ----------


def test_header_has_accessible_nav_toggle(client):
    """좁은 화면 사이드바를 여는 버튼 — 스크린리더가 상태를 읽을 수 있어야 한다."""
    body = client.get("/").text

    assert 'aria-controls="site-nav"' in body
    assert 'aria-expanded="false"' in body
    assert 'id="site-nav"' in body


def test_header_is_identical_across_pages(client, monkeypatch):
    """페이지마다 헤더 구성이 달라지면 안 된다(관리자 화면에만 링크가 더 생겼었다)."""
    admin = _as_admin()
    # 웰컴("/")은 get_optional_user를 쓴다 — 같은 계정으로 봐야 헤더가 비교된다.
    main.app.dependency_overrides[auth.get_optional_user] = lambda: admin
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 0)
    monkeypatch.setattr(db, "search_chunks", lambda **kwargs: [])
    monkeypatch.setattr(db, "count_chunks", lambda **kwargs: 0)

    def header(path: str) -> str:
        body = client.get(path).text
        return body[body.index("<header"): body.index("</header>")]

    assert header("/admin/chunks") == header("/")
    assert header("/admin") == header("/")
    assert "관리자 대시보드" not in client.get("/admin/chunks").text.split("</header>")[0]


# ---------- 웰컴/생성 화면 분리 ----------


def test_root_is_welcome_without_form(client, monkeypatch):
    """"/"는 소개 페이지다 — 생성 폼도, 남은 횟수 배너도 없다."""
    monkeypatch.setattr(
        db, "count_lesson_requests_since", lambda user_id, window: pytest.fail("웰컴이 한도를 조회함")
    )

    body = client.get("/").text

    assert 'id="generation-form"' not in body
    assert 'id="rate-limit"' not in body


def test_welcome_cta_follows_login_state(client):
    logged_in = client.get("/").text
    assert 'href="/generate"' in logged_in

    main.app.dependency_overrides[auth.get_optional_user] = lambda: None
    anonymous = client.get("/").text
    assert 'class="cta-btn"' in anonymous
    assert 'href="/login"' in anonymous


def test_generate_page_renders_form(client, monkeypatch):
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 0)

    body = client.get("/generate").text

    assert 'id="generation-form"' in body
    assert 'hx-post="/generate"' in body


def test_generate_page_help_uses_agent_constants(client, monkeypatch):
    """거부 조건 안내는 A1의 상수에서 파생한다 — 화면에 숫자를 박아두지 않는다."""
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 0)

    body = client.get("/generate").text

    assert f"{concept_logic.MIN_LENGTH}~{concept_logic.MAX_LENGTH}자" in body
    for term in concept_logic.BROAD_TERMS:
        assert term in body
    assert "생성 횟수는 차감되지 않습니다" in body


# ---------- 남은 횟수 배너 ----------


def test_generate_page_shows_remaining_counts(client, monkeypatch):
    monkeypatch.setattr(
        db, "count_lesson_requests_since", lambda user_id, window: 2 if window.days == 1 else 4
    )

    body = " ".join(client.get("/generate").text.split())

    # 일 5회 중 2회 사용 → 3회 남음, 주 15회 중 4회 사용 → 11회 남음
    assert "오늘 <strong>3</strong>/5회" in body
    assert "이번 주 <strong>11</strong>/15회" in body


def test_banner_shows_when_limits_recover(client, monkeypatch):
    """롤링 윈도우라 자정 초기화가 아니다 — 언제 1회 회복되는지 알려준다."""
    oldest = datetime.now(timezone.utc) - timedelta(hours=21)
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 2)
    monkeypatch.setattr(db, "oldest_lesson_request_since", lambda user_id, window: oldest)

    body = " ".join(client.get("/generate").text.split())

    # 일 윈도우(24h)는 3시간 뒤, 주 윈도우(7d)는 6일 뒤에 가장 오래된 행이 빠진다.
    assert "일 한도 약 3시간 뒤 1회 회복" in body
    assert "주 한도 약 7일 뒤 1회 회복" in body


def test_banner_omits_reset_when_unused(client, monkeypatch):
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 0)

    body = client.get("/generate").text

    assert "회복" not in body


def test_rate_limit_block_message_names_blocking_window(client, monkeypatch, saved_rows):
    """일 한도로 막혔으면 일 한도 회복 시각만 말한다 — 주 한도 시각은 훨씬 멀다."""
    oldest = datetime.now(timezone.utc) - timedelta(hours=22)
    monkeypatch.setattr(
        db, "count_lesson_requests_since", lambda user_id, window: 5 if window.days == 1 else 6
    )
    monkeypatch.setattr(db, "oldest_lesson_request_since", lambda user_id, window: oldest)

    response = client.post("/generate", data={"concept": "이미지 인식", "grade": 5})

    assert response.status_code == 429
    assert "일 한도는 약 2시간 뒤 1회 회복됩니다." in response.text
    assert "주 한도" not in response.text


def test_until_label_rounds_up_by_unit():
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

    assert main.until_label(now + timedelta(minutes=59), now) == "약 59분 뒤"
    assert main.until_label(now + timedelta(hours=2, minutes=1), now) == "약 3시간 뒤"
    assert main.until_label(now + timedelta(days=2, hours=1), now) == "약 3일 뒤"
    # 이미 지난 시각은 다음 조회에서 카운트가 빠진다 — 음수 시간을 보여주지 않는다.
    assert main.until_label(now - timedelta(minutes=5), now) == "곧"


def test_until_label_treats_naive_value_as_utc():
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

    assert main.until_label(datetime(2026, 8, 14, 14, 0), now) == "약 2시간 뒤"


def test_generate_page_shows_unlimited_for_admin(client, monkeypatch):
    admin = _user(role="admin")
    main.app.dependency_overrides[auth.get_optional_user] = lambda: admin

    body = client.get("/generate").text

    assert "무제한" in body


def test_generate_page_skips_rate_lookup_when_anonymous(client, monkeypatch):
    """비로그인은 폼이 없으므로 배너도, 한도 조회 DB 왕복도 없어야 한다."""
    main.app.dependency_overrides[auth.get_optional_user] = lambda: None
    monkeypatch.setattr(
        db,
        "count_lesson_requests_since",
        lambda user_id, window: pytest.fail("비로그인인데 한도를 조회함"),
    )

    body = client.get("/generate").text

    assert 'id="rate-limit"' not in body
    assert "로그인</a>이 필요합니다" in body


def test_generate_refreshes_banner_out_of_band(client, monkeypatch, saved_rows):
    """htmx는 #result만 갈아끼우므로, 폼 옆 배너는 OOB swap으로만 갱신된다."""
    monkeypatch.setattr(
        main,
        "run_pipeline",
        lambda concept_input, on_stage=None: PipelineResult(
            lesson_plan={},
            validation=ValidationResult(passed=False),
            status=PipelineStatus.UNSUPPORTED_CONCEPT,
            warning="AI 개념이 아닙니다",
        ),
    )

    _post, response = _generate(client, concept="김치찌개", grade=5)
    body = response.text

    assert 'id="rate-limit"' in body
    assert 'hx-swap-oob="true"' in body


def test_pages_do_not_use_document_write(client, monkeypatch):
    """hx-boost가 body를 교체한 뒤 실행되는 document.write는 문서 전체를 덮어쓴다.

    푸터 연도를 그렇게 찍고 있어서, 링크로 이동하면 페이지에 연도 네 글자만
    남고 새로고침해야 정상으로 돌아왔다. 연도는 서버에서 렌더한다.
    """
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 0)

    for path in ("/", "/generate", "/login", "/signup"):
        body = client.get(path).text
        assert "document.write" not in body, path
        assert f"&copy; {main.current_year()} MATCHU" in body, path


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
    monkeypatch.setattr(db, "list_lesson_requests", lambda user_id, limit, offset, subject=None: rows)
    monkeypatch.setattr(db, "count_lesson_requests", lambda user_id, subject=None: len(rows))

    body = client.get("/mypage").text

    assert "생성 실패" in body
    # PipelineStatus 도입 전 행은 경고 문구가 그대로 들어 있다 — 원문 유지.
    assert "검색 결과가 없습니다" in body


def test_created_at_is_shown_in_kst(client, monkeypatch, user):
    """DB에는 UTC로 저장되고 화면에는 KST로 나온다.

    이전에는 템플릿이 UTC 값을 그대로 찍어 9시간 뒤처진 시각이 떴다(24시간제라
    3시간 차이처럼 보였다).
    """
    row = _lesson_request(user.id, LESSON_PLAN)
    row.created_at = datetime(2026, 8, 13, 5, 23, tzinfo=timezone.utc)
    monkeypatch.setattr(db, "list_lesson_requests", lambda user_id, limit, offset, subject=None: [row])
    monkeypatch.setattr(db, "count_lesson_requests", lambda user_id, subject=None: 1)
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: row)

    for path in ("/mypage", f"/mypage/requests/{row.id}"):
        body = client.get(path).text
        assert "2026-08-13 14:23" in body, path
        assert "2026-08-13 05:23" not in body, path


def test_mypage_shows_subject_column(client, monkeypatch, user):
    """과목은 lesson_output에 저장된 값을 그대로 보여준다. 실패 기록은 값이 없어 "-"."""
    rows = [
        _lesson_request(user.id, LESSON_PLAN),
        _lesson_request(user.id, {}, validation_status="no_curriculum_match"),
    ]
    monkeypatch.setattr(
        db, "list_lesson_requests", lambda user_id, limit, offset, subject=None: rows
    )
    monkeypatch.setattr(db, "count_lesson_requests", lambda user_id, subject=None: len(rows))

    body = client.get("/mypage").text

    assert "<td>실과</td>" in body
    assert "<td>-</td>" in body


def test_mypage_passes_subject_filter_to_db(client, monkeypatch, user):
    calls = {}

    def fake_list(user_id, limit, offset, subject=None):
        calls["subject"] = subject
        return []

    monkeypatch.setattr(db, "list_lesson_requests", fake_list)
    monkeypatch.setattr(db, "count_lesson_requests", lambda user_id, subject=None: 0)

    body = client.get("/mypage", params={"subject": "수학"}).text

    assert calls["subject"] == "수학"
    # 결과가 0건이어도 필터 폼은 남아 있어야 되돌아올 수 있다.
    assert 'name="subject"' in body


def test_mypage_subject_options_come_from_labels(client, monkeypatch):
    """옵션은 하드코딩이 아니라 C의 subject_label()에서 파생한다."""
    monkeypatch.setattr(
        db, "list_lesson_requests", lambda user_id, limit, offset, subject=None: []
    )
    monkeypatch.setattr(db, "count_lesson_requests", lambda user_id, subject=None: 0)

    body = client.get("/mypage").text

    for label in main.SUBJECT_LABEL_OPTIONS:
        assert f'<option value="{label}"' in body


def test_mypage_pagination_keeps_subject_filter(client, monkeypatch, user):
    rows = [_lesson_request(user.id, LESSON_PLAN)]
    monkeypatch.setattr(
        db, "list_lesson_requests", lambda user_id, limit, offset, subject=None: rows
    )
    monkeypatch.setattr(
        db, "count_lesson_requests", lambda user_id, subject=None: main.MYPAGE_PAGE_SIZE * 3
    )

    body = client.get("/mypage", params={"subject": "수학"}).text

    assert f"subject={quote('수학')}&amp;page=2" in body


# ---------- 마이페이지: 담당 학년 기본값 ----------


def _stub_empty_history(monkeypatch):
    monkeypatch.setattr(
        db, "list_lesson_requests", lambda user_id, limit, offset, subject=None: []
    )
    monkeypatch.setattr(db, "count_lesson_requests", lambda user_id, subject=None: 0)


def test_mypage_preselects_saved_default_grade(client, monkeypatch):
    _stub_empty_history(monkeypatch)
    main.app.dependency_overrides[auth.get_current_user] = lambda: _user(default_grade=4)

    body = " ".join(client.get("/mypage").text.split())

    assert '<option value="4" selected>4학년</option>' in body
    assert '<option value="">설정 안 함</option>' in body


def test_generate_page_defaults_to_saved_grade(client, monkeypatch):
    """설정돼 있으면 생성 화면 학년이 그 값으로 열린다."""
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 0)
    user_with_grade = _user(default_grade=6)
    main.app.dependency_overrides[auth.get_optional_user] = lambda: user_with_grade

    body = " ".join(client.get("/generate").text.split())

    # 왜 미리 골라져 있는지 보이게 표시한다.
    assert '<option value="6" selected>6학년 (기본 설정)</option>' in body
    assert "selected" not in body.split('<option value="6"')[0]
    assert "기본 설정" not in body.split('<option value="6"')[0]


def test_generate_page_has_no_preselection_without_setting(client, monkeypatch):
    """설정이 없으면 첫 옵션(1학년)이 선택된 기존 동작 그대로다."""
    monkeypatch.setattr(db, "count_lesson_requests_since", lambda user_id, window: 0)

    body = client.get("/generate").text

    assert "selected" not in body


def test_mypage_settings_saves_grade_and_redirects(client, monkeypatch, user):
    calls = {}
    monkeypatch.setattr(
        db,
        "update_user_default_grade",
        lambda user_id, default_grade: calls.update(user_id=user_id, grade=default_grade),
    )

    response = client.post(
        "/mypage/settings", data={"default_grade": "3"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/mypage"
    assert calls == {"user_id": user.id, "grade": 3}


def test_mypage_settings_clears_grade_with_empty_value(client, monkeypatch):
    """"설정 안 함"을 고르면 NULL로 되돌린다."""
    calls = {}
    monkeypatch.setattr(
        db,
        "update_user_default_grade",
        lambda user_id, default_grade: calls.update(grade=default_grade),
    )

    response = client.post(
        "/mypage/settings", data={"default_grade": ""}, follow_redirects=False
    )

    assert response.status_code == 303
    assert calls == {"grade": None}


@pytest.mark.parametrize("value", ["7", "0", "abc", "3.5"])
def test_mypage_settings_rejects_out_of_range_grade(client, monkeypatch, value):
    """폼을 직접 조립한 요청 방어 — CHECK 제약에 닿기 전에 막고 저장도 하지 않는다."""
    monkeypatch.setattr(
        db,
        "update_user_default_grade",
        lambda user_id, default_grade: pytest.fail(f"잘못된 값이 저장됨: {default_grade}"),
    )

    response = client.post(
        "/mypage/settings", data={"default_grade": value}, follow_redirects=False
    )

    assert response.status_code == 400


def test_grade_choices_come_from_grade_band_mapping():
    """생성 폼과 설정 UI가 같은 근거(학년군 매핑)를 쓴다 — 하드코딩 금지."""
    assert main.GRADE_CHOICES == (1, 2, 3, 4, 5, 6)


def test_to_kst_treats_naive_value_as_utc():
    """DDL이 timestamptz가 아니게 만들어지는 사고에 대한 방어(auth._is_expired와 같은 규약)."""
    assert main.to_kst(datetime(2026, 8, 13, 5, 23)) == "2026-08-13 14:23"


def test_mypage_renders_status_code_as_label(client, monkeypatch, user):
    """validation_status에 저장된 PipelineStatus 값은 화면에서 한글로 바뀐다."""
    rows = [
        _lesson_request(user.id, LESSON_PLAN, validation_status="success"),
        _lesson_request(user.id, {}, validation_status="no_curriculum_match"),
    ]
    monkeypatch.setattr(db, "list_lesson_requests", lambda user_id, limit, offset, subject=None: rows)
    monkeypatch.setattr(db, "count_lesson_requests", lambda user_id, subject=None: len(rows))

    body = client.get("/mypage").text

    assert "검증 통과" in body
    assert "연결 가능한 성취기준 없음" in body
    assert "no_curriculum_match" not in body


def test_mypage_detail_hides_download_for_failed_row(client, monkeypatch, user):
    item = _lesson_request(user.id, {}, validation_status="pipeline_error")
    monkeypatch.setattr(db, "get_lesson_request_by_id", lambda request_id: item)

    body = client.get(f"/mypage/requests/{item.id}").text

    assert "DOCX 재출력" not in body
    assert "생성에 실패한 요청입니다" in body


# ---------- 관리자: 성취기준 관리(REQ-002 관리자용 데이터 수정 API) ----------


def _chunk(**overrides) -> CurriculumChunk:
    fields = {
        "chunk_id": "4과02-01",
        "subject": Subject.SCIENCE,
        "grade_band": GradeBand.G3_4,
        "unit_name": "동물의 생활",
        "domain": "생명",
        "core_idea": "동물 분류",
        "achievement_code": "[4과02-01]",
        "achievement_text": "동물을 분류할 수 있다.",
        "explanation": "관찰 가능한 특징을 기준으로 분류한다.",
        "inquiry_activities": [],
        "source_page": 12,
    }
    fields.update(overrides)
    return CurriculumChunk(**fields)


def _as_admin():
    admin = _user(role="admin")
    main.app.dependency_overrides[auth.get_current_user] = lambda: admin
    return admin


def test_admin_chunks_forbidden_for_non_admin(client):
    # client 픽스처의 기본 user는 role="user".
    response = client.get("/admin/chunks")
    assert response.status_code == 403


def test_admin_chunks_lists_search_results(client, monkeypatch):
    _as_admin()
    monkeypatch.setattr(db, "search_chunks", lambda **kwargs: [_chunk()])
    monkeypatch.setattr(db, "count_chunks", lambda **kwargs: 1)

    body = client.get("/admin/chunks").text

    assert "4과02-01" in body
    assert "동물의 생활" in body


def test_admin_chunks_passes_query_and_subject_to_db(client, monkeypatch):
    _as_admin()
    calls = {}

    def fake_search(**kwargs):
        calls.update(kwargs)
        return []

    monkeypatch.setattr(db, "search_chunks", fake_search)
    # total_pages(3) >= page(2)여야 클램프 없이 page=2 그대로 조회된다.
    monkeypatch.setattr(db, "count_chunks", lambda **kwargs: main.ADMIN_CHUNKS_PAGE_SIZE * 3)

    client.get("/admin/chunks", params={"q": "분류", "subject": "SCIENCE", "page": 2})

    assert calls["query"] == "분류"
    assert calls["subject"] == Subject.SCIENCE
    assert calls["offset"] == main.ADMIN_CHUNKS_PAGE_SIZE  # page=2 → 두 번째 페이지 offset


def test_pagination_shows_ends_only_beyond_threshold(client, monkeypatch):
    """페이지가 몇 장 안 되면 이전/다음만, 임계값을 넘으면 처음/마지막도 나온다."""
    _as_admin()
    monkeypatch.setattr(db, "search_chunks", lambda **kwargs: [_chunk()])

    def body_for(total_pages: int) -> str:
        monkeypatch.setattr(
            db, "count_chunks", lambda **kwargs: main.ADMIN_CHUNKS_PAGE_SIZE * total_pages
        )
        return client.get("/admin/chunks", params={"page": 2}).text

    under = body_for(main.PAGINATION_ENDS_THRESHOLD)
    assert "처음" not in under
    assert "마지막" not in under

    over = body_for(main.PAGINATION_ENDS_THRESHOLD + 1)
    assert "처음" in over
    assert "마지막" in over


def test_pagination_preserves_filters_in_links(client, monkeypatch):
    """페이지를 넘어가도 검색어·과목 필터가 유지돼야 한다."""
    _as_admin()
    monkeypatch.setattr(db, "search_chunks", lambda **kwargs: [_chunk()])
    monkeypatch.setattr(db, "count_chunks", lambda **kwargs: main.ADMIN_CHUNKS_PAGE_SIZE * 3)

    body = client.get("/admin/chunks", params={"q": "분류", "subject": "SCIENCE"}).text

    assert f"q={quote('분류')}&amp;subject=SCIENCE&amp;page=2" in body


def test_admin_chunks_clamps_page_beyond_total_pages(client, monkeypatch):
    """오래된 북마크 등으로 total_pages를 넘는 page가 오면 마지막 페이지로
    되돌린다 — 안 그러면 결과가 있는데도 '검색 결과 없음'으로 보여 페이지네이션
    링크 자체가 사라지고 되돌아올 방법이 없어진다."""
    _as_admin()
    calls = {}
    monkeypatch.setattr(db, "search_chunks", lambda **kwargs: calls.update(kwargs) or [_chunk()])
    monkeypatch.setattr(db, "count_chunks", lambda **kwargs: 3)  # total_pages = 1

    response = client.get("/admin/chunks", params={"page": 9999})

    assert response.status_code == 200
    assert calls["offset"] == 0  # 1페이지로 클램프


def test_admin_chunks_ignores_invalid_subject_value(client, monkeypatch):
    """오타·구버전 링크로 잘못된 subject 값이 들어와도 500이 아니라 필터 없음으로 처리."""
    _as_admin()
    calls = {}
    monkeypatch.setattr(db, "search_chunks", lambda **kwargs: calls.update(kwargs) or [])
    monkeypatch.setattr(db, "count_chunks", lambda **kwargs: 0)

    response = client.get("/admin/chunks", params={"subject": "존재안함"})

    assert response.status_code == 200
    assert calls["subject"] is None


def test_admin_chunk_edit_form_404_when_missing(client, monkeypatch):
    _as_admin()
    monkeypatch.setattr(db, "get_chunk_by_id", lambda chunk_id: None)

    assert client.get("/admin/chunks/없는코드/edit").status_code == 404


def test_admin_chunk_edit_form_shows_existing_values(client, monkeypatch):
    _as_admin()
    monkeypatch.setattr(db, "get_chunk_by_id", lambda chunk_id: _chunk())

    body = client.get("/admin/chunks/4과02-01/edit").text

    assert "동물을 분류할 수 있다." in body


def test_admin_chunk_update_saves_and_shows_new_values(client, monkeypatch):
    _as_admin()
    updated = _chunk(achievement_text="정정된 원문")
    calls = {}

    def fake_update(chunk_id, **kwargs):
        calls["chunk_id"] = chunk_id
        calls.update(kwargs)
        return updated

    monkeypatch.setattr(db, "update_chunk", fake_update)

    response = client.post(
        "/admin/chunks/4과02-01",
        data={
            "unit_name": "동물의 생활",
            "domain": "생명",
            "core_idea": "동물 분류",
            "achievement_text": "정정된 원문",
            "explanation": "관찰 가능한 특징을 기준으로 분류한다.",
        },
    )

    assert response.status_code == 200
    assert "정정된 원문" in response.text
    assert calls["chunk_id"] == "4과02-01"


def test_admin_chunk_update_cannot_override_identity_fields(client, monkeypatch):
    """폼에 chunk_id/subject/achievement_code를 끼워 넣어도 라우트가 애초에 안 받는다
    (FastAPI가 선언 안 된 폼 필드를 조용히 무시) — db.update_chunk에도 안 넘어간다."""
    _as_admin()
    calls = {}

    def fake_update(chunk_id, **kwargs):
        calls["chunk_id"] = chunk_id
        calls.update(kwargs)
        return _chunk()

    monkeypatch.setattr(db, "update_chunk", fake_update)

    client.post(
        "/admin/chunks/4과02-01",
        data={
            "chunk_id": "다른코드",
            "subject": "MATH",
            "achievement_code": "[9수99-99]",
            "unit_name": "x",
            "domain": "x",
            "core_idea": "x",
            "achievement_text": "x",
            "explanation": "x",
        },
    )

    assert calls["chunk_id"] == "4과02-01"  # path 파라미터가 그대로 씀
    assert "subject" not in calls
    assert "achievement_code" not in calls


def test_admin_chunk_update_404_when_missing(client, monkeypatch):
    _as_admin()
    monkeypatch.setattr(db, "update_chunk", lambda chunk_id, **kwargs: None)

    response = client.post(
        "/admin/chunks/없는코드",
        data={
            "unit_name": "x",
            "domain": "x",
            "core_idea": "x",
            "achievement_text": "x",
            "explanation": "x",
        },
    )

    assert response.status_code == 404
