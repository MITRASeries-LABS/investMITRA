"""
investMITRA — Backtesting Framework
Tests whether investMITRA scores predict future stock returns.

Methodology:
  1. For each trading day T in test period:
     - Take all stocks with investMITRA scores
     - Group into quintiles (Q1=bottom 20%, Q5=top 20%)
  2. Compute actual returns over T+5, T+20, T+60 days
  3. Measure if Q5 (Strong Buy) consistently outperforms Q1 (Strong Sell)
  4. Compute hit rate, IC (Information Coefficient), Sharpe ratio

Key metrics:
  - Hit Rate: % of Strong Buy stocks with positive 20d return
  - IC (Information Coefficient): correlation of score with forward return
  - Quintile Returns: average return by score quintile
  - Alpha vs Nifty: excess return over benchmark

Run:
  python scripts/backtest.py --start 2025-01-01 --end 2026-08-07
  python scripts/backtest.py --quick    # test with last 30 days
"""
from __future__ import annotations
import argparse, io, logging, os
from datetime import date, datetime, timedelta, timezone
import duckdb, pandas as pd, numpy as np
import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IST          = timezone(timedelta(hours=5, minutes=30))
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL")
AWS_KEY      = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET   = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET       = os.getenv("CC_BUCKET_RAW", "cc-raw")
ENV          = os.getenv("CC_ENV", "prod")

HORIZONS = [5, 20, 60]  # trading days forward


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


def load_scores(con, start: date, end: date) -> pd.DataFrame:
    """Load all investMITRA scores for date range."""
    years = list(range(start.year, end.year + 1))
    paths = ", ".join(
        f"'s3://{BUCKET}/{ENV}/scores/investmitra_score/year={y}/**/*.parquet'"
        for y in years
    )
    try:
        df = con.execute(f"""
            SELECT isin, score_date::DATE AS score_date,
                   investmitra_score, signal, sector,
                   momentum_score, financial_health_score, management_quality_score
            FROM read_parquet([{paths}], union_by_name=true)
            WHERE score_date >= '{start}' AND score_date <= '{end}'
              AND investmitra_score IS NOT NULL
        """).df()
        logger.info("Loaded %d score records (%d dates)",
                    len(df), df["score_date"].nunique())
        return df
    except Exception as e:
        logger.error("Failed to load scores: %s", e)
        return pd.DataFrame()


def load_prices(con, start: date, end: date) -> pd.DataFrame:
    """Load NSE closing prices for the test period + 60 days forward."""
    price_end = end + timedelta(days=90)  # buffer for forward returns
    years     = list(range(start.year, price_end.year + 1))
    paths     = ", ".join(
        f"'s3://{BUCKET}/{ENV}/market_data/equity_prices/year={y}/**/*.parquet'"
        for y in years
    )
    try:
        df = con.execute(f"""
            SELECT isin, trade_date::DATE AS trade_date, close::DOUBLE AS close
            FROM read_parquet([{paths}], union_by_name=true)
            WHERE trade_date >= '{start}' AND trade_date <= '{price_end}'
              AND isin IS NOT NULL AND close > 0
              AND LENGTH(CAST(isin AS VARCHAR)) = 12
        """).df()
        logger.info("Loaded %d price records", len(df))
        return df
    except Exception as e:
        logger.error("Failed to load prices: %s", e)
        return pd.DataFrame()


def compute_forward_returns(scores: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """
    For each (isin, score_date), find the actual return N days forward.
    Uses next available trading day prices (not calendar days).
    """
    # Create a price lookup: isin -> sorted list of (date, close)
    price_pivot = prices.pivot_table(index="trade_date", columns="isin", values="close")
    trading_days = sorted(price_pivot.index.tolist())
    trading_day_idx = {d: i for i, d in enumerate(trading_days)}

    results = []
    score_dates = sorted(scores["score_date"].unique())

    logger.info("Computing forward returns for %d score dates...", len(score_dates))

    for score_date in score_dates:
        day_scores = scores[scores["score_date"] == score_date].copy()

        if score_date not in trading_day_idx:
            continue

        t_idx = trading_day_idx[score_date]

        # Get forward prices for each horizon
        for horizon in HORIZONS:
            fwd_idx = t_idx + horizon
            if fwd_idx >= len(trading_days):
                continue
            fwd_date = trading_days[fwd_idx]

            for _, row in day_scores.iterrows():
                isin = row["isin"]
                try:
                    p0 = price_pivot.loc[score_date, isin] if score_date in price_pivot.index and isin in price_pivot.columns else None
                    pf = price_pivot.loc[fwd_date, isin] if fwd_date in price_pivot.index and isin in price_pivot.columns else None

                    if p0 and pf and p0 > 0:
                        ret = (pf - p0) / p0 * 100
                        results.append({
                            "isin":             isin,
                            "score_date":       score_date,
                            "forward_date":     fwd_date,
                            "horizon":          horizon,
                            "investmitra_score": row["investmitra_score"],
                            "signal":           row.get("signal"),
                            "sector":           row.get("sector"),
                            "forward_return":   round(ret, 4),
                        })
                except:
                    pass

        if score_date == score_dates[0] or score_dates.index(score_date) % 20 == 0:
            logger.info("Processed %s (%d/%d)", score_date,
                        score_dates.index(score_date) + 1, len(score_dates))

    return pd.DataFrame(results)


def run_backtest_analysis(df: pd.DataFrame) -> dict:
    """Compute backtest metrics from forward returns."""
    results = {}

    for horizon in HORIZONS:
        h_df = df[df["horizon"] == horizon].copy()
        if h_df.empty:
            continue

        # 1. Information Coefficient (IC) — correlation of score with return
        ic = h_df["investmitra_score"].corr(h_df["forward_return"])

        # 2. Quintile analysis
        h_df["quintile"] = pd.qcut(h_df["investmitra_score"], 5,
                                    labels=["Q1\n(Worst)", "Q2", "Q3", "Q4", "Q5\n(Best)"])
        q_returns = h_df.groupby("quintile")["forward_return"].agg(["mean", "median", "count"])

        # 3. Hit rate for top quintile
        top_q = h_df[h_df["quintile"] == "Q5\n(Best)"]
        hit_rate = (top_q["forward_return"] > 0).mean() * 100

        # 4. Signal-based returns
        sig_returns = h_df.groupby("signal")["forward_return"].agg(["mean", "count"])

        # 5. Long-Short return (Q5 - Q1)
        q5_ret = q_returns.loc["Q5\n(Best)", "mean"] if "Q5\n(Best)" in q_returns.index else 0
        q1_ret = q_returns.loc["Q1\n(Worst)", "mean"] if "Q1\n(Worst)" in q_returns.index else 0
        ls_return = q5_ret - q1_ret

        results[horizon] = {
            "ic":           round(ic, 4),
            "hit_rate":     round(hit_rate, 1),
            "ls_return":    round(ls_return, 2),
            "q_returns":    q_returns,
            "sig_returns":  sig_returns,
            "n_obs":        len(h_df),
        }

    return results


def print_results(results: dict):
    print("\n" + "="*70)
    print("investMITRA BACKTEST RESULTS")
    print("="*70)

    for horizon, r in results.items():
        print(f"\n{'─'*70}")
        print(f"HORIZON: {horizon} trading days forward")
        print(f"{'─'*70}")
        print(f"Observations:        {r['n_obs']:,}")
        print(f"Information Coeff:   {r['ic']:.4f}  {'✅ Good' if abs(r['ic']) > 0.05 else '⚠️ Weak'}")
        print(f"Hit Rate (Top 20%):  {r['hit_rate']:.1f}%  {'✅ Good' if r['hit_rate'] > 55 else '⚠️ Weak'}")
        print(f"Long-Short Return:   {r['ls_return']:.2f}%  {'✅ Good' if r['ls_return'] > 1 else '⚠️ Weak'}")

        print(f"\nQuintile Returns:")
        print(r["q_returns"][["mean", "count"]].to_string())

        print(f"\nSignal Returns:")
        print(r["sig_returns"][["mean", "count"]].to_string())

    print("\n" + "="*70)
    print("INTERPRETATION:")
    print("  IC > 0.05 = Score predicts returns well")
    print("  Hit Rate > 55% = More than half of top picks go up")
    print("  Long-Short > 2% = Meaningful spread between best and worst picks")
    print("="*70)


def write_results_to_r2(df: pd.DataFrame, start: date, end: date):
    """Save forward returns data to R2 for future analysis."""
    key = f"{ENV}/backtest/forward_returns_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.parquet"
    s3  = boto3.client("s3", endpoint_url=AWS_ENDPOINT,
                       aws_access_key_id=AWS_KEY, aws_secret_access_key=AWS_SECRET,
                       region_name="auto")
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), buf, compression="snappy")
    buf.seek(0)
    s3.put_object(Bucket=BUCKET, Key=key, Body=buf.read())
    logger.info("Saved backtest data → s3://%s/%s", BUCKET, key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat,
                        default=(datetime.now(IST).date() - timedelta(days=60)))
    parser.add_argument("--end",   type=date.fromisoformat,
                        default=(datetime.now(IST).date() - timedelta(days=5)))
    parser.add_argument("--quick", action="store_true",
                        help="Quick test: last 30 days only")
    args = parser.parse_args()

    if args.quick:
        args.start = datetime.now(IST).date() - timedelta(days=30)
        args.end   = datetime.now(IST).date() - timedelta(days=5)

    logger.info("Backtesting %s to %s", args.start, args.end)

    con = get_duckdb_con()

    # Load scores and prices
    scores = load_scores(con, args.start, args.end)
    prices = load_prices(con, args.start, args.end)
    con.close()

    if scores.empty or prices.empty:
        logger.error("No data available for backtest period")
        return

    # Compute forward returns
    fwd_returns = compute_forward_returns(scores, prices)

    if fwd_returns.empty:
        logger.error("No forward returns computed")
        return

    logger.info("Computed %d forward return observations", len(fwd_returns))

    # Run analysis
    results = run_backtest_analysis(fwd_returns)

    # Print results
    print_results(results)

    # Save to R2
    write_results_to_r2(fwd_returns, args.start, args.end)


if __name__ == "__main__":
    main()
