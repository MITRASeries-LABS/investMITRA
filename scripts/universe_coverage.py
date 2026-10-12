"""Session-local discovery evidence, never consulted by trading policy."""
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import uuid


class UniverseCoverage:
    def __init__(self, day, startup_symbols=None):
        self.day = str(day)
        self.run_id = uuid.uuid4().hex[:12]
        self.lock = threading.Lock()
        self.stages = defaultdict(set)
        self.reasons = defaultdict(set)
        self.startup_known = startup_symbols is not None
        self.add('startup_valid', startup_symbols or ())

    def add(self, stage, symbols):
        with self.lock:
            self.stages[stage].update(symbols)

    def exclude(self, reason, symbols):
        with self.lock:
            self.reasons[reason].update(symbols)

    def snapshot(self):
        with self.lock:
            stages = {k: sorted(v) for k,v in self.stages.items()}
            reasons = {k: sorted(v) for k,v in self.reasons.items()}
        for stage in ('startup_valid','catalog_eligible','watchlist_selected','discovery_considered',
                      'quote_requested','quote_received','discovery_selected','subscription_requested',
                      'websocket_seen','evaluated'):
            stages.setdefault(stage, [])
        catalog = set(stages['catalog_eligible'])
        return dict(version='universe-coverage-v1',day=self.day,run_id=self.run_id,
            as_of=datetime.now(timezone.utc).isoformat(),startup_known=self.startup_known,
            counts={k:len(v) for k,v in stages.items()},symbols=stages,
            exclusions=reasons,exclusion_counts={k:len(v) for k,v in reasons.items()},
            catalog_never_evaluated=sorted(catalog-set(stages['evaluated'])),
            catalog_never_quote_requested=sorted(catalog-set(stages['quote_requested'])),
            caveat='Unique symbols observed during this process, not a strict funnel. Reasons can overlap or later resolve. Subscription requested is not exchange acknowledgement. Unknown startup coverage is not zero valid stocks.')

    def save(self, root='data/coverage'):
        report=self.snapshot()
        path=Path(root)/f'{self.day}_{self.run_id}.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(report,sort_keys=True,indent=2),encoding='utf-8')
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)
        return path
