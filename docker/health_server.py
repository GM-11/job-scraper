"""HTTP server for Render's web-service health check plus a manual trigger.

The scraper normally runs on cron; this process proves the container is alive
and, on request, kicks off the same script cron uses.

Logging note: supervisord forwards this program's stdout to the container's
log stream (stdout_logfile=/dev/stdout in supervisord.conf), which is what
Render's log viewer reads. Every write is flushed immediately -- an idle
process can otherwise sit on a buffered line indefinitely.

No dependencies beyond the stdlib so it starts instantly.
"""

import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUN_SCRIPT = "/app/docker/run_scraper.sh"
HEALTH_PATHS = ("/", "/healthz")


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sys.stdout.write(f"{ts} [health_server] {msg}\n")
    sys.stdout.flush()


class Handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _trigger(self) -> None:
        log(f"/run triggered ({self.command} from {self.client_address[0]})")

        # run_scraper.sh takes an flock, so a run already in progress (from
        # cron or an earlier manual trigger) is skipped rather than
        # overlapping. The child inherits our stdout/stderr, so its output
        # lands in the same log stream. Return immediately -- a full scrape
        # takes minutes.
        subprocess.Popen(
            [RUN_SCRIPT, "manual"],
            stdout=sys.stdout,
            stderr=sys.stderr,
            start_new_session=True,
        )
        self._respond(202, {"status": "started"})

    def _handle(self) -> None:
        path = self.path.split("?", 1)[0]
        path = path.rstrip("/") or "/"

        if path == "/run":
            # Accept GET too: hitting the URL in a browser is the obvious way
            # to trigger this by hand, and a GET-only 200 "ok" would silently
            # look like success while running nothing.
            self._trigger()
        elif path in HEALTH_PATHS:
            self._respond(200, {"status": "ok"})
        else:
            log(f"404 {self.command} {self.path}")
            self._respond(404, {"error": "not found", "hint": "GET or POST /run"})

    do_GET = _handle
    do_POST = _handle

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Suppress the default per-request access log; Render's health check
        # polls constantly and would drown out everything else. Interesting
        # requests are logged explicitly above.
        pass


def main() -> None:
    port = int(os.environ.get("PORT", "10000"))
    log(f"listening on port {port} (GET or POST /run to trigger a scrape)")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
