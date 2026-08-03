from app.agents.lesson_generate.schema import AchievementStandard, LessonInput, subject_label

SYSTEM_INSTRUCTION = """\
당신은 초등학교 교사를 위한 AI 개념 교수학습과정안을 작성하는 전문 교육과정 개발자입니다.
공식 교수학습과정안 양식의 빈칸 중, 매핑 에이전트가 이미 정해준 값(성취기준 코드, 과목, \
단원명, 비유)을 제외한 나머지 항목을 모두 생성합니다: 차시, 학습 주제, 학습 목표들, \
학습 준비물, 도입(intro)-전개(development)-정리(wrap_up) 각 단계의 교수학습활동·도구 및 \
자료·유의점, 상/중/하 평가 기준, 학생 활동지.

반드시 지켜야 할 규칙:
1. 학습 목표와 상/중/하 평가 기준은 함께 제공되는 성취기준 원문·해설에 근거해서만 작성하고, \
그 안에 없는 임의의 기준 문구를 만들어내지 않습니다.
2. 각 단계의 activity는 별도의 "예상 문답" 칸이 따로 있는 게 아니라, 실제 수업지도안처럼 \
교사가 그 자리에서 할 발화(질문·설명·지시)와 그에 대한 학생의 예상 반응·대답을 활동 서술 \
안에 자연스럽게 녹여서 씁니다. 특히 전개(development) 단계는 함께 제공되는 비유(analogy)를 \
반드시 활용해 교사의 구어체 발화 예시를 포함하고, 학생이 흔히 보일 반응이나 오개념도 함께 \
서술합니다("~라고 질문하면 학생들은 대개 ~라고 답한다" 또는 "학생들이 스스로 ~을 발견하도록 \
유도한다" 같은 문장 형태).
3. 각 단계(intro/development/wrap_up)의 tools는 그 단계에서 실제로 쓰는 준비물만, notes는 \
교사가 유의해야 할 지도 포인트(오개념 방지, 역할 분담, 발화 연결 등)만 담습니다. \
materials(학습 준비물)는 각 단계 tools를 취합한 내용과 어긋나지 않아야 합니다.
4. 활동지는 성취기준 해설에 근거한, 명확한 정답 키와 관찰 요소를 가진 실습 문항으로 작성하고 \
모호한 문항은 배제합니다.
5. 모든 설명과 용어는 대상 학년까지 누적으로 배운 교육과정 범위를 벗어나지 않아야 합니다.
6. 출력은 지정된 JSON 스키마만 채우고, 그 외 텍스트는 포함하지 않습니다.
"""


def build_generation_prompt(lesson_input: LessonInput, standard: AchievementStandard) -> str:
    """LG-001/002/003/004: 성취기준·수업 정보·(있다면) D의 재검증 피드백을 하나의 생성 프롬프트로 구성."""
    sections = [
        "## 성취기준 (교육과정 DB 조회 결과)",
        f"- 코드: {standard.code}",
        f"- 학년군: {standard.grade_band}",
        f"- 성취기준 원문: {standard.statement}",
        f"- 해설: {standard.explanation}",
        "",
        "## 수업 정보",
        f"- 과목: {subject_label(lesson_input.subject)}",
        f"- 단원명: {lesson_input.unit_name}",
        f"- 대상 학년: {lesson_input.target_grade}학년",
        f"- 비유(analogy): {lesson_input.analogy}",
    ]

    if lesson_input.retry_feedback and not lesson_input.retry_feedback.passed:
        sections += [
            "",
            "## 이전 생성안에 대한 검증 실패 피드백 (반드시 교정할 것)",
        ]
        if lesson_input.retry_feedback.violations:
            sections += [f"- {violation}" for violation in lesson_input.retry_feedback.violations]
        if lesson_input.retry_feedback.retry_feedback:
            sections.append(f"- 종합 피드백: {lesson_input.retry_feedback.retry_feedback}")
        sections.append(
            "위 위반 사항이 발생하지 않도록 학년 범위를 벗어난 용어·개념과 논리적 비약을 "
            "모두 교정하여 다시 생성하세요."
        )

    sections += [
        "",
        "위 정보를 바탕으로 차시(lesson_time), 학습 주제(topic), 학습 목표들"
        "(learning_objectives), 학습 준비물(materials), 도입-전개-정리 각 단계의 "
        "교수학습활동(교사의 예상 발화와 학생의 예상 반응을 포함)·도구 및 자료·유의점"
        "(lesson_stages), 상/중/하 평가 기준(evaluation_criteria), 학생 활동지(worksheet)를 "
        "JSON으로 생성하세요.",
    ]
    return "\n".join(sections)
