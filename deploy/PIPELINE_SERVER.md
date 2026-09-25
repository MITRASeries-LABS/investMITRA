# Direct server pipeline: remove GitHub from nightly execution

**Status: implementation prepared; no server has been selected or activated.**

Run on an always-on Linux server with systemd, Python 3.12+, outbound HTTPS and
access to Neon/R2/NSE. Use an isolated service account, environment and working
directory. The laptop still runs the paper trading engine. This service prepares
data only: it never starts the trading engine, logs into Kite, or places orders.
Do not provide Kite credentials in this service's environment.

The server executes every existing price ingestion, feature, score, context,
top-picks and analysis stage directly. There is no GitHub dispatch, runner queue,
webhook, or nightly git pull. GitHub is used only to distribute reviewed releases.
The installed commit is recorded in `/opt/investmitra-data/REVISION`.

## Schedule in IST

| Time | Action |
|---|---|
| 20:30, Monday–Friday | Initial preparation |
| 23:30, Monday–Friday | Retry if completion receipt/data checks fail |
| 02:30, Tuesday–Saturday | Retry |
| 04:30, Monday–Friday | Final retry |
| 07:00, Monday–Friday | Independent check-only job, Telegram result and external heartbeat |

The data service is a single systemd oneshot unit: another timer activation does
not launch a duplicate while it is active. Always start it using `systemctl`, not
by running a second Python process manually. Missing recent sessions are ingested
oldest first; scoring follows successful ingestion. A command failure stops the
chain. The runner has a 90-minute command budget and the service a 100-minute
hard timeout. Individual commands are bounded at 30 minutes. Process failures or
timeouts invoke the failure-notification service. Automatic recovery refuses to
start between 06:00 and 18:00 IST; freshness checks are never bypassed.

The morning check is a separate service and does not wait for the data service.
Systemd timers use the explicit Asia/Kolkata timezone, one-second accuracy and
zero random delay. Persistent timers catch up after a reboot, subject to the
rebuild cutoff. This avoids GitHub scheduling delays but still depends on server
uptime, correct clock, networking and data providers.

## Independent missed-run monitoring is required

Configure a heartbeat/dead-man check on a service **outside this server**:

- Expect success Monday–Friday at 07:00 Asia/Kolkata, with a 15-minute grace period.
- Alert you if no success heartbeat arrives by 07:15; this catches a powered-off
  server, a stopped timer, networking failure or an incomplete check.
- Put its HTTPS success and failure ping URLs in the environment file.
- Verify the external monitor's alert recipient and test its missed-heartbeat
  notification before treating deployment as complete.

The morning job requires a success URL. A configured URL alone does not prove
that the monitor has the correct schedule or recipient; acceptance must establish
that. Failure pings and Telegram do not substitute for missed-heartbeat monitoring.

## Prepare the chosen host

Use a reviewed checkout containing this document. Do not reuse another product's
service account, virtual environment, database credentials or deployment folders.
Check free memory/disk and outbound NSE access on that host before activating.

```bash
sudo bash deploy/install_pipeline_server.sh
sudoedit /etc/investmitra-data/pipeline.env
```

The installer creates `/opt/investmitra-data`, a dedicated `investmitra-data`
account, virtual environment and systemd units. It does not start jobs or enable
timers. It refuses to overwrite an existing installation. The source archive
contains tracked files only; untracked credentials are not copied. Populate the
existing pipeline's Neon/R2, Telegram and analysis secrets securely on the server.
Leave unused optional values empty. The environment file stays root-only (0600).

## Acceptance and cutover

1. Verify dependency installation and the offline plan; it makes no network calls:

   ```bash
   /opt/investmitra-data/venv/bin/python /opt/investmitra-data/app/scripts/run_server_pipeline.py --plan --date 2026-09-24
   ```

2. In GitHub Actions, disable **Overnight recovery and morning readiness** using
   its workflow menu, and wait for existing data/feature runs to finish. Keep the
   test workflow enabled. Do not leave two independent writers active. The manual
   data workflows remain emergency tools but must not overlap server runs.

3. During the allowed evening/overnight window, run one full acceptance cycle:

   ```bash
   sudo systemctl start investmitra-data.service
   sudo systemctl status investmitra-data.service --no-pager
   sudo journalctl -u investmitra-data.service -n 100 --no-pager
   sudo systemctl start investmitra-readiness.service
   sudo systemctl status investmitra-readiness.service --no-pager
   ```

   Confirm the target session, actual loaded rows, all scoring stages, completion
   receipt, Telegram message, and external success heartbeat. An NSE access failure
   on this host must be resolved here; passing offline tests is not acceptance.
   Failure notifications are inspected with `journalctl -u investmitra-data-failure.service`.

4. After successful acceptance, enable the timers:

   ```bash
   sudo systemctl enable --now investmitra-data.timer investmitra-readiness.timer
   systemctl list-timers 'investmitra-*' --all
   ```

   Confirm the displayed next runs match the intended IST schedule. Enabling a
   persistent timer can start a catch-up invocation immediately; the cutoff still
   applies. Verify the external monitor's missed-run alert as well.

Until these checks pass, this is **prepared code, not an operational migration**.
No additional laptop process is required for the overnight pipeline.

## Updates and rollback

Updates are deliberate deployments, not automatic nightly pulls. Stop both timers,
wait for running services to finish, install a reviewed revision and dependencies
without replacing `/etc/investmitra-data/pipeline.env` or the data directory, then
repeat acceptance. Keep the previous application/venv release for rollback.

To stop server scheduling:

```bash
sudo systemctl disable --now investmitra-data.timer investmitra-readiness.timer
```

This stops future timer activations, not an already-running service. Wait for it
to finish before re-enabling GitHub scheduling or another writer. Retain journals,
receipts and logs. Never delete trading journals to resolve a data-pipeline fault.
