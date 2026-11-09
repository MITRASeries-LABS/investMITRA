"""Receipt invariants stay strict while live classification coverage is re-audited."""
from datetime import date
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent / 'scripts'))
import pipeline_readiness as readiness

DAY = date(2026, 11, 6)
OBJECTS = {'prices': 'price-etag', 'features': 'feature-etag',
           'momentum': 'momentum-etag', 'composite': 'composite-etag'}
COUNTS = {'prices': 2512, 'scores': 5133, 'history': 157514,
          'signal_ready': True, 'classified_expected': 1988, 'classified_eligible': 1988}


def receipt(counts=None):
    return dict(version=1, date=str(DAY), objects=OBJECTS,
                counts=dict(COUNTS if counts is None else counts))


class ReceiptCoverageTests(unittest.TestCase):
    def test_fully_covered_master_refresh_does_not_invalidate_unchanged_data(self):
        for size in (1987, 1989):
            current = dict(COUNTS, classified_expected=size, classified_eligible=size)
            with self.subTest(size=size):
                self.assertTrue(readiness.receipt_matches(receipt(), DAY, OBJECTS, current))

    def test_substantive_counts_and_unknown_keys_remain_bound(self):
        current = dict(COUNTS, classified_expected=1987, classified_eligible=1987)
        for key in ('prices', 'scores', 'history', 'unexpected_new_counter'):
            changed = dict(current, **{key: current.get(key, 0) + 1})
            with self.subTest(key=key):
                self.assertFalse(readiness.receipt_matches(receipt(), DAY, OBJECTS, changed))
        for key in ('prices', 'scores', 'history'):
            changed = dict(current)
            del changed[key]
            self.assertFalse(readiness.receipt_matches(receipt(), DAY, OBJECTS, changed))

    def test_receipt_date_version_objects_and_presence_remain_bound(self):
        current = dict(COUNTS, classified_expected=1987, classified_eligible=1987)
        for old in (None, dict(receipt(), version=0), dict(receipt(), date='2026-11-05'),
                    dict(receipt(), objects={}), dict(receipt(), counts=None)):
            self.assertFalse(readiness.receipt_matches(old, DAY, OBJECTS, current))
        self.assertFalse(readiness.receipt_matches(receipt(), DAY, {'prices': 'new'}, current))

    def test_coverage_drift_requires_valid_complete_nonempty_audits_on_both_sides(self):
        current = dict(COUNTS, classified_expected=1987, classified_eligible=1987)
        invalid = [dict(current, signal_ready=False), dict(current, signal_ready=1),
                   dict(current, classified_eligible=1986),
                   dict(current, classified_expected=0, classified_eligible=0),
                   dict(current, classified_expected=-1, classified_eligible=-1),
                   dict(current, classified_expected='1987', classified_eligible='1987'),
                   dict(current, classified_expected=True, classified_eligible=True)]
        for key in ('signal_ready', 'classified_expected', 'classified_eligible'):
            changed = dict(current)
            del changed[key]
            invalid.append(changed)
        for counts in invalid:
            with self.subTest(counts=counts):
                self.assertFalse(readiness.receipt_matches(receipt(), DAY, OBJECTS, counts))
                self.assertFalse(readiness.receipt_matches(receipt(counts), DAY, OBJECTS, COUNTS))

    def inspect(self, current, missing=(), verify_error=None):
        client, validator = Mock(), Mock(side_effect=verify_error)
        with patch.object(readiness, 'lake_client', return_value=client), \
                patch.object(readiness, 'database_counts', return_value=(current, list(missing))), \
                patch.object(readiness, 'lake_fingerprint', return_value=OBJECTS), \
                patch.object(readiness, 'read_receipt', return_value=receipt()), \
                patch.dict(sys.modules, {'verify_pipeline_data': SimpleNamespace(verify=validator)}):
            result = readiness.inspect(DAY, [DAY])
        client.put_object.assert_not_called()
        return result, validator

    def test_accepted_master_drift_still_validates_all_dated_producers_without_writes(self):
        current = dict(COUNTS, classified_expected=1987, classified_eligible=1987)
        result, validator = self.inspect(current)
        self.assertEqual(result, (True, []))
        self.assertEqual([call.args for call in validator.call_args_list],
                         [(DAY, stage) for stage in ('prices', 'features', 'momentum', 'composite')])

    def test_incomplete_live_coverage_or_missing_session_still_blocks(self):
        current = dict(COUNTS, classified_expected=1987, classified_eligible=1986, signal_ready=False)
        result, validator = self.inspect(current)
        self.assertEqual(result, (False, []))
        validator.assert_not_called()
        result, validator = self.inspect(COUNTS, missing=[DAY])
        self.assertEqual(result, (False, [DAY]))
        validator.assert_not_called()

    def test_invalid_producer_still_blocks_even_with_complete_live_coverage(self):
        current = dict(COUNTS, classified_expected=1987, classified_eligible=1987)
        result, validator = self.inspect(current, verify_error=RuntimeError('invalid data'))
        self.assertEqual(result, (False, []))
        self.assertEqual(validator.call_count, 1)


if __name__ == '__main__':
    unittest.main()
