#!/usr/bin/env python3
"""Stub upstream for the reverse-proxy contract smoke test.

Stands in for backend (:8000) and frontend (:3000) so proxy_contract_smoke.sh
can exercise the REAL nginx / Caddy config against canned upstream responses —
no FastAPI / Next.js / pg / redis needed. Run inside one container with two
network aliases (`backend`, `frontend`); a single process listens on both ports
and reports which port a request landed on, so routing assertions can tell
backend-routed (/api, /health) from frontend-routed (/) traffic apart.

Endpoints (port-agnostic — the proxy decides which upstream a path reaches):
  /api/v1/stream/*  → SSE: emit chunk1, sleep 1.5s, emit chunk2. The gap is how
                      the test detects buffering-off (chunks arrive incrementally)
                      vs buffering-on (both arrive together at stream end).
  everything else   → 200 JSON {served_by_port, path, headers} so the test can
                      assert routing (served_by_port) and header injection
                      (headers['x-real-ip']).
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SSE_GAP_SECONDS = 1.5  # must exceed the test's pass threshold with margin


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence — keeps `docker logs` clean
        pass

    def _echo(self):
        port = self.server.server_address[1]
        body = json.dumps(
            {
                "served_by_port": port,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self):
        # Close-delimited stream (Connection: close, no Content-Length, no manual
        # Transfer-Encoding). This mirrors how a real SSE backend streams and,
        # crucially, does NOT hand-roll chunked framing — nginx's stream location
        # sets `chunked_transfer_encoding off`, which collides with an upstream
        # that declares its own chunked encoding. Body ends at EOF; the proxy
        # forwards each write as it arrives iff buffering is off.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(b"data: chunk1\n\n")
        self.wfile.flush()
        time.sleep(SSE_GAP_SECONDS)
        self.wfile.write(b"data: chunk2\n\n")
        self.wfile.flush()
        self.wfile.write(b"data: done\n\n")
        self.wfile.flush()

    def do_GET(self):
        if self.path.startswith("/api/v1/stream/"):
            self._sse()
        else:
            self._echo()

    def do_POST(self):
        # Drain the body so the socket stays clean; the proxy's upload cap should
        # 413 oversize bodies before they ever reach here, but a within-limit POST
        # must still get a clean response.
        length = int(self.headers.get("Content-Length", 0) or 0)
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)
        self._echo()


def serve(port: int):
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    # backend alias listens on 8000, frontend alias on 3000 — same process.
    threading.Thread(target=serve, args=(3000,), daemon=True).start()
    serve(8000)
