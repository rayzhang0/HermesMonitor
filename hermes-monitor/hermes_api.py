from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from hermes_monitor import DEFAULT_DB_PATH, init_database, register_push_token, send_push_notification

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PUBLIC_JSON = Path("state/public_inventory.json")


class InventoryHandler(BaseHTTPRequestHandler):
    public_json = DEFAULT_PUBLIC_JSON
    db_path = DEFAULT_DB_PATH

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/", "/public_inventory.json"}:
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

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Hermes-Push-Test-Token")
        self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/push/register":
            self.handle_push_register()
            return
        if path == "/push/test":
            self.handle_push_test()
            return
        self.send_error(404)

    def handle_push_register(self) -> None:
        try:
            body = self.read_json_body()
            token = str(body.get("token") or "")
            platform = str(body.get("platform") or "ios")
            app_version = str(body.get("app_version") or "")
            register_push_token(self.db_path, token, platform=platform, app_version=app_version)
        except Exception as error:
            self.write_json({"ok": False, "error": str(error)}, status=400)
            return
        self.write_json({"ok": True})

    def handle_push_test(self) -> None:
        expected = os.environ.get("HERMES_PUSH_ADMIN_TOKEN")
        if expected and self.headers.get("X-Hermes-Push-Test-Token") != expected:
            self.write_json({"ok": False, "error": "unauthorized"}, status=401)
            return
        try:
            result = send_push_notification(self.db_path, "Hermes Monitor test", "Push notification settings are working.")
        except Exception as error:
            self.write_json({"ok": False, "error": str(error)}, status=500)
            return
        self.write_json({"ok": bool(result.get("configured")), "push": result})

    def read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 16384:
            raise ValueError("Invalid request body length")
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def write_json(self, payload: dict[str, object], *, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        print("api", self.address_string(), format % args, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Hermes Monitor public inventory JSON for local SSH tunnel testing.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--json", type=Path, default=DEFAULT_PUBLIC_JSON)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    InventoryHandler.public_json = args.json
    InventoryHandler.db_path = args.db
    init_database(args.db)
    server = ThreadingHTTPServer((args.host, args.port), InventoryHandler)
    print(f"Serving {args.json} at http://{args.host}:{args.port}/public_inventory.json", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
