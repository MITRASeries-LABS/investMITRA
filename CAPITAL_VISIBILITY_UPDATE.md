# Capital reuse and terminal visibility update

Prepared for installation after the market session. Build:
`2026-09-25-capital-visibility1`. The existing laptop process is unaffected by
preparing this branch. This update keeps `auto_paper`; it does not activate live orders.

## What changes

- New sessions use a ₹35,000 reusable capital ceiling. Confirmed exit fills release
  entry-price exposure, with realised losses, costs and pending commitments retained.
- Reports distinguish turnover from capital in use. ₹1,000–₹10,000 tickets, ₹1,500
  planned risk, three simultaneous positions, the two-loss rule and daily-loss
  threshold remain unchanged. Released capital does not override risk blocks.
- Entries, confirmed fills and exits appear in the terminal independently of Telegram.
  Closure messages include quantities, average prices, reason and provisional net.
- SHORT routing and repeated priority checks no longer flood INFO logs. A queued
  candidate is not marked as traded; executor-rejected candidates can qualify later.
- Post-exit discovery works independently of ticks, and periodic discovery runs
  every five minutes during the entry window. Blocked scans explain why.
- A minute heartbeat shows stream/executor ages, capital and entry eligibility.
  WebSocket errors are visible. Quote freshness is labelled as of the last cycle.

## After-market installation from the prepared branch

First let the engine finish and confirm it reports `flat=True`. Preserve the
execution journal. These commands do not start the engine or change execution mode.
Git will refuse the switch if local edits would be overwritten; preserve those edits
and inspect the conflict rather than resetting or cleaning them away.

```powershell
cd C:\MITRAseries\investMITRA
git fetch origin codex/capital-reuse-visibility
if ($LASTEXITCODE -ne 0) { throw "Fetch failed" }
git switch --track origin/codex/capital-reuse-visibility
if ($LASTEXITCODE -ne 0) { throw "Switch needs review; keep local edits and journal" }
python -X utf8 -m unittest -q test_auto_trading test_review_fixes test_capital_visibility test_pipeline_dates test_overnight_readiness test_server_pipeline
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
