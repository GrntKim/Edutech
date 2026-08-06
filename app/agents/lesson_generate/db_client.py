from app.agents.lesson_generate.schema import AchievementStandard
from app.lib.db import get_chunk_by_code


def fetch_achievement_standard(achievement_code: str) -> AchievementStandard:
    """achievement_code로 curriculum_chunks에서 성취기준 원문·해설을 조회한다.

    app/lib/db.py(E 소유, 팀 공용 계층)의 get_chunk_by_code()를 경유한다.
    연결 실패 시 DatabaseError 계열이 그대로 전파되어, orchestrate.py(D)의
    전역 예외 핸들러가 잡을 수 있다.

    테이블은 curriculum_chunks(A2 ingest_curriculum.py 산출물)이며, 실제 컬럼은
    app.lib.types.CurriculumChunk 기준 achievement_text이지만 AchievementStandard
    필드명(statement)과의 하위 호환을 위해 여기서 옮겨 담는다.
    """
    chunk = get_chunk_by_code(achievement_code)

    if chunk is None:
        raise ValueError(f"성취기준을 찾을 수 없습니다: {achievement_code}")

    return AchievementStandard(
        code=chunk.achievement_code,
        grade_band=chunk.grade_band,
        statement=chunk.achievement_text,
        explanation=chunk.explanation,
    )
