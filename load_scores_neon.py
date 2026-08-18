import duckdb, psycopg2, os, pandas as pd
from psycopg2.extras import execute_values
from dotenv import load_dotenv
load_dotenv('.env.prod')

con = duckdb.connect()
ep = os.getenv('AWS_ENDPOINT_URL').replace('https://','')
con.execute(f"SET s3_access_key_id='{os.getenv('AWS_ACCESS_KEY_ID')}'; SET s3_secret_access_key='{os.getenv('AWS_SECRET_ACCESS_KEY')}'; SET s3_endpoint='{ep}'; SET s3_region='auto'; SET s3_use_ssl=true; SET s3_url_style='path';")

df = con.execute("SELECT * FROM read_parquet('s3://cc-raw/prod/scores/investmitra_score/year=2026/month=08/investmitra_score_20260815.parquet')").df()
con.close()
print(f'Loaded {len(df)} scores')
print(df.columns.tolist())
print(df.head(3))
