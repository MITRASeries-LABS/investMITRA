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
    LIMIT 10
""")
print(f'{"Table":<30} {"Size":>10}')
print('-'*42)
total = 0
for r in cur.fetchall():
    print(f'{r[0]:<30} {r[1]:>10}')
    total += r[2]
print(f'\nTop 10 total: {total/1024/1024:.1f} MB')
conn.close()
