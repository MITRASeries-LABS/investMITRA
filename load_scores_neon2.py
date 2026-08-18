import duckdb, psycopg2, os, pandas as pd
from psycopg2.extras import execute_values
from dotenv import load_dotenv
load_dotenv('.env.prod')

# Load from R2
con = duckdb.connect()
ep = os.getenv('AWS_ENDPOINT_URL').replace('https://','')
con.execute(f"SET s3_access_key_id='{os.getenv('AWS_ACCESS_KEY_ID')}'; SET s3_secret_access_key='{os.getenv('AWS_SECRET_ACCESS_KEY')}'; SET s3_endpoint='{ep}'; SET s3_region='auto'; SET s3_use_ssl=true; SET s3_url_style='path';")
df = con.execute("SELECT * FROM read_parquet('s3://cc-raw/prod/scores/investmitra_score/year=2026/month=08/investmitra_score_20260815.parquet')").df()
con.close()
print(f'Loaded {len(df)} scores from R2')

# Write to Neon
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = False
cur = conn.cursor()

def sf(v):
    try:
        import math
        f = float(v)
        return None if math.isnan(f) else round(f, 4)
    except: return None

rows = [
    (r['isin'], r['score_date'], r.get('sector'), sf(r.get('price')),
     sf(r.get('investmitra_score')), str(r.get('signal','')) if r.get('signal') else None,
     sf(r.get('momentum_score')), sf(r.get('financial_health_score')),
     sf(r.get('management_quality_score')), sf(r.get('financial_stress_score')),
     sf(r.get('ret_252d_pct')), sf(r.get('vol_20d_pct')), sf(r.get('pos_52w')),
     sf(r.get('debt_equity')), sf(r.get('pat_margin')),
     sf(r.get('insider_pct')), sf(r.get('institution_pct')))
    for _, r in df.iterrows()
]

execute_values(cur, """
    INSERT INTO investmitra.daily_scores
        (isin, score_date, sector, price, investmitra_score, signal,
         momentum_score, financial_health_score, management_quality_score,
         financial_stress_score, ret_252d_pct, vol_20d_pct, pos_52w,
         debt_equity, pat_margin, insider_pct, institution_pct)
    VALUES %s
    ON CONFLICT (isin, score_date) DO UPDATE SET
        investmitra_score = EXCLUDED.investmitra_score,
        signal = EXCLUDED.signal,
        momentum_score = EXCLUDED.momentum_score,
        price = EXCLUDED.price
""", rows, page_size=500)

conn.commit()
cur.close(); conn.close()
print(f'Written {len(rows)} rows to Neon daily_scores')
