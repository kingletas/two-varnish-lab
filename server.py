"""A slow origin: every page takes 300 ms and says which pod made it."""

import os
import signal
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

POD = os.environ.get("POD_NAME", "local")
DELAY = float(os.environ.get("ORIGIN_DELAY", "0.3"))


class Origin(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.reply("ok\n", cacheable=False)
            return
        time.sleep(DELAY)
        made = time.strftime("%H:%M:%S")
        self.reply(f"path={self.path} pod={POD} made={made}\n", cacheable=True)

    def reply(self, body, cacheable):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Cache-Control", "public, s-maxage=120" if cacheable else "no-store"
        )
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"{POD} {fmt % args}", flush=True)


def stop(signum, frame):
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    ThreadingHTTPServer(("0.0.0.0", 8080), Origin).serve_forever()
