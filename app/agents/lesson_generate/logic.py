import re
from typing import Any

from app.agents.lesson_generate.db_client import fetch_achievement_standard
from app.agents.lesson_generate.prompts import (
    build_generation_prompt,
    build_system_instruction,
)
from app.agents.lesson_generate.schema import (
    SCHOOL_LEVEL,
    GeneratedLessonContent,
    LessonOutput,
    build_lesson_input,
    subject_label,
)
from app.lib.gemini import generate_structured
from app.lib.types import MappingResult, PipelineContext, ValidationResult


def _unescape_literal_newlines(value: Any) -> Any:
    """Gemini 구조화 출력이 가끔 실제 줄바꿈 대신 리터럴 "\\n"(역슬래시+n) 두 글자를
    문자열 그대로 내보내는 경우가 있다(이스케이프가 한 번 더 감긴 경우). teacher/student
    필드는 줄 구분(_split_heading_body 등)을 실제 "\n" 문자에 의존하므로, 이 상태로
    두면 DOCX/화면에 "\n" 글자가 그대로 노출된다. dict/list를 재귀적으로 훑어 문자열 안의
    리터럴 "\\n"·"\\t"만 실제 개행·탭 문자로 되돌린다(이미 정상인 문자열은 매치할 게 없어
    그대로 통과한다)."""
    if isinstance(value, str):
        return value.replace("\\n", "\n").replace("\\t", "\t")
    if isinstance(value, list):
        return [_unescape_literal_newlines(item) for item in value]
    if isinstance(value, dict):
        return {key: _unescape_literal_newlines(item) for key, item in value.items()}
    return value


# teacher/student 필드 안에서 "새 줄"로 취급해야 하는 마커. prompts.py 규칙(제목 줄 →
# "-" 서술 → "T : "/"S : " 발화 → 번호 절차)이 지정한 시작 기호들이다.
_LINE_MARKER = re.compile(r"(?:T\s*:\s|S\d*\s*:\s|-\s|◦|\d+\.\s)")

_T_LINE = re.compile(r"^T\s*:\s")
_S_LINE = re.compile(r"^S(\d*)\s*:\s")


def _ensure_structural_linebreaks(text: str) -> str:
    """Gemini가 가끔 제목 줄·"-" 서술·"T : "/"S : " 발화를 실제 개행 없이 공백만으로
    이어붙여 내보내는 경우가 있다(이번 "이미지 인식" 생성에서 실제 확인됨 — teacher/
    student 필드에 "\n"이 단 하나도 없이 한 줄로 뭉쳐 나옴). 이 상태로는 DOCX/화면에서
    발문·서술이 한 문단으로 뭉쳐 보이고, 뒤이은 S 재표기 판단(_renumber_single_answer_lines)도
    "줄" 단위가 없어 불가능하다. 이 마커들이 줄 맨 앞이 아닌 위치(공백 뒤)에 나타나면 그
    앞에 개행을 끼워 넣어, 이미 올바르게 개행된 문자열은 그대로 두고 뭉친 경우만 되돌린다."""

    def _break_before(match: re.Match) -> str:
        return match.group(0) if match.start() == 0 else "\n" + match.group(0)

    repaired = "\n".join(_LINE_MARKER.sub(_break_before, piece) for piece in text.split("\n"))
    return "\n".join(line.strip() for line in repaired.split("\n"))


def _renumber_single_answer_lines(teacher_text: str, student_text: str) -> str:
    """prompts.py 규칙 4: teacher의 T:가 여러 개면 student도 그만큼 "S : "(질문마다 답
    하나씩)를 쓰고, "S1 : "/"S2 : " 번호는 "한 발문에 반응이 여럿"일 때만 쓴다. 그런데
    모델이 가끔 이 둘을 헷갈려, 서로 다른 질문 각각의 단일 답변에 번호를 붙인다(예: T:가
    2개, 각각 답이 1개씩인데 S1/S2로 표기 — 실제로는 "한 질문에 답 하나"이므로 둘 다
    "S : "여야 한다). T: 줄 수와 S 줄 수가 정확히 같으면(질문 수만큼 답이 하나씩 있다는
    뜻) 번호를 모두 제거해 "S : "로 통일한다. 개수가 다르면(진짜로 한 질문에 답이 여럿인
    경우 등) 판단이 애매하므로 손대지 않는다."""
    teacher_lines = teacher_text.split("\n")
    student_lines = student_text.split("\n")
    t_count = sum(1 for line in teacher_lines if _T_LINE.match(line))
    s_line_indices = [i for i, line in enumerate(student_lines) if _S_LINE.match(line)]
    if t_count and len(s_line_indices) == t_count:
        for i in s_line_indices:
            student_lines[i] = _S_LINE.sub("S : ", student_lines[i])
    return "\n".join(student_lines)


# content_label별 고정 제목 문구(prompts.py 규칙 3). "학습 문제 안내하기" 같은 행은
# 본문이 "-"/"T :"/"◦" 마커 없이 순수 서술 한 문장으로 시작해서 _ensure_structural_
# linebreaks의 마커 탐지로는 제목과 본문을 나눌 수 없다(예: "◘ 학습 문제 안내하기
# 대상의 시각적 특징 탐색과..."처럼 제목 뒤에 개행 없이 바로 이어붙으면, DOCX의
# _split_heading_body가 전체를 제목으로 잘못 인식해 정작 안내 문장이 네모 상자 안에서
# 사라진다). 고정 문구를 알고 있으니 그 뒤에 개행이 없으면 직접 끼워 넣는다.
_FIXED_HEADINGS: dict[str, tuple[str, str]] = {
    "전시학습 상기": ("◎ 전시학습 상기", "◎ 전시학습 상기"),
    "학습 문제 안내하기": ("◘ 학습 문제 안내하기", "◘ 학습 문제 확인하기"),
    "학습 내용 정리": ("♥ 학습 정리하기", "♥ 학습 정리하기"),
    "차시예고": ("◎ 차시 예고", "◎ 차시 예고"),
}


def _ensure_heading_break(text: str, heading: str) -> str:
    """text가 heading으로 시작하는데 그 뒤에 개행 없이 본문이 바로 이어붙었으면
    개행을 끼워 넣는다. heading과 정확히 같거나 이미 개행이 있으면 그대로 둔다."""
    if text.startswith(heading) and len(text) > len(heading) and text[len(heading)] != "\n":
        return heading + "\n" + text[len(heading) :].lstrip()
    return text


_MINUTE_PATTERN = re.compile(r"(\d+)\s*분")
# "총 160분"/"전체 160분"처럼 이 차시가 아니라 단원 전체 시간을 가리키는
# 숫자 앞에 붙는 말. 이런 접두어가 붙은 숫자는 이 차시의 배정 시간이 아니므로
# 건너뛴다(그 뒤에 "40분"처럼 진짜 차시 시간이 따로 있으면 그걸 쓴다).
_TOTAL_TIME_PREFIX = re.compile(r"(총|전체)\s*$")


def _normalize_lesson_time(text: str) -> str:
    """lesson_time을 "40분" 형태로 통일한다. prompts.py 규칙 12로 LLM에 이미
    분수 표기를 쓰지 말라고 지시했지만, 프롬프트 지시만으로는 매 생성마다
    보장되지 않으므로("1/4차시 (40분)", "40분 (1/4차시)" 두 순서 모두 실제
    생성됨을 확인) 분 단위 숫자만 뽑아 고정 형식으로 되돌린다. "총 160분"처럼
    단원 전체 시간이 먼저 나오는 경우 그 숫자를 이 차시 시간으로 오인하지
    않도록 건너뛰고, 그 뒤에 오는 진짜 차시 시간을 쓴다. 분 표기 자체가
    없으면("1/4차시"처럼 분수만 있는 경우) 규칙 12의 기본값 "40분"으로
    떨어뜨린다(원본을 그대로 두면 없애려던 분수 표기가 그대로 통과한다)."""
    for match in _MINUTE_PATTERN.finditer(text):
        if _TOTAL_TIME_PREFIX.search(text[: match.start()]):
            continue
        return f"{match.group(1)}분"
    return "40분"


def _normalize_dialogue_formatting(content_dict: dict) -> dict:
    """lesson_stages의 모든 StageActivity에 제목 줄 분리(_ensure_heading_break) →
    마커 기반 개행 복구(_ensure_structural_linebreaks) → S 재표기
    (_renumber_single_answer_lines)를 순서대로 적용한다."""
    stages = content_dict.get("lesson_stages") or {}
    for activities in stages.values():
        for activity in activities:
            label = activity.get("content_label", "")
            teacher = activity.get("teacher", "")
            student = activity.get("student", "")
            if label in _FIXED_HEADINGS:
                teacher_heading, student_heading = _FIXED_HEADINGS[label]
                teacher = _ensure_heading_break(teacher, teacher_heading)
                student = _ensure_heading_break(student, student_heading)
            teacher = _ensure_structural_linebreaks(teacher)
            student = _ensure_structural_linebreaks(student)
            activity["teacher"] = teacher
            activity["student"] = _renumber_single_answer_lines(teacher, student)
    return content_dict


def generate_lesson(
    mapping_result: MappingResult,
    context: PipelineContext,
    retry_feedback: ValidationResult | None = None,
    caution_terms: list[str] | None = None,
) -> LessonOutput:
    """LG-001/LG-002/LG-003/LG-004: 교안 양식의 모든 빈칸을 한 번 채운다 (단일 시도).

    핵심 설계 원칙("B가 준 것은 그대로, 나머지는 Gemini로")에 따라, B가 이미
    판단해서 넘겨준 값(achievement_code/subject/unit_name/analogy)은 가공 없이
    그대로 echo하고, 양식에는 있지만 B·D 어디에서도 오지 않는 나머지 항목은
    Gemini 구조화 출력으로 직접 생성한다. achievement_code로 curriculum_chunks를
    조회해 학습 목표·평가 기준의 근거로 삼는다(LG-001). retry_feedback이 있으면
    (D의 재검증 실패) 프롬프트에 반영해 교정된 결과를 생성한다(LG-004). 최대
    3회 재시도 루프 자체는 오케스트레이터(D)의 책임이며, 이 함수는 한 번의
    생성만 담당한다.

    caution_terms: A1이 만든 이 개념의 노출 금지 원어·전문 용어 목록
    (concept.caution_terms). 지금까지는 D(validate)가 사후 검증할 때만 쓰이고
    C 프롬프트에는 전달되지 않아, 같은 개념인데도 노출 여부가 학년마다 뒤집히는
    현상이 있었다(2026-08-11 김준명 리뷰 지적 ③). 기본값 None이라 orchestrate.py
    호출부가 아직 이 인자를 넘기지 않아도 그대로 동작한다 — 넘겨받으면
    build_generation_prompt가 금지 목록을 프롬프트에 직접 박아 넣는다.
    """
    lesson_input = build_lesson_input(mapping_result, context, retry_feedback)
    standard = fetch_achievement_standard(lesson_input.achievement_code)
    prompt = (
        f"{build_system_instruction(lesson_input)}\n\n"
        f"{build_generation_prompt(lesson_input, standard, caution_terms)}"
    )

    # app/lib/gemini.py(E 소유)의 team 규약: 모든 에이전트는 이 모듈을 경유해서만
    # Gemini를 호출한다(google.genai 직접 import 금지). system_instruction을 별도로
    # 받지 않는 시그니처라 위에서 프롬프트에 직접 합쳤다.
    content: GeneratedLessonContent = generate_structured(
        prompt=prompt,
        response_schema=GeneratedLessonContent,
        prompt_version="lesson_generate-v1",
        # LessonOutput은 필드가 많고 중첩 구조(단계별 행, 활동지 섹션)라 low 기본값으로는
        # 활동2처럼 뒤쪽 필드가 통째로 누락되는 경우가 있어 한 단계 올린다.
        thinking_level="medium",
        # thinking_level=medium은 low보다 생성이 오래 걸려 기본 30초 타임아웃으로는
        # 504 DEADLINE_EXCEEDED가 실제로 발생함을 확인했다(2026-08-04 실측).
        timeout_s=90.0,
    )
    normalized = _normalize_dialogue_formatting(
        _unescape_literal_newlines(content.model_dump())
    )
    content = GeneratedLessonContent.model_validate(normalized)

    return LessonOutput(
        lesson_time=_normalize_lesson_time(content.lesson_time),
        school_level=SCHOOL_LEVEL,
        grade=lesson_input.target_grade,
        topic=content.topic,
        subject=subject_label(lesson_input.subject),
        achievement_code=lesson_input.achievement_code,
        achievement_statement=standard.statement,
        ai_digital_tool=content.ai_digital_tool,
        learning_objectives=content.learning_objectives,
        materials=content.materials,
        lesson_stages=content.lesson_stages,
        evaluation_criteria=content.evaluation_criteria,
        worksheet=content.worksheet,
        ai_principles=content.ai_principles,
        mapping_reason=lesson_input.mapping_reason,
        analogy=lesson_input.analogy,
    )
