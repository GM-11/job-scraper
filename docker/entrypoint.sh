#!/usr/bin/env bash
# Render injects env vars (SMTP_FROM, SMTP_PASSWORD, SMTP_TO, PORT, ...) into
# this process only. cron starts jobs with a near-empty environment, so dump
# the vars the scraper needs to a file that run_scraper.sh sources.
set -euo pipefail

: > /app/docker/env.sh
for var in SMTP_FROM SMTP_PASSWORD SMTP_TO; do
  if [ -n "${!var:-}" ]; then
    printf 'export %s=%q\n' "$var" "${!var}" >> /app/docker/env.sh
  fi
done
chmod 600 /app/docker/env.sh

# /etc/cron.d entries include a user field and are picked up by cron
# automatically -- no separate `crontab` install step needed (its format
# has no user field, so running it on this same file would fail to parse).
cp /app/docker/crontab /etc/cron.d/job-scraper
chmod 0644 /etc/cron.d/job-scraper

exec supervisord -c /app/docker/supervisord.conf
