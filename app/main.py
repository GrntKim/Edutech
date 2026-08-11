# 소유: E(REQ-006)
"""FastAPI 진입점. 라우트는 D의 오케스트레이터만 호출하고,
개별 에이전트를 직접 알지 않는다(REQ-005 VALID-000-4).
"""

import logging
import re
import tempfile
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.agents.lesson_generate import LessonOutput, render_lesson_docx
from app.agents.orchestrate import run_pipeline
from app.lib import auth, db
from app.lib.types import ConceptInput, User

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# 생성된 DOCX는 서버에 영구 보관하지 않는다 — 요청마다 시스템 임시 디렉터리에
# 써 두고 다운로드 라우트가 즉시 서빙한다. app/data/ 대신 tempdir을 쓰면
# git 추적·정리 걱정 없이 재시작 시 자연히 사라진다.
_DOCX_DIR = Path(tempfile.gettempdir()) / "edutech_docx"
_DOCX_DIR.mkdir(parents=True, exist_ok=True)

# UUID(hex) + .docx 형태만 허용 — 사용자가 URL의 filename을 임의로 바꿔
# 경로 조작을 시도해도 이 패턴에 안 맞으면 무조건 404로 막는다.
_DOCX_FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.docx$")

MYPAGE_PAGE_SIZE = 10


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request, user: User | None = Depends(auth.get_optional_user)):
    return templates.TemplateResponse(request, "index.html", {"user": user})


@app.post("/generate", response_class=HTMLResponse)
def generate(
    request: Request,
    concept: str = Form(...),
    grade: int = Form(...),
    user: User = Depends(auth.get_current_user),
):
    """폼 제출을 받아 파이프라인을 실행하고 결과 조각을 반환한다.

    htmx가 #result에 삽입하므로 전체 문서가 아니라 조각(result.html)을 돌려준다.
    subject_hint는 1단계에서 UI에 노출하지 않으며, 생략이 아니라 명시적으로
    None을 전달한다(팀 합의 2026-08-04, REQ-006 SITE-001).

    실제 파이프라인을 실행하는 요청만 레이트리밋 대상이다(재출력은 별도
    라우트라 여기 안 걸림). admin은 check_rate_limit이 항상 allowed=True.
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

    result = run_pipeline(
        ConceptInput(
            raw_concept_name=concept,
            target_grade=grade,
            subject_hint=None,
        )
    )

    docx_name = None
    if result.lesson_plan:
        try:
            lesson = LessonOutput(**result.lesson_plan)
            docx_bytes = render_lesson_docx(lesson)
            candidate_name = f"{uuid4().hex}.docx"
            (_DOCX_DIR / candidate_name).write_bytes(docx_bytes)
            docx_name = candidate_name
        except Exception:
            # DOCX 생성 실패가 교안 렌더링 자체를 막으면 안 된다 — 버튼만 숨긴다.
            logger.exception("docx_export_failed")

        # 검증까지 끝나 재출력 가능한 lesson_plan이 나온 요청만 히스토리에
        # 저장한다(A1 조기 종료 등 lesson_plan={}인 경우는 저장 안 함 —
        # 저장이 곧 레이트리밋 카운트 대상이므로 이게 카운트 기준이기도 하다).
        try:
            db.create_lesson_request(
                user_id=user.id,
                concept_name=concept,
                target_grade=grade,
                subject_hint=None,
                mapped_curriculum_code=result.lesson_plan.get("achievement_code"),
                lesson_output=result.lesson_plan,
                validation_status="passed" if result.validation.passed else (result.warning or "failed"),
            )
        except Exception:
            logger.exception("lesson_request_save_failed")

    return templates.TemplateResponse(
        request,
        "result.html",
        {"lesson": result.lesson_plan, "warning": result.warning, "docx_name": docx_name},
    )


@app.get("/download/{filename}")
def download(filename: str, title: str = "교안"):
    """생성 시점에 저장해 둔 DOCX를 서빙한다.

    filename은 UUID(hex)+.docx 형태만 허용해 경로 조작을 막는다(사용자
    입력이 파일시스템 경로에 그대로 들어가지 않도록). title은 다운로드
    파일명 표시용일 뿐 경로에는 쓰이지 않지만, 구분자 문자는 제거해
    Content-Disposition 헤더 인젝션 여지를 줄인다.
    """
    if not _DOCX_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")

    path = _DOCX_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")

    safe_title = re.sub(r'[\\/:*?"<>|]', "", title).strip() or "교안"
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{safe_title}.docx",
    )


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return templates.TemplateResponse(request, "signup.html", {"error": None})


@app.post("/signup", response_class=HTMLResponse)
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
):
    if len(password) < 8:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": "비밀번호는 8자 이상이어야 합니다."},
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
            {"error": "이미 가입된 이메일입니다."},
            status_code=400,
        )

    response = RedirectResponse(url="/mypage", status_code=303)
    auth.issue_session(response, user.id)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_email(email.strip().lower())
    if user is None or not auth.verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "이메일 또는 비밀번호가 올바르지 않습니다."},
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


@app.post("/mypage/requests/{request_id}/redownload")
def redownload(request_id: UUID, user: User = Depends(auth.get_current_user)):
    """저장된 lesson_output으로 DOCX만 재생성한다 — 에이전트/파이프라인 호출이
    없으므로 레이트리밋 대상이 아니다(generate()의 create_lesson_request 저장을
    거치지 않는다)."""
    item = _get_owned_lesson_request(request_id, user)

    try:
        lesson = LessonOutput(**item.lesson_output)
        docx_bytes = render_lesson_docx(lesson)
    except Exception:
        # C의 출력 스키마가 바뀌어 옛 저장분과 안 맞을 수 있다(하위호환 미보장,
        # 의도적 — REQ-006 스코프). 500 스택트레이스 대신 안내만 준다.
        logger.exception("redownload_docx_failed request_id=%s", request_id)
        raise HTTPException(status_code=400, detail="이 기록은 재출력할 수 없습니다(저장된 형식이 최신 양식과 다릅니다).")

    candidate_name = f"{uuid4().hex}.docx"
    (_DOCX_DIR / candidate_name).write_bytes(docx_bytes)

    return RedirectResponse(
        url=f"/download/{candidate_name}?title={quote(item.concept_name)}", status_code=303
    )


@app.post("/mypage/requests/{request_id}/delete")
def delete_lesson_request(request_id: UUID, user: User = Depends(auth.get_current_user)):
    deleted = db.delete_lesson_request(request_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="요청을 찾을 수 없습니다.")

    return RedirectResponse(url="/mypage", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, admin: User = Depends(auth.require_admin)):
    return templates.TemplateResponse(request, "admin.html", {"admin": admin})


def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


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
        request, "error.html", {"message": exc.detail}, status_code=exc.status_code
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
    return templates.TemplateResponse(request, "error.html", {"message": message}, status_code=500)