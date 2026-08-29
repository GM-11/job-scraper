#!/usr/bin/env bash
# Invoked by cron. Cron jobs don't inherit the container's environment, so
# source the env dump the entrypoint wrote out before launching the scraper.
set -euo pipefail

if [ -f /app/docker/env.sh ]; then
  # shellcheck disable=SC1091
  source /app/docker/env.sh
fi

cd /app
exec python -m scraper.main --tier all
