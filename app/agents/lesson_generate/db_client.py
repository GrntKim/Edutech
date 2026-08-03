import os

import psycopg
from dotenv import load_dotenv

from app.agents.lesson_generate.schema import AchievementStandard

load_dotenv()


def fetch_achievement_standard(achievement_code: str) -> AchievementStandard:
    """achievement_code로 curriculum_units에서 성취기준 원문·해설을 조회한다.

    app/lib/db.py는 E 소유이며 아직 빈 파일이라, 그 파일을 건드리지 않기 위해
    lesson_generate 폴더 안에 자체 연결 로직을 둔다. E가 lib/db.py를 완성하면
    이 파일의 연결 부분만 그쪽 유틸리티로 교체하면 된다.

    주의: curriculum_units 테이블 스키마는 A2(REQ-002)의 ingest_curriculum.py가
    아직 작성되지 않아 확정되지 않았다. 아래 컬럼명(achievement_code, grade_band,
    statement, explanation)은 기존에 팀이 합의했던 AchievementStandard 필드명을
    그대로 가정한 것이며, A2가 실제 테이블 스키마를 확정하면 이 SQL을 맞춰야 한다.
    """
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT achievement_code, grade_band, statement, explanation
            FROM curriculum_units
            WHERE achievement_code = %s
            LIMIT 1
            """,
            (achievement_code,),
        )
        row = cur.fetchone()

    if row is None:
        raise ValueError(f"성취기준을 찾을 수 없습니다: {achievement_code}")

    code, grade_band, statement, explanation = row
    return AchievementStandard(
        code=code,
        grade_band=grade_band,
        statement=statement,
        explanation=explanation,
    )
