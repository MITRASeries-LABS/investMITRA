import duckdb, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
con = duckdb.connect()
ep = os.getenv('AWS_ENDPOINT_URL').replace('https://','')
con.execute(f"SET s3_access_key_id='{os.getenv('AWS_ACCESS_KEY_ID')}'; SET s3_secret_access_key='{os.getenv('AWS_SECRET_ACCESS_KEY')}'; SET s3_endpoint='{ep}'; SET s3_region='auto'; SET s3_use_ssl=true; SET s3_url_style='path';")
m = set(con.execute("SELECT isin FROM read_parquet('s3://cc-raw/prod/scores/momentum/year=2026/month=08/momentum_20260814.parquet')").df()['isin'])
f = set(con.execute("SELECT isin FROM read_parquet('s3://cc-raw/prod/scores/financial_stress/year=2026/month=08/financial_stress_20260815.parquet')").df()['isin'])
g = set(con.execute("SELECT isin FROM read_parquet('s3://cc-raw/prod/scores/management_quality/year=2026/month=08/management_quality_20260815.parquet')").df()['isin'])
print(f'Momentum: {len(m)}')
print(f'Financial: {len(f)}')
print(f'Management: {len(g)}')
print(f'All 3 overlap: {len(m & f & g)}')
print(f'Momentum & Financial: {len(m & f)}')
