from __future__ import annotations

import argparse
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from html import unescape
import re

from hermes_monitor import (
    DEFAULT_DB_PATH,
    DEFAULT_EXPORT_PATH,
    Product,
    export_public_inventory,
    fetch_html,
    format_product,
    init_database,
    insert_event,
    wait_for_request_slot,
    product_key,
    product_to_json,
    send_email,
    RateLimitedError,
    notify_access_issue,
    notify_stage_access_recovered,
)

RECHECK_AFTER_SECONDS = 3 * 60 * 60
DEFAULT_INTERVAL_SECONDS = 5 * 60
DEFAULT_INITIAL_DELAY_MIN = 5 * 60
DEFAULT_INITIAL_DELAY_MAX = 10 * 60
DEFAULT_BETWEEN_PRODUCTS_MIN = 5 * 60
DEFAULT_BETWEEN_PRODUCTS_MAX = 7 * 60
DETAIL_FAILURE_BASE_COOLDOWN_SECONDS = 30 * 60
DETAIL_FAILURE_MAX_COOLDOWN_SECONDS = 6 * 60 * 60
UNAVAILABLE_MARKERS = [
    "unfortunately this product is no longer available",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Politely verify long-visible Hermes products by detail page.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--export", type=Path, default=DEFAULT_EXPORT_PATH)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--max-per-run", type=int, default=1)
    parser.add_argument("--initial-delay-min", type=int, default=DEFAULT_INITIAL_DELAY_MIN)
    parser.add_argument("--initial-delay-max", type=int, default=DEFAULT_INITIAL_DELAY_MAX)
    args = parser.parse_args()

    init_database(args.db)
    delay = random.randint(args.initial_delay_min, max(args.initial_delay_min, args.initial_delay_max))
    print(f"[{now_local()}] detail sweeper waiting {delay}s before detail checks", flush=True)
    time.sleep(delay)
    while True:
        checked, changed = sweep_once(args.db, args.export, max_per_run=args.max_per_run)
        print(f"[{now_local()}] detail sweeper checked={checked} status_changed={changed}", flush=True)
        if not args.loop:
            return
        sleep_seconds = args.interval + random.randint(0, 2 * 60)
        print(f"[{now_local()}] detail sweeper sleeping {sleep_seconds}s", flush=True)
        time.sleep(sleep_seconds)


def sweep_once(db_path: Path, export_path: Path, *, max_per_run: int) -> tuple[int, int]:
    candidates = load_candidates(db_path, max_per_run=max_per_run)
    checked = 0
    changed: list[tuple[Product, str, str]] = []
    for product in candidates:
        if checked:
            delay = random.randint(DEFAULT_BETWEEN_PRODUCTS_MIN, DEFAULT_BETWEEN_PRODUCTS_MAX)
            print(f"[{now_local()}] detail sweeper waiting {delay}s before next product", flush=True)
            time.sleep(delay)
        wait_for_request_slot(db_path, stage="product detail")
        print(f"[{now_local()}] detail sweeper checking {product.name}", flush=True)
        checked += 1
        old_status = product.purchasable_status or "unknown"
        try:
            new_status = product_detail_status(product.url)
        except Exception as error:
            if isinstance(error, RateLimitedError):
                notify_access_issue(db_path, error)
            mark_detail_failure(db_path, product, error)
            print(f"[{now_local()}] detail sweeper failed {product.name}: {error}", flush=True)
            continue
        update_purchasable_status(db_path, product, new_status)
        notify_stage_access_recovered(db_path, stage="product detail", url=product.url, successful_count=1)
        if old_status != new_status and is_notifiable_status_transition(old_status, new_status):
            changed.append((product, old_status, new_status))
    if checked:
        export_public_inventory(db_path, export_path)
    if changed:
        send_email("Hermes purchasable status changed", render_status_email(changed))
    return checked, len(changed)


def load_candidates(db_path: Path, *, max_per_run: int) -> list[Product]:
    recheck_before = (datetime.now(timezone.utc) - timedelta(seconds=RECHECK_AFTER_SECONDS)).isoformat()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.product_key, p.name, p.url, p.color, p.price, p.image_url, p.purchasable_status, p.purchasable_checked_at
            FROM products p
            JOIN availability_history h ON h.product_key = p.product_key AND h.available_until IS NULL
            WHERE p.active = 1
              AND (p.purchasable_status = 'unknown' OR p.purchasable_checked_at IS NULL OR p.purchasable_checked_at <= ?)
              AND (p.detail_retry_after IS NULL OR p.detail_retry_after <= ?)
            ORDER BY
                CASE WHEN p.purchasable_status = 'unknown' THEN 0 ELSE 1 END ASC,
                CASE WHEN p.purchasable_status = 'unknown' AND p.purchasable_checked_at IS NULL THEN 0 ELSE 1 END ASC,
                CASE WHEN p.purchasable_status = 'unknown' THEN h.available_from ELSE NULL END DESC,
                CASE WHEN p.purchasable_checked_at IS NULL THEN 0 ELSE 1 END ASC,
                p.purchasable_checked_at ASC,
                h.available_from ASC
            LIMIT ?
            """,
            (recheck_before, datetime.now(timezone.utc).isoformat(), max_per_run),
        ).fetchall()
    return [Product(name=row[1], url=row[2], color=row[3], price=row[4], image_url=row[5], purchasable_status=row[6], purchasable_checked_at=row[7]) for row in rows]


def product_detail_status(url: str) -> str:
    html, _, _ = fetch_html(url, stage="product detail")
    text = normalize_detail_text(html)
    if any(marker in text for marker in UNAVAILABLE_MARKERS):
        return "not_purchasable"
    return "purchasable"


def normalize_detail_text(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).lower().split())


def mark_detail_failure(db_path: Path, product: Product, error: Exception) -> None:
    failed_at = datetime.now(timezone.utc)
    key = product_key(product)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COALESCE(detail_failure_count, 0) FROM products WHERE product_key = ?", (key,)).fetchone()
        failure_count = (row[0] if row else 0) + 1
        if isinstance(error, RateLimitedError):
            cooldown_seconds = min(DETAIL_FAILURE_MAX_COOLDOWN_SECONDS, DETAIL_FAILURE_BASE_COOLDOWN_SECONDS * (2 ** max(0, failure_count - 1)))
        else:
            cooldown_seconds = DETAIL_FAILURE_BASE_COOLDOWN_SECONDS
        retry_after = failed_at + timedelta(seconds=cooldown_seconds)
        conn.execute(
            "UPDATE products SET detail_failed_at = ?, detail_retry_after = ?, detail_failure_count = ? WHERE product_key = ?",
            (failed_at.isoformat(), retry_after.isoformat(), failure_count, key),
        )
        conn.commit()
        print(f"[{now_local()}] detail sweeper cooldown {product.name} for {cooldown_seconds}s after failure #{failure_count}", flush=True)

def update_purchasable_status(db_path: Path, product: Product, status: str) -> None:
    if status not in {"purchasable", "not_purchasable"}:
        raise ValueError(f"Unsupported purchasable status: {status}")
    checked_at = datetime.now(timezone.utc).isoformat()
    key = product_key(product)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE products SET detail_checked_at = ?, detail_failed_at = NULL, detail_retry_after = NULL, detail_failure_count = 0, purchasable_status = ?, purchasable_checked_at = ? WHERE product_key = ?",
            (checked_at, status, checked_at, key),
        )
        conn.execute(
            "UPDATE availability_history SET purchasable_status = ?, purchasable_checked_at = ? WHERE product_key = ? AND available_until IS NULL",
            (status, checked_at, key),
        )
        insert_event(conn, "purchasable_status", key, product.purchasable_status or "unknown", status, checked_at)
        conn.commit()


def is_notifiable_status_transition(old: str, new: str) -> bool:
    notifiable = {"purchasable", "not_purchasable"}
    return old in notifiable and new in notifiable and old != new


def render_status_email(changes: list[tuple[Product, str, str]]) -> str:
    lines = ["Hermes product purchasable status changed.", ""]
    for product, old, new in changes:
        lines.append(f"- {product.name}: {old} -> {new} | {product.url}")
    return "\n".join(lines)


def now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    main()
