"""
investMITRA — Corporate Action Adjustment Logic
Layer 4 Transform: computes adj_close and adj_factor for equity_prices.

Rules:
  - Adjustments cascade BACKWARD from most recent event
  - adj_close = close * product_of_all_future_adj_factors
  - Raw lake is NEVER modified — adj_close written to Neon equity_prices table
  - Mergers/demergers → requires_manual_review = TRUE — skip standard formula
  - Always cross-validate NSE and BSE corporate actions — use earlier ex_date

Adjustment factors:
  SPLIT:    old_face_value / new_face_value
  BONUS:    (existing + new) / existing
  DIVIDEND: (price_on_ex_date - dividend_per_share) / price_on_ex_date
  RIGHTS:   theoretical_ex_rights_price / market_price
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import pandas as pd

from src.config.database import get_pg_conn

logger = logging.getLogger(__name__)

# Any adj_close gap > this % between consecutive days triggers investigation
SUSPICIOUS_GAP_PCT = 0.50


def compute_adjusted_prices(
    prices: pd.DataFrame,
    actions: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute adj_close and adj_factor for a single stock.

    Args:
        prices:  DataFrame with columns [isin, trade_date, close]
                 sorted by trade_date ascending
        actions: DataFrame with columns [ex_date, adj_factor]
                 for the same ISIN, already filtered to confirmed actions

    Returns:
        prices DataFrame with adj_close and adj_factor columns added
    """
    df = prices.sort_values("trade_date").copy()
    df["adj_factor"] = 1.0

    # Apply each action: multiply adj_factor for all dates BEFORE ex_date
    for _, action in actions.sort_values("ex_date").iterrows():
        mask = df["trade_date"] < action["ex_date"]
        df.loc[mask, "adj_factor"] *= action["adj_factor"]

    df["adj_close"] = (df["close"] * df["adj_factor"]).round(2)

    # Flag suspicious gaps
    df = _flag_suspicious_gaps(df)

    return df


def get_adj_factor_for_action(action_type: str, **kwargs) -> Optional[float]:
    """
    Compute adjustment factor for a given corporate action.

    Args (vary by action_type):
        SPLIT:    old_face_value, new_face_value
        BONUS:    existing_shares, new_shares
        DIVIDEND: price_on_ex_date, dividend_per_share
        RIGHTS:   theoretical_ex_rights_price, market_price
    """
    action_type = action_type.upper()

    if action_type == "SPLIT":
        old = kwargs.get("old_face_value")
        new = kwargs.get("new_face_value")
        if old and new and new != 0:
            return old / new
        logger.warning("SPLIT: missing old/new face value")
        return None

    elif action_type == "BONUS":
        existing = kwargs.get("existing_shares")
        new      = kwargs.get("new_shares")
        if existing and new and existing != 0:
            return (existing + new) / existing
        logger.warning("BONUS: missing existing/new shares")
        return None

    elif action_type == "DIVIDEND":
        price    = kwargs.get("price_on_ex_date")
        dividend = kwargs.get("dividend_per_share")
        if price and dividend and price != 0:
            return (price - dividend) / price
        logger.warning("DIVIDEND: missing price or dividend")
        return None

    elif action_type == "RIGHTS":
        terp   = kwargs.get("theoretical_ex_rights_price")
        market = kwargs.get("market_price")
        if terp and market and market != 0:
            return terp / market
        logger.warning("RIGHTS: missing TERP or market price")
        return None

    elif action_type in ("MERGER", "DEMERGER"):
        logger.warning(
            "%s requires manual review — no standard formula. "
            "Set requires_manual_review=TRUE in corporate_actions table.",
            action_type
        )
        return None

    else:
        logger.warning("Unknown action_type: %s", action_type)
        return None


def run_adjustment_for_isin(isin: str, source: str = "NSE") -> dict:
    """
    Fetch prices and actions for an ISIN, compute adjusted prices,
    and write back to Neon equity_prices table.

    Returns summary dict.
    """
    logger.info("[ca_adjustment] Processing %s (%s)", isin, source)

    with get_pg_conn() as conn:
        # Fetch unadjusted prices
        prices = pd.read_sql(
            """
            SELECT isin, trade_date, close
            FROM investmitra.equity_prices
            WHERE isin = %s AND source = %s
            ORDER BY trade_date ASC
            """,
            conn, params=(isin, source)
        )

        if prices.empty:
            logger.warning("[ca_adjustment] No prices for %s", isin)
            return {"isin": isin, "status": "no_prices"}

        # Fetch confirmed corporate actions (skip manual review ones)
        actions = pd.read_sql(
            """
            SELECT ex_date, adj_factor, action_type
            FROM investmitra.corporate_actions
            WHERE isin = %s
              AND requires_manual_review = FALSE
              AND (nse_confirmed = TRUE OR bse_confirmed = TRUE)
            ORDER BY ex_date ASC
            """,
            conn, params=(isin,)
        )

        if actions.empty:
            # No actions — adj_close = close, adj_factor = 1
            adj_df = prices.copy()
            adj_df["adj_factor"] = 1.0
            adj_df["adj_close"]  = adj_df["close"]
        else:
            logger.info(
                "[ca_adjustment] %s has %d corporate actions", isin, len(actions)
            )
            adj_df = compute_adjusted_prices(prices, actions)

        # Write adj_close and adj_factor back to equity_prices
        cur = conn.cursor()
        updated = 0
        for _, row in adj_df.iterrows():
            cur.execute(
                """
                UPDATE investmitra.equity_prices
                SET adj_close  = %s,
                    adj_factor = %s
                WHERE isin = %s
                  AND trade_date = %s
                  AND source = %s
                """,
                (
                    float(row["adj_close"]),
                    float(row["adj_factor"]),
                    isin,
                    row["trade_date"],
                    source,
                )
            )
            updated += cur.rowcount
        cur.close()

    suspicious = adj_df.get("suspicious_gap", pd.Series(dtype=bool))
    n_suspicious = int(suspicious.sum()) if len(suspicious) > 0 else 0

    if n_suspicious > 0:
        logger.warning(
            "[ca_adjustment] %s has %d suspicious gaps (>50%% price change). "
            "Check corporate_actions table.",
            isin, n_suspicious
        )

    logger.info("[ca_adjustment] %s — updated %d rows, %d suspicious gaps",
                isin, updated, n_suspicious)

    return {
        "isin": isin,
        "status": "completed",
        "rows_updated": updated,
        "suspicious_gaps": n_suspicious,
        "actions_applied": len(actions) if not actions.empty else 0,
    }


def run_adjustment_for_all(source: str = "NSE", batch_size: int = 100) -> dict:
    """
    Run corporate action adjustment for all ISINs in equity_prices.
    Used for full historical recalculation.
    """
    with get_pg_conn() as conn:
        isins = pd.read_sql(
            "SELECT DISTINCT isin FROM investmitra.equity_prices WHERE source = %s AND trade_date >= CURRENT_DATE - INTERVAL '7 days'",    
            conn, params=(source,)
        )["isin"].tolist()

    logger.info("[ca_adjustment] Processing %d ISINs for %s", len(isins), source)

    results = {"total": len(isins), "completed": 0, "failed": 0, "suspicious": 0}

    for i, isin in enumerate(isins):
        try:
            result = run_adjustment_for_isin(isin, source)
            results["completed"] += 1
            results["suspicious"] += result.get("suspicious_gaps", 0)
        except Exception as e:
            logger.error("[ca_adjustment] Failed for %s: %s", isin, e)
            results["failed"] += 1

        if (i + 1) % batch_size == 0:
            logger.info("[ca_adjustment] Progress: %d/%d", i + 1, len(isins))

    logger.info("[ca_adjustment] Done: %s", results)
    return results


def _flag_suspicious_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Flag rows where adj_close changes by more than 50% vs previous day."""
    df = df.copy()
    df["prev_adj_close"] = df["adj_close"].shift(1)
    df["suspicious_gap"] = (
        ((df["adj_close"] - df["prev_adj_close"]).abs() / df["prev_adj_close"]) > SUSPICIOUS_GAP_PCT
    )
    df = df.drop(columns=["prev_adj_close"])
    return df
