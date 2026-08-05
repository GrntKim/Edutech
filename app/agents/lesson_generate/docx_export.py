from io import BytesIO

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor
from docx.table import Table, _Cell

from app.agents.lesson_generate.schema import LessonOutput

# HTML(result.html)의 worksheet-section 색상과 맞춘 팔레트.
_SECTION_COLORS = ["DCEBFB", "DFF5E6", "F8E4FC", "FDF0D5"]
_MISSION_COLOR = "FFF6E0"
_HEADER_COLOR = "D9E9F7"

# 원본 교수학습과정안 PDF에서 "학습 문제 안내하기" 행은 교사/학생 칸을 가로로 합쳐
# 그 안에 학습 문제를 네모 박스로 한 번만 보여준다.
_ANNOUNCE_LABELS = {"학습 문제 안내하기"}

# 활동1/활동2 행은 teacher 셀 안에서 "T : "로 시작하는 마지막 발문 줄을 제외한
# 번호 절차(1. 2. 3. ...) 부분만 테두리 박스로 감싼다.
_PROCEDURE_LABELS = {"활동1", "활동2"}

# 가시성을 위해 교사·학생·유의점처럼 내용이 많은 열은 넓게 잡는다. 학습단계·시간은
# 기존 대비 1.5배로 넓혔고, 도구/유의점은 원래 폭(0.7in)의 1.5배(1.05in)로 넓히면서
# 그만큼 교사·학생에서 덜어내 본문 너비(기본 여백 1in 기준 6.5in)를 넘지 않도록 맞춘다.
_META_COL_WIDTHS = [Inches(1.48), Inches(4.85)]
_STAGE_COL_WIDTHS = [Inches(0.63), Inches(0.69), Inches(1.825), Inches(1.825), Inches(0.48), Inches(1.05)]

# 중첩 표(학습 문제 박스, 활동1/2 절차 박스)를 부모 셀 폭 그대로 채우면 셀 안쪽 여백
# 공간이 없어 테두리가 잘려 보인다. 이만큼 안쪽으로 줄여서 여유를 남긴다.
_BOX_WIDTH_INSET = Inches(0.2)

# 문서 전체(제목 두 줄 제외) 글자 크기를 10pt로 통일하되, 도구 및 자료/유의점 열은
# 항목이 많아 9pt로 그 열만 따로 줄인다.
_BODY_FONT_SIZE = Pt(10)
_TOOLS_NOTES_FONT_SIZE = Pt(9)


def _apply_column_widths(table: Table, widths: list[Inches]) -> None:
    """열 너비를 지정한다. autofit(내용에 맞춰 자동조정)이 켜져 있으면 워드/한컴오피스가
    지정한 너비를 무시하므로 끄고, tblGrid뿐 아니라 각 행의 셀 너비도 함께 맞춘다 —
    add_row()로 나중에 추가된 행은 그리드 너비만으로는 반영되지 않는 경우가 있다.
    가로 병합된 셀(학습 문제 박스, 학습 단계, 평가 기준 행)은 row.cells에서 같은 셀이
    여러 열 위치에 반복되므로, 병합된 만큼 너비를 합산해서 한 번만 지정한다."""
    table.autofit = False
    for idx, width in enumerate(widths):
        table.columns[idx].width = width
    for row in table.rows:
        seen_tc_ids: set[int] = set()
        for start_index, cell in enumerate(row.cells):
            tc_id = id(cell._tc)
            if tc_id in seen_tc_ids:
                continue
            seen_tc_ids.add(tc_id)
            span = 1
            while (
                start_index + span < len(row.cells)
                and id(row.cells[start_index + span]._tc) == tc_id
            ):
                span += 1
            cell.width = Emu(sum(int(w) for w in widths[start_index : start_index + span]))


def _shade_cell(cell: _Cell, hex_color: str) -> None:
    """표 셀에 배경색을 입힌다. 문단 배경색 XML 트릭보다 한컴오피스 호환성이 안정적이다."""
    cell_properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), hex_color)
    cell_properties.append(shading)


def _set_font_size(cell: _Cell, size: Pt) -> None:
    """셀 안 모든 문단의 글자 크기를 지정한다(문서 전체 기본값과 다르게 줄 때 사용)."""
    for paragraph in cell.paragraphs:
        for run in paragraph.runs:
            run.font.size = size


def _no_wrap_cell(cell: _Cell) -> None:
    """셀 폭이 좁아 한글 라벨이 한 글자씩 세로로 줄바꿈되는 것을 막는다. 폭을 늘리는 대신
    한 줄을 유지하고 필요하면 옆으로 살짝 넘치도록 한다(학습 단계/학습 내용/시간, 메타 라벨처럼
    짧지만 좁은 열에 사용)."""
    cell_properties = cell._tc.get_or_add_tcPr()
    no_wrap = OxmlElement("w:noWrap")
    cell_properties.append(no_wrap)


def _style_header_cell(cell: _Cell) -> None:
    """표 헤더/라벨 셀(메타 표 라벨, 학습 단계 헤더 행, 도입/전개/정리)에 옅은 하늘색
    배경(#D9E9F7)과 검정 굵은 글씨를 적용하고 가운데 정렬한다. cell.text로 텍스트를
    먼저 채운 뒤 호출한다."""
    _shade_cell(cell, _HEADER_COLOR)
    for paragraph in cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in paragraph.runs:
            run.bold = True
            run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)


def _split_heading_body(text: str) -> tuple[str, str]:
    """LG-002 제목 줄 규칙(prompts.py)에 따라 teacher/student 필드의 첫 줄(◎/◘/♥ 제목)과
    그 다음 본문을 분리한다."""
    heading, _, body = text.partition("\n")
    return heading, body


def _add_boxed_table(
    cell: _Cell, text: str, width: Emu, alignment: WD_ALIGN_PARAGRAPH = WD_ALIGN_PARAGRAPH.LEFT
) -> None:
    """cell 안에 실선 테두리 박스(중첩 1x1 표)를 추가하고 text를 넣는다. width를 부모 셀
    폭 그대로 주면 셀 안쪽 여백이 없어 테두리가 잘려 보이므로 _BOX_WIDTH_INSET만큼 줄이고,
    중첩 표 자체를 가운데 정렬해 부모 셀 안에 여백을 두고 예쁘게 들어가도록 한다."""
    box_width = Emu(max(int(width) - int(_BOX_WIDTH_INSET), int(Inches(0.5))))
    box_table = cell.add_table(rows=1, cols=1)
    box_table.style = "Table Grid"
    box_table.autofit = False
    box_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    box_table.columns[0].width = box_width
    box_cell = box_table.rows[0].cells[0]
    box_cell.width = box_width
    box_cell.text = text
    box_cell.paragraphs[0].alignment = alignment


def _add_announce_box(cell: _Cell, teacher_text: str, student_text: str, width: Emu) -> None:
    """"학습 문제 안내하기" 행의 교사·학생 병합 셀을 채운다: 제목 줄 두 개를 나란히
    굵게 쓴 뒤, 그 아래에 실선 테두리 박스로 학습 문제 문장을 병합 셀 폭 그대로 넣는다."""
    teacher_heading, body = _split_heading_body(teacher_text)
    student_heading, _ = _split_heading_body(student_text)

    heading_paragraph = cell.paragraphs[0]
    heading_run = heading_paragraph.add_run(f"{teacher_heading}    {student_heading}")
    heading_run.bold = True

    _add_boxed_table(cell, body, width, alignment=WD_ALIGN_PARAGRAPH.CENTER)


def _add_procedure_box(cell: _Cell, teacher_text: str, width: Emu) -> None:
    """"활동1"/"활동2" 행의 teacher 셀을 채운다: 제목 줄은 굵게, 번호 절차(1. 2. 3. ...)는
    테두리 박스로 감싸 눈에 띄게 하고, 마지막 "T : " 발문 줄은 박스 밖 일반 텍스트로 남긴다."""
    lines = teacher_text.split("\n")
    heading, *rest = lines
    if rest and rest[-1].startswith("T : "):
        prompt_line = rest[-1]
        procedure_lines = rest[:-1]
    else:
        prompt_line = None
        procedure_lines = rest

    heading_paragraph = cell.paragraphs[0]
    heading_run = heading_paragraph.add_run(heading)
    heading_run.bold = True

    if procedure_lines:
        _add_boxed_table(cell, "\n".join(procedure_lines), width)

    if prompt_line:
        cell.add_paragraph(prompt_line)


def render_lesson_docx(lesson: LessonOutput) -> bytes:
    """LessonOutput을 공식 수업지도안 양식 + 활동지 DOCX로 변환한다(개선-3).

    교사가 다운로드 후 워드/한글에서 직접 편집할 수 있도록 표 기반 문서로
    만든다. 디스크에 저장하지 않고 메모리(BytesIO)에서 bytes로 반환한다.
    실제 "다운로드" 버튼과 라우트는 main.py(D/E 소유)에서 연결해야 하며,
    이 함수는 그 라우트가 호출할 순수 변환 로직만 담당한다.
    """
    doc = Document()
    doc.styles["Normal"].font.size = _BODY_FONT_SIZE

    heading = doc.add_heading("교수학습과정안", level=1)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    meta_rows = [
        ("차시(시간)", lesson.lesson_time),
        ("교육 대상 - 학교급", lesson.school_level),
        ("교육 대상 - 학년", f"{lesson.grade}학년"),
        ("학습 주제", lesson.topic),
        ("관련 과목(영역)", lesson.subject),
        ("성취 기준", lesson.achievement_code),
        ("학습 목표", "\n".join(f"- {objective}" for objective in lesson.learning_objectives)),
        ("학습 준비물 및 활용 자료", ", ".join(lesson.materials)),
        ("AI·디지털 도구", lesson.ai_digital_tool),
    ]
    meta_table = doc.add_table(rows=len(meta_rows), cols=2)
    meta_table.style = "Table Grid"
    meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for row_index, (label, value) in enumerate(meta_rows):
        label_cell, value_cell = meta_table.rows[row_index].cells
        label_cell.text = label
        _style_header_cell(label_cell)
        _no_wrap_cell(label_cell)
        value_cell.text = value
    _apply_column_widths(meta_table, _META_COL_WIDTHS)

    doc.add_paragraph()

    stage_table = doc.add_table(rows=1, cols=6)
    stage_table.style = "Table Grid"
    stage_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    header_cells = stage_table.rows[0].cells
    # 학습 단계(0)/학습 내용(1)/시간(4)은 좁은 열이라 줄바꿈을 막아 가로 한 줄을 유지한다.
    _no_wrap_columns = {0, 1, 4}
    for col_index, (cell, text) in enumerate(
        zip(
            header_cells,
            ["학습 단계", "학습 내용", "교사", "학생", "시간(분)", "도구 및 자료(□) / 유의점(◆)"],
        )
    ):
        cell.text = text
        _style_header_cell(cell)
        if col_index in _no_wrap_columns:
            _no_wrap_cell(cell)
        if col_index == 5:
            _set_font_size(cell, _TOOLS_NOTES_FONT_SIZE)

    stages = [
        ("도입", lesson.lesson_stages.intro),
        ("전개", lesson.lesson_stages.development),
        ("정리", lesson.lesson_stages.wrap_up),
    ]
    for label, activities in stages:
        first_row_index = len(stage_table.rows)
        for activity in activities:
            row = stage_table.add_row().cells
            row[1].text = activity.content_label
            _no_wrap_cell(row[1])
            if activity.content_label in _ANNOUNCE_LABELS:
                merged = row[2].merge(row[3])
                merged_width = Emu(int(_STAGE_COL_WIDTHS[2]) + int(_STAGE_COL_WIDTHS[3]))
                _add_announce_box(merged, activity.teacher, activity.student, merged_width)
            elif activity.content_label in _PROCEDURE_LABELS:
                _add_procedure_box(row[2], activity.teacher, _STAGE_COL_WIDTHS[2])
                row[3].text = activity.student
            else:
                row[2].text = activity.teacher
                row[3].text = activity.student
            row[4].text = activity.time
            _no_wrap_cell(row[4])
            row[5].text = "\n".join(
                [f"□ {tool}" for tool in activity.tools]
                + [f"◆ {note}" for note in activity.notes]
            )
            _set_font_size(row[5], _TOOLS_NOTES_FONT_SIZE)
        # 학습 단계 열은 그 단계의 모든 학습내용 행에 걸쳐 세로로 병합한다.
        last_row_index = len(stage_table.rows) - 1
        stage_cell = stage_table.rows[first_row_index].cells[0]
        if last_row_index > first_row_index:
            stage_cell = stage_cell.merge(stage_table.rows[last_row_index].cells[0])
        stage_cell.text = label
        _style_header_cell(stage_cell)
        _no_wrap_cell(stage_cell)

    eval_row = stage_table.add_row().cells
    eval_cell = eval_row[0]
    for col_index in range(1, 6):
        eval_cell = eval_cell.merge(eval_row[col_index])
    eval_cell.text = (
        "평가 기준\n"
        f"상 — {lesson.evaluation_criteria.high}\n"
        f"중 — {lesson.evaluation_criteria.mid}\n"
        f"하 — {lesson.evaluation_criteria.low}"
    )
    eval_cell.paragraphs[0].runs[0].bold = True

    _apply_column_widths(stage_table, _STAGE_COL_WIDTHS)

    # 활동지는 별도 페이지로 분리한다(교사가 이 페이지만 따로 인쇄해 나눠줄 수 있도록).
    doc.add_page_break()
    worksheet_title = doc.add_heading(f"{lesson.worksheet.icon} {lesson.worksheet.title}", level=1)
    worksheet_title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 미션 소개문: 색상 박스(이미지 대체 시각 요소)로 표시한다.
    mission_table = doc.add_table(rows=1, cols=1)
    mission_cell = mission_table.rows[0].cells[0]
    _shade_cell(mission_cell, _MISSION_COLOR)
    mission_cell.text = lesson.worksheet.mission
    mission_cell.paragraphs[0].runs[0].italic = True
    doc.add_paragraph()

    for section_index, section in enumerate(lesson.worksheet.sections):
        color = _SECTION_COLORS[section_index % len(_SECTION_COLORS)]

        # 섹션 전체를 색상 박스(표 셀) 하나 안에 담아 시각적으로 구분한다.
        section_table = doc.add_table(rows=1, cols=1)
        section_cell = section_table.rows[0].cells[0]
        _shade_cell(section_cell, color)

        section_cell.paragraphs[0].text = section.step_label
        section_cell.paragraphs[0].runs[0].bold = True
        section_cell.add_paragraph(section.instruction)

        if section.table is not None:
            visual_table = section_cell.add_table(
                rows=1 + len(section.table.rows), cols=len(section.table.headers)
            )
            visual_table.style = "Table Grid"
            for cell, header in zip(visual_table.rows[0].cells, section.table.headers):
                cell.text = header
                cell.paragraphs[0].runs[0].bold = True
            for row_index, row_values in enumerate(section.table.rows, start=1):
                for cell, value in zip(visual_table.rows[row_index].cells, row_values):
                    cell.text = value

        for item_index, item in enumerate(section.items, start=1):
            # Word 내장 "List Number" 스타일은 한컴오피스에서 번호가 깨져 보이는
            # 경우가 있어, 스타일 대신 번호를 텍스트로 직접 써넣는다.
            section_cell.add_paragraph(f"{item_index}. {item}")

        doc.add_paragraph()

    doc.add_heading("생각해 보기", level=2)
    reflection_table = doc.add_table(rows=1, cols=1)
    reflection_cell = reflection_table.rows[0].cells[0]
    _shade_cell(reflection_cell, "EAF0FF")
    reflection_cell.paragraphs[0].text = (
        f"1. {lesson.worksheet.reflection_questions[0]}"
        if lesson.worksheet.reflection_questions
        else ""
    )
    for question_index, question in enumerate(lesson.worksheet.reflection_questions[1:], start=2):
        reflection_cell.add_paragraph(f"{question_index}. {question}")

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
