# Canonical signal evidence release

## October 6 controlled strategy comparison

Build `2026-10-06-controlled-comparison1` adds prospective input capture and an
isolated, execution-aware comparison. Existing active entry thresholds and score
weights remain unchanged. It does not claim that the alternatives are profitable.

The existing minute diagnostics cannot reconstruct stops, partial exits or the
price path after rejected candidates. New runs therefore record every received
engine tick batch (including REST scan ticks), its pre-evaluation stock/index,
volume, quality and opening-range inputs, frozen configuration and source hashes.
This covers only the monitored universe. It cannot invent new discoveries that a
counterfactual post-exit scan might have found. Every simulated candidate is
rescored through `IntradayEngine`; each portfolio runs `AutoOrderManager` with
its actual sizing, partial/target/trailing/reversal/dead-trade/square-off rules,
capital reuse, priority queue and combined daily loss protection.

Five hypotheses are frozen before the next session:

| Variant | Daily volume baseline | Priority | Normal / lunch RVOL floor |
|---|---|---|---|
| mean_gated (control) | Mean | Existing gate and ranking | 5 / 8 |
| median_gated | Median | Existing gate and ranking | 5 / 8 |
| mean_rank | Mean | Ranking only | 5 / 8 |
| median_rank | Median | Ranking only | 5 / 8 |
| median_rank_rvol2 | Median | Ranking only | 2 / 3 |

The 2/3 floors are an explicit experimental hypothesis, not tuned or recommended
settings. Median substitution recalculates RVOL, gap classification, opportunity,
blended score and priority. It does not simply relabel an old gate outcome. All
variants keep sector/identity/freshness/direction/hold/profit/risk controls and the
same captured capital limits (currently Rs35,000, Rs10,000/ticket, three open
positions, Rs1,500 combined loss trigger). Stops and loss triggers cannot guarantee
a maximum realised loss when prices gap.

Prior NSE history is fetched once on the background capture thread with read-only
access and bounded timeouts. Mean/median use the same prior 30-calendar-day window.
Comparison requires at least ten valid sessions, no conflicting source days, and
a mean matching the engine's captured baseline. Missing or revised history is a
reported common-universe exclusion in every variant, with no median fallback.
Corporate actions are not automatically adjusted. The linear elapsed-time RVOL
model remains a separate unvalidated assumption.

Each variant runs two separate portfolios: base Rs80/filled trade + 5bps adverse
slippage per fill, and stress Rs160 + 10bps. Cost changes affect reservations and
risk admission as well as final P&L. Quotes must have actual exchange timestamps;
no fills at stale/missing prices, no synthetic circuit bounds for shorts, and no
invented end-of-tape exits. These are full-size observed-LTP fill assumptions, not
an order-book/liquidity model. The control is a simulation, not a guaranteed exact
reproduction of the live paper worker's timing. Reports include fills, mean closed
net, observed marked-equity drawdown, exposure, costs, coverage and full snapshots.
Unknown marks and unfinished positions make the portfolio comparison incomplete.

Capture starts automatically in auto-paper (`INVESTMITRA_COMPARISON_CAPTURE=0`
disables research only). It uses a bounded queue and a 512MiB per-run tape quota;
queue overflow, disk failure, source mismatch and abrupt shutdown invalidate the
tape. New files go under `data/comparison/`; nothing is uploaded periodically to
Neon, and no existing journal or shadow rows are rewritten. Source hashes are
newline-normalized for Windows/Linux. Replay requires the same captured source
revision. Preserve that checkout along with tapes if changing research code.

After the normal flat, stopped session and existing reports, the comparison runs
automatically in an isolated child process, with a ten-minute timeout. A timeout
retains the tape for a manual rerun. Outputs are printed and saved beside the tape
as `.comparison.json`. No extra morning terminal, server, pipeline rebuild or
schema migration is required for this change. Manual retry:

```powershell
python -X utf8 scripts/strategy_comparison.py --tape 'data/comparison/EXACT_FILE_FROM_LOG.sqlite3'
```

Validation protocol: October 6 remains hypothesis-development evidence. Keep this
five-variant family fixed for subsequent complete sessions. Compare paired daily
portfolio outcomes, not correlated tick counts; stratify later analysis by market
regime. Report missing inputs, no-trade days and cost stress. No variant is promoted
automatically. If evidence suggests a winner, freeze that choice and confirm it
on a further untouched period before changing the active engine. A two-week
observation window or a positive small sample is not proof of an edge.

## October 6 RVOL and priority audit

`scripts/rvol_priority_audit.py --date YYYY-MM-DD` now runs automatically with
the post-close reports, using the saved observer path and execution date. It
examines all saved minute samples, grouped by strategy and session; reports first
crossings per symbol of eight diagnostic gates; and compares four fixed cases:
baseline, without RVOL, without priority, and without both. Every other recorded
gate (including direction/VWAP) is retained, and comparisons use a common-known
sample. These are admission diagnostics, not backtests: continuous hold, sizing,
executions and capital competition are not replayed, and no earlier markout is
reused as P&L for a different timestamp. No trading threshold or score is changed.

The audit checks recorded volume operands, elapsed fraction, NSE provenance,
baseline dates and cumulative volume decreases. Optional `--verify-neon` opens a
read-only connection using existing `.env.prod`, recomputes prior-session daily
baselines, compares them with captured values and reports concentration/conflicts.
Current DB history may contain revisions. Consistent arithmetic is not proof of
exchange accuracy or an intraday seasonal volume model; no historical minute
volume or corporate-action adjustment is invented. No DB writes or data rebuild.

Read-only Neon investigation on October 6 confirmed NAUKRI, ROUTE, TIPSMUSIC and
INDUSTOWER have `Communication Services` in both company master and October 5
scores. The engine has no configured proxy for that broad sector. This is an
unmapped sector, not a missing score field. An index must be justified by the
business/constituents before changing this gate; this release does not assign
these disparate companies to an arbitrary index.

## October 6 decision visibility update

Build `2026-10-06-decision-visibility1` adds explicit early-return reasons (gap
confirmation loss/reset, recovery, cooldown, risk/capital/position limits,
session timing, fade-risk controls and rounded stop distance). Evaluation
exceptions retain their type in research evidence and still propagate.
Admission rules and order handling are unchanged.

The existing automatic decision report now lists up to ten closest candidates
per session by failed-gate count, using each stock's latest sampled scored
observation. This is not an entry recommendation or a full eligibility ranking.
Unknown evidence is listed separately, checks never reached remain NOT_EVALUATED,
and any later unscored outcome is displayed rather than hidden. No future price
outcome is used for ranking. Existing first-observation coverage totals remain.

The automatic shadow report reconciles mean gross markout, fixed cost allowance,
modelled slippage and net on the same completed cohort, for base and stress
assumptions. These are research costs, not confirmed broker charges.

No data rebuild, journal reset, schema migration or parameter changes are needed
for this update. Re-running reports can explain existing stored score/cost
evidence; previously generic rejection reasons cannot be reconstructed. Explicit
early-return reasons are collected only by the updated engine on future runs.

Prepared against main `e4842e0eba90b943920a7b252abbe0cf852eb94b`.
Build: `2026-10-05-canonical-evidence1`. Paper trading only.

## What this release repairs

| Area | Implemented behaviour | Verification |
|---|---|---|
| Runtime daily prices | NSE-only ATR/MA/history; ATR requires fourteen true ranges with previous closes; latest NSE date required | Actual production query exercised against dual-exchange fixtures |
| Pipeline features | One venue per ISIN history, NSE preferred; BSE-only instruments retain BSE; exact duplicate reruns collapse; conflicting revisions fail | Actual feature query and duplicate/conflict tests |
| Pipeline retries | Features and momentum recompute rather than reuse potentially stale same-date outputs | Old outputs cannot pass the contract check |
| Score provenance | `daily-venue-v2` propagated through features, momentum, composite and Neon; old/unverified scores cannot enter the catalog | Version validation plus dated pipeline tests |
| Discovery | Initial watchlist and dynamic scans share liquidity, event exclusions, score components and quality formula; missing scores never invented | Actual initial/dynamic route comparison |
| Classification | Cap aliases unified; unknown sectors reported, never guessed; ambiguous symbol joins excluded visibly | Micro-cap alias and coverage tests |
| Opening range | At most one recovery attempt per maintenance cycle; three attempts per symbol, five-minute retry spacing; exactly 09:15–09:29 completed minute candles | Missing, conflicting, wrong-day and future-window tests; lock and gap-hold tests |
| Context | Fresh Nifty spot refreshes domestic regime; startup global context remains explicitly labelled; timestamp-verified breadth refreshes with indices | Quote-age and breadth tests; existing context regressions |
| Admission | Engine and executor recheck market-context freshness; sector gate retained; common direction/VWAP function | Lifecycle and policy regressions |
| Rejections | Numeric score changes no longer defeat log throttling; eligibility still evaluated on each tick | Repeated changing-score test |
| Decision evidence | First evaluation per symbol/minute is written asynchronously to local research DB; all independent score gates recorded; downstream checks not reached remain NOT_EVALUATED | Storage, sampling, multi-failure and lifecycle tests |
| Research | Separate first-rejected/first-queued cohorts and frozen assumptions preserved; paired common-known gate ablations added | Unknown-evidence and interacting-gate tests |

The consistent quality baseline is the existing initial-watchlist formula:
`0.50 × daily_score + 20 × min(screen_count/20,1) + 15 × Piotroski/9 + 15 × Graham/4`.
Using this on later discoveries changes their scores intentionally. It is a consistency correction, not a demonstrated trading edge.

## Preserved controls and features

- Automatic paper mode; no live activation or real order path introduced.
- Reusable ₹35,000 equity ceiling; ₹1,000–₹10,000 tickets; maximum three simultaneous positions.
- ₹1,500 combined daily loss protection. Consecutive losses remain diagnostic.
- Existing risk sizing, charge reserves, executor resizing checks and candidate retention/priority ordering.
- Existing 55 blended floor, session gap thresholds, 5×/8× RVOL floors, 3/5 priority floors and session multipliers.
- Existing 40/60 blend, 1.5 ATR stop, 3 ATR target and ₹250 conservative target-profit screen.
- Continuous observed gap hold, quote freshness and confirmed F&O eligibility for shorts.
- Partial exit, trailing/target, reversal, dead trade, square-off, durable journal, recovery and no-blind-retry controls.
- Scheduled scans, post-exit detection/rescans, heartbeats, alerts, end-of-session Neon upload and automatic trade/shadow reports.
- Historical journals and shadow studies are not rewritten or relabelled.

Discovery now shares the initial catalog's average traded-value and event exclusions. It no longer bypasses these using a latest-day-only liquidity check. Both directional universes stay available when the fresh domestic regime changes. These changes must be compared as a new strategy version, not pooled with the previous version.

## Readiness and installation order

Do not install only `intraday_signals.py`. The new imports and score contract are coordinated dependencies.

1. Review the PR and Windows/Linux CI. Keep the running session unchanged.
2. After merge, run the complete dated pipeline for the latest completed NSE session: load prices, features, momentum, composite, Neon scores, and readiness validation. The existing score loader adds the nullable provenance column. Old rows remain unverified; it does not falsely backfill them.
3. Run `python -X utf8 scripts/signal_input_readiness.py --date YYYY-MM-DD` using that exact session date. This is read-only. Review missing sector/cap/NSE-price lists as well as the contract result.
4. Confirm the existing overnight readiness receipt passes. A current filename/date without the new contract is insufficient.
5. Sync the whole reviewed commit to the laptop after market; keep its journal and research DB. Run the complete regression command below.
6. Start paper mode through the existing startup procedure. Review INPUT COVERAGE and fresh index status before expecting admissions. Unknown classifications remain excluded by existing hard gates.

No database, pipeline, broker account or laptop has been modified by preparing this branch. Production classification coverage, historical-data entitlement and production source revisions still need the above checks. QPOWER's correct classification has not been verified and is not hardcoded.

If rollback is required, stop only after confirmed flat shutdown, preserve the databases and return to the prior reviewed commit. Extra nullable provenance columns and research tables are additive. Do not delete/reset the execution journal. Rebuilt source-correct scores will differ from old scores; rolling back code does not reproduce the old experiment.

## Reports

Existing end-of-session trade and shadow reports remain automatic. Two local research reports are added after shadow shutdown:

```
python -X utf8 scripts/signal_decision_report.py --date YYYY-MM-DD
python -X utf8 scripts/strategy_gate_study.py --start YYYY-MM-DD --end YYYY-MM-DD
```

Decision summaries prefer the first scored observation in each symbol/session; if scoring was never reached, they show the pre-score outcome. Repeated ticks do not become independent stocks. Evidence sampling is not a complete tick recording. Research rows remain in the existing local shadow database; this release does not add periodic Neon writes or claim that the new decision table is mirrored to Neon.

Gate alternatives are fixed: baseline; omit score floor; omit RVOL floor; omit priority floor; omit RVOL and priority floors; remove the session opportunity discount. They evaluate only seven independent gates on a common known sample. They do not change production settings or replay sizing, continuous holds, fills, partial exits, stops, costs actually incurred, capital competition or portfolio drawdown. Unknown evidence remains excluded and reported.

## Strategy validation after the repair

1. Freeze this corrected baseline and record the exact strategy/configuration ID.
2. Examine gate coverage and missing-data exclusions first. A low trade count alone is not a tuning objective.
3. Review the predefined gate ablations across separate sessions/regimes. Distinguish software/data defects from selection preferences.
4. Use trade-path evidence to separate weak entry movement from premature exits. Markouts are not full-strategy returns.
5. Only promote a proposed parameter change after a complete execution-aware comparison with realistic costs and a later untouched forward period. Report expectancy, cost sensitivity, drawdown, turnover, coverage and win rate; do not optimize win rate alone.

Remaining research limitations: linear elapsed-volume RVOL (not a learned time-of-day volume curve), unadjusted corporate-action history, broad sector proxies rather than verified index membership, and startup-only global context. Missing optional score components retain the documented baseline treatment; coverage must be understood before interpreting the score as stock quality. No profitability claim is made by this release.

The existing optional `weight_optimizer.py` had an unterminated notification string; this release repairs syntax only. It has not been executed or wired into any job. Do not run its automatic weight-application path during the fixed-parameter validation period.

## Regression command

The test runner requires `PyYAML`, `duckdb`, `pandas`, `kiteconnect`,
`psycopg2-binary` and `python-dotenv` (CI installs them).

```
python -X utf8 -m unittest -q test_auto_trading test_review_fixes test_capital_visibility test_shadow_validation test_neon_session_upload test_session_reports test_pipeline_dates test_overnight_readiness test_server_pipeline test_entry_evidence test_signal_contracts test_price_identity test_rvol_priority_audit test_strategy_comparison
```

The original 271 tests remain included. New tests execute production SQL and discovery functions, verify canonical source selection, quality/cap consistency, completed-candle recovery, freshness, multi-gate diagnostics and evidence persistence. Offline tests do not replace the production-data checks above.


## October 6 identity and coverage correction

The October 5 recovery completed but produced only 344 master-matched NSE scores,
all unclassified. The NSE full bhavcopy contains null ISINs; the Neon price loader
enriches these, but the feature lake reader previously discarded them. With
NSE-preferred history, this removed current rows for the normal NSE universe.

`price_identity.py` now resolves missing NSE ISINs from unique company-master
symbol mappings before venue selection. Native ISINs remain authoritative; BSE
symbols are never resolved through the NSE mapping. Ambiguous mappings are reported
and remain unresolved. The current master is used (as in the Neon loader); historic
symbol reuse/change without a native ISIN still needs a point-in-time security
master. No classification, exchange row or price is fabricated.

The contract advances to `daily-venue-v2`, forcing a dated rebuild. Readiness now
starts from current NSE prices joined to the company master, so absent scores
cannot vanish from the denominator. Every uniquely identified priced stock with a known master sector
and supported cap category requires a finite, current-contract score and a known
score sector. Zero eligible rows is a failure. Unknown classifications remain
reported exclusions and are not assigned a guessed sector. This is input coverage,
not a guarantee that any stock passes liquidity, event, sector-proxy or entry gates.

The coverage check runs after the Neon score load, before a readiness receipt is
written, in the laptop readiness CLI, and in engine preflight. A green workflow
alone is not enough without classified score coverage. Rebuild October 5 explicitly
even when launching after midnight on October 6. Preserve journals and paper mode.

## October 6 ambiguous identity containment

The subsequent production rebuild loaded 5,348 scores. Validation found 1,998
complete classified NSE stocks out of 1,999, with KIRLPNU split across two master
ISINs; four unclassified, unpriced symbols retained older contract rows. The legacy
Neon price loader used last-row-wins while feature enrichment rejected ambiguity.

Both loaders now use the same unique-symbol identity map. Native ISINs stay intact,
including in partially enriched input files. The entry catalog excludes symbols
with multiple master ISINs before initial or dynamic admission, requires an NSE
price on the score date and known master/score sectors, and still requires the
current score contract. Preflight and readiness visibly report ambiguous symbols
as quarantined. They cannot trade even if one duplicate has a valid score.

Readiness requires complete scores for the remaining unique, classified, currently
priced NSE universe. Missing or wrong-version scores in that universe still fail;
zero eligible coverage still fails. Old-version rows outside it remain reported
and excluded; they are not deleted or relabelled. This is per-symbol containment,
not a repaired security master: dated corporate-action identities and historical
price corrections remain outstanding. KIRLPNU stays excluded until resolved.

The already rebuilt outputs may be revalidated without recomputing them using
`python -X utf8 scripts/pipeline_readiness.py --date 2026-10-05 --mark-ready --check-only`
after syncing this release. This checks actual lake outputs and database coverage
before recording readiness. It neither rebuilds data nor bypasses failed checks.
