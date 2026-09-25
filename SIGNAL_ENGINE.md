# investMITRA — Signal Engine Feature Reference

Updated: 25 September 2026. Verified against code commit `96e5817`.
Engine build: `2026-09-24-feature-completion1`. All times below are IST.

This document describes the implemented automatic-paper system. Changes to trading
parameters require testing and explicit sign-off. Updating this reference does not
change parameters or enable live trading.

## Operating mode

- **Current mode: `auto_paper`** — live market quotes, simulated orders and fills.
- After morning Kite login and engine startup, entries and exits run automatically;
  no approval is required for each simulated trade.
- The engine currently rejects modes other than `auto_paper`. Setting a live-mode
  environment variable is not a supported activation procedure.
- The universe includes micro, small, mid and large caps, subject to data, liquidity
  and signal checks. It is not restricted to micro/small caps.
- No deposit is needed for this paper trial. Profit on every trade is not promised.

## The 25 key features

| # | Feature | Implemented behaviour |
|---|---|---|
| 1 | Maximum ticket | ₹10,000; enforced independently by the executor after price/quantity adjustment. |
| 2 | Daily allowance | ₹35,000 cumulative entry allocation, reservations and provisional costs. Closing a trade does not replenish it. |
| 3 | Planned stop risk | Maximum ₹1,500 per trade for sizing; gaps, slippage and costs can make realised losses larger. |
| 4 | ATR target | Target at 3×ATR; initial stop at 1.5×ATR. The initial target/stop geometry is 2:1. |
| 5 | Minimum net screen | Estimated net must be at least ₹250 and twice estimated costs; executor rechecks after resizing. |
| 6 | Priority score | RVOL × absolute gap percentage × blended score / 100; minimum 3, or 5 during lunch. |
| 7 | Concurrent positions | Maximum three; executor includes outstanding entry orders in its admission checks. |
| 8 | RVOL floor | At least 5×; at least 8× during lunch. Discovery rules cannot bypass final entry checks. |
| 9 | Neutral-day short score | Underlying stock score ≥65 for every neutral-day short route, enforced in engine and executor. |
| 10 | Market breadth | Computed from NIFTY 50 advances/declines, with support evaluated in the trade direction. |
| 11 | Post-exit rescan | Completed filled trades request a rescan; simultaneous requests are combined. |
| 12 | 10 AM scan | Scheduled discovery for stocks whose volume builds later; all entry checks still apply. |
| 13 | All-day rescans | Event-driven scans during 9:35 AM–3 PM, subject to remaining budget and executor readiness. |
| 14 | Fresh NSE movers | `get_nse_gainers_losers()` plus the liquid-stock discovery universe; no nonexistent Kite gainers API. |
| 15 | Fresh-mover score gate | Actual score metadata required; blended score ≥55 before an entry offer. Discovery itself may include lower scores. |
| 16 | Neutral-day shorts | Falling, F&O-eligible stocks may qualify. Missing/failed eligibility lookup blocks short entries. |
| 17 | Telegram signal box | Executor-owned entry message includes direction, symbol/cap, resized quantity, entry limit, target, stop, gap, RVOL and blended score. |
| 18 | Neon mirror | Execution snapshot mirrored every 30 seconds and at shutdown, when the database is available. |
| 19 | Close detection | Polls executor snapshots independently of WebSocket ticks. |
| 20 | Concurrency protection | A shared state lock protects tick/scan evaluation; one scan runs at a time. |
| 21 | Weight caching | Opportunity weights loaded at startup; no database query for weights in tick callbacks. |
| 22 | Accurate scan counts | Queued candidates counted separately from submitted or filled trades; execution state determines positions and usage. |
| 23 | Executor ticket ceiling | `order_manager.py` bounds buys using entry limit and shorts using upper-circuit reservation; actual-fill overruns block further entries. |
| 24 | Correct rescan volume | Each stock uses its own fresh quote volume, open, previous close and average traded price. |
| 25 | Correct session | Actual IST time determines the session and its filters on every signal path. |

## Capital and risk limits

| Setting | Current value |
|---|---|
| Daily cumulative allowance | ₹35,000 |
| Ticket size | ₹1,000 minimum; ₹10,000 maximum |
| Maximum planned stop risk per trade | ₹1,500 |
| Maximum simultaneous positions | 3 |
| Daily loss threshold | ₹6,000, with open-risk checks before entry |
| Consecutive-loss entry limit | 2 |
| Executor provisional cost allowance | ₹80 per filled trade/reserved entry |

**There is no separately protected ₹5,000 reserve.** The earlier description
“₹30,000 deployable + ₹5,000 always reserved” did not match implementation.
Three simultaneous ₹10,000 tickets do not imply a three-trade daily maximum:
additional entries may fit only within the unspent cumulative daily allowance.
Do not delete/reset the execution journal to reclaim that allowance.

## Scoring and qualification

```text
Blended score = 0.40 × quality_score + 0.60 × opportunity_score
Minimum blended score = 55
```

`quality_score` and the underlying `investmitra_score` are distinct fields. The
neutral-day short floor of 65 applies to the latter; it does not replace the
blended-score gate.

Opportunity components cover gap strength, RVOL, VWAP, opening-range breakout,
gap holding, sector strength, breadth, market direction, key levels, sentiment,
bulk deals and pre-open conviction. Default weights total 1.0. Database-supplied
weights are validated individually but are not normalised to sum to 1.0.
The opportunity score is multiplied by 1.0 in momentum, 0.7 at lunch and 0.85 in
the afternoon. These are time-of-day adjustments, not proof of a flat market.

Weights are frozen within the running process. Weekly review can update weights
for a later start; a configuration fingerprint is attached to candidates. Keep
results from different configurations distinguishable during validation.

Additional entry requirements include:

- True gap calculated from today's open versus previous close; absolute hard
  floor 0.30%. Default session thresholds are 0.30% / 0.60% / 0.40%.
  Loaded configuration may change session thresholds, but not bypass the hard floor.
- `small_gap` and `exhaustion` classifications rejected. `fade_risk` has additional
  conditions and can be disabled by configuration.
- Five minutes of observed gap confirmation. A gap fill or an observation gap
  longer than 60 seconds resets confirmation; scans do not backdate the timer.
- Actual ATR, a volume baseline and fresh quote data; no fabricated ATR/previous close.
- Longs require price above VWAP; shorts require price below VWAP. Direction,
  stock-score, sizing, cost and portfolio checks also apply.
- RVOL uses cumulative volume divided by historical average daily volume scaled
  by elapsed trading-session time. It is not a historical time-of-day volume profile.
- The net-profit screen uses a conservative fraction of target profit less estimated
  costs. It is not statistical expectancy or a promise of that profit.

## Scan schedule and market direction

| Time | Behaviour |
|---|---|
| 9:15–9:30 AM | Capture opening prices and opening range. |
| From 9:31 AM | Morning dynamic discovery; entries still wait until 9:35 AM. |
| 9:35–11:30 AM | Momentum entries: RVOL ≥5×, priority ≥3. |
| From 10 AM | Late discovery scan, routed through the normal confirmation/entry checks. |
| 11:30 AM–1:30 PM | Lunch entries: RVOL ≥8×, priority ≥5, unless session disabled by configuration. |
| 1:30–3 PM | Afternoon entries: RVOL ≥5×, priority ≥3. |
| On completed trades | Request a post-exit scan; scans are spaced at least 30 seconds apart. |
| From 3 PM | Stop new entries and request square-off. |
| From 3:05 PM | Finish only after the executor confirms flat; otherwise continue monitoring. |

The maintenance worker checks roughly every two seconds. Network delays and
readiness checks mean scheduled times are earliest eligibility, not guaranteed
completion times. All-day rescanning is event-driven, not continuous full-market polling.

| Market direction | Entry routes |
|---|---|
| BULLISH | Qualifying longs. |
| NEUTRAL | Qualifying longs and F&O-eligible shorts with underlying stock score ≥65. |
| BEARISH | Qualifying dedicated shorts and eligible quality-stock short routes; the neutral-only 65 floor does not apply. |

## Execution, exits and recovery

The signal engine starts the integrated executor and its mirror worker. The
executor owns order intent, fills, quantities, risk accounting and recovery.
It journals intent before submission, reconciles uncertain responses and does
not blindly resend orders after timeouts. Protective orders follow confirmed
fills; cancellation is reconciled before replacement exits.

- At 1R, request a partial exit of half the filled quantity, rounded down.
- After partial completion, stop moves toward entry and trails in favourable steps.
  Breakeven at entry price does not cover costs or guarantee the exit price.
- Reaching the target requests closure of the remainder; a direct jump to target
  can close the whole position. Half at 1R plus half at 2R is about 1.5R gross,
  before costs, rather than a full-position 2R return.
- Dead-trade rule: after 40 minutes, a position without a completed partial exit
  can close when currently adverse to entry. It does not require 40 continuous
  minutes below entry. The existing 120-minute gap-loss condition also remains.
- Reversal exit evaluates sustained adverse movement relative to open and entry,
  symmetrically for longs and shorts.
- Stops, daily halts and session square-off remain active. Ctrl+C requests an
  orderly flatten-and-confirm shutdown; do not force-close the process mid-exit.

Quote requests share one paced client. Calls are serialized and the default
1.05-second spacing starts after completion, including failure. Waiting execution
requests take priority over scans. Network calls stay outside the engine state lock.

## Alerts and persistence

The detailed signal box **replaces** the brief executor entry-submission alert.
It reports the final resized quantity and distinguishes submitted-but-unfilled
orders from unknown submission outcomes awaiting reconciliation. Separate fill,
stop, partial-exit, closure, halt and session-completion alerts remain.

Telegram credentials/connectivity are required. Alert attempts are durably
deduplicated across restarts; uncertain delivery is not automatically retried,
so delivery is not guaranteed. The old manual “open Kite and place an order” box
is not used in automatic-paper mode.

| Store | Role |
|---|---|
| `data/execution_auto_paper.sqlite3` | Authoritative local paper execution journal. Keep it across restarts. |
| `investmitra.execution_sessions` in Neon | Account/mode/day-scoped reporting snapshot, mirrored every 30 seconds and at shutdown. |
| Legacy `engine_positions` / `trade_log` | Older engine records; do not mix these with automatic-paper execution P&L. |

`INVESTMITRA_EXECUTION_DB` can override the journal location. The summary reads
persisted execution limits; older journals without limits require verification.
The local journal remains authoritative if Neon mirroring is unavailable.

## Data readiness and separate announcement monitoring

Startup checks require prior regular NSE-session prices and scores, sufficient
historical price rows, today's market-index records, credentials and a usable
exchange holiday calendar. Missing current market context triggers an automatic
fetch attempt. Stale data or unavailable calendar validation blocks entries.
Special trading sessions or missing prior-year calendar coverage need explicit
support; the engine does not guess them.

Scheduled GitHub workflows:

| Scheduled IST time | Workflow |
|---|---|
| 8:30 PM, weekdays | Market Data — NSE + BSE Daily Pipeline |
| 10:30 PM, weekdays | Feature Engineering — Daily Price Features |

Confirm successful completion and data dates. Scheduling does not guarantee an
on-time run or fresh data; the startup checks are the final entry gate.

`fetch_nse_announcements.py --loop` is a **separate monitor**. Between 9 AM and
4 PM it fetches every 30 minutes, saves to Neon and prints console alerts. Outside
that window it sleeps and remains running. It does not automatically push fresh
exclusions into the already-running engine: announcement exclusions and context
are read at engine startup. Its watchlist is also loaded at monitor startup.

## Windows morning startup

Use the project root and the same configured Python environment. Keep the laptop
awake, powered and connected. Run only one trading engine. Do not separately start
`order_manager.py` or the legacy `broker_reconciler.py` for this automatic-paper mode.

**Window 2 — around 9 AM, before starting the engine:**

```powershell
cd C:\MITRAseries\investMITRA
$env:PYTHONUTF8 = "1"
python scripts\fetch_nse_announcements.py --loop
```

Let the first announcement fetch finish. Keep this window open for monitoring.

**Window 1 — around 9:05 AM:**

```powershell
& {
    $ErrorActionPreference = "Stop"
    Set-Location "C:\MITRAseries\investMITRA"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONUNBUFFERED = "1"
    $env:INVESTMITRA_EXECUTION_MODE = "auto_paper"
    $env:INVESTMITRA_LIVE_TRADING = "NO"

    $running = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(w)?([0-9.]*)?\.exe$' -and
        $_.CommandLine -match '(intraday_signals|order_manager)\.py'
    })
    if ($running.Count -gt 0) { throw "A trading process is already running." }

    python scripts\kite_login.py
    if ($LASTEXITCODE -ne 0) { throw "Kite login failed; engine not started." }

    python -u scripts\intraday_signals.py
    if ($LASTEXITCODE -ne 0) { throw "Engine stopped with an error; retain output." }
}
```

Complete browser login/2FA when prompted. The login script saves the token but
never starts the engine itself; the next command above starts it. Check for a
successful preflight and the expected build/mode. Do not bypass a failed check.
No money needs to be loaded for this trial.

After the engine confirms flat and finishes, run:

```powershell
python scripts\auto_paper_summary.py --date (Get-Date -Format 'yyyy-MM-dd')
```

The date command assumes the laptop is on India local time. Supply an explicit
`YYYY-MM-DD` for another session. Stop the separate announcement monitor with
Ctrl+C when finished.

## Verification and limits

After code updates, with trading stopped:

```powershell
python -m unittest -q test_auto_trading test_review_fixes
```

At code commit `96e5817`, all **94 offline tests** passed on Windows and Ubuntu,
Python 3.12 and 3.13. Simulated failure messages can appear during successful tests;
the final result must be `OK`. These tests use fake brokers and temporary journals,
not live Telegram, broker orders or the production database.

Paper fills remain simplified LTP-based simulations, not order-book/liquidity
modelling. Costs are estimates. Passing tests demonstrates checked implementation
behaviour, not profitability, optimal scoring weights or live-trading readiness.
Keep the agreed forward paper trial and evaluate net results, drawdown and market
conditions before a separate live activation decision.

## Key files and completed corrections

| File | Responsibility |
|---|---|
| `scripts/intraday_signals.py` | Signals, scoring, watchlists, scans, startup and worker coordination. |
| `scripts/order_manager.py` | Execution lifecycle, ticket/risk checks, recovery, alerts and Neon mirror. |
| `scripts/signal_runtime.py` | Shared quote pacing and exchange-session/data freshness helpers. |
| `scripts/auto_paper_summary.py` | Journal-based daily allocation and P&L summary. |
| `scripts/fetch_nse_announcements.py` | Separate announcement ingestion and console monitoring. |
| `intraday_signals.py` at repository root | Compatibility entry point delegating to the maintained scripts engine. |
| `test_auto_trading.py`, `test_review_fixes.py` | Offline regression coverage. |
| `REVIEW_FIXES.md` | Detailed correction notes and validation limits. |

| Merge | Completed work |
|---|---|
| `4414abb` / PR #1 | Scan/data/risk/recovery corrections, target execution and consolidated engine. |
| `a69ac04` / PR #2 | Explicit UTF-8 test reads and Windows/Python 3.13 CI coverage. |
| `96e5817` / PR #3 | Executor signal box, universal neutral-day short floor and quote-pacing correction. |

Earlier feature descriptions and changes remain available in Git history. This
reference supersedes the obsolete three-terminal startup, tick-dependent close
detection, separately protected ₹5,000 reserve and ₹300 minimum-net descriptions.
