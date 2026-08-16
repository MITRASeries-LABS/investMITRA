"""
investMITRA — Load Scores to Neon v2
Loads composite investMITRA scores from R2 into Neon daily_scores table.
Includes company name enrichment from company_master.

Run:
  python scripts/load_scores_to_neon.py              # today
  python scripts/load_scores_to_neon.py --date 2026-08-15
"""
from __future__ import annotations
import argparse, logging, math, os
from datetime import date, datetime, timedelta, timezone
import duckdb, psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IST          = timezone(timedelta(hours=5, minutes=30))
NEON_URL     = os.getenv("CC_POSTGRES_URL")
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL")
AWS_KEY      = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET   = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET       = os.getenv("CC_BUCKET_RAW", "cc-raw")
ENV          = os.getenv("CC_ENV", "prod")


def get_duckdb_con():
    con = duckdb.connect()
    endpoint = (AWS_ENDPOINT or "").replace("https://", "").replace("http://", "")
    use_ssl  = "true" if (AWS_ENDPOINT or "").startswith("https") else "false"
    con.execute(f"""
        SET s3_access_key_id     = '{AWS_KEY}';
        SET s3_secret_access_key = '{AWS_SECRET}';
        SET s3_endpoint          = '{endpoint}';
        SET s3_region            = 'auto';
        SET s3_use_ssl           = {use_ssl};
        SET s3_url_style         = 'path';
    """)
    return con


def ensure_table():
    """Create daily_scores table if not exists, add company_name if missing."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.daily_scores (
            isin                     VARCHAR(12),
            score_date               DATE,
            company_name             VARCHAR(200),
            sector                   VARCHAR(100),
            price                    DECIMAL(15,2),
            investmitra_score        DECIMAL(6,2),
            signal                   VARCHAR(20),
            momentum_score           DECIMAL(6,2),
            financial_health_score   DECIMAL(6,2),
            management_quality_score DECIMAL(6,2),
            financial_stress_score   DECIMAL(6,2),
            ret_252d_pct             DECIMAL(10,4),
            vol_20d_pct              DECIMAL(10,4),
            pos_52w                  DECIMAL(6,4),
            debt_equity              DECIMAL(10,4),
            pat_margin               DECIMAL(10,4),
            insider_pct              DECIMAL(6,2),
            institution_pct          DECIMAL(6,2),
            ingested_at              TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (isin, score_date)
        )
    """)
    # Add company_name column if it doesn't exist (for older tables)
    cur.execute("""
        ALTER TABLE investmitra.daily_scores
        ADD COLUMN IF NOT EXISTS company_name VARCHAR(200)
    """)
    cur.close(); conn.close()


def load_for_date(target_date: date) -> int:
    """Load scores for a specific date from R2 to Neon."""
    path = (f"s3://{BUCKET}/{ENV}/scores/investmitra_score"
            f"/year={target_date.year}/month={target_date.month:02d}"
            f"/investmitra_score_{target_date.strftime('%Y%m%d')}.parquet")

    try:
        con = get_duckdb_con()
        df  = con.execute(f"SELECT * FROM read_parquet('{path}')").df()
        con.close()
        logger.info("Loaded %d scores from R2 for %s", len(df), target_date)
    except Exception as e:
        logger.warning("No score file for %s: %s", target_date, e)
        return 0

    if df.empty:
        return 0

    def sf(v):
        try:
            f = float(v)
            return None if math.isnan(f) else round(f, 4)
        except:
            return None

    rows = [
        (r["isin"], target_date, r.get("sector"),
         sf(r.get("price")), sf(r.get("investmitra_score")),
         str(r.get("signal", "")) if r.get("signal") else None,
         sf(r.get("momentum_score")), sf(r.get("financial_health_score")),
         sf(r.get("management_quality_score")), sf(r.get("financial_stress_score")),
         sf(r.get("ret_252d_pct")), sf(r.get("vol_20d_pct")), sf(r.get("pos_52w")),
         sf(r.get("debt_equity")), sf(r.get("pat_margin")),
         sf(r.get("insider_pct")), sf(r.get("institution_pct")))
        for _, r in df.iterrows()
        if r.get("isin")
    ]

    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()

    execute_values(cur, """
        INSERT INTO investmitra.daily_scores
            (isin, score_date, sector, price, investmitra_score, signal,
             momentum_score, financial_health_score, management_quality_score,
             financial_stress_score, ret_252d_pct, vol_20d_pct, pos_52w,
             debt_equity, pat_margin, insider_pct, institution_pct)
        VALUES %s
        ON CONFLICT (isin, score_date) DO UPDATE SET
            investmitra_score        = EXCLUDED.investmitra_score,
            signal                   = EXCLUDED.signal,
            momentum_score           = EXCLUDED.momentum_score,
            financial_health_score   = EXCLUDED.financial_health_score,
            management_quality_score = EXCLUDED.management_quality_score,
            financial_stress_score   = EXCLUDED.financial_stress_score,
            price                    = EXCLUDED.price,
            ret_252d_pct             = EXCLUDED.ret_252d_pct,
            debt_equity              = EXCLUDED.debt_equity,
            pat_margin               = EXCLUDED.pat_margin,
            insider_pct              = EXCLUDED.insider_pct
    """, rows, page_size=500)

    conn.commit(); cur.close(); conn.close()
    logger.info("Written %d scores to Neon for %s", len(rows), target_date)

    # Enrich with company names from company_master
    conn2 = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn2.autocommit = True
    cur2  = conn2.cursor()
    cur2.execute("""
        UPDATE investmitra.daily_scores ds
        SET company_name = cm.company_name
        FROM investmitra.company_master cm
        WHERE ds.isin = cm.isin
          AND ds.score_date = %s
          AND ds.company_name IS NULL
    """, (target_date,))
    enriched = cur2.rowcount
    cur2.close(); conn2.close()
    logger.info("Enriched %d rows with company names", enriched)

    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat)
    args    = parser.parse_args()
    target  = args.date or datetime.now(IST).date()

    ensure_table()
    written = load_for_date(target)
    print(f"Loaded {written} scores for {target}")


if __name__ == "__main__":
    main()
