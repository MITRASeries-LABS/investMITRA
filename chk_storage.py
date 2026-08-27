import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT tablename,
           pg_size_pretty(pg_total_relation_size('investmitra.'||tablename)) as size,
           pg_total_relation_size('investmitra.'||tablename) as bytes
    FROM pg_tables
    WHERE schemaname='investmitra'
    ORDER BY bytes DESC
""")
print(f'{"Table":<30} {"Size":>10}')
print('-'*42)
for r in cur.fetchall():
    print(f'{r[0]:<30} {r[1]:>10}')
conn.close()
