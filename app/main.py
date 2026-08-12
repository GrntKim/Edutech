# 소유: E(REQ-006)
"""FastAPI 진입점. 라우트는 D의 오케스트레이터만 호출하고,
개별 에이전트를 직접 알지 않는다(REQ-005 VALID-000-4).
"""

import logging
import re
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.agents.lesson_generate import LessonOutput, render_lesson_docx
from app.agents.orchestrate import run_pipeline
from app.lib import auth, db
from app.lib.types import ConceptInput, LessonRequest, PipelineResult, Subject, User

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

MYPAGE_PAGE_SIZE = 10
ADMIN_CHUNKS_PAGE_SIZE = 20


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
    테이블을 센다). A1 조기 종료(unsupported_concept)나 A2 검색 0건도 이미
    Gemini를 호출해 비용이 발생한 뒤이므로 반드시 카운트한다 — 예전에는
    lesson_plan이 비면 저장을 건너뛰어, 0건이 재현되는 조합을 반복 클릭하면
    한도 없이 API를 쓸 수 있었다.

    실패 시도는 lesson_output이 빈 dict로 남는다(NULL이 아니므로 DDL 변경
    불필요). 마이페이지·재출력은 이 값이 비었는지로 실패 행을 판별한다 —
    validation_status는 경고 문구가 그대로 들어가는 자유 문자열이라 판별에
    쓸 수 없다.

    result=None은 run_pipeline이 예외로 죽은 경우.
    """
    lesson_plan = result.lesson_plan if result is not None else {}
    if result is None:
        status = "pipeline_error"
    else:
        status = "passed" if result.validation.passed else (result.warning or "failed")

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

    try:
        result = run_pipeline(
            ConceptInput(
                raw_concept_name=concept,
                target_grade=grade,
                subject_hint=None,
            )
        )
    except Exception:
        # 예외로 죽어도 여기까지 온 Gemini 호출 비용은 이미 발생했다 — 카운트하고 넘긴다.
        _record_attempt(user, concept, grade, None)
        raise

    saved = _record_attempt(user, concept, grade, result)

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "user": user,
            "lesson": result.lesson_plan,
            "warning": result.warning,
            # 저장에 실패했으면 다운로드할 근거(lesson_requests 행)가 없으므로 버튼을 숨긴다.
            "request_id": saved.id if saved is not None and saved.lesson_output else None,
            # 방금 기록한 행까지 반영된 값이어야 하므로 위 rate_status를 재사용하지 않고 다시 센다.
            "rate_status": auth.check_rate_limit(user),
        },
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
    return templates.TemplateResponse(request, "admin.html", {"admin": admin})


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
        "admin": admin,
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
        request, "admin_chunk_edit.html", {"admin": admin, "chunk": chunk, "saved": False}
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
        request, "admin_chunk_edit.html", {"admin": admin, "chunk": updated, "saved": True}
    )


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