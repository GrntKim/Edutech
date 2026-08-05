import os

import psycopg
from dotenv import load_dotenv

from app.agents.lesson_generate.schema import AchievementStandard

load_dotenv()


def fetch_achievement_standard(achievement_code: str) -> AchievementStandard:
    """achievement_code로 curriculum_chunks에서 성취기준 원문·해설을 조회한다.

    app/lib/db.py는 E 소유이며 아직 빈 파일이라, 그 파일을 건드리지 않기 위해
    lesson_generate 폴더 안에 자체 연결 로직을 둔다. E가 lib/db.py를 완성하면
    이 파일의 연결 부분만 그쪽 유틸리티로 교체하면 된다. 연결 방식(DB_NAME/
    DB_USER/DB_PASSWORD/DB_HOST/DB_PORT 개별 변수)은 scripts/check_db.py(E)와
    맞췄다 — 예전에는 DATABASE_URL 단일 변수였으나 .env.example 개편으로 바뀌었다.

    테이블은 curriculum_chunks(A2 ingest_curriculum.py 산출물)이며, 실제 컬럼은
    app.lib.types.CurriculumChunk 기준 achievement_text이지만 AchievementStandard
    필드명(statement)과의 하위 호환을 위해 AS statement로 별칭을 준다.
    """
    with psycopg.connect(
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
    ) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT achievement_code, grade_band, achievement_text AS statement, explanation
            FROM curriculum_chunks
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
