"""Offline regression suite. No network, credentials, Telegram or Neon needed."""
import ast
import copy
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from order_manager import AutoOrderManager, Journal, KiteBroker, PaperBroker, IST, tick_round

class FakeBroker:
    mode = 'auto_paper'
    def __init__(self, clock):
        self.clock=clock; self.book=[]; self.price=100.; self.actions=[]
        self.entry_fill=None; self.exit_fill=None
        self.throw_after=False; self.throw_before=False; self.cancel_fills=False
        self.cancel_pending=False; self.fail_quotes=False; self.fail_orders=False; self.stale=False
    def quotes(self, symbols):
        if self.fail_quotes: raise ConnectionError('quotes unavailable')
        return {'NSE:'+s:dict(last_price=self.price,timestamp=self.clock()-timedelta(seconds=30 if self.stale else 0),upper_circuit_limit=120) for s in symbols}
    def orders(self):
        if self.fail_orders:raise ConnectionError('orders unavailable')
        return copy.deepcopy(self.book)
    def positions(self):
        result={}
        for o in self.book:
            k=(o['tradingsymbol'],o['exchange'],o['product'])
            result[k]=result.get(k,0)+o['filled_quantity']*(1 if o['transaction_type']=='BUY' else -1)
        return [dict(tradingsymbol=k[0],exchange=k[1],product=k[2],quantity=v) for k,v in result.items()]
    def place(self,params):
        self.actions.append(('place',copy.deepcopy(params)))
        if self.throw_before:self.throw_before=False;raise TimeoutError('request outcome unknown')
        entry=not any(o['tradingsymbol']==params['tradingsymbol'] for o in self.book)
        fill=self.entry_fill if entry else self.exit_fill
        if params['order_type']=='SL-M':fill=0
        elif fill is None:fill=params['quantity']
        status='TRIGGER PENDING' if params['order_type']=='SL-M' else ('COMPLETE' if fill==params['quantity'] else 'CANCELLED')
        o=dict(params,order_id=str(len(self.book)+1),status=status,filled_quantity=fill,average_price=self.price if fill else 0)
        self.book.append(o)
        if self.throw_after:self.throw_after=False;raise TimeoutError('accepted but response lost')
        return o['order_id']
    def cancel(self,oid):
        self.actions.append(('cancel',oid));o=next(o for o in self.book if o['order_id']==oid)
        if self.cancel_pending:return
        if self.cancel_fills:o.update(status='COMPLETE',filled_quantity=o['quantity'],average_price=98)
        elif o['status'] not in {'COMPLETE','CANCELLED','REJECTED'}:o['status']='CANCELLED'
    def modify(self,oid,**params):
        self.actions.append(('modify',oid));next(o for o in self.book if o['order_id']==oid).update(params)

class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'execution.sqlite3'
        self.now=datetime(2026,9,10,10,0,tzinfo=IST);self.clock=lambda:self.now
        self.broker=FakeBroker(self.clock)
        self.meta=[dict(tradingsymbol=s,exchange='NSE',segment='NSE',tick_size=.05) for s in ('A','B','C')]
        self.journal=Journal(self.path,'test','auto_paper');self.alerts=[]
        self.manager=self.make_manager();self.manager.step()
    def tearDown(self):self.journal.close();self.tmp.cleanup()
    def make_manager(self):return AutoOrderManager(self.broker,self.journal,self.meta,clock=self.clock,alerts=self.alerts.append)
    def signal(self,symbol='A',qty=20,direction='LONG'):
        return dict(symbol=symbol,entry=100,position_size=qty,direction=direction,stoploss=98 if direction=='LONG' else 102,target=103,today_open=100,offered_at=self.now.timestamp(),entry_at=self.now)
    def enter(self,**kwargs):self.manager.offer(self.signal(**kwargs));self.manager.step();self.manager.step()
    def restart(self):
        self.journal.close();self.journal=Journal(self.path,'test','auto_paper');self.manager=self.make_manager();self.manager.step()
    def test_default_live_gate(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(RuntimeError):KiteBroker(object(),'test')
    def test_entry_submission_is_not_fill(self):
        self.manager.offer(self.signal());self.manager.step()
        self.assertEqual(self.manager.snapshot()['trades']['A']['orders'][0]['filled'],0)
        self.manager.step();t=self.manager.state['trades']['A']
        self.assertEqual(t['orders'][0]['filled'],20);self.assertEqual(t['orders'][1]['qty'],20)
    def test_partial_entry_only_protects_fills(self):
        self.broker.entry_fill=7;self.enter()
        self.assertEqual(self.manager.state['trades']['A']['orders'][1]['qty'],7)
        self.assertEqual(self.manager.snapshot()['tickets'],700)
    def test_unfilled_entry_no_stop_no_pnl(self):
        self.broker.entry_fill=0;self.enter()
        self.assertEqual(len(self.broker.book),1);self.assertEqual(self.manager.snapshot()['net'],0)
        self.assertEqual(self.manager.snapshot()['remaining'],25000)
    def test_daily_budget_reserves_charges(self):
        self.enter(qty=250);self.assertLessEqual(self.manager._budget_used(),25000)
        self.assertLess(self.broker.book[0]['quantity'],250)
        self.manager.offer(self.signal('B'));self.manager.step();self.assertNotIn('B',self.manager.state['trades'])
    def test_minimum_ticket(self):self.enter(qty=9);self.assertEqual(self.broker.book,[])
    def test_short_budget_and_exit_direction(self):
        self.enter(qty=250,direction='SHORT');t=self.manager.state['trades']['A']
        self.assertEqual(t['reservation_price'],120);self.assertLessEqual(t['orders'][0]['qty']*120+80,25000)
        self.assertEqual(t['orders'][1]['params']['transaction_type'],'BUY')
    def test_cancel_fill_race_does_not_reverse(self):
        self.enter();self.broker.price=103;self.broker.cancel_fills=True
        self.manager.step();self.manager.step()
        self.assertEqual(len(self.broker.book),2);self.assertTrue(self.manager.snapshot()['flat'])
        self.assertEqual(self.manager.snapshot()['gross'],-40)
    def test_cancel_ack_does_not_authorize_replacement(self):
        self.enter();self.broker.price=103;self.broker.cancel_pending=True
        for _ in range(3):self.manager.step()
        self.assertEqual(len(self.broker.book),2);self.assertFalse(self.manager.state['trades']['A']['partial_done'])
    def test_partial_fill_breakeven_trailing_original_risk(self):
        self.enter();self.broker.price=103
        for _ in range(3):self.manager.step()
        t=self.manager.state['trades']['A']
        self.assertTrue(t['partial_done']);self.assertEqual(t['initial_risk'],2);self.assertEqual(t['stop'],100)
        self.assertEqual(self.manager.snapshot()['gross'],30)
        self.broker.price=106;self.manager.step();self.assertEqual(t['stop'],102);self.assertEqual(t['initial_risk'],2)
    def test_timeout_after_accept_reconciles_tag_once(self):
        self.broker.throw_after=True;self.enter()
        self.assertEqual(len([o for o in self.broker.book if o['transaction_type']=='BUY']),1)
        self.assertEqual(self.manager.state['trades']['A']['orders'][0]['filled'],20)
    def test_unknown_absent_order_is_never_resubmitted(self):
        self.broker.throw_before=True;self.enter()
        for _ in range(3):self.manager.step()
        self.assertFalse(self.manager.snapshot()['ready']);self.assertEqual(len(self.broker.actions),1)
    def test_restart_existing_stop_not_duplicated(self):
        self.enter();before=self.manager.snapshot()['remaining'];self.restart()
        self.assertEqual(self.manager.snapshot()['remaining'],before);self.assertEqual(len(self.broker.book),2)
    def test_closed_ticket_stays_spent_after_restart(self):
        self.enter();before=self.manager.snapshot()['remaining'];self.manager.request_flatten()
        for _ in range(4):self.manager.step()
        self.assertTrue(self.manager.snapshot()['flat']);self.restart()
        self.assertEqual(self.manager.snapshot()['remaining'],before)
    def test_timer_squareoff_without_quotes(self):
        self.enter();self.now=self.now.replace(hour=15,minute=0);self.broker.fail_quotes=True
        for _ in range(4):self.manager.step()
        self.assertTrue(self.manager.snapshot()['flat']);self.assertEqual(self.broker.book[-1]['order_type'],'MARKET')
    def test_broker_failure_not_false_close(self):
        self.enter();self.broker.fail_orders=True;self.manager.request_flatten();self.manager.step()
        self.assertFalse(self.manager.snapshot()['flat']);self.assertFalse(self.manager.snapshot()['ready'])
        self.assertEqual(len(self.broker.book),2)
    def test_unrelated_delivery_position_untouched(self):
        self.enter();self.broker.book.append(dict(tradingsymbol='OTHER',exchange='NSE',product='CNC',transaction_type='BUY',quantity=100,filled_quantity=100,average_price=50,status='COMPLETE',tag='manual',order_id='outside'))
        self.manager.request_flatten()
        for _ in range(4):self.manager.step()
        self.assertFalse(any(a[0]=='cancel' and a[1]=='outside' for a in self.broker.actions))
        self.assertFalse(any(a[0]=='place' and a[1]['tradingsymbol']=='OTHER' for a in self.broker.actions))
    def test_manual_intraday_blocks_new_entries(self):
        self.broker.book.append(dict(tradingsymbol='OTHER',exchange='NSE',product='MIS',transaction_type='BUY',quantity=1,filled_quantity=1,average_price=50,status='COMPLETE',tag='manual',order_id='outside'))
        self.manager.step();self.enter();self.assertNotIn('A',self.manager.state['trades'])
    def test_stale_quote_blocks_entry(self):self.broker.stale=True;self.enter();self.assertEqual(self.broker.book,[])
    def test_tick_precision(self):
        self.assertEqual(tick_round(100.011,.05,True),100.05);self.assertEqual(tick_round(100.019,.01,False),100.01)
    def test_disk_failure_before_submit(self):
        original=self.journal.save
        def fail():
            if self.manager.state['trades']:raise OSError('disk full')
            original()
        self.journal.save=fail;self.manager.offer(self.signal());self.manager.step();self.assertEqual(self.broker.book,[])
    def test_lost_journal_blocks_against_tagged_broker_orders(self):
        self.enter();self.manager.state['trades']={};self.manager.step();self.assertFalse(self.manager.snapshot()['ready'])
    def test_real_signal_method_reaches_executor_without_paper_fill(self):
        from collections import defaultdict
        import logging
        from datetime import date
        tree=ast.parse(Path(__file__).with_name('intraday_signals.py').read_text(encoding='utf-8'))
        ns=dict(datetime=datetime,date=date,IST=IST,defaultdict=defaultdict,logger=logging.getLogger('integration'),
                EXECUTION_MODE='auto_paper',PAPER_TRADING=True)
        # Import only literals, arithmetic assignments and the actual pure
        # classes/functions; exclude all production imports and startup calls.
        for node in tree.body:
            if isinstance(node,ast.Assign) and not any(isinstance(x,ast.Call) for x in ast.walk(node)):
                try:exec(compile(ast.Module(body=[node],type_ignores=[]),'config','exec'),ns)
                except NameError:pass
        selected=[n for n in tree.body if (isinstance(n,ast.ClassDef) and n.name in {'DailyRiskManager','IntradayEngine'})
                  or (isinstance(n,ast.FunctionDef) and n.name=='estimate_costs')]
        exec(compile(ast.Module(body=selected,type_ignores=[]),'engine','exec'),ns)
        stock=dict(symbol='A',investmitra_score=90,quality_score=90,market_cap_category='MID')
        engine=ns['IntradayEngine']([stock],[],{'A':1},{'A':98},'NEUTRAL',{'vix_signal':'CALM'},
                                    {'A':1000},{'A':{'atr14':2}},{},{})
        engine.execution=self.manager;engine.today_open['A']=100;engine.vwap['A']=99
        engine.gap_first_seen['A']=self.now-timedelta(minutes=6);engine.gap_direction['A']='LONG'
        engine._compute_opportunity_score=lambda *args:(90,{'gap_type':'continuation','rvol':5})
        def forbidden(*args,**kwargs):raise AssertionError('Legacy simulated position mutated in automatic mode')
        engine.risk.open_position=forbidden
        engine._check_signal('A',100,1000,self.now,'momentum')
        self.assertFalse(engine.signals);self.assertFalse(engine.risk.positions)
        self.manager.step();self.manager.step()
        self.assertGreater(self.manager.snapshot()['tickets'],0)
        self.assertLessEqual(self.manager._budget_used(),25000)

    def test_engine_candidate_routed_before_paper_mutation(self):
        tree=ast.parse(Path(__file__).with_name('intraday_signals.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='IntradayEngine')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_check_signal')
        src=ast.unparse(method)
        self.assertLess(src.index('self.execution.offer(candidate)'),src.index('self.risk.open_position('))
    def test_unprotected_partial_entry_blocks_other_candidates(self):
        self.manager.offer(self.signal());self.manager.step()
        self.broker.book[0].update(status='OPEN',filled_quantity=7,average_price=100)
        self.broker.cancel_pending=True
        self.manager.step();self.manager.offer(self.signal('B'));self.manager.step()
        self.assertFalse(self.manager.snapshot()['ready'])
        self.assertNotIn('B',self.manager.state['trades'])
    def test_broker_read_failure_cannot_confirm_flat_even_if_journal_empty(self):
        self.broker.fail_orders=True;self.manager.step()
        self.assertFalse(self.manager.snapshot()['flat'])

    def test_rejected_stop_attempts_owned_exit(self):
        self.enter()
        self.broker.book[1]['status']='REJECTED'
        self.manager.step();self.manager.step();self.manager.step()
        self.assertTrue(self.manager.snapshot()['flat'])
        self.assertTrue(self.manager.state['halt'])
    def test_partial_exit_zero_fill_does_not_book_profit(self):
        self.enter();self.broker.price=103;self.broker.exit_fill=0
        self.manager.step();self.manager.step()
        self.assertEqual(self.manager.snapshot()['gross'],0)
        self.assertFalse(self.manager.state['trades']['A']['partial_done'])
    def test_partial_stop_fill_closes_only_residual(self):
        self.enter();stop=self.broker.book[1]
        stop.update(status='CANCELLED',filled_quantity=7,average_price=98)
        self.manager.step();self.manager.step()
        self.assertEqual(self.broker.book[-1]['quantity'],13)
        self.assertTrue(self.manager.snapshot()['flat'])
    def test_single_share_never_submits_zero_partial(self):
        self.broker.price=1200
        sig=self.signal(qty=1);sig.update(entry=1200,stoploss=1190,target=1215,today_open=1200)
        self.manager.offer(sig);self.manager.step();self.manager.step()
        self.broker.price=1215;self.manager.step()
        self.assertTrue(all(o['quantity']>0 for o in self.broker.book))
        self.assertEqual(len(self.broker.book),2)
    def test_account_or_mode_mismatch_rejected(self):
        self.journal.close()
        with self.assertRaises(RuntimeError):Journal(self.path,'wrong-account','auto_paper')
        self.journal=Journal(self.path,'test','auto_paper')
    def test_process_lock_prevents_second_local_worker(self):
        with self.assertRaises((BlockingIOError,OSError)):Journal(self.path,'test','auto_paper')
    def test_missing_quote_does_not_duplicate_existing_stop(self):
        self.enter();self.broker.fail_quotes=True
        self.manager.step();self.assertEqual(len(self.broker.book),2)
    def test_prior_day_unfinished_position_blocks_reset(self):
        self.enter();self.now+=timedelta(days=1)
        with self.assertRaises(RuntimeError):self.make_manager()
    def test_flat_prior_day_archived_before_reset(self):
        self.enter();self.manager.request_flatten()
        for _ in range(4):self.manager.step()
        self.now+=timedelta(days=1)
        # Kite's order book resets each day; model that here.
        self.broker.book=[]
        m=self.make_manager();m.step()
        self.assertEqual(m.snapshot()['remaining'],25000)
        self.assertIsNotNone(self.journal.db.execute('SELECT body FROM history WHERE day=?',('2026-09-10',)).fetchone())
    def test_failed_full_exits_rearm_protection_and_alert(self):
        self.enter();self.broker.exit_fill=0;self.manager.request_flatten()
        for _ in range(8):self.manager.step()
        self.assertFalse(self.manager.snapshot()['flat'])
        self.assertTrue(self.manager.state['trades']['A'].get('manual_required'))
        self.assertEqual(self.broker.book[-1]['order_type'],'SL-M')
        self.assertEqual(self.broker.book[-1]['status'],'TRIGGER PENDING')

    def test_simulator_only_uses_quote_api(self):
        class QuoteOnly:
            def quote(inner,symbols):return self.broker.quotes([s[4:] for s in symbols])
        paper=PaperBroker(QuoteOnly(),self.journal);m=AutoOrderManager(paper,self.journal,self.meta,clock=self.clock,alerts=self.alerts.append)
        m.step();m.offer(self.signal());m.step();m.step()
        self.assertEqual(m.snapshot()['tickets'],2000);self.assertEqual(self.broker.book,[])

if __name__=='__main__':unittest.main()
