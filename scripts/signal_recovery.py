"""Timestamp-checked context and opening-window recovery. Pure helpers."""
from datetime import datetime, timedelta, timezone
import math

IST = timezone(timedelta(hours=5, minutes=30))


def opening_candles(candles, now):
    """Require all fifteen completed minute candles, 09:15 through 09:29 IST.

    Do not use the 09:30 candle, daily high/low, or seed the live gap-hold timer.
    """
    now = now.astimezone(IST)
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = start + timedelta(minutes=15)
    if now < end:
        return None
    values = {}
    for row in candles:
        try:
            at = row['date']
            at = datetime.fromisoformat(at) if isinstance(at, str) else at
            if at.tzinfo is None: return None
            at = at.astimezone(IST)
            if not start <= at < end: continue
            if at.second or at.microsecond: return None
            o,h,l,c = [float(row[k]) for k in ('open','high','low','close')]
            if not all(math.isfinite(v) and v > 0 for v in (o,h,l,c)) or not l <= min(o,c) <= max(o,c) <= h:
                return None
            if at in values and values[at] != (h,l): return None
            values[at] = (h,l)
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
    if set(values) != {start+timedelta(minutes=i) for i in range(15)}:
        return None
    return dict(high=max(v[0] for v in values.values()), low=min(v[1] for v in values.values()),
                span=dict(first=555, last=569, at=end.timestamp(), max_gap=60,
                          source='kite_completed_minute_candles', recovered_at=now.isoformat()))


def breadth_evidence(breadth, now, sign):
    data = breadth.get('NIFTY 50', {})
    result = dict(breadth=0, breadth_source='nse_all_indices', breadth_status='unverified',
                  breadth_quote_at=data.get('quote_at'))
    try:
        at = datetime.fromisoformat(data['quote_at'])
        if at.tzinfo is None: return result
        if at.astimezone(IST).date() != now.astimezone(IST).date() or not 0 <= (now-at).total_seconds() <= 120:
            result['breadth_status'] = 'stale_or_future'; return result
        adv, dec = int(data['advances']), int(data['declines'])
        if min(adv,dec) < 0 or not 0 < adv+dec <= 50: return result
        fav, opp = (adv,dec) if sign > 0 else (dec,adv)
        ratio = fav/max(opp,1)
        result.update(breadth=min(ratio/3*100,100) if ratio>1 else 30,
                      breadth_status='fresh', advances=adv, declines=dec)
    except (KeyError, TypeError, ValueError):
        pass
    return result
