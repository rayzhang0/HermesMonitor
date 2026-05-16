from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PUBLIC_JSON = Path("state/public_inventory.json")


class InventoryHandler(BaseHTTPRequestHandler):
    public_json = DEFAULT_PUBLIC_JSON

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] not in {"/", "/public_inventory.json"}:
            self.send_error(404)
            return
        try:
            payload = self.public_json.read_bytes()
        except FileNotFoundError:
            payload = json.dumps({"available": [], "history": [], "error": "inventory export not found"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        print("api", self.address_string(), format % args, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Hermes Monitor public inventory JSON for local SSH tunnel testing.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--json", type=Path, default=DEFAULT_PUBLIC_JSON)
    args = parser.parse_args()
    InventoryHandler.public_json = args.json
    server = ThreadingHTTPServer((args.host, args.port), InventoryHandler)
    print(f"Serving {args.json} at http://{args.host}:{args.port}/public_inventory.json", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
