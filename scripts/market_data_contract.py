"""Versioned daily-price and stock-metadata contracts. No network or DB on import."""
import math

VERSION = "daily-venue-v1"
QUALITY_VERSION = "quality-four-inputs-v1"


def canonical_price_ctes(raw="raw_prices"):
    """DuckDB CTEs: exact reruns collapse; conflicting reruns fail the build.

    Prefer NSE for an ISIN's entire input window, otherwise BSE. Never splice
    BSE days into NSE history. Caller bounds the window before calling this.
    """
    if raw != "raw_prices":
        raise ValueError("Unexpected raw price relation")
    return """
    distinct_prices AS (
        SELECT DISTINCT isin, trade_date, source, close, volume, turnover_cr, delivery_pct
        FROM raw_prices WHERE source IN ('NSE','BSE')
    ), price_conflicts AS (
        SELECT isin, trade_date, source, COUNT(*) AS variants,
            MIN(close) AS close_min, MAX(close) AS close_max,
            MIN(volume) AS volume_min, MAX(volume) AS volume_max,
            MIN(turnover_cr) AS turnover_min, MAX(turnover_cr) AS turnover_max,
            MIN(delivery_pct) AS delivery_min, MAX(delivery_pct) AS delivery_max,
            COUNT(close) AS close_present, COUNT(volume) AS volume_present,
            COUNT(turnover_cr) AS turnover_present, COUNT(delivery_pct) AS delivery_present
        FROM distinct_prices
        GROUP BY isin, trade_date, source HAVING COUNT(*) > 1
    ), conflict_examples AS (
        SELECT * FROM price_conflicts ORDER BY isin, trade_date, source LIMIT 10
    ), price_validation AS (
        SELECT CASE WHEN EXISTS(SELECT 1 FROM price_conflicts)
          THEN error('Conflicting same-venue daily prices; resolve source revisions before scoring. '
            || 'Conflicting keys=' || CAST((SELECT COUNT(*) FROM price_conflicts) AS VARCHAR)
            || '; first 10 examples (present counts expose NULL differences): '
            || (SELECT CAST(to_json(list(conflict_examples)) AS VARCHAR) FROM conflict_examples))
          ELSE 1 END AS ok
    ), venues AS (
        SELECT isin, CASE WHEN MAX(CASE WHEN source='NSE' THEN 1 ELSE 0 END)=1
                    THEN 'NSE' ELSE 'BSE' END AS source
        FROM distinct_prices GROUP BY isin
    ), prices AS (
        SELECT p.* FROM distinct_prices p JOIN venues v USING(isin,source)
        CROSS JOIN price_validation WHERE ok=1
    )
    """


def quality_score(investmitra, screens, piotroski, graham):
    values = [float(v) for v in (investmitra, screens, piotroski, graham)]
    if not all(math.isfinite(v) and v >= 0 for v in values):
        raise ValueError("Invalid quality inputs")
    inv, sc, pi, gr = values
    if inv > 100 or pi > 9 or gr > 4:
        raise ValueError("Quality inputs outside documented range")
    return round(inv*.50 + min(sc/20, 1)*20 + pi/9*15 + gr/4*15, 2)


def canonical_stock(stock):
    """All routes use the initial watchlist formula, preserving component evidence."""
    result = dict(stock)
    cap = stock.get("market_cap_category") or stock.get("cap")
    if cap not in {"MICRO", "SMALL", "MID", "LARGE"}:
        raise ValueError("Missing market-cap classification")
    result["cap"] = result["market_cap_category"] = cap
    pi = stock.get("piotroski", stock.get("piotroski_score", 0))
    result["piotroski"] = result["piotroski_score"] = pi
    result["quality_score"] = quality_score(stock["investmitra_score"],
        stock.get("screen_count", 0), pi, stock.get("graham", 0))
    result["quality_version"] = QUALITY_VERSION
    result["avg_vol"] = result["avg_volume"] = stock.get("avg_volume", stock.get("avg_vol", 0))
    result["bulk_deal"] = result["in_bulk_deal"] = bool(stock.get("in_bulk_deal", False))
    return result


def coverage(stocks, levels, sector_map):
    """Coverage is reported, never silently interpreted as a passing gate."""
    return {"stocks": len(stocks),
        "unmapped_sector": sorted(s for s, v in stocks.items() if v.get("sector") not in sector_map),
        "missing_atr": sorted(s for s in stocks if not levels.get(s, {}).get("atr14")),
        "quality_versions": sorted({v.get("quality_version", "unversioned") for v in stocks.values()}),
        "sector_mapping_basis": "broad_sector_proxy_not_verified_index_membership"}
