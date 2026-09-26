# Capital reuse and terminal visibility update

Prepared for installation after the market session. Build:
`2026-09-26-auto-reports1`. The existing laptop process is unaffected by
preparing this branch. This update keeps `auto_paper`; it does not activate live orders.

## What changes

- New sessions use a ₹35,000 reusable capital ceiling. Confirmed exit fills release
  entry-price exposure, with realised losses, costs and pending commitments retained.
- Reports distinguish turnover from capital in use. ₹1,000–₹10,000 tickets and
  three simultaneous positions remain. The user-approved **₹1,500 combined daily
  loss threshold** replaces ₹6,000 and the two-loss restriction. Loss streaks are
  diagnostic only; entries still require room for costs and all planned open risk.
  The threshold triggers a persistent halt and square-off, not a guaranteed final
  loss ceiling. Slippage/gaps can exceed it. The running laptop retains old settings
  until this build is installed and started.
- Entries, confirmed fills and exits appear in the terminal independently of Telegram.
  Closure messages include quantities, average prices, reason and provisional net.
- SHORT routing and repeated priority checks no longer flood INFO logs. A queued
  candidate is not marked as traded; executor-rejected candidates can qualify later.
- Post-exit discovery works independently of ticks, and periodic discovery runs
  every five minutes during the entry window. Blocked scans explain why.
- A minute heartbeat shows stream/executor ages, capital and entry eligibility.
  WebSocket errors are visible. Quote freshness is labelled as of the last cycle.
- Neon execution uploads happen at orderly session shutdown only, after the
  executor stops. There is no 30-second intraday upload thread. A failed upload
  gets two retries (after 2 and 5 seconds), then an explicit failure message;
  the local execution journal remains intact. Missing connection configuration
  is reported rather than silently skipped. This changes execution reporting
  only; shadow research stays local.
- The detailed trade summary and shadow comparison now print automatically at
  orderly shutdown after confirmed square-off and worker stop. They use the
  session's saved date and configured local paths. Upload/report failures cannot
  skip the remaining report or journal cleanup; unavailable data is labelled.
- An isolated shadow observer records scored rejections and queued candidates for
  fixed 30-minute markout comparisons. It tests VWAP extension and opening-range
  hypotheses without changing entry decisions. See [SHADOW_VALIDATION.md](SHADOW_VALIDATION.md)
  for coverage, cost assumptions, limitations and the report command.

## After-market installation from the prepared branch

First let the engine finish and confirm it reports `flat=True`. Preserve the
execution journal. These commands do not start the engine or change execution mode.
Git will refuse the switch if local edits would be overwritten; preserve those edits
and inspect the conflict rather than resetting or cleaning them away.

```powershell
cd C:\MITRAseries\investMITRA
git fetch origin codex/automatic-session-reports
if ($LASTEXITCODE -ne 0) { throw "Fetch failed" }
git switch --track origin/codex/automatic-session-reports
if ($LASTEXITCODE -ne 0) { throw "Switch needs review; keep local edits and journal" }
python -X utf8 -m unittest -q test_auto_trading test_review_fixes test_capital_visibility test_shadow_validation test_neon_session_upload test_session_reports test_pipeline_dates test_overnight_readiness test_server_pipeline
if ($LASTEXITCODE -ne 0) { throw "Tests failed; do not start the engine" }
git log -1 --oneline
```

If the branch is already checked out, use `git pull --ff-only` instead of creating
it again. After this PR is merged, normal `main` sync can be used instead.

The next session starts through the existing login/start procedure. Expect the
new build marker and `capital_model=reusable_equity_v1` in execution status.
An old journal restarted on the **same day** keeps its legacy cumulative model;
it is explicitly labelled. A fresh session switches only after flat-state validation
and archives the preceding session. No historical P&L is rewritten. Do not edit the
journal or reset counters to force the new policy into an active session.

## Verification scope

Offline tests cover capital release after full/partial fills, costs and losses,
unknown/pending reservations, profit ceiling, short bounds, restart/model transition,
unchanged risk blocks, repeated candidates without duplicate orders, independent
console notifications, polling-based close detection, periodic scans and heartbeat.
They simulate broker behaviour; they do not prove live fill quality or profitability.
