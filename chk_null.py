import duckdb, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
con = duckdb.connect()
ep = os.getenv('AWS_ENDPOINT_URL').replace('https://','')
con.execute(f"SET s3_access_key_id='{os.getenv('AWS_ACCESS_KEY_ID')}'; SET s3_secret_access_key='{os.getenv('AWS_SECRET_ACCESS_KEY')}'; SET s3_endpoint='{ep}'; SET s3_region='auto'; SET s3_use_ssl=true; SET s3_url_style='path';")
df = con.execute("""
    SELECT 
        COUNT(*) as total,
        COUNT(debt_equity) as with_debt,
        COUNT(insider_pct) as with_insider,
        COUNT(financial_health_score) as with_fin
    FROM read_parquet('s3://cc-raw/prod/scores/investmitra_score/year=2026/month=08/investmitra_score_20260814.parquet')
""").df()
print(df.to_string())
