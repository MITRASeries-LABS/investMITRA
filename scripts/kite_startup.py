"""Paper-only intraday inputs from completed Kite daily candles.

No database writes, no order submission, no overnight lake dependency.
The NSE-only momentum ranking cohort is versioned separately from EOD scoring.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import statistics
import time
import uuid

from market_data_contract import canonical_stock, VERSION as EOD_VERSION
from signal_evidence import VolumeBaselines
from signal_runtime import load_nse_holidays, previous_session

IST = timezone(timedelta(hours=5, minutes=30))
VERSION = 'kite-startup-nse-v1'
MAX_SLOW_SESSION_AGE = 3
MIN_COHORT_COVERAGE = .95
LOG = logging.getLogger(__name__)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, sort_keys=True, allow_nan=False), encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def session_dates(start, end, holidays):
    return [start + timedelta(days=i) for i in range((end-start).days+1)
            if (start+timedelta(days=i)).weekday() < 5 and start+timedelta(days=i) not in holidays]


def calendar(today, cache_dir, fetch=load_nse_holidays, sleep=time.sleep):
    """Bounded retries, then a previously validated official response, max seven days."""
    path = Path(cache_dir) / 'calendar.json'
    for attempt in range(3):
        try:
            holidays = fetch(today)
            previous_session(today, holidays)
            write_json(path, dict(fetched=today.isoformat(), source='NSE_CM',
                                  holidays=sorted(d.isoformat() for d in holidays)))
            return holidays
        except Exception as exc:
            LOG.warning('Calendar attempt %d/3 failed (%s)', attempt+1, type(exc).__name__)
            if attempt < 2: sleep(attempt+1)
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
        if cached['source'] != 'NSE_CM' or not 0 <= (today-date.fromisoformat(cached['fetched'])).days <= 7:
            raise ValueError('calendar cache expired')
        holidays = {date.fromisoformat(d) for d in cached['holidays']}
        previous_session(today, holidays)
        LOG.warning('Using verified calendar cached on %s', cached['fetched'])
        return holidays
    except Exception as exc:
        raise RuntimeError('Official calendar unavailable and no valid local calendar cache') from exc


def slow_cutoff(expected, holidays):
    day = expected
    for _ in range(MAX_SLOW_SESSION_AGE):
        day -= timedelta(days=1)
        while day.weekday() >= 5 or day in holidays: day -= timedelta(days=1)
    return day


def validate_candles(rows, expected, holidays):
    values = {}
    for r in rows:
        at = r['date']
        at = datetime.fromisoformat(at) if isinstance(at, str) else at
        if not isinstance(at, datetime) or at.tzinfo is None:
            raise ValueError('candle timestamp has no timezone')
        day = at.astimezone(IST).date()
        if day > expected:
            raise ValueError('incomplete or future daily candle')
        o,h,l,c,v = [float(r[k]) for k in ('open','high','low','close','volume')]
        if (not all(math.isfinite(x) for x in (o,h,l,c,v)) or min(o,h,l,c) <= 0
                or v < 0 or v != int(v) or not l <= min(o,c) <= max(o,c) <= h):
            raise ValueError('invalid OHLCV')
        row = dict(date=day.isoformat(), open=o, high=h, low=l, close=c, volume=v)
        if day in values and values[day] != row: raise ValueError('conflicting daily candles')
        values[day] = row
    bars = [values[d] for d in sorted(values)]
    if len(bars) < 21: raise ValueError('need at least 21 completed sessions')
    if bars[-1]['date'] != expected.isoformat() or bars[-1]['volume'] <= 0:
        raise ValueError('previous-session candle unavailable or untraded')
    required = session_dates(expected-timedelta(days=30), expected, holidays)
    if not set(required).issubset(values): raise ValueError('recent session history has holes')
    for a,b in zip(bars, bars[1:]):
        if abs(b['close']/a['close']-1) > .4:
            raise ValueError('large historical discontinuity needs corporate-action review')
    return bars


def features(bars, today):
    closes = [r['close'] for r in bars]
    last = bars[-1]
    recent = [r for r in bars if r['date'] >= (today-timedelta(days=30)).isoformat()]
    positive = [r for r in recent if r['volume'] > 0]
    if len(positive) < 10: raise ValueError('insufficient positive volume samples')
    avg = lambda xs: sum(xs)/len(xs)
    trs = [max(b['high']-b['low'], abs(b['high']-a['close']), abs(b['low']-a['close']))
           for a,b in zip(bars,bars[1:])]
    atr = avg(trs[-14:])
    if atr <= 0: raise ValueError('zero ATR')
    low, high = min(closes[-252:]), max(closes[-252:])
    momentum = {f'ret_{n}d_pct': round((closes[-1]/closes[-1-n]-1)*100,4) if len(closes)>n else None for n in (5,20,60,252)}
    momentum.update(pos_52w=round((closes[-1]-low)/(high-low),4) if high>low else None,
                    ma_cross_signal_pct=100 if avg(closes[-50:]) > avg(closes[-200:]) else 0,
                    vol_ratio_20d=min(round(last['volume']/avg([b['volume'] for b in bars[-20:]]),4),5))
    levels = dict(prev_open=last['open'], prev_high=last['high'], prev_low=last['low'],
                  prev_close=last['close'], ma20=avg(closes[-20:]), ma50=avg(closes[-50:]),
                  atr14=atr, daily_range=last['high']-last['low'],
                  high_52w=max(r['high'] for r in bars[-252:]), low_52w=min(r['low'] for r in bars[-252:]),
                  prev_day_chg=(closes[-1]/closes[-2]-1)*100)
    volumes = [r['volume'] for r in positive]
    baseline = dict(source='NSE', provider='Kite', sample_days=len(positive),
                    first_date=positive[0]['date'], last_date=positive[-1]['date'], as_of=today.isoformat(),
                    window_start=(today-timedelta(days=30)).isoformat(), window_days=30,
                    method='mean_positive_daily_volume', corporate_action_adjusted=False, adjustment_status='provider_history_unverified')
    research = dict(status='recomputed_daily_baseline', sessions=len(positive),
                    first_date=positive[0]['date'], last_date=positive[-1]['date'],
                    mean_volume=avg(volumes), median_volume=statistics.median(volumes),
                    excluded_conflicting_days=0, provider='Kite')
    return dict(levels=levels, momentum=momentum, avg_volume=avg(volumes),
                avg_price=avg([b['close'] for b in recent]),
                avg_traded=avg([b['volume']*b['close'] for b in recent]),
                baseline=baseline, research=research)


def rank_momentum(records):
    """Same feature weights/rank/tie normalization; explicit NSE startup cohort."""
    import pandas as pd
    weights = dict(ret_252d_pct=.25, ret_60d_pct=.20, ret_20d_pct=.15,
                   pos_52w=.15, ma_cross_signal_pct=.10, vol_ratio_20d=.10, ret_5d_pct=.05)
    frame = pd.DataFrame([dict(symbol=s, sector=r['sector'], **r['inputs']['momentum'])
                          for s,r in records.items()]).set_index('symbol')
    raw = sum(frame.groupby('sector')[k].rank(pct=True)*100*w for k,w in weights.items())
    ranked = raw.groupby(frame['sector']).transform(lambda x: (x-x.min())/(x.max()-x.min()+1e-10)*100).round(2)
    # Match the existing composite's explicit neutral fallback for missing long history.
    return ranked.fillna(50.).to_dict()


def load_metadata(conn, expected, holidays):
    """Read slow inputs only. Deliberately no equity_prices join or lake receipt."""
    cutoff = slow_cutoff(expected, holidays)
    with conn.cursor() as cur:
        cur.execute("""SELECT MAX(score_date) FROM investmitra.daily_scores
                       WHERE score_date BETWEEN %s AND %s AND price_contract_version=%s""",
                    (cutoff, expected, EOD_VERSION))
        score_day = cur.fetchone()[0]
        if score_day is None: raise RuntimeError('No compatible slow score snapshot within three sessions')
        cur.execute("""SELECT MAX(signal_date) FROM investmitra.screener_signals WHERE signal_date<=%s""", (expected,))
        screen_day = cur.fetchone()[0]
        if screen_day is None or screen_day < cutoff:
            raise RuntimeError('Screener inputs older than three sessions or unavailable')
        cur.execute("""SELECT cm.nse_symbol,cm.isin,cm.company_name,cm.sector,cm.market_cap_category,
                       ds.investmitra_score,ds.momentum_score,COALESCE(ss.n,0),
                       COALESCE(vq.piotroski_score,0),COALESCE(vq.graham_criteria_met,0)
            FROM investmitra.company_master cm
            JOIN investmitra.daily_scores ds ON ds.isin=cm.isin AND ds.score_date=%s
              AND ds.price_contract_version=%s
            LEFT JOIN (SELECT isin,COUNT(DISTINCT screen_name) n FROM investmitra.screener_signals
                       WHERE signal_date=%s GROUP BY isin) ss ON ss.isin=cm.isin
            LEFT JOIN investmitra.value_quality vq ON vq.isin=cm.isin
            WHERE cm.nse_symbol IS NOT NULL
              AND cm.market_cap_category IN ('MICRO','SMALL','MID','LARGE')
              AND LOWER(TRIM(COALESCE(cm.sector,''))) NOT IN ('','unknown','nan','none')
              AND (SELECT COUNT(DISTINCT UPPER(TRIM(x.isin))) FROM investmitra.company_master x
                   WHERE UPPER(TRIM(x.nse_symbol))=UPPER(TRIM(cm.nse_symbol)))=1""",
                    (score_day,EOD_VERSION,screen_day))
        rows = cur.fetchall()
    counts = Counter(r[0] for r in rows)
    records = {}
    for symbol,isin,name,sector,cap,score,momentum,screens,pi,graham in rows:
        if counts[symbol] != 1: continue
        try:
            score,momentum,pi,graham = map(float,(score,momentum,pi,graham))
            if not (all(math.isfinite(v) for v in (score,momentum,pi,graham))
                    and 0<=score<=100 and 0<=momentum<=100 and 0<=pi<=9 and 0<=graham<=4): continue
            residual = score-.25*momentum
            if not -.01<=residual<=75.01: continue
            records[symbol] = dict(symbol=symbol,isin=isin,company_name=name,sector=sector,cap=cap,
                                   slow_residual=residual,screen_count=int(screens),piotroski=pi,graham=graham,
                                   slow_score_date=score_day.isoformat(),screen_date=screen_day.isoformat())
        except (ValueError, TypeError): continue
    if not records: raise RuntimeError('No unambiguous classified slow-input records')
    return records


class CandleClient:
    def __init__(self, kite, cache_dir, sleep=time.sleep, monotonic=time.monotonic):
        self.kite,self.root,self.sleep,self.clock = kite,Path(cache_dir),sleep,monotonic
        self.next_call = 0.

    def history(self, symbol, isin, token, expected, holidays):
        identity = dict(symbol=symbol,isin=isin,token=int(token),expected=expected.isoformat(),version=VERSION)
        key = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        path = self.root / 'candles' / (key+'.json')
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
            if doc['identity'] != identity: raise ValueError('cache identity mismatch')
            return validate_candles(doc['rows'],expected,holidays)
        except (OSError,ValueError,KeyError,TypeError): pass
        for attempt in range(3):
            self.sleep(max(0,self.next_call-self.clock()))
            try:
                rows = self.kite.historical_data(int(token),
                    datetime.combine(expected-timedelta(days=500), datetime.min.time()),
                    datetime.combine(expected, datetime.max.time()).replace(microsecond=0), 'day')
                serial = [dict(r,date=r['date'].isoformat() if isinstance(r['date'],datetime) else r['date']) for r in rows]
                bars = validate_candles(serial,expected,holidays)
                write_json(path,dict(identity=identity,rows=serial))
                return bars
            except Exception as exc:
                if type(exc).__name__ in {'TokenException','PermissionException'} or getattr(exc,'code',None) == 403:
                    raise RuntimeError('Kite authentication/historical permission failed; log in/check entitlement') from exc
                if isinstance(exc,(ValueError,KeyError,TypeError)) or attempt==2: raise
                self.sleep(attempt+1)
            finally:
                self.next_call=self.clock()+.36


def build_snapshot(kite, instruments, conn, today, cache_dir='data/startup'):
    holidays=calendar(today,cache_dir)
    expected=previous_session(today,holidays)
    records=load_metadata(conn,expected,holidays)
    grouped={}
    for i in instruments:
        if i.get('exchange')=='NSE' and i.get('segment')=='NSE' and i.get('instrument_type')=='EQ':
            grouped.setdefault(i['tradingsymbol'],[]).append(i)
    records={s:r for s,r in records.items() if len(grouped.get(s,[]))==1}
    if not records: raise RuntimeError('No current NSE equity instrument identities')
    client=CandleClient(kite,cache_dir)
    good,excluded={},{}
    network_failures = 0
    for index,(s,r) in enumerate(sorted(records.items()),1):
        try:
            bars=client.history(s,r['isin'],grouped[s][0]['instrument_token'],expected,holidays)
            good[s]=dict(r,inputs=features(bars,today))
            network_failures = 0
        except RuntimeError: raise
        except Exception as exc:
            excluded[s]=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            network_failures = 0 if isinstance(exc,(ValueError,KeyError,TypeError)) else network_failures+1
            if network_failures >= 5:
                write_json(Path(cache_dir)/'latest_attempt.json',dict(day=today.isoformat(),expected=expected.isoformat(),
                    error='five consecutive history request failures',excluded=excluded))
                raise RuntimeError('Kite history service unavailable after bounded retries; no stale-price fallback') from exc
        if index%25==0 or index==len(records):
            LOG.info('Kite startup history %d/%d; valid=%d excluded=%d',index,len(records),len(good),len(excluded))
    manifest=dict(version=VERSION,day=today.isoformat(),expected=expected.isoformat(),
                  total=len(records),valid=len(good),excluded=excluded,
                  slow_score_dates=sorted({r['slow_score_date'] for r in records.values()}))
    write_json(Path(cache_dir)/'latest_attempt.json',manifest)
    if len(good)<100 or len(good)/len(records)<MIN_COHORT_COVERAGE:
        raise RuntimeError('Kite history coverage below 95% or 100 symbols; see data/startup/latest_attempt.json')
    scores=rank_momentum(good)
    stocks,levels,values,metadata,research={},{},{},{},{}
    for s,r in good.items():
        f=r.pop('inputs')
        inv=round(min(100,max(0,r.pop('slow_residual')+.25*scores[s])),2)
        labels=('Strong Sell','Sell','Neutral','Buy','Strong Buy')
        stock=dict(r,investmitra_score=inv,momentum_score=scores[s],signal=labels[min(4,max(0,math.ceil(inv/20)-1))],
                   avg_volume=f['avg_volume'],avg_price=f['avg_price'],avg_traded=f['avg_traded'],
                   prev_day_chg=f['levels']['prev_day_chg'],prev_close=f['levels']['prev_close'],
                   input_version=VERSION,price_date=expected.isoformat(),momentum_date=expected.isoformat(),
                   momentum_status='computed' if all(v is not None for v in f['momentum'].values()) else 'neutral_missing_long_history',
                   ranking_cohort='classified_NSE_valid_Kite_history')
        stocks[s]=canonical_stock(stock)
        levels[s]=f['levels'];values[s]=f['avg_volume'];metadata[s]=f['baseline'];research[s]=f['research']
    payload=dict(manifest=manifest,stocks=stocks,levels=levels,volumes=values,volume_metadata=metadata,research=research)
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()
    payload['snapshot_id']=VERSION+':'+digest[:16]
    write_json(Path(cache_dir)/(today.isoformat()+'_'+digest[:16]+'.json'),payload)
    LOG.info('Kite startup validated: %s; price session %s; slow score dates %s',payload['snapshot_id'],expected,manifest['slow_score_dates'])
    return payload


def catalog(snapshot, ctx, sensitive):
    result=[]
    for stock in snapshot['stocks'].values():
        s=stock['symbol']
        if (s in sensitive or s in ctx.get('results_3days',set()) or
                abs(stock['prev_day_chg'])>5 or stock['avg_traded']<5_000_000 or
                not 50<=stock['avg_price']<=20000): continue
        result.append(canonical_stock(dict(stock,in_bulk_deal=s in ctx.get('bulk_deals',set()),
                                           preopen_gap=round((ctx.get('preopen_prices', {}).get(s, stock['avg_price']) / stock['avg_price'] - 1)*100, 2))))
    return sorted(result, key=lambda r: (-r['investmitra_score'], r['symbol']))


def baselines(snapshot):
    return VolumeBaselines(snapshot['volumes'],snapshot['volume_metadata'])


def price_rejection(stock, observed_previous_close):
    """Detect an ex-date/identity mismatch before admission, never during exits."""
    if stock.get('input_version') != VERSION:
        return None
    try:
        saved, observed = float(stock['prev_close']), float(observed_previous_close)
        if not all(math.isfinite(v) and v > 0 for v in (saved, observed)):
            raise ValueError('invalid close')
        if abs(saved-observed) > max(.05, saved*.001):
            return 'Kite history and quote previous close disagree; corporate-action/identity review required'
    except (ValueError, TypeError, KeyError):
        return 'Kite previous-close evidence unavailable'
    return None


def main():
    """Optional early preparation, after login; no executor and no orders."""
    import os
    from dotenv import load_dotenv
    from kiteconnect import KiteConnect
    import psycopg2
    load_dotenv('.env.prod')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    required=('KITE_API_KEY','KITE_ACCESS_TOKEN','CC_POSTGRES_URL')
    if any(not os.getenv(k) for k in required):
        raise SystemExit('Missing Kite/database configuration; run scripts/kite_login.py first')
    kite=KiteConnect(api_key=os.environ['KITE_API_KEY'])
    kite.set_access_token(os.environ['KITE_ACCESS_TOKEN'])
    instruments=kite.instruments('NSE')
    con=psycopg2.connect(os.environ['CC_POSTGRES_URL'],connect_timeout=10,options='-c statement_timeout=15000')
    try:
        con.set_session(readonly=True)
        snapshot=build_snapshot(kite,instruments,con,datetime.now(IST).date())
        print('Prepared '+snapshot['snapshot_id']+'; engine will revalidate inputs at startup.')
    finally:
        con.close()


if __name__=='__main__':main()
