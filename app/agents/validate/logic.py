# 소유: D(REQ-005)
"""교안 학년 제약조건 검증 (REQ-005 VALID-001).

C가 생성한 교안(`LessonOutput`을 dict로 변환한 값)에 대상 학년까지 배우지
않은 용어, 또는 초등 학습자에게 그대로 노출되면 안 되는 AI 전문 용어가
섞여 있는지 1단계(금지 용어 매칭)로 검사한다. 2단계(비유-개념 논리 검증,
LLM 기반)는 이 모듈 범위 밖이며 `prompts.py`에 골격만 있다.

## 판정 기준 (금지 용어 ① — 사람이 검수한 확정 목록)

처음에는 학년군 어휘 차집합(`vocab(전체) - vocab(대상 학년까지 누적)`)을
자동으로 금지어로 채택했다. 형태소 분석기 없이 표면형만 비교하는 방식이라
같은 낱말이 활용형 차이로 학년군 양쪽에 흩어지는 문제가 구조적으로
발생했고, 2026-08-06 1차 통합 실행("분류" 4학년)에서 정상 교안이 서술
표현("있는지","확인하기","새로운","친구들") 때문에 반복 반려되었다. 재시도마다
걸리는 용어가 완전히 달라 수렴하지 않았고(길이 필터·용언 필터·빈도
하한을 추가해도 노이즈가 근본적으로 사라지지 않음), 특히 `target_grade`가
낮을수록(예: 2학년) 누적 범위가 좁아 금지어가 폭증하는 구조적 문제도
있었다(2학년 기준 G1_2 어휘에 없는 모든 표면형이 금지어가 되는 셈).

그래서 판정 기준을 다음과 같이 바꿨다: **차집합은 후보 생성기로만 쓰고
(`scripts/explore_forbidden_terms.py`), 그 결과를 사람이 검수해 확정한
목록(`_CURATED_FORBIDDEN`)을 실제 판정 기준으로 삼는다.** "정량적으로
후보를 뽑은 뒤 사람이 검수해 확정"하는 절차이므로 REQ-005의 "검증 기준
명시화" 원칙에 여전히 부합하고("주관적으로 즉흥 판단"이 아니라 재현 가능한
파이프라인의 산출물을 검수한 것), "과도한 차단 방지(오탐 회피 우선)"
원칙에도 자동 차집합보다 더 잘 맞는다 — 사람이 서술 표현·일상어를 걸러낼
수 있기 때문이다.

`_CURATED_FORBIDDEN`은 학년군(GradeBand)별 확정 금지어 집합이고,
`grade_to_bands()`(`app/lib/types.py`)로 대상 학년의 누적 접근 가능
학년군을 구한 뒤, 그 밖의 학년군 목록만 합쳐 금지어로 쓴다:

    forbidden(target_grade) = union(
        _CURATED_FORBIDDEN[band] for band not in grade_to_bands(target_grade)
    ) | _ALWAYS_FORBIDDEN

`_ALWAYS_FORBIDDEN`("레이블","라벨")은 학년군 차집합과 무관하게 항상 합쳐진다
— 교육과정에 아예 등장하지 않는 용어라 "누적 범위에 들어왔다"는 개념이
성립하지 않기 때문이다. 따라서 6학년처럼 학년군 차집합이 비는 경우에도
금지어가 0개가 되지는 않는다(`test_grade6_has_no_band_derived_terms` 참고).

예를 들어 4학년은 `grade_to_bands(4) == {G1_2, G3_4}`이므로 G5_6 목록만
금지어가 되고, 2학년은 G3_4·G5_6 목록이 모두 금지어가 된다. G1_2는
`_CURATED_FORBIDDEN`에 정의하지 않는다 — 1~2학년 어휘는 어느 학년에서도
금지되지 않기 때문이다(가장 낮은 학년군이라 "아직 안 배운" 상위 개념이
없음). DB를 더 이상 조회하지 않으므로 매 검증마다 결과가 결정론적이고,
과목별 컬럼이 없는 대신(1차 프로토타입 범위) 향후 과목별 목록으로
세분화할 수 있게 `validate()`·`_build_forbidden_term_pool()`은 `subject`
인자를 계속 받는다.

## 매칭 규칙

REQ-005 예외 처리 원칙("과도한 차단 방지")에 따라 **오탐(과잉 차단) 회피를
미탐 감수보다 우선**한다. ①(큐레이션 목록)은 이미 사람이 검수했으므로
아래 필터는 ②(A1의 `caution_terms`, LLM이 매 요청마다 생성하는 동적 값)에만
적용한다:

1. **길이 필터**: 1~2음절 단독 금지어는 애초에 후보에서 제외한다. LLM이
   생성하는 `caution_terms`에 "값","수" 같은 일상어가 섞이면 정상 교안이
   통째로 반려되기 때문이다.
2. **용언·부사 필터** (`_is_predicate_form`): 활용형("탐구하여")·부사형
   ("명확하게")·용언 어간("구별하")은 애초에 명사가 아니므로 제외한다.
   2026-08-06 실측에서 확인된 실제 오탐 패턴이다.
3. **상용어 제외 목록** (`_ALWAYS_ALLOWED`): 위 두 필터를 통과해도 남을 수
   있는 상용 서술 표현·동음이의 일상어에 대한 안전망("모서리"처럼 교육과정
   용어이자 일상어라 문맥 판단이 불가능한 경우 포함).
4. **자기참조 제외** (`_is_self_reference`): 사용자가 입력한 개념명 자체나
   그 접두 파생어는 후보에서 뺀다. 2026-08-12 실측(#50)에서 "분류" 질의에
   `분류기`가, "패턴 인식" 질의에 `패턴 인식`이 `caution_terms`로 생성돼
   교안이 상시 반려됐다 — 가르치려는 개념 이름을 쓰지 않고 그 개념을
   설명하는 것은 불가능하므로 C가 무엇을 만들든 위반이 된다. 판정은 방향을
   가리지 않는다 — 개념명이 더 긴 경우("레이블링" 수업의 "레이블")도 같은
   교착이므로 함께 뺀다. 이 필터만은 ②뿐 아니라 ①에도 적용한다 — ①에도
   "프로그래밍"처럼 교사가 개념명으로 그대로 입력할 수 있는 단어가 있기
   때문이다(`_build_forbidden_term_pool` 참고). 다만 "분류" → "분류학"처럼
   접미사가 다른 학문 분야를 가리키면 자기참조로 보지 않는다.

매칭 자체("금지어가 텍스트 어디에 있는지 찾는 것")은 ①② 공통이며 이전과
동일하다:

5. **어절 경계 매칭**: 금지어는 어절 "시작"에서만 매칭하고, 그 뒤가
   어절 끝이거나 알려진 조사여야 한다. 조사 목록 외의 임의 접미사(파생어)는
   매칭하지 않는다 — 예를 들어 "분류"가 금지어일 때 "분류를"은 검출하지만
   "분류학"/"분류하기"는 검출하지 않는다. 파생어까지 잡으려 하면 완전히
   다른 개념("분류학"은 생물 분류학일 수도, AI 분류 문제일 수도 있음)을
   오판정할 위험이 더 크다고 판단해, "정확히 그 형태 + 조사"까지만 잡는
   보수적 정책을 택했다.
6. 부분 문자열 매칭은 금지한다("미분류"에 "분류"가 매칭되지 않도록 어절
   시작 위치를 lookbehind로 강제한다).

## 검사 대상 필드

`LessonOutput`(C 소유, dict로 전달됨) 중 학생에게 노출되는 부분만 검사한다:
topic, learning_objectives, materials, lesson_stages(intro/development/wrap_up)의
content_label/teacher/student/tools, worksheet 전체(title/mission/sections/
reflection_questions/table).

제외: `lesson_stages.*.notes`(교사용 유의점, 학생에게 노출되지 않음),
`evaluation_criteria.*`(교사용 평가 루브릭 — notes와 같은 성격의 교사 전용
참고자료로 취급).

## 학년별 AI 원리 개수 검증 (금지어 매칭과 별개)

위 ①~⑥은 모두 "금지어가 텍스트에 있는지"를 보는 검사다. 이와 성격이 다른
검사가 하나 더 있어 별도로 서술한다 — C가 채우는 `ai_principles` 배열의
**길이**를 학년별 기준값과 비교한다(REQ-005).

C 프롬프트 규칙 8이 "학년이 올라갈수록 다루는 원리 요소 수를 늘린다
(4학년 1개 / 5학년 2개 / 6학년 3개)"고 지시하고, 규칙 9가 그 원리들을
`ai_principles` 배열로 다시 나열하게 한다. 지시만 있고 검증 수단이 없어
실행마다 결과가 갈렸다(2026-08-11 실측: 예측·분류·패턴 인식 6학년은 3개,
군집화 6학년은 2개). 배열 길이 비교는 LLM 판정 없이 결정론적으로 되므로
D에서 잡는다.

- 기준값은 `_PRINCIPLE_COUNT_BY_GRADE` 상수다. 관리자 화면에서 조정 가능하게
  하는 방안도 검토했으나 이번 범위에서 그 요구가 확인되지 않아 하드코딩한다.
  1~3학년은 잠정 1개다 — C 프롬프트가 4~6학년만 명시하고 있어 가장 낮은
  단계에 맞췄다.
- **정확히 일치**를 요구한다(초과도 위반). 상한만 보면 "4학년 교안에 원리
  3개"를 놓치는데, 그건 이 검사가 지키려는 학년별 난이도 차등이 정확히
  무너진 경우다. 정확 일치는 재시도를 늘릴 수 있지만 재시도 상한은
  오케스트레이터의 MAX_RETRIES로 이미 막혀 있다.
- `ai_principles`가 `None`이거나 빈 배열이면 **검증을 건너뛴다.** C가 못
  채울 수 있고 기존 히스토리 데이터에는 이 필드가 아예 없다. 이 필드 하나
  때문에 재시도 루프가 도는 상황을 만들지 않는다.

위반은 기존 금지어 위반과 같은 경로를 탄다(`ValidationResult.violations`에
담기고 `retry_feedback`으로 C에 전달). 다만 violations 원소가 "검출된
금지어"인 기존 계약과 성격이 달라(개수 서술) `PRINCIPLE_COUNT_VIOLATION_PREFIX`
접두사로 구분한다 — 타입을 나누려면 공용 `app/lib/types.py`를 고쳐야 하고,
오케스트레이터의 수렴 불가 판정도 이 접두사로 개수 위반을 걸러낸다
(`orchestrate.py`의 `_forbidden_term_violations` 참고).

## 알려진 한계

2026-08-06 실측에서 A1의 `caution_terms`가 영어 원어("K-Means",
"Feature Vector")나 괄호 병기("표본 공간 (Sample Space)") 형태로 생성되는
것이 확인됐다. C의 교안은 한국어이므로 이 값들은 매칭되지 않아 ②가 사실상
항상 통과한다. A1에 프롬프트 조정을 요청한 상태이며, 그 전까지는 ①만으로
검증이 성립하도록 설계했다(②가 빈 리스트여도 정상 동작).

2026-08-12(#50)에는 그 반대 방향 실패가 확인됐다 — 위 건이 "매칭이 안 돼
②가 항상 통과"였다면, 이번엔 "매칭이 너무 정확해 회피 불가능한 단어까지
금지어가 됨"이다. 위 필터 4번(자기참조 제외)으로 대응했다. `caution_terms`는
매 호출 3~5개가 재선정되는 값이라 A1 프롬프트로는 보장되지 않는 반면(A1
담당자 확인) D 필터는 결정론적이라 D에서 처리한다.
"""

from __future__ import annotations

import logging
import re
import time
from functools import lru_cache
from typing import NamedTuple

from app.lib.types import GradeBand, PipelineContext, Subject, ValidationResult, grade_to_bands

logger = logging.getLogger(__name__)

# 매칭 시 term 뒤에 허용하는 조사(길이 긴 것부터 — 짧은 조사가 긴 조사의
# 접두사인 경우 먼저 매치되면 나머지가 어절 경계 판정에서 어긋나므로).
_JOSA_SUFFIXES = sorted(
    [
        "으로써", "로써", "이라는", "라는", "에서는", "에게서", "부터는",
        "까지는", "이지만", "지만", "이라고", "라고", "이며", "며",
        "으로", "에서", "부터", "까지", "에게", "한테", "이다", "이나",
        "이란", "란", "이랑", "랑", "만큼", "처럼", "보다",
        "은", "는", "이", "가", "을", "를", "의", "와", "과",
        "도", "만", "로", "에", "께", "나",
    ],
    key=len,
    reverse=True,
)

# 어절 시작 판정용: 직전 문자가 한글 음절이면 어절 중간이므로 매칭 제외.
_HANGUL_RANGE = "가-힣"

_MIN_TERM_LENGTH = 3  # 1~2음절 단독 금지어 제외(② caution_terms 전용)

# 자기참조 판정(#50). 짧은 쪽 뒤에 이 길이 이하가 덧붙은 관계까지만 같은 말로
# 보고 제외한다 — "분류"→"분류기"(+1)는 빼되 "분류 모델"/"분류모델"(+2)은
# 남긴다. 한국어 파생 접미사는 대부분 1음절("-기","-법","-자")이라 1이면
# 충분하고, 2로 두면 공백 없이 붙여 쓴 복합어("분류모델")가 새어 나간다
# (공백을 지운 뒤에는 복합어와 파생어를 어절로 구분할 수 없기 때문이다).
_MAX_SELF_REFERENCE_SUFFIX = 1

# 개념명이 이보다 짧으면 자기참조 필터를 아예 적용하지 않는다. 1음절
# 개념명으로 접두 매칭을 하면 무관한 용어까지 대량으로 빠져나간다.
_MIN_CONCEPT_LENGTH_FOR_FILTER = 2

# 그 자체가 별개의 학문 분야를 가리키는 파생 접미사. 개념명에 이 접미사만
# 붙은 항목은 길이 예산 안에 들어와도 자기참조로 보지 않는다 — "분류" 개념의
# 교안에서 "분류학"(생물 분류학)은 안 써도 되는 다른 용어이기 때문이다.
# 이 모듈 상단 "매칭 규칙" 4번이 경고하는 모호성이 정확히 이 케이스다.
_DISCIPLINE_SUFFIXES = ("학", "론")

# 조사 제거 후 이 음절로 끝나면 용언 어간 잔재로 간주해 버린다(② 전용).
_PREDICATE_STEM_REMNANTS = ("하", "되", "시키", "있", "없")

# 어절 자체가 활용형/부사형으로 끝나는 경우(② 전용). 길이가 긴 접미사부터
# 검사해야 짧은 접미사가 먼저 걸려 잘못 판정되지 않는다.
_PREDICATE_ENDINGS = (
    "하여", "하고", "하며", "하지", "하는", "하게", "해서",
    "되어", "되고", "되며", "되는",
    "시켜", "시키는",
    "있는", "있으며", "없는", "없으며",
    "아서", "어서", "았다", "었다", "아야", "어야", "여야",
    "한", "할", "된",
)

# "만들어"류(들다·짓다 등 ㄹ/불규칙 활용, ② 전용) — 하다/되다 패턴이 아니라
# 어절 전체가 아/어로 끝난다. 나머지 어미 패턴으로 못 잡는 활용형을 보수적으로
# 넓게 잡아 오탐(과잉 차단)보다 미탐을 택한다 — 이 때문에 "용어","언어"처럼
# 아/어로 끝나는 정상 명사도 후보에서 제외될 수 있음을 감수한다.
_PREDICATE_BARE_ENDINGS = ("아", "어")

# 실측(2026-08-06, "분류" 4학년)에서 위 필터를 거치고도 남았거나, 필터
# 로직으로 커버하기 애매한 상용 서술 표현·동음이의 일상어에 대한 안전망
# (② 전용 — ①은 사람이 이미 검수했으므로 이 목록을 거치지 않는다).
_ALWAYS_ALLOWED = frozenset(
    {
        "만들어", "명확하게", "탐구하여", "구별하",  # 2026-08-06 실측 오탐
        "있는지", "확인하기", "새로운", "친구들",  # 성취기준 해설문 서술 표현·일상어
        "이해하고", "설명할", "표현할", "활용하여", "바탕으로",
        "모서리",  # 교육과정 용어이자 일상어라 문맥 판단 불가 — 미탐 감수하고 제외
    }
)

# 차집합으로 파생한 후보(scripts/explore_forbidden_terms.py)를 사람이 검수해
# 확정한 목록 (2026-08-06).
#
# 검수 기준:
# - 채택: 해당 학년군에서 처음 도입되는 교과 용어
# - 제외: 성취기준 해설문의 서술 표현("있도록", "연계된다", "확인하기")
# - 제외: 교육과정 메타 어휘("초등학교", "성취기준", "1∼3학년군")
# - 제외: 일상어로도 쓰이는 낱말("친구들", "계산기", "모서리") — 오탐 방지
# - 제외: 파싱 잔재("름다움", "모눈종", "문제해") — A2에 공유함
# - 제외: "인공지능", "데이터" — 본 시스템의 교안 주제 자체라 금지 불가
#
# 학년군과 무관하게 전 밴드에 적용하는 고정 금지어. 교육과정 424청크를
# 전수 조회한 결과 두 용어 모두 0건이라(A1 담당자 확인, 2026-08-12) 어느
# 학년에서도 "이미 배운 말"이 될 수 없다. 군집화 교안에서 "정답 레이블
# 없이"라는 표현이 학습목표·정리 단계에 노출되고도 검증을 통과한 사례가
# 계기다 — 군집화에서 "레이블"은 개념이 쓰는 용어가 아니라 "없다"고
# 부정하는 용어라 A1의 caution_terms 후보에 잘 잡히지 않는다.
_ALWAYS_FORBIDDEN = frozenset({"레이블", "라벨"})

# G1_2는 정의하지 않는다. 1~2학년 어휘는 어느 학년에서도 금지되지 않는다.
_CURATED_FORBIDDEN: dict[GradeBand, frozenset[str]] = {
    GradeBand.G3_4: frozenset({
        # 수와 연산
        "나눗셈", "나눗셈식", "단위분수", "진분수", "가분수", "대분수",
        "소수점", "어림셈", "어림하기", "사칙계산", "나누어떨어진다",
        # 도형과 측정
        "반직선", "예각삼각형", "둔각삼각형", "직각삼각형", "이등변삼각형",
        "직사각형", "정사각형", "사다리꼴", "평행사변형", "다각형", "정다각형",
        "대각선", "반지름", "평행선", "측정도구",
        # 자료와 가능성
        "막대그래프", "꺾은선그래프", "그림그래프",
        # 과학
        "생태계",
    }),
    GradeBand.G5_6: frozenset({
        # 수와 연산
        "기약분수", "공배수", "공약수", "최대공약수", "최소공배수",
        "공통분모", "소인수", "반올림", "분모끼리",
        # 도형과 측정
        "원주율", "전개도", "각기둥", "정육면체", "겉넓이",
        "선대칭도형", "점대칭도형", "대칭축", "대응각", "대응변", "대응점",
        # 변화와 관계
        "백분율",
        # 자료와 가능성
        "원그래프", "띠그래프",
        # 실과·과학
        "프로그래밍", "블록기반", "스마트팜", "산성화", "지속가능성", "의식주",
    }),
}


# 학년별로 교안이 다뤄야 하는 AI 원리 개수(모듈 docstring "학년별 AI 원리 개수
# 검증" 절 참고). 4~6학년은 C 프롬프트 규칙 8과 같은 값이고, 1~3학년은 C
# 프롬프트에 명시가 없어 가장 낮은 단계인 1개로 잠정 고정한다.
_PRINCIPLE_COUNT_BY_GRADE: dict[int, int] = {1: 1, 2: 1, 3: 1, 4: 1, 5: 2, 6: 3}

# 개수 위반을 `ValidationResult.violations`에 담을 때 붙이는 접두사. violations의
# 나머지 원소는 "검출된 금지어" 문자열이라 성격이 다르고, 오케스트레이터가
# 수렴 불가 판정에서 개수 위반을 제외하는 데도 이 접두사를 쓴다.
PRINCIPLE_COUNT_VIOLATION_PREFIX = "AI 원리 개수"


def expected_principle_count(target_grade: int) -> int | None:
    """해당 학년이 다뤄야 하는 AI 원리 개수. 기준값이 없는 학년이면 None.

    검증 본체(`_check_principle_count`)와 진단 스크립트(scripts/try_pipeline.py)가
    같은 기준을 보도록 공개한다 — 스크립트가 기준값을 따로 들고 있으면 두 곳이
    조용히 어긋난다.
    """
    return _PRINCIPLE_COUNT_BY_GRADE.get(target_grade)


class _PrincipleCountIssue(NamedTuple):
    """원리 개수 위반 1건.

    violations에 넣을 라벨과 C에게 줄 지시문을 함께 들고 다닌다 — 라벨은
    사후 집계·수렴 판정용이라 짧아야 하고, 지시문은 C가 무엇을 고쳐야 하는지
    알 수 있을 만큼 구체적이어야 해서 문구가 서로 다르다.
    """

    label: str
    feedback: str


def _check_principle_count(lesson_plan: dict, target_grade: int) -> _PrincipleCountIssue | None:
    """`ai_principles` 배열 길이가 학년 기준값과 정확히 같은지 본다. 맞으면 None.

    `ai_principles`가 없거나 비어 있으면 검증을 건너뛴다(모듈 docstring 참고).
    기준값이 없는 학년(_PRINCIPLE_COUNT_BY_GRADE 밖)도 마찬가지로 건너뛴다.
    """
    principles = lesson_plan.get("ai_principles")
    if not principles:
        return None

    expected = expected_principle_count(target_grade)
    if expected is None:
        return None

    actual = len(principles)
    if actual == expected:
        return None

    return _PrincipleCountIssue(
        label=f"{PRINCIPLE_COUNT_VIOLATION_PREFIX}: {expected}개 필요, {actual}개 생성",
        feedback=(
            # 미달·초과 양쪽에 쓰이는 문구다("{actual}개만 있습니다"처럼 미달을
            # 전제한 표현을 쓰면 초과일 때 지시가 어긋난다).
            f"대상 학년({target_grade}학년) 교안은 AI 원리를 {expected}개 다뤄야 하는데 "
            f"지금은 {actual}개입니다. 활동2와 학습 목표에서 다루는 원리 개수를 "
            f"{expected}개로 맞추고, ai_principles 배열도 그 원리들을 난이도 오름차순으로 "
            f"{expected}개 나열해 주세요."
        ),
    )


def _is_predicate_form(term: str) -> bool:
    """용언 어간 잔재·활용형·부사형으로 판단되면 True(② 후보에서 제외 대상).

    형태소 분석기 없이 접미사 패턴만으로 판정하므로 완벽하지 않다. "은하"처럼
    "하"로 끝나는 정상 명사가 함께 걸러질 수 있는데, REQ-005 예외 처리
    원칙(과도한 차단 방지)에 따라 이런 미탐은 감수한다.
    """
    if term.endswith(_PREDICATE_STEM_REMNANTS):
        return True
    if term.endswith(_PREDICATE_ENDINGS):
        return True
    return term.endswith(_PREDICATE_BARE_ENDINGS)


def _normalize_term(text: str) -> str:
    """공백 차이를 흡수한다("패턴 인식" == "패턴인식")."""
    return text.replace(" ", "")


def _is_self_reference(term: str, concept_name: str) -> bool:
    """가르치려는 개념명 자체이거나 그 접두 파생어면 True(제외 대상).

    #50: A1이 "분류" 질의에 `분류기`를, "패턴 인식" 질의에 `패턴 인식`을
    caution_terms로 내보내 교안이 상시 반려됐다. 개념명을 쓰지 않고 그 개념을
    가르치는 것은 불가능하므로 금지어로 성립하지 않는다.

    공백을 지워 정규화한 뒤, 완전 일치이거나 **한쪽이 다른 쪽의 접두이면서
    길이 차이가 `_MAX_SELF_REFERENCE_SUFFIX` 이하**면 같은 말로 본다. 방향을
    가리지 않는 이유는 양쪽 모두 같은 교착을 만들기 때문이다.

    - 항목이 더 긴 경우: 개념명 "분류" → 항목 "분류기"
    - 개념명이 더 긴 경우: 개념명 "레이블링" → 항목 "레이블". 이쪽을 빼먹으면
      "레이블링"을 가르치는 교안이 "레이블"이라는 말을 못 써서 영원히 반려된다.

    길이 차이 1이라는 예산이 좁게 잡는 장치다. "분류 모델"·"분류모델"(+2)이나
    "분류 알고리즘"(+4)은 개념명으로 시작하더라도 독립된 전문 용어이므로 계속
    금지어로 남고, "패턴 인식" → "패턴 인식기"(+1)처럼 접미사 하나만 붙은
    파생어는 제외된다. 어절 수로 복합어를 가려내는 방법은 쓰지 않는다 —
    정규화가 공백을 지우므로 "분류 모델"과 "분류모델"이 서로 다르게 판정되어
    표기 차이만으로 결과가 갈린다.

    예산 안에 들어와도 `_DISCIPLINE_SUFFIXES`로 끝나는 파생어는 제외하지
    않는다. 이 모듈 상단 "매칭 규칙" 4번이 경고하는 "분류학"(생물 분류학)이
    그 경우로, 개념명 "분류"와 1음절 차이지만 다른 학문 분야를 가리키는
    독립 용어다. 반대 방향(개념명이 "분류학"이고 항목이 "분류")은 그대로
    제외한다 — 분류학을 가르치면서 "분류"라는 말을 안 쓸 수는 없다.
    """
    normalized_concept = _normalize_term(concept_name)
    if len(normalized_concept) < _MIN_CONCEPT_LENGTH_FOR_FILTER:
        return False

    normalized_term = _normalize_term(term)
    if normalized_term == normalized_concept:
        return True

    term_is_longer = len(normalized_term) > len(normalized_concept)
    longer, shorter = (
        (normalized_term, normalized_concept)
        if term_is_longer
        else (normalized_concept, normalized_term)
    )
    if not longer.startswith(shorter):
        return False
    if len(longer) - len(shorter) > _MAX_SELF_REFERENCE_SUFFIX:
        return False
    return not (term_is_longer and longer.endswith(_DISCIPLINE_SUFFIXES))


def _is_valid_forbidden_term(term: str) -> bool:
    """② caution_terms 채택 조건: 길이/한글 포함/용언 아님/상용어 아님을 모두 만족해야 함.

    ①(`_CURATED_FORBIDDEN`)은 사람이 이미 검수했으므로 이 필터를 거치지
    않는다 — LLM이 매 요청마다 생성하는 동적 값(②)에만 적용한다.
    """
    stripped = _normalize_term(term)
    if len(stripped) < _MIN_TERM_LENGTH:
        return False
    if not any("가" <= ch <= "힣" for ch in stripped):
        return False
    if _is_predicate_form(stripped):
        return False
    return term not in _ALWAYS_ALLOWED


def _curriculum_forbidden_terms(target_grade: int) -> frozenset[str]:
    """대상 학년이 아직 도달하지 않은 학년군의 확정 금지어 + 전 밴드 고정 금지어.

    grade_to_bands()가 누적 범위를 주므로, `_CURATED_FORBIDDEN`의 전체
    학년군 키에서 그것을 빼면 "아직 배우지 않은 학년군"이 된다. 예를 들어
    4학년은 {G1_2, G3_4}가 누적이므로 G5_6 목록만 금지어가 된다. DB 조회가
    없는 순수 함수라 캐싱이 필요 없다.

    `_ALWAYS_FORBIDDEN`은 학년군 차집합과 무관하게 항상 합친다 — 교육과정에
    아예 등장하지 않는 용어라 "누적 범위에 들어왔다"는 개념이 성립하지 않는다.
    """
    accessible_bands = grade_to_bands(target_grade)
    not_yet_bands = set(_CURATED_FORBIDDEN) - accessible_bands

    forbidden: set[str] = set(_ALWAYS_FORBIDDEN)
    for band in not_yet_bands:
        forbidden |= _CURATED_FORBIDDEN[band]
    return frozenset(forbidden)


def _build_forbidden_term_pool(
    target_grade: int, subject: Subject, caution_terms: list[str], concept_name: str
) -> set[str]:
    """① 큐레이션 목록 + ② A1 caution_terms를 합집합한다.

    `subject`는 현재 ① 파생에 쓰이지 않는다(전 과목 공통 목록). 그래도
    인자를 받는 이유는 (1) `validate()`의 시그니처·오케스트레이터 계약을
    유지하고, (2) 향후 과목별 확정 목록으로 세분화할 여지를 남기기 위해서다.

    `concept_name`을 이용한 자기참조 제외(#50)는 **출처를 가리지 않고 전부**
    적용한다. 처음에는 "①은 사람이 검수한 교과 용어라 AI 개념명으로 입력될
    일이 없다"고 보고 ②에만 걸었는데, `_CURATED_FORBIDDEN[G5_6]`에 이미
    "프로그래밍"이 있어 그 전제가 성립하지 않는다 — "프로그래밍"을 3학년
    대상으로 요청하면 그 단어를 안 쓰고 교안을 쓸 수 없어 #50과 똑같은
    무한 반려가 된다. 교착(무조건 반려)이 누출(그 개념을 가르치는 교안 한
    편에 그 단어가 노출)보다 나쁘다는 판단은 모든 출처에 똑같이 적용된다.
    """
    curriculum_terms = {
        t
        for t in _curriculum_forbidden_terms(target_grade)
        if not _is_self_reference(t, concept_name)
    }
    caution_filtered = {
        t
        for t in caution_terms
        if _is_valid_forbidden_term(t) and not _is_self_reference(t, concept_name)
    }
    return curriculum_terms | caution_filtered


@lru_cache(maxsize=None)
def _term_pattern(term: str) -> re.Pattern[str]:
    """금지어 하나를 어절 경계(시작) + (어절 끝 | 조사)에서만 매칭하는 정규식으로 컴파일.

    금지어 풀이 수백 개 규모라 검증 1회마다 동일 term의 패턴을 반복
    컴파일하지 않도록 캐싱한다(term은 재시도 간, 그리고 같은 학년 간에
    대부분 겹친다).
    """
    escaped = re.escape(term)
    josa_alt = "|".join(re.escape(j) for j in _JOSA_SUFFIXES)
    boundary = rf"(?=$|[^{_HANGUL_RANGE}A-Za-z0-9]|{josa_alt})"
    lookbehind = rf"(?<![{_HANGUL_RANGE}])"
    return re.compile(lookbehind + escaped + boundary)


def _term_matches(text: str, term: str) -> bool:
    """단일 금지어가 텍스트 안에서 어절 경계 규칙에 맞게 등장하는지 검사한다."""
    return _term_pattern(term).search(text) is not None


def _find_violations(texts: list[str], forbidden_terms: set[str]) -> list[str]:
    """검사 대상 텍스트 목록 전체에서 실제로 검출된 금지어만 반환한다(중복 제거)."""
    found: list[str] = []
    seen: set[str] = set()
    for term in forbidden_terms:
        if term in seen:
            continue
        for text in texts:
            if _term_matches(text, term):
                found.append(term)
                seen.add(term)
                break
    return found


def _collect_student_facing_texts(lesson_plan: dict) -> list[str]:
    """LessonOutput(dict) 중 학생에게 노출되는 텍스트만 모은다.

    제외: lesson_stages.*.notes(교사용 유의점), evaluation_criteria(교사용
    평가 루브릭) — 모듈 docstring "검사 대상 필드" 절 참고.
    """
    texts: list[str] = []

    topic = lesson_plan.get("topic")
    if topic:
        texts.append(str(topic))

    texts.extend(str(x) for x in lesson_plan.get("learning_objectives", []) or [])
    texts.extend(str(x) for x in lesson_plan.get("materials", []) or [])

    lesson_stages = lesson_plan.get("lesson_stages") or {}
    for stage_key in ("intro", "development", "wrap_up"):
        for activity in lesson_stages.get(stage_key, []) or []:
            if not isinstance(activity, dict):
                continue
            for field in ("content_label", "teacher", "student"):
                value = activity.get(field)
                if value:
                    texts.append(str(value))
            texts.extend(str(x) for x in activity.get("tools", []) or [])
            # notes는 교사용 유의점이라 학생에게 노출되지 않으므로 의도적으로 제외한다.

    worksheet = lesson_plan.get("worksheet")
    if worksheet:
        for field in ("title", "mission"):
            value = worksheet.get(field)
            if value:
                texts.append(str(value))
        texts.extend(str(x) for x in worksheet.get("reflection_questions", []) or [])
        for section in worksheet.get("sections", []) or []:
            if not isinstance(section, dict):
                continue
            instruction = section.get("instruction")
            if instruction:
                texts.append(str(instruction))
            texts.extend(str(x) for x in section.get("items", []) or [])
            table = section.get("table")
            if table:
                texts.extend(str(x) for x in table.get("headers", []) or [])
                for row in table.get("rows", []) or []:
                    texts.extend(str(x) for x in row or [])

    # evaluation_criteria는 교사용 평가 루브릭이라 notes와 같은 이유로 제외한다.

    return texts


def _build_retry_feedback(
    term_violations: list[str], principle_issue: _PrincipleCountIssue | None
) -> str:
    """C의 재생성 프롬프트에 그대로 들어갈 지시문 형태의 피드백을 만든다.

    두 종류의 위반은 고쳐야 할 것이 서로 달라(용어 순화 vs 원리 개수 조정)
    문단을 나눠서 담는다. 둘 다 없으면 빈 문자열이다.
    """
    sections: list[str] = []

    if term_violations:
        bullet_lines = "\n".join(
            f"- '{term}': 대상 학년 학생에게 아직 낯선 용어입니다." for term in term_violations
        )
        sections.append(
            "다음 용어들은 대상 학년 학생이 아직 배우지 않았거나 그대로 노출하면 "
            "안 되는 전문 용어입니다. 같은 의미를 쉬운 표현이나 비유로 바꿔서 "
            "다시 작성해 주세요:\n"
            f"{bullet_lines}"
        )

    if principle_issue is not None:
        sections.append(principle_issue.feedback)

    return "\n\n".join(sections)


def validate(
    lesson_plan: dict,
    context: PipelineContext,
    *,
    subject: Subject,
    caution_terms: list[str],
    concept_name: str,
) -> ValidationResult:
    """교안을 검증하고 ValidationResult를 반환한다.

    매 호출을 독립적으로 처리한다 — 몇 번째 재시도인지는 알 필요가 없다
    (재시도 카운터는 오케스트레이터가 지역 변수로 관리).

    `concept_name`은 A1이 구조화한 개념명(`StructuredConcept.concept_name`)이며
    자기참조 금지어 제외에 쓴다(#50). `PipelineContext`에는 없는 값이라
    오케스트레이터가 별도 인자로 넘긴다.
    """
    start = time.monotonic()

    forbidden_terms = _build_forbidden_term_pool(
        context.target_grade, subject, caution_terms, concept_name
    )
    texts = _collect_student_facing_texts(lesson_plan)
    term_violations = _find_violations(texts, forbidden_terms)

    # 금지어 매칭과 독립적으로 판정한다 — 두 위반이 동시에 나면 둘 다 리포트한다.
    principle_issue = _check_principle_count(lesson_plan, context.target_grade)

    violations = list(term_violations)
    if principle_issue is not None:
        violations.append(principle_issue.label)

    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        f"validate_end grade={context.target_grade} subject={subject.value} "
        f"forbidden_term_count={len(forbidden_terms)} violation_count={len(violations)} "
        f"principle_count_violation={principle_issue is not None} "
        f"elapsed_ms={elapsed_ms:.1f}"
    )

    return ValidationResult(
        passed=len(violations) == 0,
        violations=violations,
        retry_feedback=_build_retry_feedback(term_violations, principle_issue),
    )
