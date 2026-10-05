"""Read-only dated score coverage check. No Kite login, orders or DB writes."""
import argparse
from datetime import date
import json
import math
from market_data_contract import VERSION


def audit(conn, target):
    # Start from the master plus current NSE prices, not only existing scores:
    # otherwise missing scores disappear from both the numerator and denominator.
    with conn.cursor() as cur:
        cur.execute('''WITH priced AS (
            SELECT DISTINCT isin FROM investmitra.equity_prices
            WHERE source='NSE' AND trade_date=%s AND close>0
        )
        SELECT cm.nse_symbol,ds.price_contract_version,ds.sector,
               cm.market_cap_category,p.isin IS NOT NULL,
               ds.isin IS NOT NULL,ds.investmitra_score,cm.sector,
               (SELECT COUNT(DISTINCT UPPER(TRIM(other.isin)))
                FROM investmitra.company_master other
                WHERE UPPER(TRIM(other.nse_symbol))=UPPER(TRIM(cm.nse_symbol)))
        FROM investmitra.company_master cm
        LEFT JOIN priced p ON p.isin=cm.isin
        LEFT JOIN investmitra.daily_scores ds ON ds.isin=cm.isin AND ds.score_date=%s
        WHERE cm.nse_symbol IS NOT NULL AND (p.isin IS NOT NULL OR ds.isin IS NOT NULL)''',
        (target,target))
        rows=cur.fetchall()
    return summarise(rows, target)


def summarise(rows, target):
    def known(value):
        return bool(value) and str(value).strip().lower() not in {'','unknown','nan','none'}
    def valid_score(value):
        try: return math.isfinite(float(value)) and 0 <= float(value) <= 100
        except (ValueError,TypeError): return False
    caps={'MICRO','SMALL','MID','LARGE'}
    scored=[r for r in rows if r[5]]
    # Ambiguous security identities are quarantined in the entry catalog too.
    # Keep them visible; never choose an ISIN using score/metadata availability.
    unique=lambda r: len(r)>8 and r[8]==1
    expected=[r for r in rows if unique(r) and r[4] and known(r[7]) and r[3] in caps]
    complete=lambda r: r[5] and r[1]==VERSION and known(r[2]) and valid_score(r[6])
    missing=sorted(r[0] for r in expected if not complete(r))
    eligible=sorted(r[0] for r in expected if complete(r))
    result=dict(date=target.isoformat(),price_contract=VERSION,
        scored_nse_symbols=len(scored),
        expected_classified_priced_nse_symbols=len(expected),
        eligible_classified_priced_nse_symbols=len(eligible),
        missing_classified_scores=missing,
        quarantined_ambiguous_symbols=sorted({r[0] for r in rows if not unique(r)}),
        blocking_wrong_contract=sorted(r[0] for r in expected if r[5] and r[1]!=VERSION),
        wrong_contract=sorted(r[0] for r in scored if r[1]!=VERSION),
        missing_sector=sorted(r[0] for r in scored if not known(r[2])),
        missing_cap=sorted(r[0] for r in scored if r[3] not in caps),
        missing_target_nse_price=sorted(r[0] for r in scored if not r[4]),
        ready=bool(eligible) and not missing,
        limitation='Input coverage only; liquidity, event, sector-index mapping and entry gates still apply')
    return result


def require_ready(result):
    if not result['ready']:
        raise RuntimeError('NOT READY: classified priced NSE score coverage incomplete '
            f"(eligible={result['eligible_classified_priced_nse_symbols']}, "
            f"expected={result['expected_classified_priced_nse_symbols']}, "
            f"missing={result['missing_classified_scores'][:20]}, "
            f"blocking_wrong_contract={len(result['blocking_wrong_contract'])}). Rebuild dated scores; do not bypass.")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--date',required=True,type=date.fromisoformat)
    args=parser.parse_args()
    from dotenv import load_dotenv
    import os,psycopg2
    load_dotenv('.env.prod')
    conn=psycopg2.connect(os.environ['CC_POSTGRES_URL'],connect_timeout=10,
                         options='-c statement_timeout=15000')
    try:
        conn.set_session(readonly=True)
        result=audit(conn,args.date)
    finally: conn.close()
    print(json.dumps(result,indent=2))
    try: require_ready(result)
    except RuntimeError as exc: raise SystemExit(str(exc))
    print('Dated score coverage valid. Reported unclassified symbols remain excluded; entry gates still apply.')


if __name__=='__main__': main()
