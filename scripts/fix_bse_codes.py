"""
investMITRA — Fix BSE codes and NSE symbols in company_master
Extracts BSE codes from BSE EOD Parquet files in R2.
NSE symbols already exist in company_master from seeding.

Run: python scripts/fix_bse_codes.py
"""
from __future__ import annotations
import logging, os
from datetime import datetime, timezone
import duckdb, psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

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


def extract_bse_codes_from_r2() -> dict[str, str]:
    """
    Read BSE EOD Parquet files from R2 and extract ISIN -> BSE code mapping.
    BSE EOD files have both ISIN and bse_code columns.
    """
    logger.info("Extracting BSE codes from R2 Parquet files...")
    con = get_duckdb_con()

    # Read recent BSE files (last 2 years is enough — codes don't change)
    path = f"s3://{BUCKET}/{ENV}/market_data/equity_prices/year=2026/**/*.parquet"

    try:
        df = con.execute(f"""
            SELECT DISTINCT isin, bse_code
            FROM read_parquet('{path}', union_by_name=true, hive_partitioning=true)
            WHERE isin IS NOT NULL
              AND bse_code IS NOT NULL
              AND LENGTH(CAST(isin AS VARCHAR)) = 12
              AND LENGTH(CAST(bse_code AS VARCHAR)) >= 4
              AND bse_code != '0'
              AND bse_code != 'nan'
        """).df()
        con.close()

        mapping = {row["isin"]: str(row["bse_code"]).strip() for _, row in df.iterrows()}
        logger.info("Found %d ISIN->BSE code mappings from 2026 files", len(mapping))

        if len(mapping) < 1000:
            # Try more years
            logger.info("Not enough — trying 2025 files too...")
            con2 = get_duckdb_con()
            path2 = f"s3://{BUCKET}/{ENV}/market_data/equity_prices/year=2025/**/*.parquet"
            df2 = con2.execute(f"""
                SELECT DISTINCT isin, bse_code
                FROM read_parquet('{path2}', union_by_name=true, hive_partitioning=true)
                WHERE isin IS NOT NULL AND bse_code IS NOT NULL
                  AND LENGTH(CAST(isin AS VARCHAR)) = 12
                  AND LENGTH(CAST(bse_code AS VARCHAR)) >= 4
                  AND bse_code != '0' AND bse_code != 'nan'
            """).df()
            con2.close()
            for _, row in df2.iterrows():
                if row["isin"] not in mapping:
                    mapping[row["isin"]] = str(row["bse_code"]).strip()
            logger.info("Total with 2025: %d mappings", len(mapping))

        return mapping

    except Exception as e:
        logger.error("Failed to extract BSE codes: %s", e)
        con.close()
        return {}


def extract_nse_symbols_from_r2() -> dict[str, str]:
    """
    Extract ISIN -> NSE symbol from NSE Bhavcopy files.
    Old format files have ISIN column directly.
    """
    logger.info("Extracting NSE symbols from R2 Parquet files...")
    con = get_duckdb_con()

    path = f"s3://{BUCKET}/{ENV}/market_data/equity_prices/year=2026/**/*.parquet"

    try:
        df = con.execute(f"""
            SELECT DISTINCT isin, nse_symbol
            FROM read_parquet('{path}', union_by_name=true, hive_partitioning=true)
            WHERE isin IS NOT NULL
              AND nse_symbol IS NOT NULL
              AND LENGTH(CAST(isin AS VARCHAR)) = 12
              AND nse_symbol != 'nan'
        """).df()
        con.close()

        mapping = {row["isin"]: str(row["nse_symbol"]).strip()
                   for _, row in df.iterrows()
                   if str(row["nse_symbol"]).strip()}
        logger.info("Found %d ISIN->NSE symbol mappings", len(mapping))
        return mapping

    except Exception as e:
        logger.error("Failed to extract NSE symbols: %s", e)
        con.close()
        return {}


def update_company_master(bse_map: dict, nse_map: dict):
    """Update company_master with BSE codes and NSE symbols."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()

    # Get all ISINs
    cur.execute("SELECT isin, bse_code, nse_symbol FROM investmitra.company_master WHERE isin IS NOT NULL")
    existing = {row[0]: {"bse_code": row[1], "nse_symbol": row[2]} for row in cur.fetchall()}
    logger.info("Existing company_master rows: %d", len(existing))

    bse_updated = 0
    nse_updated = 0

    for isin, data in existing.items():
        updates = {}
        if isin in bse_map and not data["bse_code"]:
            updates["bse_code"] = bse_map[isin]
            bse_updated += 1
        if isin in nse_map and not data["nse_symbol"]:
            updates["nse_symbol"] = nse_map[isin]
            nse_updated += 1

        if updates:
            set_clause = ", ".join(f"{k} = %s" for k in updates)
            values = list(updates.values()) + [isin]
            cur.execute(
                f"UPDATE investmitra.company_master SET {set_clause}, updated_at = NOW() WHERE isin = %s",
                values
            )

    conn.commit()
    cur.close()
    conn.close()

    logger.info("Updated BSE codes: %d", bse_updated)
    logger.info("Updated NSE symbols: %d", nse_updated)

    return bse_updated, nse_updated


def verify():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(bse_code) AS with_bse,
            COUNT(nse_symbol) AS with_nse,
            COUNT(sector) AS with_sector
        FROM investmitra.company_master
        WHERE is_active = TRUE
    """)
    r = cur.fetchone()
    print(f"\ncompany_master summary:")
    print(f"  Total:       {r[0]}")
    print(f"  BSE codes:   {r[1]}")
    print(f"  NSE symbols: {r[2]}")
    print(f"  Sectors:     {r[3]}")
    cur.close()
    conn.close()


def main():
    # Extract from R2
    bse_map = extract_bse_codes_from_r2()
    nse_map = extract_nse_symbols_from_r2()

    # Update Neon
    bse_updated, nse_updated = update_company_master(bse_map, nse_map)

    # Verify
    verify()

    print(f"\nBSE codes added: {bse_updated}")
    print(f"NSE symbols added: {nse_updated}")


if __name__ == "__main__":
    main()
