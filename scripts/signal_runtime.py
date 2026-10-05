"""Shared quote pacing and explicit NSE session validation. No I/O at import."""
from datetime import datetime, timedelta
import threading
import time


class RateLimitedKite:
    """One quote allowance per API client; execution gets priority over scans."""
    def __init__(self, kite, interval=1.05):
        self._kite = kite
        self._interval = interval
        self._condition = threading.Condition()
        self._next_quote = 0.0
        self._quote_in_flight = False
        self._execution_waiters = 0

    def __getattr__(self, name):
        return getattr(self._kite, name)

    def quote(self, *args, **kwargs):
        return self._quote(False, *args, **kwargs)

    def execution_quote(self, *args, **kwargs):
        return self._quote(True, *args, **kwargs)

    def _quote(self, execution, *args, **kwargs):
        with self._condition:
            if execution:
                self._execution_waiters += 1
            try:
                while True:
                    delay = self._next_quote - time.monotonic()
                    if not self._quote_in_flight and delay <= 0 and (execution or not self._execution_waiters):
                        self._quote_in_flight = True
                        break
                    self._condition.wait(timeout=max(delay, 0.01))
            finally:
                if execution:
                    self._execution_waiters -= 1
                self._condition.notify_all()
        try:
            return self._kite.quote(*args, **kwargs)
        finally:
            # Pace from completion: reserving a start time before releasing the
            # lock lets a descheduled thread bunch requests with the next one.
            with self._condition:
                self._next_quote = time.monotonic() + self._interval
                self._quote_in_flight = False
                self._condition.notify_all()


def previous_session(today, holidays):
    """Require a calendar covering the relevant year; never infer from DB rows."""
    if today.year not in {d.year for d in holidays}:
        raise ValueError("NSE holiday calendar does not cover the current year")
    if today.weekday() >= 5 or today in holidays:
        raise ValueError("Today is not a regular NSE trading session")
    previous = today - timedelta(days=1)
    while previous.weekday() >= 5 or previous in holidays:
        previous -= timedelta(days=1)
    if previous.year != today.year and previous.year not in {d.year for d in holidays}:
        raise ValueError("Previous-year NSE calendar required to validate data freshness")
    return previous


def load_nse_holidays(today, *, validate_session=True):
    """Official capital-market calendar. Failure blocks new session startup."""
    import requests
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                                "Referer": "https://www.nseindia.com/"})
        session.get("https://www.nseindia.com/", timeout=10).raise_for_status()
        response = session.get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=10)
        response.raise_for_status()
        holidays = {datetime.strptime(row["tradingDate"], "%d-%b-%Y").date()
                    for row in response.json()["CM"]}
    if today.year not in {d.year for d in holidays}:
        raise ValueError("NSE holiday calendar does not cover the current year")
    if validate_session:
        previous_session(today, holidays)  # engine startup still requires a session
    return holidays


def freshness_errors(today, expected, score_date, price_date, indices_today, price_rows):
    errors = []
    if score_date != expected:
        errors.append(f"Scores dated {score_date}; require {expected}")
    if price_date != expected:
        errors.append(f"Prices dated {price_date}; require {expected}")
    if indices_today <= 0:
        errors.append(f"Market indices missing for {today}")
    if price_rows <= 10000:
        errors.append("Insufficient historical price rows")
    return errors


# Index quotes refresh outside the WebSocket callback; scoring checks exchange time.
SECTOR_REFRESH_SECONDS = 60
SECTOR_MAX_AGE_SECONDS = 120


def index_quote_context(quote, now):
    """No synthetic zero return, and no local receive time substituted for exchange time."""
    import math
    from datetime import timezone
    result = {"change_pct": None, "quote_at": None, "status": "missing"}
    if not isinstance(quote, dict) or not quote:
        return result
    try:
        at = quote.get("timestamp")
        if isinstance(at, str):
            at = datetime.fromisoformat(at)
        if not isinstance(at, datetime):
            result["status"] = "timestamp_missing"
            return result
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        result["quote_at"] = at.isoformat()
        age = (now-at).total_seconds()
        if age < 0 or age > SECTOR_MAX_AGE_SECONDS or at.astimezone(now.tzinfo).date() != now.date():
            result["status"] = "stale_or_future"
            return result
        prev = float(quote.get("ohlc", {}).get("close", 0))
        last = float(quote.get("last_price", 0))
        if not all(math.isfinite(x) and x > 0 for x in (prev, last)):
            result["status"] = "invalid_price"
            return result
        result.update(change_pct=(last/prev-1)*100, status="fresh")
    except (TypeError, ValueError, OverflowError, AttributeError):
        result["status"] = "invalid_quote"
    return result


def relative_strength_context(price, previous_close, sign, sector_quote, nifty_quote, now):
    """Compare concurrent close-to-current returns, direction-aware for shorts."""
    import math
    sector = index_quote_context(sector_quote, now)
    nifty = index_quote_context(nifty_quote, now)
    stock_change = None
    try:
        price, previous_close = float(price), float(previous_close)
        if all(math.isfinite(x) and x > 0 for x in (price, previous_close)):
            stock_change = (price/previous_close-1)*100
    except (TypeError, ValueError, OverflowError):
        pass
    known = stock_change is not None and sector["status"] == nifty["status"] == "fresh"
    score = 0  # Unknown observations cannot award a relative-strength bonus.
    if known:
        stock, sec, idx = (x*sign for x in (stock_change, sector["change_pct"], nifty["change_pct"]))
        score = 90 if stock > sec > idx else 70 if stock > sec else 55 if stock > idx else 35
    return {"sector_rs": score, "sector_rs_available": known,
            "sector_status": sector["status"], "nifty_status": nifty["status"],
            "sector_quote_at": sector["quote_at"], "nifty_quote_at": nifty["quote_at"],
            "sector_chg": sector["change_pct"], "nifty_chg": nifty["change_pct"],
            "stock_change_pct": stock_change,
            "stock_vs_sector": stock_change-sector["change_pct"] if known else None,
            "sector_return_basis": "previous_close_to_current"}


def opening_range_context(high, low, is_set, span, price, sign):
    """Distinguish missing/partial sampled opening ranges from a verified non-breakout."""
    import math
    valid = all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (high, low)) and high > low
    complete = bool(valid and is_set and span and span["first"] <= 556
                    and span["last"] >= 569 and span["max_gap"] <= 60)
    score = 0
    breakout = None
    if complete:
        breakout = price > high if sign > 0 else price < low
        if breakout:
            score = min(((price-high) if sign > 0 else (low-price))/(high-low)*100, 100)
    status = "complete_breakout" if breakout else "complete_no_breakout" if complete else "incomplete" if valid else "unavailable"
    return {"orb_score": round(score, 1), "opening_range_status": status,
            "opening_range_complete": complete, "opening_breakout": breakout,
            "opening_range_high": high if valid else None,
            "opening_range_low": low if valid else None,
            "opening_range_source": (span or {}).get("source", "observed_ticks")}
