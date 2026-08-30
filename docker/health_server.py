"""Minimal HTTP server for Render's web-service health check, plus a manual
trigger endpoint so the scraper can be run on demand instead of waiting for
the hourly cron.

The actual work normally happens on cron; this process only proves the
container is alive and, on request, kicks off the same script cron uses.
No dependencies beyond the stdlib so it starts instantly.
"""

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUN_SCRIPT = "/app/docker/run_scraper.sh"
LOCK_FILE = "/tmp/job-scraper.lock"


class HealthHandler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok\n")

    def do_POST(self) -> None:
        if self.path != "/run":
            self._json(404, {"error": "not found"})
            return

        # run_scraper.sh flocks /tmp/job-scraper.lock, so a run already in
        # progress (from cron or a previous manual trigger) just gets
        # skipped rather than overlapping. Launch detached and return
        # immediately -- a full scrape can take a while.
        with open("/proc/1/fd/1", "ab", buffering=0) as out, open(
            "/proc/1/fd/2", "ab", buffering=0
        ) as err:
            out.write(b"[health_server] manual /run trigger received\n")
            subprocess.Popen([RUN_SCRIPT], stdout=out, stderr=err, start_new_session=True)

        self._json(202, {"status": "started"})

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def main() -> None:
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
