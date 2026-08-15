"""
investMITRA — Score Backfill
Computes all scores (momentum, financial stress, management quality, composite)
for a date range. Skips dates already computed.

Run:
  python scripts/backfill_scores.py --start 2024-01-01 --end 2024-12-31
"""
from __future__ import annotations
import argparse, logging, os, subprocess, sys
from datetime import date, datetime, timedelta, timezone
import boto3
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IST      = timezone(timedelta(hours=5, minutes=30))
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL")
AWS_KEY      = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET   = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET       = os.getenv("CC_BUCKET_RAW", "cc-raw")
ENV          = os.getenv("CC_ENV", "prod")

_s3 = None

def get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", endpoint_url=AWS_ENDPOINT,
                           aws_access_key_id=AWS_KEY,
                           aws_secret_access_key=AWS_SECRET,
                           region_name="auto")
    return _s3


def score_exists(score_type: str, target_date: date) -> bool:
    prefix = (f"{ENV}/scores/{score_type}/year={target_date.year}"
              f"/month={target_date.month:02d}"
              f"/{score_type}_{target_date.strftime('%Y%m%d')}.parquet")
    result = get_s3().list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return len(result.get("Contents", [])) > 0


def feature_exists(target_date: date) -> bool:
    prefix = (f"{ENV}/features/price_features/year={target_date.year}"
              f"/month={target_date.month:02d}"
              f"/price_features_{target_date.strftime('%Y%m%d')}.parquet")
    result = get_s3().list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return len(result.get("Contents", [])) > 0


def run_script(script: str, date_str: str) -> bool:
    try:
        result = subprocess.run(
            [sys.executable, f"scripts/{script}", "--date", date_str],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            logger.error("%s failed for %s: %s", script, date_str, result.stderr[-200:])
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("%s timed out for %s", script, date_str)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end",   type=date.fromisoformat, required=True)
    args = parser.parse_args()

    current    = args.start
    total      = 0
    skipped    = 0
    failed     = 0

    logger.info("Score backfill: %s to %s", args.start, args.end)

    while current <= args.end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        date_str = current.isoformat()

        # Check if features exist (needed for momentum)
        if not feature_exists(current):
            logger.info("%s — no features, skipping", date_str)
            current += timedelta(days=1)
            skipped += 1
            continue

        # Check if composite already done
        if score_exists("investmitra_score", current):
            logger.info("%s — already scored, skipping", date_str)
            current += timedelta(days=1)
            skipped += 1
            continue

        logger.info("Scoring %s...", date_str)

        # Step 1: Momentum (fast, uses R2 features)
        if not score_exists("momentum", current):
            if not run_script("compute_momentum_score.py", date_str):
                failed += 1
                current += timedelta(days=1)
                continue

        # Step 2: Financial stress (uses Neon company_financials — static, same result every day)
        if not score_exists("financial_stress", current):
            if not run_script("compute_financial_stress_score.py", date_str):
                failed += 1

        # Step 3: Management quality (uses Neon ownership_data — static)
        if not score_exists("management_quality", current):
            if not run_script("compute_management_quality_score.py", date_str):
                failed += 1

        # Step 4: Composite
        run_script("compute_investmitra_score.py", date_str)

        total += 1
        current += timedelta(days=1)

        if total % 10 == 0:
            logger.info("Progress: %d scored, %d skipped, %d failed", total, skipped, failed)

    logger.info("Done: %d scored, %d skipped, %d failed", total, skipped, failed)


if __name__ == "__main__":
    main()
