# September 24 automatic-paper review fixes

## Follow-up: 25-feature checklist

- The executor entry alert now includes direction, symbol/cap category, actual
  resized quantity, entry limit, target, stop, gap, RVOL and blended score. It
  replaces the terse entry-submission alert, so it is one executor-owned message.
  Unknown submission outcomes are labelled pending reconciliation, never as fills.
  Existing durable alert deduplication and separate fill/stop/exit alerts remain.
- Every NEUTRAL-day short now requires stock score >=65, including the dedicated
  low-score and direction-override routes. Both the engine and executor enforce
  this rule. Short offers with missing market context are rejected. This uses the
  underlying stock score; the separate blended-score >=55 gate still applies.
  BEARISH-day routing and F&O eligibility requirements are retained.
- The Rs10,000 executor ticket ceiling was already included in the first patch.
  Capital limits, scoring weights and paper-only mode are unchanged.

This change addresses the code review of `5e10bac`. It does not enable live orders.
`SIGNAL_ENGINE.md` remains unchanged as requested; this document records the actual
corrected behaviour and the remaining validation limits.

## Execution and capital

- Daily cumulative allowance remains **Rs35,000**, including order reservations and
  provisional costs. Closing a trade releases a position slot, not daily allocation.
- Tickets are **Rs1,000 minimum / Rs10,000 maximum**. The executor now limits quantity
  using the buy limit price or the short's upper-circuit reservation, independently
  of engine sizing. The configured stop-risk, loss and concurrency limits are retained.
- The Rs5,000 mentioned in the old comment is not a separately protected reserve.
  No additional reserve or increase in the approved daily allowance is introduced.
- The executor rechecks the target-profit screen after changing the order quantity.
  This screen is a heuristic; it is not estimated statistical expectancy.
- At the signal target, the executor cancels/reconciles the existing stop and exits
  the remaining filled quantity. Existing partial-fill and cancellation-race handling
  still applies. A direct move to target exits the full position. After half exits
  at 1R and the remainder at 2R, gross payoff is approximately **1.5R**, not 2R.
- The 40-minute rule is retained: a non-partial position currently below entry in
  its trade direction can exit after 40 minutes. It does not require 40 uninterrupted
  minutes below entry. The existing 120-minute gap-loss rule also remains.
- Reversal exits now work for shorts as well as longs.
- Execution limits are stored in the journal and mirrored with the snapshot. The
  summary uses those limits unless explicitly overridden. For older journals without
  limits, defaults are Rs35,000/Rs80 and a warning requests verification. Use
  `--daily-cap 25000` for an older Rs25,000 session when appropriate.

## Signals and rescans

- One maintenance worker handles morning discovery (from 09:31), the 10 AM scan,
  and post-exit scans. Close detection polls execution snapshots independently of
  WebSocket ticks. Requests coalesce and scans are at least 30 seconds apart.
- Every candidate uses its own quote volume, open, previous close and average
  traded price through the normal signal path. New stocks require scored metadata,
  a valid ATR, volume baseline and instrument token. NSE discovery uses the existing
  NSE helper rather than the nonexistent `KiteConnect.gainers_losers()` method.
- No fabricated previous close or backdated confirmation. A new symbol must build
  five minutes of observed confirmation. Gap fills and observation gaps longer than
  60 seconds reset the hold. Reconnection resubscribes the expanded token map.
- Entry offers begin at 09:35 and stop at 15:00. Actual session thresholds apply to
  all paths. Lunch requires RVOL >=8 and priority >=5 using the actual blended score;
  other sessions retain RVOL >=5 and priority >=3. Tier 2 no longer bypasses RVOL 5.
- Quality shorts on NEUTRAL days require score >=65 and membership in the F&O
  strategy universe. F&O eligibility is cached at startup; lookup failure blocks
  short entries. Opportunity components now evaluate support for the trade direction.
- Engine state is locked during tick/scan evaluation, but network and database
  discovery happen outside that lock. All Kite quote calls share pacing, with
  execution requests prioritised over scans.
- Signal weights are loaded once at startup, rather than on every tick. Each
  candidate records the weights and a configuration fingerprint. A restart or a
  later session may load newer weights; do not pool different fingerprints as one
  unchanged validation run. This patch does not disable the weekly review workflow.

## Data, tests and rollout

- Startup requires scores and prices for the previous regular NSE session, enough
  historical price rows and current-day indices. The official NSE cash-market holiday
  calendar determines that session. Unknown/unavailable calendars block startup;
  special trading sessions and missing prior-year calendar coverage require explicit
  support rather than a guess. Existing positions remain owned by the independent
  executor while preflight runs.
- Intraday loaders exclude today's incomplete score/price snapshots. No automatic
  schema changes or live Neon/broker writes were performed while developing this patch.
- The root engine file delegates to `scripts/intraday_signals.py`; regression tests
  explicitly read that maintained file. Run on Python 3.12:

  ```powershell
  python -m unittest -v test_auto_trading test_review_fixes
  ```

- The PR workflow runs only offline tests, without trading credentials or secrets.
- Install the complete change together between sessions, after confirming the
  executor is flat. Retain `data/execution_auto_paper.sqlite3`; do not delete/reset
  the journal to reclaim the day's allowance. Changes are proposed on a branch and
  do not alter an already running Windows process.
- Paper fills remain simplified LTP fills, not an order-book/liquidity simulation.
  Costs are estimates. Passing these regressions establishes implementation checks,
  not profitability. Forward-test this version separately from earlier runs.
- A timezone/date correction cannot guarantee punctual GitHub Actions scheduling.
  This patch blocks stale inputs; it does not claim to eliminate scheduler delays.
