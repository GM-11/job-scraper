#!/usr/bin/env bash
# Invoked by cron and by the health server's /run endpoint. Cron jobs don't
# inherit the container's environment, so source the env dump the entrypoint
# wrote out before launching the scraper.
set -uo pipefail

# Fallback for cron's bare PATH (/usr/bin:/bin), which misses /usr/local/bin
# where this image's python lives. env.sh carries the container's real PATH
# and overrides this when present.
export PATH="/usr/local/bin:/usr/local/sbin:${PATH}"

if [ -f /app/docker/env.sh ]; then
  # shellcheck disable=SC1091
  source /app/docker/env.sh
fi

# Cron starts with a near-empty environment, so set this here too rather than
# relying on the Dockerfile's ENV: unbuffered stdout keeps log lines flowing
# to Render's log viewer instead of sitting in a buffer.
export PYTHONUNBUFFERED=1

cd /app

# "$1" lets the caller label the trigger source (cron / manual).
trigger="${1:-cron}"
log() { echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') [run_scraper] $*"; }

log "triggered by ${trigger}"

# Flock so the hourly cron run and a manually-triggered run never execute
# concurrently. Non-blocking: an overlapping trigger is dropped, not queued.
exec 9>/tmp/job-scraper.lock
if ! flock -n 9; then
  log "a scrape is already running, skipping this ${trigger} trigger"
  exit 0
fi

log "starting scrape (tier all)"
start=$(date +%s)

python -m scraper.main --tier all
status=$?

log "scrape finished with exit code ${status} in $(( $(date +%s) - start ))s"
exit "$status"
