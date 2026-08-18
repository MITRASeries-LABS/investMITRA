import duckdb, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
con = duckdb.connect()
ep = os.getenv('AWS_ENDPOINT_URL').replace('https://','')
con.execute(f"SET s3_access_key_id='{os.getenv('AWS_ACCESS_KEY_ID')}'; SET s3_secret_access_key='{os.getenv('AWS_SECRET_ACCESS_KEY')}'; SET s3_endpoint='{ep}'; SET s3_region='auto'; SET s3_use_ssl=true; SET s3_url_style='path';")
df = con.execute("SELECT isin, sector, investmitra_score, signal, momentum_score, financial_health_score, management_quality_score, ret_252d_pct FROM read_parquet('s3://cc-raw/prod/scores/investmitra_score/year=2026/month=08/investmitra_score_20260814.parquet') WHERE financial_health_score != 50.0 AND management_quality_score != 50.0 ORDER BY investmitra_score DESC LIMIT 20").df()
print(df.to_string())
