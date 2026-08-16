"""
investMITRA — Weight Optimization
Tests different weight combinations for the composite score
and finds which weights give the best IC and Long-Short returns.

Run:
  python scripts/optimize_weights.py --backtest-file s3://cc-raw/prod/backtest/forward_returns_20260401_20260430.parquet
"""
from __future__ import annotations
import argparse, logging, os
from datetime import date, datetime, timedelta, timezone
import duckdb, pandas as pd, numpy as np
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


def load_backtest_data(path: str) -> pd.DataFrame:
    con = get_duckdb_con()
    df  = con.execute(f"SELECT * FROM read_parquet('{path}')").df()
    con.close()
    logger.info("Loaded %d backtest records", len(df))
    return df


def load_scores_for_dates(score_dates: list) -> pd.DataFrame:
    """Load component scores (momentum, financial_health, management) for backtest dates."""
    years = list(set(d.year for d in score_dates))
    paths = ", ".join(
        f"'s3://{BUCKET}/{ENV}/scores/investmitra_score/year={y}/**/*.parquet'"
        for y in years
    )
    con = get_duckdb_con()
    df  = con.execute(f"""
        SELECT isin, score_date::DATE AS score_date,
               momentum_score,
               financial_health_score,
               management_quality_score
        FROM read_parquet([{paths}], union_by_name=true)
        WHERE score_date IN ({','.join(f"'{d}'" for d in score_dates)})
    """).df()
    con.close()
    logger.info("Loaded component scores for %d records", len(df))
    return df


def test_weights(fwd_returns: pd.DataFrame, components: pd.DataFrame,
                 w_mom: float, w_fin: float, w_mgmt: float,
                 horizon: int = 60) -> dict:
    """Test a specific weight combination and return IC and LS return."""

    # Merge forward returns with component scores
    merged = fwd_returns[fwd_returns["horizon"] == horizon].merge(
        components, on=["isin", "score_date"], how="inner"
    )

    if merged.empty or len(merged) < 100:
        return {"ic": 0, "ls": 0, "hit_rate": 0}

    # Compute composite score with new weights
    merged["composite"] = (
        merged["momentum_score"].fillna(50)           * w_mom +
        merged["financial_health_score"].fillna(50)   * w_fin +
        merged["management_quality_score"].fillna(50) * w_mgmt
    )

    # IC
    ic = merged["composite"].corr(merged["forward_return"])

    # Quintile returns
    merged["quintile"] = pd.qcut(merged["composite"], 5, labels=False, duplicates="drop")
    q_ret = merged.groupby("quintile")["forward_return"].mean()
    ls    = q_ret.iloc[-1] - q_ret.iloc[0] if len(q_ret) >= 2 else 0

    # Hit rate top quintile
    top = merged[merged["quintile"] == merged["quintile"].max()]
    hit = (top["forward_return"] > 0).mean() * 100

    return {"ic": round(ic, 4), "ls": round(ls, 2), "hit_rate": round(hit, 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest-file", required=True)
    parser.add_argument("--horizon", type=int, default=60)
    args = parser.parse_args()

    # Load backtest data
    fwd = load_backtest_data(args.backtest_file)
    score_dates = [pd.Timestamp(d).date() for d in fwd["score_date"].unique()]

    # Load component scores
    components = load_scores_for_dates(score_dates)

    if components.empty:
        logger.error("No component scores found")
        return

    # Test weight combinations
    # Weights must sum to 1.0
    weight_combos = [
        # (momentum, financial_health, management_quality, label)
        (0.40, 0.30, 0.30, "Current (40/30/30)"),
        (0.20, 0.50, 0.30, "Financial Heavy (20/50/30)"),
        (0.20, 0.30, 0.50, "Management Heavy (20/30/50)"),
        (0.10, 0.60, 0.30, "Quality Focus (10/60/30)"),
        (0.30, 0.40, 0.30, "Balanced (30/40/30)"),
        (0.50, 0.25, 0.25, "Momentum Heavy (50/25/25)"),
        (0.20, 0.45, 0.35, "Conservative (20/45/35)"),
        (0.15, 0.55, 0.30, "Very Financial (15/55/30)"),
        (0.25, 0.35, 0.40, "Management Tilt (25/35/40)"),
        (0.10, 0.50, 0.40, "No Momentum (10/50/40)"),
    ]

    print(f"\n{'='*75}")
    print(f"WEIGHT OPTIMIZATION — {args.horizon}d forward return")
    print(f"{'='*75}")
    print(f"{'Label':<35} {'IC':>8} {'LS Ret%':>10} {'Hit Rate':>10}")
    print(f"{'─'*75}")

    results = []
    for w_mom, w_fin, w_mgmt, label in weight_combos:
        r = test_weights(fwd, components, w_mom, w_fin, w_mgmt, args.horizon)
        results.append({
            "label":    label,
            "w_mom":    w_mom,
            "w_fin":    w_fin,
            "w_mgmt":   w_mgmt,
            **r
        })
        ic_flag  = "✅" if r["ic"] > 0.03 else "⚠️"
        ls_flag  = "✅" if r["ls"] > 1.0  else "⚠️"
        hit_flag = "✅" if r["hit_rate"] > 55 else "⚠️"
        print(f"{label:<35} {ic_flag} {r['ic']:>6.4f}  {ls_flag} {r['ls']:>7.2f}%  {hit_flag} {r['hit_rate']:>7.1f}%")

    # Find best weights
    df_r = pd.DataFrame(results)
    # Score each combination (normalize IC, LS, hit_rate)
    df_r["score"] = (
        df_r["ic"].rank(pct=True) * 0.4 +
        df_r["ls"].rank(pct=True) * 0.4 +
        df_r["hit_rate"].rank(pct=True) * 0.2
    )
    best = df_r.loc[df_r["score"].idxmax()]

    print(f"\n{'='*75}")
    print(f"BEST WEIGHTS: {best['label']}")
    print(f"  Momentum:          {best['w_mom']:.0%}")
    print(f"  Financial Health:  {best['w_fin']:.0%}")
    print(f"  Management Quality:{best['w_mgmt']:.0%}")
    print(f"  IC:       {best['ic']:.4f}")
    print(f"  LS Return:{best['ls']:.2f}%")
    print(f"  Hit Rate: {best['hit_rate']:.1f}%")
    print(f"{'='*75}")
    print(f"\nTo update: edit compute_investmitra_score.py line with weights")
    print(f"  momentum_score           * {best['w_mom']:.2f} +")
    print(f"  financial_health_score   * {best['w_fin']:.2f} +")
    print(f"  management_quality_score * {best['w_mgmt']:.2f}")


if __name__ == "__main__":
    main()
