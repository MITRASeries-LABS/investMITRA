import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()
cur.execute("DROP TABLE IF EXISTS investmitra.top_picks")
print('Table dropped')
conn.close()
