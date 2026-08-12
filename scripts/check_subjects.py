from app.lib.db import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT subject, COUNT(*) FROM curriculum_chunks GROUP BY subject ORDER BY 2 DESC")
        for row in cur.fetchall():
            print(row)
        cur.execute("SELECT COUNT(*) FROM curriculum_chunks")
        print("총:", cur.fetchone())
