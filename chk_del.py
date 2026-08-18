import duckdb, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
con = duckdb.connect()
ep = os.getenv('AWS_ENDPOINT_URL').replace('https://','')
con.execute(f"SET s3_access_key_id='{os.getenv('AWS_ACCESS_KEY_ID')}'; SET s3_secret_access_key='{os.getenv('AWS_SECRET_ACCESS_KEY')}'; SET s3_endpoint='{ep}'; SET s3_region='auto'; SET s3_use_ssl=true; SET s3_url_style='path';")
df = con.execute("""
    SELECT trade_date, COUNT(*) as stocks,
           COUNT(delivery_pct) as with_delivery,
           ROUND(AVG(delivery_pct),1) as avg_delivery
    FROM read_parquet('s3://cc-raw/prod/market_data/equity_prices/year=2026/**/*.parquet', union_by_name=true)
    WHERE trade_date >= '2026-06-01' AND isin LIKE 'INE%'
    GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10
""").df()
print(df.to_string())
