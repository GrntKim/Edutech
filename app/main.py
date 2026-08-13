# 소유: E(REQ-006)
"""FastAPI 진입점. 라우트는 D의 오케스트레이터만 호출하고,
개별 에이전트를 직접 알지 않는다(REQ-005 VALID-000-4).
"""

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.agents.lesson_generate import LessonOutput, render_lesson_docx
from app.agents.orchestrate import MAX_RETRIES, STAGES, run_pipeline
from app.lib import auth, db
from app.lib.types import (
    ConceptInput,
    LessonRequest,
    PipelineResult,
    PipelineStatus,
    Subject,
    User,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

MYPAGE_PAGE_SIZE = 10
ADMIN_CHUNKS_PAGE_SIZE = 20

# lesson_requests.validation_status에는 PipelineStatus 값이 그대로 들어간다.
# 화면에는 사람이 읽을 수 있는 문구로 바꿔서 보여준다.
_STATUS_LABELS = {
    PipelineStatus.SUCCESS.value: "검증 통과",
    PipelineStatus.MAX_RETRIES_EXCEEDED.value: "검증 미통과(재시도 소진)",
    PipelineStatus.VALIDATION_DIVERGED.value: "검증 미통과(수렴 실패)",
    PipelineStatus.UNSUPPORTED_CONCEPT.value: "AI 개념으로 인식되지 않음",
    PipelineStatus.AMBIGUOUS_INPUT.value: "입력이 너무 넓어 특정 불가",
    PipelineStatus.NO_CURRICULUM_MATCH.value: "연결 가능한 성취기준 없음",
    PipelineStatus.AGENT_ERROR.value: "일시적 오류로 생성 실패",
    PipelineStatus.PIPELINE_ERROR.value: "일시적 오류로 생성 실패",
}


def status_label(value: str) -> str:
    """validation_status 값을 화면용 문구로 바꾼다.

    PipelineStatus 도입 전에 저장된 행에는 경고 문구가 자유 문자열로 들어가
    있으므로, 매핑에 없는 값은 원문 그대로 내보낸다(기존 기록 호환).
    """
    return _STATUS_LABELS.get(value, value)


templates.env.filters["status_label"] = status_label


def current_year() -> int:
    """푸터의 저작권 연도.

    예전에는 템플릿에서 document.write(new Date().getFullYear())로 찍었는데,
    hx-boost가 body를 교체하면 그 스크립트가 문서 로드 이후에 실행된다 —
    그 시점의 document.write는 문서 전체를 덮어써서 페이지가 연도 네 글자만
    남는다. 서버에서 렌더해 스크립트 자체를 없앤다.
    """
    return datetime.now().year


templates.env.globals["current_year"] = current_year


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request, user: User | None = Depends(auth.get_optional_user)):
    # 비로그인은 폼 자체가 없으므로 한도도 조회하지 않는다(불필요한 DB 왕복 회피).
    rate_status = auth.check_rate_limit(user) if user is not None else None
    return templates.TemplateResponse(
        request, "index.html", {"user": user, "rate_status": rate_status}
    )


def _record_attempt(
    user: User, concept: str, grade: int, result: PipelineResult | None
) -> LessonRequest | None:
    """파이프라인을 실행한 요청을 결과와 무관하게 lesson_requests에 남긴다.

    이 INSERT 자체가 레이트리밋 카운트다(count_lesson_requests_since가 이
    테이블을 센다). A1의 unsupported_concept나 A2 검색 0건처럼 조기 종료한
    실행도 이미 Gemini를 호출해 비용이 발생한 뒤이므로 카운트한다 — 예전에는
    lesson_plan이 비면 저장을 건너뛰어, 0건이 재현되는 조합을 반복 클릭하면
    한도 없이 API를 쓸 수 있었다.

    단, ambiguous_input은 예외다. A1이 LLM을 부르기 전에 규칙으로 걸러내는
    경로라 Gemini 호출이 0회이고, 따라서 반복해도 우회 위험이 없다. 이 판정은
    호출부(generate)에서 하며 여기까지 오지 않는다.

    validation_status에는 PipelineStatus 값을 그대로 넣는다 — 예전처럼 경고
    문구를 담으면 사후에 실패 사유를 집계할 수 없다. 화면 표시는 status_label()이
    맡는다.

    실패 시도는 lesson_output이 빈 dict로 남는다(NULL이 아니므로 DDL 변경
    불필요). 마이페이지·재출력은 계속 이 값이 비었는지로 실패 행을 판별한다.

    result=None은 run_pipeline이 예외로 죽은 경우.
    """
    lesson_plan = result.lesson_plan if result is not None else {}
    status = (
        result.status.value if result is not None else PipelineStatus.PIPELINE_ERROR.value
    )

    try:
        return db.create_lesson_request(
            user_id=user.id,
            concept_name=concept,
            target_grade=grade,
            subject_hint=None,
            mapped_curriculum_code=lesson_plan.get("achievement_code"),
            lesson_output=lesson_plan,
            validation_status=status,
        )
    except Exception:
        logger.exception("lesson_request_save_failed")
        return None


def _docx_response(docx_bytes: bytes, raw_title: str) -> Response:
    """DOCX 바이트를 첨부파일 응답으로 감싼다 — 디스크에 아무것도 남기지 않는다.

    FileResponse가 대신 해 주던 파일명 인코딩을 직접 처리한다. 한글 파일명은
    RFC 5987 filename*(UTF-8 percent-encoding)으로 내려야 깨지지 않고, 그걸
    모르는 클라이언트를 위해 ASCII 폴백을 filename=에 함께 둔다.

    경로 구분자와 제어문자는 제거한다 — 파일명이 경로로 해석되거나 개행이
    Content-Disposition 헤더에 섞여 들어가는 것을 막기 위함이다.
    """
    safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "", raw_title).strip() or "교안"
    filename = f"{safe_title}.docx"
    ascii_fallback = re.sub(r"[^\x20-\x7e]", "_", filename).strip("_") or "lesson_plan.docx"

    return Response(
        content=docx_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_fallback}"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@dataclass
class _GenerationJob:
    """진행 중인 교안 생성 하나의 상태.

    파이프라인이 백그라운드에서 도는 동안 화면이 1초마다 폴링해 이 값을 읽는다.
    """

    user: User
    concept: str
    grade: int
    started_at: float = field(default_factory=time.monotonic)
    stage: str = STAGES[0][0]
    retry_count: int = 0
    done: bool = False
    result: PipelineResult | None = None
    request_id: UUID | None = None


# 진행 상태는 인스턴스 메모리에만 있다. 인스턴스가 둘 이상이면 폴링 요청이
# 다른 인스턴스로 가 job을 못 찾으므로, Cloud Run은 --max-instances=1로
# 배포해야 한다(session affinity는 best-effort라 대안이 못 된다).
# 스케일아웃이 필요해지면 이 상태를 Cloud SQL로 옮기는 것이 정공법이다.
_generation_jobs: dict[str, _GenerationJob] = {}
_generation_jobs_lock = threading.Lock()

# 폴링이 끝난 job을 남겨두면 메모리가 계속 는다. 결과를 이미 받아간 뒤에도
# 잠깐은 남겨둬야 재요청(스왑 직전 한 번 더 나가는 폴링 등)이 404가 나지 않는다.
GENERATION_JOB_TTL_SECONDS = 1800

# 이 시간을 넘겨도 안 끝나면 폴링을 멈춘다. 파이프라인 자체는 계속 돌아
# 기록은 마이페이지에 남는다.
GENERATION_TIMEOUT_SECONDS = 600

MSG_GENERATION_FAILED = "교안 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."
MSG_GENERATION_TIMEOUT = (
    "생성이 예상보다 오래 걸리고 있습니다. 완료되면 마이페이지에서 확인할 수 있습니다."
)


def _purge_expired_jobs() -> None:
    """TTL이 지난 job을 정리한다. 호출부가 락을 잡은 상태로 부른다."""
    now = time.monotonic()
    for job_id in [
        key
        for key, job in _generation_jobs.items()
        if now - job.started_at > GENERATION_JOB_TTL_SECONDS
    ]:
        del _generation_jobs[job_id]


def _run_generation(job_id: str) -> None:
    """백그라운드에서 파이프라인을 돌리고 결과를 job에 넣는다.

    응답을 이미 돌려보낸 뒤 실행되므로, 여기서 예외가 새어나가도 사용자에게
    전달할 응답이 없다. 모든 실패를 job.result로 변환해 폴링 쪽에서 보여준다.
    """
    with _generation_jobs_lock:
        job = _generation_jobs.get(job_id)
    if job is None:
        return

    def on_stage(stage: str, phase: str, retry_count: int) -> None:
        # "end"는 무시한다 — 다음 단계의 "start"가 곧바로 뒤따르므로, 그 사이에
        # 잠깐 "아무 단계도 아님" 상태를 만들 이유가 없다.
        if phase != "start":
            return
        with _generation_jobs_lock:
            current = _generation_jobs.get(job_id)
            if current is None:
                return
            current.stage = stage
            current.retry_count = retry_count

    try:
        result = run_pipeline(
            ConceptInput(
                raw_concept_name=job.concept,
                target_grade=job.grade,
                subject_hint=None,
            ),
            on_stage=on_stage,
        )
    except Exception:
        logger.exception("generation_failed")
        # 예외로 죽어도 여기까지 온 Gemini 호출 비용은 이미 발생했다 — 카운트하고 넘긴다.
        _record_attempt(job.user, job.concept, job.grade, None)
        _finish_generation(job_id, None, None)
        return

    if result.status is PipelineStatus.AMBIGUOUS_INPUT:
        # A1이 LLM 호출 전에 규칙만으로 걸러낸 경로라 API 비용이 0이다("AI",
        # "인공지능", 빈 값 등 — 처음 쓰는 교사가 가장 먼저 칠 입력들이 여기
        # 걸린다). 반복해도 쿼터를 안 쓰므로 레이트리밋에서 제외한다.
        # 나머지 경로는 Gemini를 이미 호출했으므로 현행대로 카운트한다.
        saved = None
    else:
        saved = _record_attempt(job.user, job.concept, job.grade, result)

    # 저장에 실패했으면 다운로드할 근거(lesson_requests 행)가 없으므로 버튼을 숨긴다.
    request_id = saved.id if saved is not None and saved.lesson_output else None
    _finish_generation(job_id, result, request_id)


def _finish_generation(
    job_id: str, result: PipelineResult | None, request_id: UUID | None
) -> None:
    with _generation_jobs_lock:
        job = _generation_jobs.get(job_id)
        if job is None:
            return
        job.result = result
        job.request_id = request_id
        job.done = True


def _progress_fragment(request: Request, job_id: str, job: _GenerationJob) -> Response:
    """진행 패널 조각. 자기 자신을 교체하며 계속 폴링한다."""
    stage_codes = [code for code, _ in STAGES]
    current_index = stage_codes.index(job.stage) if job.stage in stage_codes else 0
    return templates.TemplateResponse(
        request,
        "_generate_progress.html",
        {
            "job_id": job_id,
            "stages": STAGES,
            "current_index": current_index,
            "retry_count": job.retry_count,
            "max_retries": MAX_RETRIES,
        },
    )


def _result_fragment(
    request: Request,
    user: User,
    result: PipelineResult | None,
    request_id: UUID | None,
    warning: str | None = None,
) -> Response:
    """결과 조각. result가 None이면 lesson 없이 안내 문구만 나온다."""
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "user": user,
            "lesson": result.lesson_plan if result is not None else None,
            "warning": warning if result is None else result.warning,
            "request_id": request_id,
            # 방금 기록한 행까지 반영돼야 하므로 사전 검사 때 값을 재사용하지 않고 다시 센다.
            "rate_status": auth.check_rate_limit(user),
        },
    )


@app.post("/generate", response_class=HTMLResponse)
def generate(
    request: Request,
    background_tasks: BackgroundTasks,
    concept: str = Form(...),
    grade: int = Form(...),
    user: User = Depends(auth.get_current_user),
):
    """폼 제출을 받아 파이프라인을 시작하고 진행 패널 조각을 즉시 반환한다.

    파이프라인은 언어모델 호출이 여러 번 들어가 수십 초 이상 걸린다. 응답을
    붙잡고 있으면 그동안 화면에 보여줄 수 있는 것이 고정 문구뿐이라, 실행은
    백그라운드로 넘기고 화면은 /generate/progress/{job_id}를 폴링한다.

    htmx가 #result에 삽입하므로 전체 문서가 아니라 조각을 돌려준다.
    subject_hint는 1단계에서 UI에 노출하지 않으며, 생략이 아니라 명시적으로
    None을 전달한다(팀 합의 2026-08-04, REQ-006 SITE-001).

    실제 파이프라인을 실행하는 요청만 레이트리밋 대상이다(재출력은 별도
    라우트라 여기 안 걸림). admin은 check_rate_limit이 항상 allowed=True.

    DOCX는 여기서 만들지 않는다 — 다운로드 링크는 저장된 lesson_output으로
    그때그때 재생성하는 라우트를 가리킨다(인스턴스 로컬 파일 의존 제거).
    """
    rate_status = auth.check_rate_limit(user)
    if not rate_status.allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"생성 횟수를 초과했습니다 (일 {rate_status.daily_used}/{rate_status.daily_limit}회, "
                f"주 {rate_status.weekly_used}/{rate_status.weekly_limit}회). 잠시 후 다시 시도해 주세요."
            ),
        )

    job_id = uuid4().hex
    job = _GenerationJob(user=user, concept=concept, grade=grade)
    with _generation_jobs_lock:
        _purge_expired_jobs()
        _generation_jobs[job_id] = job

    background_tasks.add_task(_run_generation, job_id)
    return _progress_fragment(request, job_id, job)


@app.get("/generate/progress/{job_id}", response_class=HTMLResponse)
def generate_progress(
    request: Request, job_id: str, user: User = Depends(auth.get_current_user)
):
    """진행 중이면 진행 패널을, 끝났으면 결과 조각을 돌려준다.

    진행 패널은 자기 자신을 outerHTML로 교체하며 폴링하므로, 폴링 속성이 없는
    결과 조각으로 바뀌는 순간 폴링이 저절로 멈춘다.
    """
    with _generation_jobs_lock:
        job = _generation_jobs.get(job_id)

    # 소유자만 볼 수 있다. 남의 job_id를 찍어도 존재 여부조차 알려주지 않는다.
    if job is None or job.user.id != user.id:
        raise HTTPException(status_code=404, detail="생성 요청을 찾을 수 없습니다.")

    if job.done:
        if job.result is None:
            return _result_fragment(request, user, None, None, MSG_GENERATION_FAILED)
        return _result_fragment(request, user, job.result, job.request_id)

    if time.monotonic() - job.started_at > GENERATION_TIMEOUT_SECONDS:
        # 파이프라인은 계속 돌아 기록은 남는다. 화면의 폴링만 멈춘다.
        logger.warning(f"generation_poll_timeout job_id={job_id}")
        return _result_fragment(request, user, None, None, MSG_GENERATION_TIMEOUT)

    return _progress_fragment(request, job_id, job)


@app.get("/signup", response_class=HTMLResponse)
def signup_form(
    request: Request, current_user: User | None = Depends(auth.get_optional_user)
):
    return templates.TemplateResponse(
        request, "signup.html", {"error": None, "user": current_user}
    )


@app.post("/signup", response_class=HTMLResponse)
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    current_user: User | None = Depends(auth.get_optional_user),
):
    if len(password) < 8:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": "비밀번호는 8자 이상이어야 합니다.", "user": current_user},
            status_code=400,
        )

    try:
        user = db.create_user(
            email=email.strip().lower(),
            password_hash=auth.hash_password(password),
            name=name,
            role="user",
        )
    except db.EmailAlreadyExistsError:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": "이미 가입된 이메일입니다.", "user": current_user},
            status_code=400,
        )

    response = RedirectResponse(url="/mypage", status_code=303)
    auth.issue_session(response, user.id)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request, current_user: User | None = Depends(auth.get_optional_user)
):
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "user": current_user}
    )


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    current_user: User | None = Depends(auth.get_optional_user),
):
    user = db.get_user_by_email(email.strip().lower())
    if user is None or not auth.verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "이메일 또는 비밀번호가 올바르지 않습니다.", "user": current_user},
            status_code=401,
        )

    response = RedirectResponse(url="/mypage", status_code=303)
    auth.issue_session(response, user.id)
    return response


@app.post("/logout")
def logout(session_id: str | None = Cookie(default=None, alias=auth.SESSION_COOKIE_NAME)):
    """로그인 여부를 따지지 않는다 — 세션이 이미 만료/무효여도 쿠키는 항상
    지운다(get_current_user를 의존성으로 걸면 만료 세션에서 401이 먼저 나서
    쿠키가 안 지워지는 문제가 있었음)."""
    response = RedirectResponse(url="/", status_code=303)
    auth.clear_session(response, session_id)
    return response


@app.get("/mypage", response_class=HTMLResponse)
def mypage(request: Request, page: int = 1, user: User = Depends(auth.get_current_user)):
    page = max(page, 1)
    offset = (page - 1) * MYPAGE_PAGE_SIZE
    items = db.list_lesson_requests(user.id, limit=MYPAGE_PAGE_SIZE, offset=offset)
    total = db.count_lesson_requests(user.id)
    total_pages = max((total + MYPAGE_PAGE_SIZE - 1) // MYPAGE_PAGE_SIZE, 1)
    return templates.TemplateResponse(
        request,
        "mypage.html",
        {"user": user, "items": items, "page": page, "total_pages": total_pages},
    )


def _get_owned_lesson_request(request_id: UUID, user: User):
    """존재 확인(404) + 소유권 확인(403)을 한곳에 모은다 — 상세/재출력에서 공용.
    삭제는 SQL WHERE에 user_id를 직접 걸어 db.delete_lesson_request에서 자체
    스코프하므로 이 헬퍼를 쓰지 않는다."""
    item = db.get_lesson_request_by_id(request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="요청을 찾을 수 없습니다.")
    if item.user_id != user.id:
        raise HTTPException(status_code=403, detail="본인의 요청만 접근할 수 있습니다.")
    return item


@app.get("/mypage/requests/{request_id}", response_class=HTMLResponse)
def mypage_request_detail(
    request: Request, request_id: UUID, user: User = Depends(auth.get_current_user)
):
    item = _get_owned_lesson_request(request_id, user)
    return templates.TemplateResponse(request, "mypage_detail.html", {"user": user, "item": item})


@app.get("/mypage/requests/{request_id}/redownload")
def redownload(request_id: UUID, user: User = Depends(auth.get_current_user)):
    """저장된 lesson_output으로 DOCX를 재생성해 바이트를 그대로 반환한다.

    생성 직후 다운로드와 마이페이지 재출력이 모두 이 라우트를 쓴다. 파일을
    디스크에 두지 않으므로 Cloud Run이 요청마다 다른 인스턴스로 라우팅하거나
    인스턴스를 재활용해도 깨지지 않는다(이전 구조는 tempdir 파일 + 303
    리다이렉트라 리다이렉트된 요청이 다른 인스턴스에 닿으면 404였다).

    에이전트/파이프라인 호출이 없으므로 레이트리밋 대상이 아니다 —
    _record_attempt를 거치지 않는다. 상태를 바꾸지 않는 순수 조회라 GET이다.
    """
    item = _get_owned_lesson_request(request_id, user)

    # 파이프라인이 조기 종료한 실패 기록은 lesson_output이 비어 있다.
    if not item.lesson_output:
        raise HTTPException(
            status_code=400, detail="생성에 실패한 요청이라 내려받을 교안이 없습니다."
        )

    try:
        lesson = LessonOutput(**item.lesson_output)
        docx_bytes = render_lesson_docx(lesson)
    except Exception:
        # C의 출력 스키마가 바뀌어 옛 저장분과 안 맞을 수 있다(하위호환 미보장,
        # 의도적 — REQ-006 스코프). 500 스택트레이스 대신 안내만 준다.
        logger.exception("redownload_docx_failed request_id=%s", request_id)
        raise HTTPException(status_code=400, detail="이 기록은 재출력할 수 없습니다(저장된 형식이 최신 양식과 다릅니다).")

    # 결과 화면과 파일명을 맞추기 위해 교안의 topic을 우선 쓴다(없으면 입력 개념명).
    return _docx_response(docx_bytes, item.lesson_output.get("topic") or item.concept_name)


@app.post("/mypage/requests/{request_id}/delete")
def delete_lesson_request(request_id: UUID, user: User = Depends(auth.get_current_user)):
    deleted = db.delete_lesson_request(request_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="요청을 찾을 수 없습니다.")

    return RedirectResponse(url="/mypage", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, admin: User = Depends(auth.require_admin)):
    return templates.TemplateResponse(request, "admin.html", {"user": admin})


def _parse_subject_query(value: str | None) -> Subject | None:
    """쿼리스트링의 과목 값을 Subject로 변환한다. 잘못된 값(오타·구버전 링크
    등)은 500 대신 "필터 없음"으로 조용히 처리한다 — 관리자 검색 화면은
    필터가 아니라 목록 자체가 핵심이라 잘못된 필터로 전체가 막히면 안 된다."""
    if not value:
        return None
    try:
        return Subject(value)
    except ValueError:
        return None


@app.get("/admin/chunks", response_class=HTMLResponse)
def admin_chunks(
    request: Request,
    q: str | None = None,
    subject: str | None = None,
    page: int = 1,
    admin: User = Depends(auth.require_admin),
):
    """성취기준 검색·목록(RS-004 관리자용 데이터 수정 API 진입점).

    mypage.html과 동일하게 일반 GET 쿼리스트링 기반 페이지네이션을 쓴다(이
    화면만 htmx 조각 응답을 도입할 이유가 없음 — admin 화면 전반이 풀 페이지
    네비게이션 패턴).
    """
    page = max(page, 1)
    subject_filter = _parse_subject_query(subject)

    total = db.count_chunks(query=q, subject=subject_filter)
    total_pages = max((total + ADMIN_CHUNKS_PAGE_SIZE - 1) // ADMIN_CHUNKS_PAGE_SIZE, 1)
    # total_pages보다 큰 page 요청(오래된 북마크 등)은 그대로 offset을 키워
    # 빈 목록을 내려주면 안 된다 — items가 비면 템플릿이 "검색 결과 없음"
    # 분기를 타 페이지네이션 링크 자체가 사라지고, 관리자가 URL을 직접 고치는
    # 것 외에는 되돌아올 방법이 없어진다.
    page = min(page, total_pages)
    offset = (page - 1) * ADMIN_CHUNKS_PAGE_SIZE

    items = db.search_chunks(
        query=q, subject=subject_filter, limit=ADMIN_CHUNKS_PAGE_SIZE, offset=offset
    )

    context = {
        "user": admin,
        "items": items,
        "q": q or "",
        "subject": subject or "",
        "subjects": list(Subject),
        "page": page,
        "total_pages": total_pages,
        "total": total,
    }
    return templates.TemplateResponse(request, "admin_chunks.html", context)


@app.get("/admin/chunks/{chunk_id}/edit", response_class=HTMLResponse)
def admin_chunk_edit_form(
    request: Request, chunk_id: str, admin: User = Depends(auth.require_admin)
):
    chunk = db.get_chunk_by_id(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="성취기준을 찾을 수 없습니다.")
    return templates.TemplateResponse(
        request, "admin_chunk_edit.html", {"user": admin, "chunk": chunk, "saved": False}
    )


@app.post("/admin/chunks/{chunk_id}", response_class=HTMLResponse)
def admin_chunk_update(
    request: Request,
    chunk_id: str,
    unit_name: str = Form(...),
    domain: str = Form(...),
    core_idea: str = Form(...),
    achievement_text: str = Form(...),
    explanation: str = Form(...),
    admin: User = Depends(auth.require_admin),
):
    """텍스트 콘텐츠만 수정한다 — chunk_id/subject/grade_band/achievement_code는
    폼에 없다(db.update_chunk가 애초에 안 받음, REQ-002 §RS-000-3 데이터 계약
    보호). 임베딩은 재계산하지 않는다(db.update_chunk 문서 참고) — 텍스트를
    많이 고친 성취기준은 별도 재적재가 필요할 수 있음을 관리자가 알아야 한다.
    """
    updated = db.update_chunk(
        chunk_id,
        unit_name=unit_name,
        domain=domain,
        core_idea=core_idea,
        achievement_text=achievement_text,
        explanation=explanation,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="성취기준을 찾을 수 없습니다.")
    return templates.TemplateResponse(
        request, "admin_chunk_edit.html", {"user": admin, "chunk": updated, "saved": True}
    )


def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _user_from_request(request: Request) -> User | None:
    """예외 핸들러용 로그인 상태 조회.

    핸들러는 의존성 주입을 못 쓰므로 쿠키에서 직접 세션을 찾는다. base.html의
    네비게이션이 user 하나만 보기 때문에, 이 값이 없으면 로그인한 사용자가
    에러 페이지에서만 비로그인 상태로 보인다.

    조회 실패는 삼킨다 — DB가 죽어서 500이 난 상황에서 에러 페이지가 또
    DB를 찔러 죽으면 사용자에게 아무것도 못 보여준다.
    """
    try:
        return auth.get_optional_user(request.cookies.get(auth.SESSION_COOKIE_NAME))
    except Exception:
        logger.exception("error_page_user_lookup_failed")
        return None


@app.exception_handler(HTTPException)
def handle_http_exception(request: Request, exc: HTTPException) -> Response:
    """401은 로그인 페이지로 리다이렉트. 나머지(403/404/429)는 htmx 요청이면
    #result 등에 그대로 삽입 가능한 조각으로, 일반 브라우저 네비게이션(직접
    URL 접근 등)이면 완전한 HTML 페이지(error.html)로 반환한다 — 안 그러면
    페이지 전체가 스타일 없는 div 하나로 나온다."""
    if exc.status_code == 401:
        return RedirectResponse(url="/login", status_code=303)
    if _is_htmx_request(request):
        return HTMLResponse(f'<div class="notice">{exc.detail}</div>', status_code=exc.status_code)
    return templates.TemplateResponse(
        request,
        "error.html",
        {"message": exc.detail, "user": _user_from_request(request)},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
def handle_error(request: Request, exc: Exception) -> Response:
    """예상 못 한 실패 시 사용자 친화적 문구만 노출한다(스택 트레이스 노출 금지).
    HTTPException 핸들러와 동일하게 htmx/일반 네비게이션을 구분해서 응답한다.
    """
    logger.exception("unhandled_exception")
    message = "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
    if _is_htmx_request(request):
        return HTMLResponse(f'<div class="notice">{message}</div>', status_code=500)
    return templates.TemplateResponse(
        request,
        "error.html",
        {"message": message, "user": _user_from_request(request)},
        status_code=500,
    )