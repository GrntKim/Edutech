import os
from dotenv import load_dotenv
import psycopg

load_dotenv()

conn = psycopg.connect(
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    host=os.environ["DB_HOST"],
    port=os.environ["DB_PORT"],
)
with conn.cursor() as cur:
    cur.execute("SELECT current_database(), current_user;")
    print(cur.fetchone())
    cur.execute("SELECT extname FROM pg_extension WHERE extname='vector';")
    print("pgvector:", cur.fetchone())
    cur.execute("SELECT count(*) FROM curriculum_chunks;")
    print("rows:", cur.fetchone()[0])
conn.close()