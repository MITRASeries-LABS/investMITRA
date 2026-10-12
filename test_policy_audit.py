"""Policy consistency, isolated floor comparison and discovery accounting."""
import copy
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).parent/'scripts'))
import intraday_signals as engine
from signal_diagnostics import effective_weights, session_policy, score_gates
from order_manager import entry_policy_rejection, IST
from strategy_comparison import VARIANTS, variant_policy
from universe_coverage import UniverseCoverage
import test_auto_trading as fixtures


class PolicyAudit(unittest.TestCase):
    def test_deprecated_weights_do_not_change_active_policy(self):
        weights={'rvol_min_continuation':1.5,'tier1_rvol_min':1,'tier2_rvol_min':2,
                 'skip_fade_risk':False,'rvol_score':.13,'gap_threshold_momentum':.3}
        original=copy.deepcopy(weights);log=Mock()
        self.assertEqual(effective_weights(weights,log),{'rvol_score':.13,'gap_threshold_momentum':.3})
        self.assertEqual(weights,original);log.warning.assert_called_once()
        self.assertEqual([session_policy(s,.3)['rvol_min'] for s in ('momentum','choppy','afternoon')],[5,8,5])

    def test_no_warning_without_deprecated_settings(self):
        log=Mock();self.assertEqual(effective_weights({'rvol_score':.1},log),{'rvol_score':.1})
        log.warning.assert_not_called()

    def test_fade_is_rejected_even_with_high_volume_and_skip_false(self):
        s=dict(true_gap=1,final_score=90,direction='LONG',signal_weights={'skip_fade_risk':False},details=dict(gap_type='fade_risk',rvol=20))
        self.assertEqual(entry_policy_rejection(s),'blocked gap type: fade_risk')
        self.assertEqual(score_gates(s,datetime.now(IST),session_policy('momentum',.3))['gap_type']['status'],'FAIL')

    def test_engine_cannot_queue_fade_when_configuration_allows_it(self):
        f=fixtures.ExecutionTests();f.setUp();self.addCleanup(f.tearDown)
        e=f._make_signal_engine();e.signal_weights['skip_fade_risk']=False
        e._compute_opportunity_score=Mock(return_value=(90,dict(gap_type='fade_risk',rvol=20)))
        e._check_signal('A',100,1000,f.now,'momentum')
        self.assertEqual(e._decision_reason,'blocked gap type: fade_risk')
        self.assertTrue(f.manager.inbox.empty())

    def test_floor_variant_changes_only_floor_in_all_sessions(self):
        variant=VARIANTS['mean_gated_rvol2']
        self.assertEqual(variant['baseline'],'mean');self.assertTrue(variant['priority_gate'])
        for session,floor in [('momentum',2),('choppy',3),('afternoon',2)]:
            base=variant_policy(VARIANTS['mean_gated'],session,.3,3)
            trial=variant_policy(variant,session,.3,3)
            self.assertEqual(trial,dict(base,rvol_min=floor))

    def test_siemens_volume_example_changes_only_volume_gate(self):
        s=dict(true_gap=1.2,final_score=70,details=dict(gap_type='continuation',rvol=3.29))
        now=datetime.now(IST)
        control=score_gates(s,now,variant_policy(VARIANTS['mean_gated'],'momentum',.3,3))
        lower=score_gates(s,now,variant_policy(VARIANTS['mean_gated_rvol2'],'momentum',.3,3))
        self.assertEqual(control['rvol']['status'],'FAIL');self.assertEqual(lower['rvol']['status'],'PASS')
        self.assertEqual({k:v for k,v in control.items() if k!='rvol'},{k:v for k,v in lower.items() if k!='rvol'})

    def test_coverage_unique_counts_and_unobserved_are_distinct(self):
        cov=UniverseCoverage('2026-10-13',['A','B','C'])
        cov.add('catalog_eligible',['A','B']);cov.add('subscription_requested',['A'])
        cov.add('evaluated',['A','A']);cov.exclude('catalog:price_range',['C'])
        row=cov.snapshot()
        self.assertEqual(row['counts']['evaluated'],1)
        self.assertEqual(row['counts']['websocket_seen'],0)
        self.assertEqual(row['catalog_never_evaluated'],['B'])
        self.assertEqual(row['exclusion_counts']['catalog:price_range'],1)
        with tempfile.TemporaryDirectory() as root:
            saved=cov.save(root)
            self.assertEqual(json.loads(saved.read_text(encoding='utf-8'))['counts'],row['counts'])

    def test_unknown_startup_not_reported_as_known_zero(self):
        self.assertFalse(UniverseCoverage('2026-10-13').snapshot()['startup_known'])
        self.assertTrue(UniverseCoverage('2026-10-13',[]).snapshot()['startup_known'])

    def test_discovery_reports_top200_limit_and_twenty_selection(self):
        now=datetime(2026,10,13,9,35,tzinfo=IST)
        stocks=[dict(symbol=f'S{i}',avg_traded=10000000-i,market_cap_category='SMALL') for i in range(250)]
        cov=UniverseCoverage(now.date(),[s['symbol'] for s in stocks])
        kite=Mock();kite.quote.side_effect=lambda names:{s:dict(ohlc=dict(open=101,close=100),last_price=101,volume=10000) for s in names}
        with patch.object(engine,'UNIVERSE_COVERAGE',cov),patch.object(engine,'get_signal_catalog',return_value=stocks),patch.object(engine,'get_nse_gainers_losers',return_value=([],[])),patch.object(engine,'get_rvol_baseline',return_value={s['symbol']:10000 for s in stocks}),patch.object(engine,'datetime',SimpleNamespace(now=lambda tz:now)):
            found=engine.get_dynamic_gappers(kite,set(),{})
        report=cov.snapshot()
        self.assertEqual(len(found),20)
        self.assertEqual(report['counts']['discovery_considered'],200)
        self.assertEqual(report['counts']['quote_received'],200)
        self.assertEqual(report['exclusion_counts']['discovery:outside_top200_and_movers'],50)
        self.assertEqual(report['exclusion_counts']['discovery:twenty_per_scan_limit'],180)
        self.assertEqual(report['counts']['evaluated'],0)

    def test_evaluation_records_actual_engine_calls(self):
        f=fixtures.ExecutionTests();f.setUp();self.addCleanup(f.tearDown)
        e=f._make_signal_engine();e.universe_coverage=UniverseCoverage(f.now.date(),['A'])
        e._entry_blocked=True
        e._check_signal('A',100,1000,f.now,'momentum')
        self.assertEqual(e.universe_coverage.snapshot()['counts']['evaluated'],1)
        self.assertTrue(f.manager.inbox.empty())


if __name__=='__main__':unittest.main()
