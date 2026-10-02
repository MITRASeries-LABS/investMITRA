"""Exchange-matched volume evidence and configured sector admission. No I/O on import."""
import math
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
SECTOR_POLICY_DEFAULTS = {"min_sector_chg_long": -0.3, "max_sector_chg_short": 0.3}


class VolumeBaselines(dict):
    """Numeric mapping retained for callers, with independently persisted provenance."""
    def __init__(self, values=(), metadata=None):
        super().__init__(values)
        self.metadata = metadata or {}


def load_volume_baselines(conn, as_of):
    # NSE intraday volume must never be compared with a mixed BSE/NSE average.
    # Conflicting symbol/date rows are excluded instead of double-counted. The
    # latest historical date and sample size are evidence, not calendar inference.
    start = as_of - timedelta(days=30)
    with conn.cursor() as cur:
        cur.execute("""
            WITH daily AS (
                SELECT cm.nse_symbol AS symbol, ep.trade_date, MAX(ep.volume) AS volume
                FROM investmitra.equity_prices ep
                JOIN investmitra.company_master cm ON ep.isin=cm.isin
                WHERE ep.source = 'NSE' AND ep.trade_date >= %s AND ep.trade_date < %s
                  AND ep.volume > 0 AND cm.nse_symbol IS NOT NULL
                GROUP BY cm.nse_symbol, ep.trade_date
                HAVING COUNT(DISTINCT ep.volume) = 1 AND COUNT(DISTINCT ep.isin) = 1
            )
            SELECT symbol, AVG(volume), COUNT(*), MIN(trade_date), MAX(trade_date)
            FROM daily GROUP BY symbol
        """, (start, as_of))
        rows = cur.fetchall()
    result = VolumeBaselines()
    for symbol, average, count, first, last in rows:
        average = float(average)
        if not math.isfinite(average) or average <= 0 or not count:
            continue
        result[symbol] = average
        result.metadata[symbol] = dict(source="NSE", sample_days=int(count),
            first_date=first.isoformat(), last_date=last.isoformat(), as_of=as_of.isoformat(),
            window_start=start.isoformat(), window_days=30,
            method="mean_positive_daily_volume", corporate_action_adjusted=False)
    return result


def volume_evidence(baselines, metadata, symbol, volume, now):
    """Retain the existing linear intraday model; expose its actual inputs."""
    average = float(baselines.get(symbol, 0))
    volume = float(volume)
    fraction = max((now.hour * 60 + now.minute - 555) / 375, .05)
    valid = math.isfinite(average) and average > 0 and math.isfinite(volume) and volume >= 0
    expected = average * fraction if valid else None
    raw = volume / expected if expected else None
    provenance = dict(metadata.get(symbol, {}))
    return {"rvol": min(raw, 200.0) if raw is not None else 0.0,
            "rvol_uncapped": raw, "rvol_live_volume": volume if math.isfinite(volume) else None,
            "rvol_avg_daily_volume": average if math.isfinite(average) and average > 0 else None,
            "rvol_elapsed_fraction": fraction, "rvol_expected_volume": expected,
            "rvol_method": "linear_elapsed_session_fraction",
            "rvol_baseline": provenance, "rvol_baseline_status": "verified_nse" if valid and provenance.get("source") == "NSE" else "unverified"}


def sector_policy_rejection(signal, now):
    """Recheck frozen configured limits and quote age at both queue and admission."""
    try:
        weights = signal.get("signal_weights", {})
        direction = signal.get("direction")
        name = "min_sector_chg_long" if direction == "LONG" else "max_sector_chg_short"
        if name not in weights:
            return None  # compatibility for old signals; production freezes defaults
        if direction not in {"LONG", "SHORT"}:
            return "sector gate: invalid direction"
        bound = float(weights[name])
        details = signal["details"]
        change = float(details["sector_chg"])
        if not all(math.isfinite(x) for x in (bound, change)):
            return "sector gate: invalid change/limit"
        if details.get("sector_status") != "fresh" or not details.get("sector_index"):
            return "sector gate: fresh mapped sector required"
        at = datetime.fromisoformat(details["sector_quote_at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=IST)
        age = (now - at).total_seconds()
        if not 0 <= age <= 120 or at.astimezone(IST).date() != now.astimezone(IST).date():
            return "sector gate: quote stale or in future"
        if (direction == "LONG" and change < bound) or (direction == "SHORT" and change > bound):
            return f"sector gate: {direction} sector {change:+.3f}% violates {name}={bound:+.3f}%"
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return "sector gate: missing or invalid configured sector evidence"
    return None
