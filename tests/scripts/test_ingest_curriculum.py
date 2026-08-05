"""app/scripts/ingest_curriculum.py의 정규식 기반 상태기계 파서 회귀 테스트.

실제 PDF 파일 대신 짧은 합성(fixture) 텍스트 페이지로 parse_achievement_records()를
검증한다. build_curriculum_chunks()는 실제 PDF(SOURCE_FILES)에 의존하는 통합 테스트
성격이라, extract_pages/parse_achievement_records를 mock해서 Pydantic 검증 실패 시
에러를 모으는 동작만 별도로 검증한다.
"""

import pytest

from scripts import ingest_curriculum
from scripts.ingest_curriculum import (
    CurriculumChunk,
    GradeBand,
    Subject,
    parse_achievement_records,
)


class TestParseAchievementRecords:
    def test_single_achievement_standard_is_parsed_with_grade_band_and_domain(self):
        page = "\n".join(
            [
                "1. 성격",
                "이 부분은 성취기준 섹션이 아니다",
                "나. 성취기준",
                "[초등학교 3~4학년]",
                "(1) 도형과 측정",
                "[4수03-05] 도형의 성질을 이해한다.",
                "<탐구 활동>",
                "• 도형 찾기 놀이를 한다.",
                "(가) 성취기준 해설",
                "• [4수03-05] 여러 도형을 관찰하는 것이다.",
            ]
        )

        records = parse_achievement_records([page], Subject.MATH)

        assert len(records) == 1
        record = records[0]
        assert record["chunk_id"] == "4수03-05"
        assert record["subject"] == Subject.MATH
        assert record["grade_band"] == GradeBand.G3_4
        assert record["unit_name"] == "도형과 측정"
        assert record["domain"] == "도형과 측정"
        assert record["achievement_code"] == "[4수03-05]"
        assert record["achievement_text"] == "도형의 성질을 이해한다."
        assert record["inquiry_activities"] == ["도형 찾기 놀이를 한다."]
        assert "관찰하는 것이다" in record["explanation"]

    def test_grade_band_header_maps_to_correct_band(self):
        def _page(header: str, code: str) -> str:
            return "\n".join(
                [
                    "나. 성취기준",
                    header,
                    "(1) 영역",
                    f"[{code}] 성취기준 텍스트",
                ]
            )

        low = parse_achievement_records([_page("[초등학교 1~2학년]", "2수01-01")], Subject.MATH)
        mid = parse_achievement_records([_page("[초등학교 3~4학년]", "4수01-01")], Subject.MATH)
        high = parse_achievement_records([_page("[초등학교 5~6학년]", "6수01-01")], Subject.MATH)

        assert low[0]["grade_band"] == GradeBand.G1_2
        assert mid[0]["grade_band"] == GradeBand.G3_4
        assert high[0]["grade_band"] == GradeBand.G5_6

    def test_domestic_science_simple_header_maps_to_g5_6(self):
        """실과(기술⋅가정)/정보과는 학년군 범위 대신 "[초등학교 실과]"처럼 과목명만
        표기한다 — 실과는 5~6학년군에만 존재하므로 그 학년군으로 고정된다."""
        page = "\n".join(
            [
                "나. 성취기준",
                "[초등학교 실과]",
                "(1) 가정생활과 안전",
                "[6실01-01] 실과 성취기준 텍스트",
            ]
        )

        records = parse_achievement_records([page], Subject.DOMESTIC_SCIENCE)

        assert len(records) == 1
        assert records[0]["grade_band"] == GradeBand.G5_6

    def test_inquiry_section_present_populates_inquiry_activities(self):
        page = "\n".join(
            [
                "나. 성취기준",
                "[초등학교 3~4학년]",
                "(1) 영역",
                "[4수01-01] 성취기준 텍스트",
                "<탐구 활동>",
                "• 활동 1",
                "• 활동 2",
            ]
        )

        records = parse_achievement_records([page], Subject.MATH)

        assert records[0]["inquiry_activities"] == ["활동 1", "활동 2"]

    def test_no_inquiry_section_leaves_inquiry_activities_empty(self):
        page = "\n".join(
            [
                "나. 성취기준",
                "[초등학교 3~4학년]",
                "(1) 영역",
                "[4수01-01] 성취기준 텍스트",
            ]
        )

        records = parse_achievement_records([page], Subject.MATH)

        assert records[0]["inquiry_activities"] == []

    def test_missing_standards_section_header_yields_zero_records(self):
        """'나. 성취기준' 헤더가 없으면 이후 내용이 전혀 파싱되지 않는다. 헤더 정규식이
        깨졌을 때 이 동작이 '조용한 0건'으로 이어진다는 걸 명세해 둔다."""
        page = "\n".join(
            [
                "[초등학교 3~4학년]",
                "(1) 영역",
                "[4수01-01] 성취기준 텍스트",
            ]
        )

        records = parse_achievement_records([page], Subject.MATH)

        assert records == []

    def test_multi_domain_multi_code_produces_expected_nonzero_record_count(self):
        """영역 2개 x 성취기준 2개 = 4건. PDF 포맷이 바뀌어 헤더 매칭이 깨지면 이 테스트가
        4건이 아니라 0건을 반환해 실패해야 한다 — "포맷이 바뀌면 과목이 통째로 0개로
        파싱돼도 안 드러난다"는 리뷰 지적에 대한 회귀 테스트."""
        page = "\n".join(
            [
                "나. 성취기준",
                "[초등학교 3~4학년]",
                "(1) 영역A",
                "[4수01-01] 영역A 성취기준 1",
                "[4수01-02] 영역A 성취기준 2",
                "(2) 영역B",
                "[4수02-01] 영역B 성취기준 1",
                "[4수02-02] 영역B 성취기준 2",
            ]
        )

        records = parse_achievement_records([page], Subject.MATH)

        assert len(records) == 4
        assert {r["chunk_id"] for r in records} == {
            "4수01-01",
            "4수01-02",
            "4수02-01",
            "4수02-02",
        }

    def test_top_level_section_boundary_resets_grade_band_and_domain(self):
        """다음 최상위 절(예: '2. 목표')로 넘어가면 이전 학년군/영역 상태가 리셋되어,
        그 이후 줄이 이전 영역에 잘못 귀속되지 않는다."""
        page = "\n".join(
            [
                "나. 성취기준",
                "[초등학교 3~4학년]",
                "(1) 영역A",
                "[4수01-01] 영역A 성취기준",
                "2. 목표",
                "여기는 성취기준 섹션이 아니다",
                "[4수99-99] 이 줄은 파싱되면 안 된다",
            ]
        )

        records = parse_achievement_records([page], Subject.MATH)

        assert len(records) == 1
        assert records[0]["chunk_id"] == "4수01-01"


class TestBuildCurriculumChunks:
    """실제 PDF(SOURCE_FILES)를 건드리지 않도록 extract_pages/parse_achievement_records를
    mock하고, Pydantic 검증 실패를 모아서 raise하는 동작만 검증한다."""

    def _valid_record(self, chunk_id: str, subject: Subject) -> dict:
        return dict(
            chunk_id=chunk_id,
            subject=subject,
            grade_band=GradeBand.G3_4,
            unit_name="영역",
            domain="영역",
            core_idea="핵심",
            achievement_code=f"[{chunk_id}]",
            achievement_text="텍스트",
            explanation="설명",
            inquiry_activities=[],
            source_page=1,
        )

    def test_validation_error_from_any_file_is_aggregated_and_raised(self, monkeypatch):
        monkeypatch.setattr(ingest_curriculum, "extract_pages", lambda path: [])

        def fake_parse(pages, subject):
            if subject == Subject.MATH:
                bad = self._valid_record("4수01-01", subject)
                del bad["grade_band"]  # 필수 필드 누락 -> ValidationError
                return [bad]
            return [self._valid_record(f"{subject.value}-01", subject)]

        monkeypatch.setattr(ingest_curriculum, "parse_achievement_records", fake_parse)

        with pytest.raises(ValueError) as exc_info:
            ingest_curriculum.build_curriculum_chunks()

        message = str(exc_info.value)
        assert "4수01-01" in message
        assert "math_book.pdf" in message

    def test_valid_records_across_all_source_files_produce_chunks_without_raising(
        self, monkeypatch
    ):
        monkeypatch.setattr(ingest_curriculum, "extract_pages", lambda path: [])
        monkeypatch.setattr(
            ingest_curriculum,
            "parse_achievement_records",
            lambda pages, subject: [self._valid_record(f"{subject.value}-01", subject)],
        )

        chunks = ingest_curriculum.build_curriculum_chunks()

        assert len(chunks) == len(ingest_curriculum.SOURCE_FILES)
        assert all(isinstance(chunk, CurriculumChunk) for chunk in chunks)
