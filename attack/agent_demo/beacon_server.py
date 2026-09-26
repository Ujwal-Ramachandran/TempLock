"""A harmless localhost listener that stands in for an 'attacker' endpoint.

It only ever binds to 127.0.0.1 and does nothing but log the requests it
receives. In the agent demo, the poisoned template induces the agent to call
this endpoint every iteration; watching hits appear here is the proof that a
chat-template backdoor drove a tool-enabled agent to reach out over the network.

Every hit is also appended to a log file (beacon_hits.log, next to this module)
so you can confirm afterward whether the agent called home, from code or by eye.

This is a demonstration prop, not malware: localhost only, no data is stored or
forwarded anywhere, it just records that it was called.

Run the listener:
    python -m attack.agent_demo.beacon_server

Check hits without starting the listener:
    python -m attack.agent_demo.beacon_server --status
    python -m attack.agent_demo.beacon_server --reset    # clear the log
"""

from __future__ import annotations

import argparse
import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8000
HITS_LOG = Path(__file__).resolve().parent / "beacon_hits.log"


def record_hit(path: str, client: str) -> int:
    """Append a hit to the log and return the running total."""
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(HITS_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp}\t{client}\t{path}\n")
    return hit_count()


def hit_count() -> int:
    """How many hits have been recorded so far."""
    return len(read_hits())


def read_hits() -> list[str]:
    """Return the recorded hit log lines (empty list if none)."""
    if not HITS_LOG.exists():
        return []
    return [ln for ln in HITS_LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]


class _BeaconHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        total = record_hit(self.path, self.client_address[0])
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}]  BEACON HIT #{total}  {self.command} {self.path}  "
              f"from {self.client_address[0]}")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:
        return  # silence default logging; we print our own line


def _print_status() -> None:
    hits = read_hits()
    print(f"{len(hits)} beacon hit(s) recorded in {HITS_LOG}")
    for line in hits[-5:]:
        print(f"  {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Beacon listener for the agent demo.")
    parser.add_argument("--status", action="store_true", help="Print recorded hits and exit.")
    parser.add_argument("--reset", action="store_true", help="Clear the hit log and exit.")
    parser.add_argument("--host", default=HOST,
                        help="Address to bind. Defaults to 127.0.0.1; use your LAN IP "
                             "(or 0.0.0.0) to receive beacons over the network.")
    parser.add_argument("--port", type=int, default=PORT, help="Port to bind.")
    args = parser.parse_args(argv)

    if args.reset:
        HITS_LOG.write_text("", encoding="utf-8")
        print(f"Cleared {HITS_LOG}")
        return 0
    if args.status:
        _print_status()
        return 0

    print(f"Beacon listener on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    print(f"Logging hits to {HITS_LOG}")
    print("Waiting for the agent to call home...\n")
    server = HTTPServer((args.host, args.port), _BeaconHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\nStopped. Total hits recorded: {hit_count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
