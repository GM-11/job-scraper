#!/usr/bin/env bash
# Invoked by cron. Cron jobs don't inherit the container's environment, so
# source the env dump the entrypoint wrote out before launching the scraper.
set -euo pipefail

if [ -f /app/docker/env.sh ]; then
  # shellcheck disable=SC1091
  source /app/docker/env.sh
fi

cd /app

echo "[run_scraper] triggered at $(date -u +'%Y-%m-%dT%H:%M:%SZ')"

# Flock so the hourly cron run and a manually-triggered run (via the health
# server's /run endpoint) never execute concurrently.
exec 9>/tmp/job-scraper.lock
if ! flock -n 9; then
  echo "[run_scraper] a scrape is already running, skipping this trigger"
  exit 0
fi

echo "[run_scraper] starting scraper"
exec python -m scraper.main --tier all
