#!/usr/bin/env bash
# Prepare a new, isolated installation. Does not start jobs or enable timers.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo 'Run with sudo on the chosen Linux server.' >&2
  exit 1
fi
repo_root=$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)
python3 -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ required"'
for required in systemctl systemd-analyze git tar; do command -v "$required" >/dev/null; done
if [ -e /opt/investmitra-data ] || [ -e /etc/investmitra-data ] || id investmitra-data >/dev/null 2>&1; then
  echo 'Existing investmitra-data installation/account found; use the documented update procedure.' >&2
  exit 1
fi
if ! git -C "$repo_root" diff --quiet HEAD --; then
  echo 'Tracked local modifications found. Install a reviewed committed revision.' >&2
  exit 1
fi
useradd --system --user-group --create-home --home-dir /var/lib/investmitra-data --shell /usr/sbin/nologin investmitra-data
install -d -m 0755 /opt/investmitra-data/app
install -d -m 0700 /etc/investmitra-data
git -C "$repo_root" archive HEAD | tar -x -C /opt/investmitra-data/app
git -C "$repo_root" rev-parse HEAD > /opt/investmitra-data/REVISION
install -d -o investmitra-data -g investmitra-data -m 0700 /opt/investmitra-data/app/data
python3 -m venv /opt/investmitra-data/venv
/opt/investmitra-data/venv/bin/python -m pip install -r /opt/investmitra-data/app/requirements-pipeline-server.txt
install -m 0600 /dev/null /etc/investmitra-data/pipeline.env
cat > /etc/investmitra-data/pipeline.env <<'ENV'
CC_POSTGRES_URL=
CC_TIMESCALE_URL=
AWS_ENDPOINT_URL=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
CC_ENV=prod
CC_DB_SCHEMA=investmitra
AWS_REGION=auto
CC_BUCKET_RAW=cc-raw
CC_BUCKET_QUARANTINE=cc-quarantine
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ANTHROPIC_API_KEY=
PIPELINE_HEARTBEAT_SUCCESS_URL=
PIPELINE_HEARTBEAT_FAILURE_URL=
ENV
install -m 0644 /opt/investmitra-data/app/deploy/systemd/* /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/investmitra-data.service /etc/systemd/system/investmitra-data.timer /etc/systemd/system/investmitra-readiness.service /etc/systemd/system/investmitra-readiness.timer /etc/systemd/system/investmitra-data-failure.service
systemctl daemon-reload
printf '%s\n' 'Prepared only. Fill pipeline.env, verify external monitoring, stop GitHub scheduling, then run acceptance before enabling timers. See deploy/PIPELINE_SERVER.md.'
