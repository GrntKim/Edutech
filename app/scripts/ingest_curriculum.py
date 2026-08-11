import asyncio
import json
import re
import sys
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv
from pydantic import ValidationError

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.curriculum_search.logic import embed_texts, embedding_source_text  # noqa: E402
from app.agents.curriculum_search.schema import CurriculumChunk, GradeBand, Subject  # noqa: E402
from app.lib.db import DatabaseError, get_connection  # noqa: E402

RAW_DIR = APP_ROOT / "data" / "raw"
OUTPUT_PATH = APP_ROOT / "data" / "curriculum_units.json"

SOURCE_FILES = [
    (RAW_DIR / "math_book.pdf", Subject.MATH),
    (RAW_DIR / "science_book.pdf", Subject.SCIENCE),
    (RAW_DIR / "domestic_science_book.pdf", Subject.DOMESTIC_SCIENCE),
    (RAW_DIR / "art_book.pdf", Subject.ART),
    (RAW_DIR / "social_book.pdf", Subject.SOCIAL),
    (RAW_DIR / "korean_book.pdf", Subject.KOREAN),
]

_ELEMENTARY_GRADE_BANDS = {
    (1, 2): GradeBand.G1_2,
    (3, 4): GradeBand.G3_4,
    (5, 6): GradeBand.G5_6,
}

_NOISE_LINES = {
    "공통 교육과정",
    "수학과 교육과정",
    "과학과 교육과정",
    "실과(기술⋅가정)/정보과 교육과정",
    "공통 교육과정 – 실과(기술⋅가정) -",
    "미술",
    "초등학교 교육과정",
}

_TOP_LEVEL_SECTION_RE = re.compile(r"^\d+\.\s*\S")
_CONTENT_SYSTEM_RE = re.compile(r"^가\.\s*내용\s*체계")
_STANDARDS_SECTION_RE = re.compile(r"^나\.\s*성취기준")
_GRADE_BAND_HEADER_RE = re.compile(r"^\[(초등학교|중학교|고등학교)\s*(\d+)\s*[~∼]\s*(\d+)학년\]$")
# 실과(기술⋅가정)/정보과 교육과정은 학년군 범위 대신 "[초등학교 실과]"처럼 과목명만 표기한다.
# 실과는 초등학교 5~6학년에만 존재하므로 그 학년군으로 고정한다.
_SIMPLE_SCHOOL_HEADER_RE = re.compile(r"^\[(초등학교|중학교|고등학교)\s*(\S.*)\]$")
_DOMAIN_RE = re.compile(r"^\((\d+)\)\s*(\S.*)$")
_CID_BULLET_RE = re.compile(r"^\(cid:\d+\)")
_ACHIEVEMENT_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
_EXPLANATION_HEADER_RE = re.compile(r"^\(가\)\s*성취기준\s*해설")
_CONSIDERATION_HEADER_RE = re.compile(r"^\(나\)\s*성취기준\s*적용")
_INQUIRY_HEADER = "<탐구 활동>"
_BULLET_CODE_RE = re.compile(r"^[•·]\s*\[([^\]]+)\]\s*(.*)$")
_BULLET_RE = re.compile(r"^[•·]\s*(.*)$")

# 성취기준 코드 표기에 en-dash(–)/em-dash(—)가 hyphen(-) 대신 섞여 나오는 경우가 있다
# (예: 사회과 PDF의 [4사01–01], [4사01–02] — 같은 문서 내 다른 337개 코드는 전부 hyphen).
# 코드가 achievement 줄과 (가) 성취기준 해설 불릿 양쪽에서 각각 파싱되는데, 정규화 없이
# 문자 그대로 dict key/lookup에 쓰면 두 줄의 코드 문자열이 달라져 해설이 조용히 누락된다
# (에러 없이 `code in records`가 False로만 나옴). 코드 추출 시점에 항상 정규화해 이를 막는다.
_DASH_VARIANTS_RE = re.compile(r"[–—]")


def _normalize_code(code: str) -> str:
    return _DASH_VARIANTS_RE.sub("-", code)
_PAGE_NUM_RE = re.compile(r"^\d+$")


def extract_pages(pdf_path: Path) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _lines_with_pages(pages: list[str]):
    for page_no, text in enumerate(pages):
        for line in text.split("\n"):
            line = line.strip()
            if not line or line in _NOISE_LINES or _PAGE_NUM_RE.match(line):
                continue
            yield page_no, line


def parse_core_ideas(pages: list[str]) -> dict[str, str]:
    """domain 이름 -> 핵심 아이디어 텍스트. 최초 '가. 내용 체계' 사이클(초·중 공통 교육과정)만 사용한다."""
    core_ideas: dict[str, list[str]] = {}
    in_content_system = False
    current_domain: str | None = None
    collecting = False

    for _, line in _lines_with_pages(pages):
        if _STANDARDS_SECTION_RE.match(line):
            break
        if _CONTENT_SYSTEM_RE.match(line):
            in_content_system = True
            continue
        if not in_content_system:
            continue

        domain_match = _DOMAIN_RE.match(line)
        if domain_match:
            current_domain = domain_match.group(2)
            core_ideas.setdefault(current_domain, [])
            collecting = True
            continue

        if "내용 요소" in line:
            collecting = False
            continue

        line = line.replace("핵심 아이디어", "").strip()
        if not line:
            continue

        if collecting and line.startswith("⋅") and current_domain is not None:
            core_ideas[current_domain].append(line.lstrip("⋅").strip())
        elif collecting and current_domain is not None and core_ideas[current_domain]:
            core_ideas[current_domain][-1] += line

    return {domain: " ".join(bullets) for domain, bullets in core_ideas.items()}


def _apply_achievement_line(
    line: str,
    records: dict[str, dict],
    domain_index: dict[tuple, list[str]],
    current_domain: str | None,
    current_grade_band: GradeBand | None,
    current_code: str | None,
    core_ideas: dict[str, str],
    page_no: int,
    subject: Subject,
) -> str | None:
    """'나. 성취기준' 영역 내 성취기준 코드/본문 줄을 파싱해 records에 반영한다.

    성취기준 코드 줄([4수03-05] ...)이면 새 record를 만들고 domain_index에
    (domain, grade_band) -> chunk_id 매핑을 추가한다. 그 외 줄은 직전 코드의
    achievement_text에 이어붙인다. 반환값은 갱신된 current_code다.
    """
    achievement_match = _ACHIEVEMENT_RE.match(line)
    if achievement_match:
        code, text = achievement_match.groups()
        code = _normalize_code(code)
        records[code] = {
            "chunk_id": code,
            "subject": subject,
            "grade_band": current_grade_band,
            "unit_name": current_domain,
            "domain": current_domain,
            "core_idea": core_ideas.get(current_domain) or current_domain,
            "achievement_code": f"[{code}]",
            "achievement_text": text.strip(),
            "explanation": "",
            "inquiry_activities": [],
            "source_page": page_no,
            "_domain": current_domain,
            "_grade_band": current_grade_band,
            "_consideration": "",
        }
        domain_index.setdefault((current_domain, current_grade_band), []).append(code)
        return code
    if current_code is not None:
        records[current_code]["achievement_text"] += " " + line
    return current_code


def _apply_inquiry_line(
    line: str,
    records: dict[str, dict],
    domain_index: dict[tuple, list[str]],
    current_domain: str | None,
    current_grade_band: GradeBand | None,
    domain_inquiry: list[str],
) -> None:
    """<탐구 활동> 불릿 줄을 domain_inquiry에 누적하고, 같은 (domain, grade_band)에
    속한 모든 record의 inquiry_activities를 갱신한다.

    대상 record는 domain_index에서 조회한다 — records 전체를 매 불릿 라인마다
    순회하지 않기 위함이다.
    """
    bullet_match = _BULLET_RE.match(line)
    if not bullet_match:
        return
    domain_inquiry.append(bullet_match.group(1).strip())
    for chunk_id in domain_index.get((current_domain, current_grade_band), []):
        records[chunk_id]["inquiry_activities"] = list(domain_inquiry)


def _apply_explanation_line(
    line: str,
    records: dict[str, dict],
    current_code: str | None,
) -> str | None:
    """(가) 성취기준 해설 영역의 '• [코드] 설명' 또는 이어지는 줄을 파싱한다."""
    bullet_match = _BULLET_CODE_RE.match(line)
    if bullet_match:
        code, text = bullet_match.groups()
        code = _normalize_code(code)
        if code in records:
            records[code]["explanation"] = text.strip()
        return code
    if current_code is not None and current_code in records:
        records[current_code]["explanation"] += " " + line
    return current_code


def _apply_consideration_line(line: str, domain_consideration: list[str]) -> None:
    """(나) 성취기준 적용 시 고려 사항 영역의 불릿/이어지는 줄을 누적한다."""
    bullet_match = _BULLET_RE.match(line)
    if bullet_match:
        domain_consideration.append(bullet_match.group(1).strip())
    elif domain_consideration:
        domain_consideration[-1] += " " + line


def parse_achievement_records(pages: list[str], subject: Subject) -> list[dict]:
    core_ideas = parse_core_ideas(pages)

    records: dict[str, dict] = {}
    domain_index: dict[tuple, list[str]] = {}
    in_standards_section = False
    current_grade_band: GradeBand | None = None
    current_domain: str | None = None
    current_code: str | None = None
    mode = None  # None | "achievement" | "inquiry" | "explanation" | "consideration"
    domain_inquiry: list[str] = []
    domain_consideration: list[str] = []

    def flush_domain_consideration():
        if current_domain is None or not domain_consideration:
            return
        text = " ".join(domain_consideration)
        for code, record in records.items():
            if record["_domain"] == current_domain and record["_grade_band"] == current_grade_band:
                record["_consideration"] = text

    for page_no, line in _lines_with_pages(pages):
        if _TOP_LEVEL_SECTION_RE.match(line):
            flush_domain_consideration()
            in_standards_section = False
            current_grade_band = None
            current_domain = None
            current_code = None
            mode = None
            continue

        if _STANDARDS_SECTION_RE.match(line):
            in_standards_section = True
            continue

        if not in_standards_section:
            continue

        grade_band_match = _GRADE_BAND_HEADER_RE.match(line)
        if grade_band_match:
            flush_domain_consideration()
            school, start, end = grade_band_match.groups()
            current_grade_band = (
                _ELEMENTARY_GRADE_BANDS.get((int(start), int(end))) if school == "초등학교" else None
            )
            current_domain = None
            current_code = None
            mode = None
            continue

        simple_header_match = _SIMPLE_SCHOOL_HEADER_RE.match(line)
        if simple_header_match:
            flush_domain_consideration()
            school, label = simple_header_match.groups()
            current_grade_band = GradeBand.G5_6 if school == "초등학교" and label == "실과" else None
            current_domain = None
            current_code = None
            mode = None
            continue

        if current_grade_band is None:
            continue

        domain_match = _DOMAIN_RE.match(line)
        if domain_match:
            flush_domain_consideration()
            current_domain = domain_match.group(2)
            current_code = None
            mode = "achievement"
            domain_inquiry = []
            domain_consideration = []
            continue

        if current_domain is None:
            continue

        if _CID_BULLET_RE.match(line):
            current_code = None
            continue

        if line == _INQUIRY_HEADER:
            mode = "inquiry"
            current_code = None
            continue

        if _EXPLANATION_HEADER_RE.match(line):
            mode = "explanation"
            current_code = None
            continue

        if _CONSIDERATION_HEADER_RE.match(line):
            mode = "consideration"
            current_code = None
            continue

        if mode == "achievement":
            current_code = _apply_achievement_line(
                line,
                records,
                domain_index,
                current_domain,
                current_grade_band,
                current_code,
                core_ideas,
                page_no,
                subject,
            )

        elif mode == "inquiry":
            _apply_inquiry_line(
                line, records, domain_index, current_domain, current_grade_band, domain_inquiry
            )

        elif mode == "explanation":
            current_code = _apply_explanation_line(line, records, current_code)

        elif mode == "consideration":
            _apply_consideration_line(line, domain_consideration)

    flush_domain_consideration()

    results = []
    for record in records.values():
        explanation = record["explanation"].strip()
        consideration = record["_consideration"].strip()
        record["explanation"] = (explanation + " " + consideration).strip()
        for key in ("_domain", "_grade_band", "_consideration"):
            del record[key]
        results.append(record)
    return results


def build_curriculum_chunks() -> list[CurriculumChunk]:
    chunks: list[CurriculumChunk] = []
    errors: list[str] = []
    empty_sources: list[str] = []
    for path, subject in SOURCE_FILES:
        pages = extract_pages(path)
        records = parse_achievement_records(pages, subject)
        if not records:
            empty_sources.append(path.name)
        for raw in records:
            try:
                chunks.append(CurriculumChunk(**raw))
            except ValidationError as exc:
                errors.append(f"{path.name} {raw.get('chunk_id', '?')}: {exc}")

    if errors:
        raise ValueError("파싱 결과 검증 실패:\n" + "\n".join(errors))
    if empty_sources:
        # 정규식 매칭 실패는 예외를 던지지 않고 조용히 건너뛰므로, PDF 포맷이
        # 바뀌면 특정 과목이 통째로 0건 파싱돼도 사람이 숫자를 눈으로 대조하기
        # 전까지 드러나지 않는다. 이걸 여기서 명시적으로 잡아 조기에 실패시킨다.
        raise ValueError(
            "파싱 결과가 0건인 파일이 있습니다(PDF 포맷 변경 의심): "
            + ", ".join(empty_sources)
        )
    return chunks


def write_curriculum_units(chunks: list[CurriculumChunk]) -> None:
    OUTPUT_PATH.write_text(
        json.dumps([chunk.model_dump(mode="json") for chunk in chunks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def embed_chunks(chunks: list[CurriculumChunk]) -> list[list[float]]:
    return await embed_texts([embedding_source_text(chunk) for chunk in chunks])


_UPSERT_SQL = """
INSERT INTO curriculum_chunks (
    chunk_id, subject, grade_band, unit_name, domain, core_idea,
    achievement_code, achievement_text, explanation, inquiry_activities,
    source_page, embedding
) VALUES (
    %(chunk_id)s, %(subject)s, %(grade_band)s, %(unit_name)s, %(domain)s, %(core_idea)s,
    %(achievement_code)s, %(achievement_text)s, %(explanation)s, %(inquiry_activities)s,
    %(source_page)s, %(embedding)s
)
ON CONFLICT (chunk_id) DO UPDATE SET
    subject = EXCLUDED.subject,
    grade_band = EXCLUDED.grade_band,
    unit_name = EXCLUDED.unit_name,
    domain = EXCLUDED.domain,
    core_idea = EXCLUDED.core_idea,
    achievement_code = EXCLUDED.achievement_code,
    achievement_text = EXCLUDED.achievement_text,
    explanation = EXCLUDED.explanation,
    inquiry_activities = EXCLUDED.inquiry_activities,
    source_page = EXCLUDED.source_page,
    embedding = EXCLUDED.embedding
"""


def _upsert_chunks_sync(chunks: list[CurriculumChunk], embeddings: list[list[float]]) -> None:
    params = [
        {
            "chunk_id": chunk.chunk_id,
            "subject": chunk.subject.value,
            "grade_band": chunk.grade_band.value,
            "unit_name": chunk.unit_name,
            "domain": chunk.domain,
            "core_idea": chunk.core_idea,
            "achievement_code": chunk.achievement_code,
            "achievement_text": chunk.achievement_text,
            "explanation": chunk.explanation,
            "inquiry_activities": chunk.inquiry_activities,
            "source_page": chunk.source_page,
            "embedding": embedding,
        }
        for chunk, embedding in zip(chunks, embeddings)
    ]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, params)
        conn.commit()


async def upsert_chunks(chunks: list[CurriculumChunk], embeddings: list[list[float]]) -> None:
    try:
        await asyncio.to_thread(_upsert_chunks_sync, chunks, embeddings)
    except DatabaseError as exc:
        raise RuntimeError(f"Cloud SQL 적재 실패: {exc}") from exc


async def main() -> None:
    load_dotenv(APP_ROOT.parent / ".env")

    chunks = build_curriculum_chunks()
    print(f"{len(chunks)}개 청크 파싱 완료.")
    write_curriculum_units(chunks)
    print(f"{OUTPUT_PATH}에 저장 완료.")

    print("임베딩 생성 중...")
    embeddings = await embed_chunks(chunks)

    print("Cloud SQL 적재 중...")
    await upsert_chunks(chunks, embeddings)
    print(f"{len(chunks)}개 청크 적재 완료.")


if __name__ == "__main__":
    asyncio.run(main())
