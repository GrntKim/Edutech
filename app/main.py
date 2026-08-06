# 소유: E(REQ-006)
"""FastAPI 진입점. 라우트는 D의 오케스트레이터만 호출하고,
개별 에이전트를 직접 알지 않는다(REQ-005 VALID-000-4).
"""

import logging
import re
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.agents.lesson_generate import LessonOutput, render_lesson_docx
from app.agents.orchestrate import run_pipeline
from app.lib.types import ConceptInput

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


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/generate", response_class=HTMLResponse)
def generate(request: Request, concept: str = Form(...), grade: int = Form(...)):
    """폼 제출을 받아 파이프라인을 실행하고 결과 조각을 반환한다.

    htmx가 #result에 삽입하므로 전체 문서가 아니라 조각(result.html)을 돌려준다.
    subject_hint는 1단계에서 UI에 노출하지 않으며, 생략이 아니라 명시적으로
    None을 전달한다(팀 합의 2026-08-04, REQ-006 SITE-001).
    """
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


@app.exception_handler(Exception)
def handle_error(request: Request, exc: Exception) -> HTMLResponse:
    """파이프라인 실패 시 사용자 친화적 문구만 노출한다(스택 트레이스 노출 금지).

    htmx 요청에 대한 응답이므로 조각으로 반환한다.
    """
    logger.exception("pipeline_failed")
    return HTMLResponse(
        '<div class="notice">생성 중 오류가 발생했습니다. '
        "잠시 후 다시 시도해 주세요.</div>",
        status_code=500,
    )