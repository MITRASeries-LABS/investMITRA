"""Read-only post-pipeline release check. No Kite login, orders or database writes."""
import argparse
from datetime import date
import json
from market_data_contract import VERSION


def audit(conn, target):
    with conn.cursor() as cur:
        cur.execute('''SELECT cm.nse_symbol,ds.price_contract_version,ds.sector,
            cm.market_cap_category,
            EXISTS(SELECT 1 FROM investmitra.equity_prices ep
                   WHERE ep.isin=ds.isin AND ep.source='NSE' AND ep.trade_date=%s)
            FROM investmitra.daily_scores ds JOIN investmitra.company_master cm USING(isin)
            WHERE ds.score_date=%s AND cm.nse_symbol IS NOT NULL''', (target,target))
        rows=cur.fetchall()
    unknown=lambda value: not value or str(value).strip().lower() in {'unknown','nan','none'}
    return dict(date=target.isoformat(), price_contract=VERSION, scored_nse_symbols=len(rows),
        wrong_contract=sorted(r[0] for r in rows if r[1] != VERSION),
        missing_sector=sorted(r[0] for r in rows if unknown(r[2])),
        missing_cap=sorted(r[0] for r in rows if r[3] not in {'MICRO','SMALL','MID','LARGE'}),
        missing_target_nse_price=sorted(r[0] for r in rows if not r[4]),
        limitation='Classification coverage only; broad sector proxies still require source/membership review')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--date',required=True,type=date.fromisoformat)
    args=parser.parse_args()
    from dotenv import load_dotenv
    import os,psycopg2
    load_dotenv('.env.prod')
    conn=psycopg2.connect(os.environ['CC_POSTGRES_URL'],connect_timeout=10)
    try: result=audit(conn,args.date)
    finally: conn.close()
    print(json.dumps(result,indent=2))
    if not result['scored_nse_symbols'] or result['wrong_contract']:
        raise SystemExit('NOT READY: rebuild features, momentum, composite and Neon scores for this date')
    print('Data contract valid. Review excluded symbols above; this is not strategy profitability approval.')


if __name__=='__main__': main()
