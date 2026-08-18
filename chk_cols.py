import duckdb, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
con = duckdb.connect()
ep = os.getenv('AWS_ENDPOINT_URL').replace('https://','')
con.execute(f"SET s3_access_key_id='{os.getenv('AWS_ACCESS_KEY_ID')}'; SET s3_secret_access_key='{os.getenv('AWS_SECRET_ACCESS_KEY')}'; SET s3_endpoint='{ep}'; SET s3_region='auto'; SET s3_use_ssl=true; SET s3_url_style='path';")
df = con.execute("SELECT * FROM read_parquet('s3://cc-raw/prod/scores/investmitra_score/year=2026/month=08/investmitra_score_20260814.parquet') LIMIT 2").df()
print('Columns:', df.columns.tolist())
print(df[['isin','investmitra_score','momentum_score','financial_health_score','management_quality_score']].head(5).to_string())
