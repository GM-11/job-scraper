FROM python:3.12-slim

# cron: runs the scraper on a schedule inside the container.
# supervisor: keeps cron and the health-check server both running as PID 1's children.
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .
RUN chmod +x /app/docker/entrypoint.sh /app/docker/run_scraper.sh

# Render's web-service health check hits this port; the cron schedule does
# the actual scraping work independently of any incoming HTTP traffic.
ENV PORT=10000
EXPOSE 10000

CMD ["/app/docker/entrypoint.sh"]
