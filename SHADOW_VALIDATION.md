# Forward candidate comparison

This experiment helps decide which proposed entry filters deserve further
testing. It does **not** change signals, blended scores, execution, the Rs35,000
capital model, or the Rs1,500 combined daily loss trigger. It does not promise
fewer losing trades. It starts collecting when the updated engine next starts;
today's missed entry conditions cannot be reconstructed from the P&L summary.

## Start and report

The normal `start_trading.ps1` starts the observer inside the engine automatically.
No extra terminal, database service, subscription or server is required. The
separate local file is `data/shadow_validation.sqlite3`; retain it across sessions.
Do not delete or replace the execution journal. Keep running `auto_paper`.

At orderly session shutdown the main engine automatically prints both the trade
summary and this shadow comparison, after confirmed square-off and writer
shutdown. No manual end-of-day report command is needed. Missing/failed data is
reported explicitly; a still-draining writer defers its report. Neither report is
emailed or uploaded to Neon by this integration. Use the commands below only to
rerun a report or aggregate several sessions.

```powershell
# From the repository root, after the session or while the engine runs:
python scripts\shadow_validation_report.py --date 2026-09-28
# Aggregate the new forward sample without mixing strategy fingerprints:
python scripts\shadow_validation_report.py --since 2026-09-28 --until 2026-10-09
```

Use actual observed session dates. The report is read-only. Missing files produce
an explicit error rather than an empty new study. The default observer is on;
`$env:INVESTMITRA_SHADOW_ENABLED = "0"` disables it at the next engine start.
`INVESTMITRA_SHADOW_DB` overrides the file; pass the same path using `--db` to report.

## Frozen experiment: markout30-v1

| Hypothesis | Pass condition | Unknown when |
| --- | --- | --- |
| VWAP extension | Price is on the favourable side of exchange ATP/VWAP and no more than 1 ATR away | ATR/VWAP missing, or only approximate tick-derived VWAP available |
| Opening breakout | Long above observed opening high; short below observed opening low | Opening observations lack coverage or a valid range |
| Both | Both hypotheses pass | Either input is unknown |

Opening coverage requires observations by 09:16, through 09:29, with no observed
inter-tick gap over 60 seconds during the opening session. It remains a sampled
opening range, not a guarantee of the exchange's exact high/low. A late start or
restart cannot invent that range. Missing inputs remain unknown.

The features include gap, RVOL, quality, opportunity, blended score, score
components, ATR, VWAP/source, opening range/coverage, session, sector, market
direction, market-cap category, last-trade timestamp and strategy fingerprint.
Startup breadth is retained **as startup context only**; it is not fresh breadth
at entry and is not used as an experimental filter. No additional API calls run
on the market callback. A future breadth experiment needs timestamp-verified,
fresh breadth observations first.

## Sample definition and outcomes

- Sample the first scored rejection and first queued candidate independently for
  each symbol/direction/strategy/day, from 09:35 up to but excluding 14:30.
- `QUEUED` means the engine offered a candidate, **not** an executor acceptance or
  confirmed fill. `SCORED_REJECTED` means it reached opportunity scoring and did
  not queue. Some have a specific rejection; later gates without a logged reason
  are labelled `later_strategy_or_execution_gate`. These cohorts must not be pooled
  or called actual trade win rates. They can include the same stock on the same day.
- Candidates rejected before scoring, periods when entries are blocked, stocks
  outside the monitored universe, and late entries are outside study coverage.
  This is a sampled study, not an audit of every reject or every market stock.
- Freeze the entry snapshot. Use the first fresh observed last-traded price at
  or after 30 minutes, within a 60-second grace window. The last trade must itself
  occur at/after that horizon; an old last price in a fresh packet cannot resolve
  it. Entry and exit last-trade timestamps must be at most 60 seconds old, never
  in the future. Unknown entry freshness and missing exit quotes stay excluded.
- The common hypothetical ticket is up to Rs10,000, whole shares, minimum
  Rs1,000. A share costing more than the maximum is unsizable.
- Base net subtracts Rs80 round-trip cost allowance plus 5 basis points per side
  of entry/exit notional. Stress net subtracts Rs160 plus 10 basis points per side.
  These are fixed **research assumptions**, not confirmed charges or expected
  slippage for a particular stock. Spread, depth, actual fills, exits, partials,
  stop losses, circuit limits, execution eligibility and capital competition are
  not simulated. Net here is a cost-adjusted price markout, not execution P&L.
- Duplicate ticks/restarts cannot rewrite entry features or resolved outcomes.
  Pending observations resume; overdue missing exits are marked `MISSING_EXIT`.
  An early shutdown before the horizon leaves unresolved rows pending until the
  next run expires them; the report does not treat them as wins or losses.

## How to read it

Each filter compares **the same complete observations with known inputs** before
and after filtering. It shows sample count, win rate, mean hypothetical net per
observation, missed winners, avoided losers, unknown coverage, and net sum under
both cost assumptions. Different strategy fingerprints and experiment versions
stay separate. Experimental rules cannot be changed under the same version.

Sequential sample drawdown is the peak-to-trough drop in cumulative hypothetical
outcomes ordered by exit time. Overlapping positions are not capital constrained;
**this is not portfolio drawdown**. Measure actual strategy P&L and drawdown from
confirmed execution records separately. Queue-level markouts cannot establish
that the full trading strategy would improve after adding the filter.

Writer errors and dropped queue events appear in engine logs and persisted
research health where storage permits. This is a bounded, independent writer;
research failure cannot place/cancel orders or mutate the execution journal.
Check coverage before drawing conclusions. If the disk itself fails, the health
write may fail too; console logs remain necessary evidence.

Keep these hypotheses unchanged through the forward observation period. Review
across multiple days and market regimes, then validate any selected filter on a
later untouched period with the actual execution/exit model. Multiple candidates
on one day are correlated; a large candidate count alone is not proof of an edge.
Do not select a filter merely because it excludes today's three losers or keeps
SHANTIGOLD. No production filter is automatically promoted by this report.
