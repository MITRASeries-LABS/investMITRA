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


def load_nse_holidays(today):
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
    previous_session(today, holidays)  # validate coverage and session before use
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
