from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import smtplib
import socket
import sqlite3
import ssl
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_URL = "https://www.hermes.com/us/en/category/leather-goods/bags-and-clutches/womens-bags-and-clutches/#|"
DEFAULT_STATE_PATH = Path("state/hermes_womens_bags.json")
DEFAULT_DB_PATH = Path("state/hermes_monitor.sqlite3")
DEFAULT_EXPORT_PATH = Path("state/public_inventory.json")
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 300
DEFAULT_JITTER_SECONDS = 120
MIN_REQUEST_GAP_SECONDS = 300
BACKOFF_BASE_SECONDS = 21600
BACKOFF_CAP_SECONDS = 86400
socket.setdefaulttimeout(30)

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
ACCEPT_LANGUAGES = ["en-US,en;q=0.9", "en-US,en;q=0.8", "en-US,en;q=0.9,fr;q=0.4"]


@dataclass(frozen=True)
class Product:
    name: str
    url: str
    color: str | None = None
    price: str | None = None
    image_url: str | None = None
    baseline_excluded: bool = False
    purchasable_status: str | None = None
    purchasable_checked_at: str | None = None


@dataclass(frozen=True)
class Snapshot:
    count: int
    products: list[Product]
    checked_at: str
    url: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class InventoryChanges:
    added: list[Product]
    removed: list[Product]
    price_changed: list[tuple[Product, Product]]
    detail_changed: list[tuple[Product, Product]]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.price_changed or self.detail_changed)

    @property
    def has_alert_changes(self) -> bool:
        return bool(self.added or self.removed)


class TextLinksImagesParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.images: list[tuple[str, str]] = []
        self._href_stack: list[str | None] = []
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "a":
            self._href_stack.append(attrs_dict.get("href"))
            self._current_link_text = []
        elif tag == "img":
            src = attrs_dict.get("src") or attrs_dict.get("data-src") or attrs_dict.get("data-original")
            key = " ".join(filter(None, [attrs_dict.get("id"), attrs_dict.get("alt")]))
            if src:
                self.images.append((normalize_text(key), absolute_hermes_url(src) or src))
        if tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href_stack:
            href = self._href_stack.pop()
            text = normalize_text(" ".join(self._current_link_text))
            if text:
                self.links.append((text, href or ""))
            self._current_link_text = []
        if tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = unescape(data)
        self.parts.append(text)
        if self._href_stack:
            self._current_link_text.append(text)

    @property
    def text(self) -> str:
        return normalize_multiline("".join(self.parts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Track Hermes women's bags availability from the category page.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--export", type=Path, default=DEFAULT_EXPORT_PATH)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="Seconds between checks in continuous mode. Minimum 300 seconds unless --allow-fast-checks is used.")
    parser.add_argument("--jitter", type=int, default=DEFAULT_JITTER_SECONDS)
    parser.add_argument("--allow-fast-checks", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--init", action="store_true", help="Seed current products as the one-time excluded baseline without sending alerts.")
    parser.add_argument("--send-test-email", action="store_true")
    parser.add_argument("--list-products", action="store_true")
    parser.add_argument("--export-json", action="store_true", help="Write public inventory JSON and exit.")
    args = parser.parse_args()

    init_database(args.db)
    migrate_json_state_to_database(args.state, args.db)

    if args.send_test_email:
        send_email("Hermes Monitor test", "Hermes Monitor email settings are working.")
        print("Test email sent.")
        return
    if args.list_products:
        print_inventory(args.db)
        return
    if args.export_json:
        export_public_inventory(args.db, args.export)
        print(f"Exported {args.export}")
        return
    if not args.allow_fast_checks and not (args.once or args.init) and args.interval < MIN_INTERVAL_SECONDS:
        raise SystemExit(f"Interval {args.interval}s is below the polite minimum {MIN_INTERVAL_SECONDS}s. Use --allow-fast-checks to override.")

    backoff_attempt = 0
    while True:
        try:
            changed, count = check_once(args.url, args.state, args.db, args.export, initialize_only=args.init)
            backoff_attempt = 0
            if not changed:
                print(f"[{now_local()}] no change products={count if count is not None else '?'}")
            if args.once or args.init:
                return
        except RateLimitedError as error:
            notify_access_issue(args.db, error)
            backoff_attempt += 1
            sleep_seconds = exponential_backoff_with_jitter(backoff_attempt)
            print(f"[{now_local()}] Hermes asked us to slow down: {error}; backoff_attempt={backoff_attempt}; sleeping {sleep_seconds}s", flush=True)
            time.sleep(sleep_seconds)
            continue
        except Exception as error:
            backoff_attempt += 1
            sleep_seconds = exponential_backoff_with_jitter(backoff_attempt, base_seconds=args.interval, cap_seconds=BACKOFF_CAP_SECONDS)
            print(f"[{now_local()}] check failed: {error}; backoff_attempt={backoff_attempt}; sleeping {sleep_seconds}s", flush=True)
            time.sleep(sleep_seconds)
            continue
        sleep_seconds = interval_with_jitter(args.interval, args.jitter)
        print(f"[{now_local()}] sleeping {sleep_seconds}s", flush=True)
        time.sleep(sleep_seconds)


def check_once(url: str, state_path: Path, db_path: Path, export_path: Path, *, initialize_only: bool = False) -> tuple[bool, int | None]:
    wait_for_request_slot(db_path, stage="category")
    snapshot = fetch_snapshot(url)
    with sqlite3.connect(db_path) as conn:
        if initialize_only:
            seed_baseline(conn, snapshot)
            save_snapshot(state_path, snapshot)
            export_public_inventory(db_path, export_path)
            print(f"[{now_local()}] baseline initialized products={len(snapshot.products)}")
            return True, len(snapshot.products)

    changes = compare_inventory(db_path, snapshot)
    save_inventory(db_path, snapshot, changes)
    save_snapshot(state_path, snapshot)
    export_public_inventory(db_path, export_path)
    notify_access_recovered(db_path, snapshot)

    if changes.has_alert_changes:
        send_email(render_change_subject(changes), render_change_email_body(changes, snapshot))
        print(f"[{now_local()}] changes added={len(changes.added)} removed={len(changes.removed)} price={len(changes.price_changed)} detail={len(changes.detail_changed)}; email sent", flush=True)
        return True, len(snapshot.products)
    return False, len(snapshot.products)


def fetch_snapshot(url: str) -> Snapshot:
    print(f"[{now_local()}] fetching category {url}", flush=True)
    html, etag, last_modified = fetch_html(url, stage="category")
    page = TextLinksImagesParser()
    page.feed(html)
    products = extract_products(page)
    return Snapshot(count=extract_counter(page.text, fallback=len(products)), products=products, checked_at=datetime.now(timezone.utc).isoformat(), url=url, etag=etag, last_modified=last_modified)


def fetch_html(url: str, *, stage: str) -> tuple[str, str | None, str | None]:
    request = Request(url, headers=build_request_headers(stage=stage))
    try:
        with force_address_family(address_family_for_stage(stage)):
            with urlopen(request, timeout=30) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace"), response.headers.get("ETag"), response.headers.get("Last-Modified")
    except HTTPError as error:
        if error.code in {403, 429, 500, 502, 503, 504}:
            raise RateLimitedError(f"HTTP {error.code}", stage=stage, status_code=error.code, url=url) from error
        raise RuntimeError(f"Could not fetch Hermes page: HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"Could not fetch Hermes page: {error}") from error


def extract_counter(text: str, *, fallback: int) -> int:
    for pattern in [r"Women's bags and clutches\s+(\d+)\s+products", r"Update\s+(\d+)\s+products", r"\b(\d+)\s+products\b"]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return fallback


def extract_products(page: TextLinksImagesParser) -> list[Product]:
    products: list[Product] = []
    seen: set[str] = set()
    lines = page.text.splitlines()
    for name, href in page.links:
        url = absolute_hermes_url(href)
        if not url or "/product/" not in url or url in seen:
            continue
        seen.add(url)
        _, price = find_color_price_near_name(lines, name)
        image_url = find_image_for_product(page.images, name, url)
        products.append(Product(name=name, url=url, color=None, price=price, image_url=image_url))
    return products


def find_image_for_product(images: list[tuple[str, str]], name: str, url: str | None = None) -> str | None:
    normalized_name = normalize_text(name).lower()
    product_code = extract_product_code(url or "")
    for key, src in images:
        normalized_key = normalize_text(key).lower()
        if product_code and product_code.lower() in normalized_key:
            return src
        if normalized_key and (normalized_key in normalized_name or normalized_name in normalized_key):
            return src
    return images[0][1] if len(images) == 1 else None


def extract_product_code(url: str) -> str | None:
    match = re.search(r"-([A-Z0-9]{8,})/?$", url)
    return match.group(1) if match else None


def color_from_product_code(url: str) -> str | None:
    code = extract_product_code(url or "")
    if not code:
        return None
    match = re.search(r"(?:CK|CC)([A-Z0-9]{2,4})$", code)
    if match:
        return f"Color code {match.group(1)}"
    return None


def find_color_price_near_name(lines: list[str], name: str) -> tuple[str | None, str | None]:
    for index, line in enumerate(lines):
        if name not in line:
            continue
        window = " ".join(lines[index : index + 8])
        color_match = re.search(r"Color:\s*([^,]+?)(?:\s*Price|\s+\$|$)", window)
        price_match = re.search(r"Price\s*(\$[\d,]+(?:\.\d{2})?)", window)
        return normalize_text(color_match.group(1)) if color_match else None, price_match.group(1) if price_match else None
    return None, None


def init_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS monitor_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                product_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                color TEXT,
                price TEXT,
                image_url TEXT,
                baseline_excluded INTEGER NOT NULL DEFAULT 0,
                detail_checked_at TEXT,
                purchasable_status TEXT NOT NULL DEFAULT 'unknown',
                purchasable_checked_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS availability_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_key TEXT NOT NULL,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                color TEXT,
                price TEXT,
                image_url TEXT,
                purchasable_status TEXT NOT NULL DEFAULT 'unknown',
                purchasable_checked_at TEXT,
                available_from TEXT NOT NULL,
                available_until TEXT,
                baseline_excluded INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS suppressed_products (
                product_key TEXT PRIMARY KEY,
                suppressed_at TEXT NOT NULL,
                reason TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                product_key TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        for column, column_type in [("image_url", "TEXT"), ("baseline_excluded", "INTEGER NOT NULL DEFAULT 0"), ("detail_checked_at", "TEXT"), ("detail_failed_at", "TEXT"), ("detail_retry_after", "TEXT"), ("detail_failure_count", "INTEGER NOT NULL DEFAULT 0"), ("purchasable_status", "TEXT NOT NULL DEFAULT 'unknown'"), ("purchasable_checked_at", "TEXT")]:
            ensure_column(conn, "products", column, column_type)
        for column, column_type in [("purchasable_status", "TEXT NOT NULL DEFAULT 'unknown'"), ("purchasable_checked_at", "TEXT")]:
            ensure_column(conn, "availability_history", column, column_type)
        normalize_legacy_purchasable_statuses(conn)
        repair_active_first_seen_from_open_history(conn)
        conn.commit()


def repair_active_first_seen_from_open_history(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE products
        SET first_seen_at = (
            SELECT h.available_from
            FROM availability_history h
            WHERE h.product_key = products.product_key AND h.available_until IS NULL
            ORDER BY h.id DESC
            LIMIT 1
        )
        WHERE active = 1
          AND EXISTS (
              SELECT 1
              FROM availability_history h
              WHERE h.product_key = products.product_key
                AND h.available_until IS NULL
                AND h.available_from != products.first_seen_at
          )
        """
    )


def normalize_legacy_purchasable_statuses(conn: sqlite3.Connection) -> None:
    for table in ["products", "availability_history"]:
        conn.execute(
            f"UPDATE {table} SET purchasable_status = 'unknown', purchasable_checked_at = NULL WHERE purchasable_status NOT IN ('unknown', 'purchasable', 'not_purchasable')"
        )


def backfill_color_codes(conn: sqlite3.Connection) -> None:
    for table in ["products", "availability_history"]:
        rows = conn.execute(f"SELECT product_key, url FROM {table} WHERE color IS NULL OR TRIM(color) = ''").fetchall()
        for key, url in rows:
            color = color_from_product_code(url or key)
            if color:
                conn.execute(f"UPDATE {table} SET color = ? WHERE product_key = ? AND (color IS NULL OR TRIM(color) = '')", (color, key))


def ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        except sqlite3.OperationalError as error:
            if "duplicate column name" not in str(error).lower():
                raise


def seed_baseline(conn: sqlite3.Connection, snapshot: Snapshot) -> None:
    checked_at = snapshot.checked_at
    for product in snapshot.products:
        upsert_product(conn, product, checked_at, active=1, baseline_excluded=1)
    current_keys = {product_key(product) for product in snapshot.products}
    conn.execute("UPDATE products SET baseline_excluded = 1 WHERE active = 1")
    set_state(conn, "baseline_seeded", "1")
    set_state(conn, "baseline_seeded_at", checked_at)
    set_state(conn, "last_count", str(len(snapshot.products)))
    set_state(conn, "last_checked_at", checked_at)
    conn.commit()


def compare_inventory(db_path: Path, snapshot: Snapshot) -> InventoryChanges:
    previous = load_active_products(db_path)
    current = {product_key(product): product for product in snapshot.products}
    previous_keys = set(previous)
    current_keys = set(current)
    added = [current[key] for key in sorted(current_keys - previous_keys)]
    removed = [previous[key] for key in sorted(previous_keys - current_keys)]
    price_changed: list[tuple[Product, Product]] = []
    detail_changed: list[tuple[Product, Product]] = []
    for key in sorted(previous_keys & current_keys):
        old = previous[key]
        new = current[key]
        if normalize_optional(old.price) != normalize_optional(new.price):
            price_changed.append((old, new))
        elif normalize_optional(old.name) != normalize_optional(new.name):
            detail_changed.append((old, new))
    return InventoryChanges(added=added, removed=removed, price_changed=price_changed, detail_changed=detail_changed)


def save_inventory(db_path: Path, snapshot: Snapshot, changes: InventoryChanges) -> None:
    checked_at = snapshot.checked_at
    raw_current = {product_key(product): product for product in snapshot.products}
    with sqlite3.connect(db_path) as conn:
        current = raw_current
        previous = load_active_products_from_conn(conn)
        added_keys = {product_key(product) for product in changes.added}
        for key, product in current.items():
            upsert_product(conn, product, checked_at, active=1, baseline_excluded=0)
            if key not in added_keys:
                ensure_open_history(conn, product, checked_at)
            update_open_history_product_fields(conn, product)
        for key, old in previous.items():
            if key not in current:
                conn.execute("UPDATE products SET active = 0, last_seen_at = ? WHERE product_key = ?", (checked_at, key))
                close_open_history(conn, key, checked_at)
                insert_event(conn, "removed", key, product_to_json(old), None, checked_at)
        for product in changes.added:
            insert_history(conn, product, checked_at, baseline_excluded=0)
            insert_event(conn, "added", product_key(product), None, product_to_json(product), checked_at)
        for old, new in changes.price_changed:
            update_open_history_product_fields(conn, new)
            insert_event(conn, "price_changed", product_key(new), old.price, new.price, checked_at)
        for old, new in changes.detail_changed:
            update_open_history_product_fields(conn, new)
            insert_event(conn, "detail_changed", product_key(new), product_to_json(old), product_to_json(new), checked_at)
        set_state(conn, "last_count", str(len(snapshot.products)))
        set_state(conn, "last_checked_at", checked_at)
        conn.commit()


def upsert_product(conn: sqlite3.Connection, product: Product, checked_at: str, *, active: int, baseline_excluded: int) -> None:
    conn.execute(
        """
        INSERT INTO products (product_key, name, url, color, price, image_url, baseline_excluded, detail_checked_at, purchasable_status, purchasable_checked_at, first_seen_at, last_seen_at, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'unknown', NULL, ?, ?, ?)
        ON CONFLICT(product_key) DO UPDATE SET
            name = excluded.name,
            url = excluded.url,
            color = COALESCE(products.color, excluded.color),
            price = excluded.price,
            image_url = COALESCE(excluded.image_url, products.image_url),
            baseline_excluded = excluded.baseline_excluded,
            detail_checked_at = CASE WHEN products.active = 0 THEN NULL ELSE products.detail_checked_at END,
            detail_failed_at = CASE WHEN products.active = 0 THEN NULL ELSE products.detail_failed_at END,
            detail_retry_after = CASE WHEN products.active = 0 THEN NULL ELSE products.detail_retry_after END,
            detail_failure_count = CASE WHEN products.active = 0 THEN 0 ELSE products.detail_failure_count END,
            purchasable_status = CASE WHEN products.active = 0 THEN 'unknown' ELSE products.purchasable_status END,
            purchasable_checked_at = CASE WHEN products.active = 0 THEN NULL ELSE products.purchasable_checked_at END,
            first_seen_at = CASE WHEN products.active = 0 THEN excluded.first_seen_at ELSE products.first_seen_at END,
            last_seen_at = excluded.last_seen_at,
            active = excluded.active
        """,
        (product_key(product), product.name, product.url, product.color, product.price, product.image_url, baseline_excluded, checked_at, checked_at, active),
    )


def insert_history(conn: sqlite3.Connection, product: Product, checked_at: str, *, baseline_excluded: int) -> None:
    conn.execute(
        """
        INSERT INTO availability_history (product_key, name, url, color, price, image_url, purchasable_status, purchasable_checked_at, available_from, available_until, baseline_excluded)
        VALUES (?, ?, ?, ?, ?, ?, 'unknown', NULL, ?, NULL, ?)
        """,
        (product_key(product), product.name, product.url, product.color, product.price, product.image_url, checked_at, baseline_excluded),
    )


def close_open_history(conn: sqlite3.Connection, key: str, checked_at: str) -> None:
    conn.execute("UPDATE availability_history SET available_until = ? WHERE product_key = ? AND available_until IS NULL", (checked_at, key))


def ensure_open_history(conn: sqlite3.Connection, product: Product, fallback_checked_at: str) -> None:
    key = product_key(product)
    row = conn.execute("SELECT id FROM availability_history WHERE product_key = ? AND available_until IS NULL", (key,)).fetchone()
    if row:
        return
    insert_history(conn, product, fallback_checked_at, baseline_excluded=0)


def update_open_history_product_fields(conn: sqlite3.Connection, product: Product) -> None:
    conn.execute(
        """
        UPDATE availability_history
        SET name = ?, url = ?, color = COALESCE(color, ?), price = ?, image_url = COALESCE(?, image_url)
        WHERE product_key = ? AND available_until IS NULL
        """,
        (product.name, product.url, product.color, product.price, product.image_url, product_key(product)),
    )


def insert_event(conn: sqlite3.Connection, event_type: str, key: str, old_value: str | None, new_value: str | None, created_at: str) -> None:
    conn.execute("INSERT INTO product_events (event_type, product_key, old_value, new_value, created_at) VALUES (?, ?, ?, ?, ?)", (event_type, key, old_value, new_value, created_at))


def load_active_products(db_path: Path) -> dict[str, Product]:
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path) as conn:
        return load_active_products_from_conn(conn)


def load_active_products_from_conn(conn: sqlite3.Connection) -> dict[str, Product]:
    ensure_column(conn, "products", "image_url", "TEXT")
    ensure_column(conn, "products", "baseline_excluded", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "products", "purchasable_status", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(conn, "products", "purchasable_checked_at", "TEXT")
    rows = conn.execute("SELECT product_key, name, url, color, price, image_url, baseline_excluded, purchasable_status, purchasable_checked_at FROM products WHERE active = 1").fetchall()
    return {row[0]: Product(name=row[1], url=row[2], color=row[3], price=row[4], image_url=row[5], baseline_excluded=bool(row[6]), purchasable_status=row[7], purchasable_checked_at=row[8]) for row in rows}


def load_suppressed_product_keys(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    with sqlite3.connect(db_path) as conn:
        return load_suppressed_product_keys_from_conn(conn)


def load_suppressed_product_keys_from_conn(conn: sqlite3.Connection) -> set[str]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS suppressed_products (
            product_key TEXT PRIMARY KEY,
            suppressed_at TEXT NOT NULL,
            reason TEXT
        )
        """
    )
    return {row[0] for row in conn.execute("SELECT product_key FROM suppressed_products").fetchall()}


def remove_missing_suppressed_products(conn: sqlite3.Connection, current_keys: set[str]) -> None:
    suppressed = load_suppressed_product_keys_from_conn(conn)
    missing = suppressed - current_keys
    if not missing:
        return
    placeholders = ",".join("?" for _ in missing)
    conn.execute(f"DELETE FROM suppressed_products WHERE product_key IN ({placeholders})", tuple(missing))


def migrate_json_state_to_database(state_path: Path, db_path: Path) -> None:
    # Existing SQLite data is authoritative; old JSON remains only a fallback artifact.
    return


def export_public_inventory(db_path: Path, export_path: Path) -> None:
    export_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        active_rows = conn.execute(
            """
            SELECT product_key, name, url, price, image_url, first_seen_at, last_seen_at, purchasable_status, purchasable_checked_at
            FROM products WHERE active = 1
            """
        ).fetchall()
        history_rows = conn.execute(
            """
            SELECT product_key, name, url, price, image_url, purchasable_status, purchasable_checked_at, available_from, available_until
            FROM availability_history WHERE available_until IS NOT NULL ORDER BY available_until DESC, id DESC LIMIT 500
            """
        ).fetchall()
        state_rows = dict(conn.execute("SELECT key, value FROM monitor_state").fetchall())
    history: list[dict[str, object]] = []
    seen_history_windows: set[tuple[str, str, str]] = set()
    for row in history_rows:
        item = dict(row)
        key = (item["product_key"], item["available_from"], item["available_until"])
        if key in seen_history_windows:
            continue
        seen_history_windows.add(key)
        history.append(item)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_checked_at": state_rows.get("last_checked_at"),
        "available": sorted(
            [dict(row) for row in active_rows],
            key=lambda item: (
                purchasable_status_rank(item.get("purchasable_status")),
                price_to_number(item.get("price")),
                (item.get("name") or "").lower(),
            ),
        ),
        "history": history,
    }
    export_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def print_inventory(db_path: Path) -> None:
    products = load_active_products(db_path)
    visible = list(products.values())
    baseline = [product for product in products.values() if product.baseline_excluded]
    print(f"visible_products={len(visible)} baseline_excluded_active={len(baseline)}")
    for product in sorted(visible, key=lambda item: item.name.lower()):
        print(format_product(product))


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM monitor_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO monitor_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def notify_access_issue(db_path: Path, error: Exception) -> None:
    issue_scope = access_issue_scope(error)
    today = datetime.now(timezone.utc).date().isoformat()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM monitor_state WHERE key = ?", (f"last_access_alert_date_{issue_scope}",)).fetchone()
        if row and row[0] == today:
            return
        send_email("Hermes Monitor access issue", render_access_issue_body(error), recipients=get_failure_recipients())
        set_state(conn, f"last_access_alert_date_{issue_scope}", today)
        set_state(conn, f"active_access_issue_at_{issue_scope}", datetime.now(timezone.utc).isoformat())
        set_state(conn, f"active_access_issue_{issue_scope}", summarize_access_issue(error))
        conn.commit()


def notify_access_recovered(db_path: Path, snapshot: Snapshot) -> None:
    notify_stage_access_recovered(db_path, stage="category", url=snapshot.url, successful_count=len(snapshot.products))


def notify_stage_access_recovered(db_path: Path, *, stage: str, url: str | None = None, successful_count: int | None = None) -> None:
    issue_scope = normalize_stage_key(stage)
    with sqlite3.connect(db_path) as conn:
        issue_row = conn.execute("SELECT value FROM monitor_state WHERE key = ?", (f"active_access_issue_{issue_scope}",)).fetchone()
        if not issue_row:
            return
        started_row = conn.execute("SELECT value FROM monitor_state WHERE key = ?", (f"active_access_issue_at_{issue_scope}",)).fetchone()
        send_email(
            "Hermes Monitor access recovered",
            render_access_recovered_body(stage=stage, issue_summary=issue_row[0], issue_started_at=started_row[0] if started_row else None, url=url, successful_count=successful_count),
            recipients=get_failure_recipients(),
        )
        conn.execute("DELETE FROM monitor_state WHERE key IN (?, ?, ?)", (f"last_access_alert_date_{issue_scope}", f"active_access_issue_{issue_scope}", f"active_access_issue_at_{issue_scope}"))
        conn.commit()


def render_change_subject(changes: InventoryChanges) -> str:
    parts = []
    if changes.added:
        parts.append(f"+{len(changes.added)} available")
    if changes.removed:
        parts.append(f"-{len(changes.removed)} gone")
    if changes.price_changed:
        parts.append(f"{len(changes.price_changed)} price")
    if changes.detail_changed:
        parts.append(f"{len(changes.detail_changed)} detail")
    return "Hermes inventory changed: " + ", ".join(parts)


def render_change_email_body(changes: InventoryChanges, current: Snapshot) -> str:
    lines = ["Hermes women's bags availability changed.", "", f"Checked at: {current.checked_at}", f"Current visible product links: {len(current.products)}", f"URL: {current.url}", ""]
    if changes.added:
        lines.append("NEWLY AVAILABLE:")
        lines.extend(f"+ {format_product(product)}" for product in sorted(changes.added, key=lambda item: item.name.lower()))
        lines.append("")
    if changes.removed:
        lines.append("NO LONGER ON MAIN PAGE:")
        lines.extend(f"- {format_product(product)}" for product in sorted(changes.removed, key=lambda item: item.name.lower()))
        lines.append("")
    if changes.price_changed:
        lines.append("PRICE CHANGES:")
        for old, new in sorted(changes.price_changed, key=lambda pair: pair[1].name.lower()):
            lines.append(f"* {new.name}: {old.price or '?'} -> {new.price or '?'} | {new.url}")
        lines.append("")
    if changes.detail_changed:
        lines.append("DETAIL CHANGES:")
        for old, new in sorted(changes.detail_changed, key=lambda pair: pair[1].name.lower()):
            lines.append(f"* {old.name} -> {new.name} | {new.url}")
    return "\n".join(lines).strip()


def render_access_issue_body(error: Exception) -> str:
    lines = ["Hermes Monitor hit an access issue and will back off automatically.", "", f"Error: {error}", f"Time: {datetime.now(timezone.utc).isoformat()}"]
    if isinstance(error, RateLimitedError):
        lines.extend(["", f"Failed stage: {error.stage}"])
        if error.url:
            lines.append(f"URL: {error.url}")
    lines.extend(["", "The monitor keeps the last known product data and will retry later with exponential backoff and jitter."])
    return "\n".join(lines)


def summarize_access_issue(error: Exception) -> str:
    if isinstance(error, RateLimitedError):
        return f"stage={error.stage}; error={error}; url={error.url or ''}"
    return str(error)


def render_access_recovered_body(*, stage: str, issue_summary: str, issue_started_at: str | None, url: str | None = None, successful_count: int | None = None) -> str:
    lines = ["Hermes Monitor access has recovered.", "", f"Recovered at: {datetime.now(timezone.utc).isoformat()}", f"Recovered stage: {stage}"]
    if successful_count is not None:
        lines.append(f"Successful product-link count: {successful_count}")
    if url:
        lines.append(f"URL: {url}")
    if issue_started_at:
        lines.append(f"Previous issue first recorded at: {issue_started_at}")
    lines.extend(["", "Previous issue:", issue_summary, "", "Change alerts still go to the normal monitor recipient list."])
    return "\n".join(lines)


def load_snapshot(path: Path) -> Snapshot | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return Snapshot(count=int(data["count"]), products=[Product(**product) for product in data.get("products", []) if product.get("url")], checked_at=data["checked_at"], url=data["url"], etag=data.get("etag"), last_modified=data.get("last_modified"))


def save_snapshot(path: Path, snapshot: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(snapshot), indent=2, ensure_ascii=False) + "\n")


def product_key(product: Product) -> str:
    return product.url


def product_to_json(product: Product) -> str:
    return json.dumps(asdict(product), ensure_ascii=False, sort_keys=True)


def format_product(product: Product) -> str:
    pieces = [product.name]
    if product.price:
        pieces.append(product.price)
    if product.purchasable_status:
        pieces.append(f"purchasable={product.purchasable_status}")
    pieces.append(product.url)
    return " | ".join(pieces)


def price_to_number(value: str | None) -> float:
    if not value:
        return float("inf")
    cleaned = re.sub(r"[^0-9.]", "", value)
    try:
        return float(cleaned)
    except ValueError:
        return float("inf")


def purchasable_status_rank(value: str | None) -> int:
    return {"purchasable": 0, "not_purchasable": 1}.get(value or "unknown", 2)


class RateLimitedError(RuntimeError):
    def __init__(self, message: str, *, stage: str, status_code: int | None = None, url: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code
        self.url = url


def build_request_headers(*, stage: str) -> dict[str, str]:
    user_agents = USER_AGENTS
    if request_queue_for_stage(stage) == "ipv4":
        user_agents = USER_AGENTS[:2]
    elif request_queue_for_stage(stage) == "ipv6":
        user_agents = USER_AGENTS[1:]
    return {
        "User-Agent": random.choice(user_agents),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
        "Cache-Control": "no-cache",
        "Connection": "close",
        "DNT": random.choice(["1", "0"]),
        "Upgrade-Insecure-Requests": "1",
    }



def wait_for_request_slot(db_path: Path, *, stage: str, min_gap_seconds: int = MIN_REQUEST_GAP_SECONDS) -> None:
    queue = request_queue_for_stage(stage)
    request_at_key = f"last_http_request_at_{queue}"
    request_stage_key = f"last_http_request_stage_{queue}"
    while True:
        now = datetime.now(timezone.utc)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT value FROM monitor_state WHERE key = ?", (request_at_key,)).fetchone()
            if row:
                last = parse_datetime(row[0])
                if last is not None:
                    elapsed = (now - last).total_seconds()
                    if elapsed < min_gap_seconds:
                        wait_seconds = int(min_gap_seconds - elapsed) + random.randint(0, 30)
                        print(f"[{now_local()}] waiting {wait_seconds}s before {stage} request on {queue} queue to keep requests at least {min_gap_seconds}s apart", flush=True)
                        time.sleep(wait_seconds)
                        continue
            set_state(conn, request_at_key, now.isoformat())
            set_state(conn, request_stage_key, stage)
            conn.commit()
            return


def request_queue_for_stage(stage: str) -> str:
    normalized = normalize_stage_key(stage)
    if normalized == "category":
        return "ipv4"
    if normalized == "product_detail":
        return "ipv6"
    return "default"


def address_family_for_stage(stage: str) -> socket.AddressFamily | None:
    queue = request_queue_for_stage(stage)
    if queue == "ipv4":
        return socket.AF_INET
    if queue == "ipv6":
        return socket.AF_INET6
    return None


@contextlib.contextmanager
def force_address_family(family: socket.AddressFamily | None):
    if family is None:
        yield
        return
    original_getaddrinfo = socket.getaddrinfo

    def family_getaddrinfo(host, port, family_arg=0, type=0, proto=0, flags=0):
        return original_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = family_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


def access_issue_scope(error: Exception) -> str:
    if isinstance(error, RateLimitedError):
        return normalize_stage_key(error.stage)
    return "general"


def normalize_stage_key(stage: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", stage.lower()).strip("_") or "general"


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def interval_with_jitter(interval_seconds: int, jitter_seconds: int) -> int:
    return interval_seconds + random.randint(0, max(0, jitter_seconds))


def exponential_backoff_with_jitter(attempt: int, *, base_seconds: int = BACKOFF_BASE_SECONDS, cap_seconds: int = BACKOFF_CAP_SECONDS) -> int:
    window = min(cap_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    lower_bound = max(1, min(base_seconds, window))
    return random.randint(lower_bound, max(lower_bound, window))


def send_email(subject: str, body: str, *, recipients: list[str] | None = None) -> None:
    host = require_env("HERMES_SMTP_HOST")
    port = int(os.environ.get("HERMES_SMTP_PORT", "587"))
    username = require_env("HERMES_SMTP_USERNAME")
    password = require_env("HERMES_SMTP_PASSWORD")
    sender = os.environ.get("HERMES_EMAIL_FROM", username)
    if recipients is None:
        recipients = [item.strip() for item in require_env("HERMES_EMAIL_TO").split(",") if item.strip()]
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port) as smtp:
            smtp.starttls(context=context)
            smtp.login(username, password)
            smtp.send_message(message)


def get_failure_recipients() -> list[str]:
    configured = require_env("HERMES_FAILURE_EMAIL_TO")
    return [item.strip() for item in configured.split(",") if item.strip()]


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def absolute_hermes_url(href: str | None) -> str | None:
    if not href:
        return None
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://www.hermes.com{href}"
    return href


def normalize_optional(value: str | None) -> str:
    return normalize_text(value or "")


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def normalize_multiline(value: str) -> str:
    lines = [normalize_text(line) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    main()
