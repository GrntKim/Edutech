"""RS-004(C) lesson_generate 유닛 테스트.

`_normalize_lesson_time`은 입출력이 문자열뿐인 순수 함수라 DB·Gemini mock
없이 직접 검증한다(#64 리뷰 — 폴백·"총" 접두어 회귀 방지).
"""

from app.agents.lesson_generate.logic import _normalize_lesson_time


def test_normalize_lesson_time_strips_fraction_notation():
    """"N/M차시"가 앞뒤 어느 순서로 붙어도 분 숫자만 남긴다."""
    assert _normalize_lesson_time("1/4차시 (40분)") == "40분"
    assert _normalize_lesson_time("40분 (1/4차시)") == "40분"
    assert _normalize_lesson_time("1차시(40분)") == "40분"  # main_routes 픽스처와 동일 형태


def test_normalize_lesson_time_already_normalized_is_unchanged():
    assert _normalize_lesson_time("40분") == "40분"


def test_normalize_lesson_time_falls_back_when_no_minute_number():
    """분 표기 자체가 없으면("1/4차시"처럼 분수만 있는 경우) 규칙 12의
    기본값으로 떨어진다 — 원본을 그대로 두면 없애려던 분수 표기가 그대로
    통과해 버린다."""
    assert _normalize_lesson_time("3/4차시") == "40분"
    assert _normalize_lesson_time("") == "40분"


def test_normalize_lesson_time_skips_total_time_prefix():
    """"총"/"전체"가 붙은 숫자는 단원 전체 시간이지 이 차시 배정 시간이
    아니므로 건너뛰고, 그 뒤에 오는 진짜 차시 시간을 쓴다."""
    assert _normalize_lesson_time("1/4차시(총 160분)") == "40분"
    assert _normalize_lesson_time("1/4차시(총 160분, 이번 시간 40분)") == "40분"
    assert _normalize_lesson_time("전체 160분 중 40분") == "40분"
