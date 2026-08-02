"""
SQLite-backed storage for the order archive.

Each order is a rental record: event details + pricing snapshot + every PDF
document issued for it (contract, invoices, credit memo). This module owns
all SQL; `app.py` only calls the functions below and never touches sqlite3
directly, so the storage shape can change without rippling into routing.

Connection model: one `sqlite3.Connection` per Flask request, opened lazily
and stashed on `flask.g`, closed in a `teardown_appcontext` hook registered
by `init_app`. Connections are never shared across threads/requests — SQLite
connections are not safe for that, and Flask's dev/prod servers may serve
requests on different threads.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from flask import Flask, g

# backend/store.py -> backend/
BACKEND_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BACKEND_ROOT / "orders.db"

DOCUMENT_KINDS = ("contract", "deposit_invoice", "rental_invoice", "credit_memo")
STATUS_OVERRIDES = ("draft", "contracted", "invoiced", "completed", "cancelled")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id               TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    club_name        TEXT NOT NULL,
    event_date       TEXT NOT NULL,
    rental_price     REAL,
    deposit_amount   REAL,
    status_override  TEXT,
    notes            TEXT NOT NULL DEFAULT '',
    snapshot         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    order_id      TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    number        TEXT NOT NULL,
    filename      TEXT NOT NULL,
    amount        REAL,
    generated_at  TEXT NOT NULL,
    payload       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_order_id ON documents(order_id);
"""


def _db_path() -> str:
    import os

    return os.environ.get("ORDERS_DB_PATH") or str(DEFAULT_DB_PATH)


def _connect() -> sqlite3.Connection:
    """Open a fresh connection with the pragmas this schema relies on.

    `foreign_keys` defaults OFF per SQLite connection (not a database-level
    setting), so it must be set here every time, not just once at startup.
    """
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | None = None) -> None:
    """Create the schema if absent. Safe to call repeatedly (idempotent).

    Also flips on WAL mode, which — unlike `foreign_keys` — is persisted in
    the database file itself, so it only needs to be requested once here
    rather than per-connection.
    """
    import os

    target = path or os.environ.get("ORDERS_DB_PATH") or str(DEFAULT_DB_PATH)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def init_app(app: Flask) -> None:
    """Wire the schema bootstrap + per-request connection teardown into `app`."""
    init_db()

    @app.teardown_appcontext
    def _close_db(_exc: BaseException | None) -> None:
        conn = g.pop("_orders_db", None)
        if conn is not None:
            conn.close()


def get_conn() -> sqlite3.Connection:
    """Return the request-scoped connection, opening one on first use."""
    if "_orders_db" not in g:
        g._orders_db = _connect()
    return g._orders_db


def new_id(prefix: str) -> str:
    """`ord_` / `doc_` + a short URL-safe random token."""
    return f"{prefix}_{secrets.token_hex(8)}"


def now_iso() -> str:
    """Timezone-aware UTC timestamp, ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


# ── validation ────────────────────────────────────────────────────────────

def _require_json_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


_EVENT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_event_date(value: Any) -> str:
    """Require a strictly zero-padded YYYY-MM-DD.

    The regex is not redundant with strptime: `%Y-%m-%d` happily accepts
    "2026-5-5", which would then be stored unpadded. `event_date` is a TEXT
    column ordered lexicographically by `list_orders`, so an unpadded value
    sorts out of place ("2026-5-5" > "2026-12-01") and the archive silently
    lists that rental in the wrong position.
    """
    if not isinstance(value, str):
        raise ValueError("eventDate must be a YYYY-MM-DD string")
    if not _EVENT_DATE_RE.match(value):
        raise ValueError("eventDate must match YYYY-MM-DD (zero-padded)")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError("eventDate must be a real calendar date")
    return value


def _validate_number_or_none(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number or null")
    return float(value)


def _validate_status_override(value: Any) -> str | None:
    if value is None:
        return None
    if value not in STATUS_OVERRIDES:
        raise ValueError(f"statusOverride must be null or one of {STATUS_OVERRIDES}")
    return value


def _validate_document_kind(value: Any) -> str:
    if value not in DOCUMENT_KINDS:
        raise ValueError(f"kind must be one of {DOCUMENT_KINDS}")
    return value


def _validate_document_input(doc: Any) -> dict[str, Any]:
    """Validate an OrderDocument-without-id. `generatedAt` is caller-supplied
    (it's the moment the PDF was actually generated, upstream of this call),
    not minted here — only `id` is server-generated.
    """
    if not isinstance(doc, dict):
        raise ValueError("document must be a JSON object")
    kind = _validate_document_kind(doc.get("kind"))
    number = doc.get("number")
    filename = doc.get("filename")
    if not isinstance(number, str) or not number.strip():
        raise ValueError("document.number is required")
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("document.filename is required")
    amount = _validate_number_or_none(doc.get("amount"), "document.amount")
    generated_at = doc.get("generatedAt")
    if not isinstance(generated_at, str) or not generated_at.strip():
        raise ValueError("document.generatedAt is required")
    payload = _require_json_object(doc.get("payload"), "document.payload")
    return {
        "kind": kind,
        "number": number,
        "filename": filename,
        "amount": amount,
        "generatedAt": generated_at,
        "payload": payload,
    }


# ── serialization (snake_case row -> camelCase dict) ────────────────────

def _document_to_json(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "number": row["number"],
        "filename": row["filename"],
        "amount": row["amount"],
        "generatedAt": row["generated_at"],
        "payload": json.loads(row["payload"]),
    }


def _order_to_json(row: sqlite3.Row, documents: Iterable[sqlite3.Row]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "clubName": row["club_name"],
        "eventDate": row["event_date"],
        "rentalPrice": row["rental_price"],
        "depositAmount": row["deposit_amount"],
        "statusOverride": row["status_override"],
        "notes": row["notes"],
        "snapshot": json.loads(row["snapshot"]),
        "documents": [_document_to_json(d) for d in documents],
    }


def _order_summary_to_json(row: sqlite3.Row, doc_kinds: list[str], doc_count: int) -> dict[str, Any]:
    return {
        "id": row["id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "clubName": row["club_name"],
        "eventDate": row["event_date"],
        "rentalPrice": row["rental_price"],
        "depositAmount": row["deposit_amount"],
        "statusOverride": row["status_override"],
        "notes": row["notes"],
        "documentCount": doc_count,
        "documentKinds": doc_kinds,
    }


# ── queries ──────────────────────────────────────────────────────────────

def list_orders(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All orders, newest event first, as OrderSummary dicts."""
    order_rows = conn.execute(
        "SELECT * FROM orders ORDER BY event_date DESC, created_at DESC"
    ).fetchall()

    doc_rows = conn.execute(
        "SELECT order_id, kind FROM documents ORDER BY order_id, generated_at ASC, rowid ASC"
    ).fetchall()
    # `documentKinds` is distinct and in DOCUMENT_KINDS order, not generation
    # order: the list view renders it as "which of the four exist", so the
    # sequence has to be stable regardless of the order they were issued in.
    kinds_by_order: dict[str, set[str]] = {}
    counts_by_order: dict[str, int] = {}
    for d in doc_rows:
        kinds_by_order.setdefault(d["order_id"], set()).add(d["kind"])
        counts_by_order[d["order_id"]] = counts_by_order.get(d["order_id"], 0) + 1

    def ordered_kinds(order_id: str) -> list[str]:
        present = kinds_by_order.get(order_id, set())
        return [k for k in DOCUMENT_KINDS if k in present]

    return [
        _order_summary_to_json(
            row,
            ordered_kinds(row["id"]),
            counts_by_order.get(row["id"], 0),
        )
        for row in order_rows
    ]


def _fetch_order_row(conn: sqlite3.Connection, order_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def _fetch_documents(conn: sqlite3.Connection, order_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM documents WHERE order_id = ? ORDER BY generated_at ASC, rowid ASC",
        (order_id,),
    ).fetchall()


def get_order(conn: sqlite3.Connection, order_id: str) -> dict[str, Any] | None:
    """Full Order dict (with documents/snapshot), or None if unknown."""
    row = _fetch_order_row(conn, order_id)
    if row is None:
        return None
    return _order_to_json(row, _fetch_documents(conn, order_id))


def create_order(conn: sqlite3.Connection, body: Any) -> dict[str, Any]:
    """Validate + insert a new order (and any inline documents). Returns the Order dict."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")

    club_name = body.get("clubName")
    if not isinstance(club_name, str) or not club_name.strip():
        raise ValueError("clubName is required")
    event_date = _validate_event_date(body.get("eventDate"))
    rental_price = _validate_number_or_none(body.get("rentalPrice"), "rentalPrice")
    deposit_amount = _validate_number_or_none(body.get("depositAmount"), "depositAmount")
    notes = body.get("notes") if body.get("notes") is not None else ""
    if not isinstance(notes, str):
        raise ValueError("notes must be a string")
    snapshot = _require_json_object(body.get("snapshot"), "snapshot")

    raw_documents = body.get("documents") or []
    if not isinstance(raw_documents, list):
        raise ValueError("documents must be an array")
    documents = [_validate_document_input(d) for d in raw_documents]

    order_id = new_id("ord")
    ts = now_iso()

    conn.execute(
        """
        INSERT INTO orders (
            id, created_at, updated_at, club_name, event_date,
            rental_price, deposit_amount, status_override, notes, snapshot
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id, ts, ts, club_name.strip(), event_date,
            rental_price, deposit_amount, None, notes, json.dumps(snapshot),
        ),
    )

    for doc in documents:
        doc_id = new_id("doc")
        conn.execute(
            """
            INSERT INTO documents (
                id, order_id, kind, number, filename, amount, generated_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id, order_id, doc["kind"], doc["number"], doc["filename"],
                doc["amount"], doc["generatedAt"], json.dumps(doc["payload"]),
            ),
        )

    conn.commit()
    return get_order(conn, order_id)  # type: ignore[return-value]


_PATCHABLE_FIELDS = {
    "clubName": ("club_name", lambda v: v if isinstance(v, str) and v.strip() else _raise("clubName must be a non-empty string")),
    "eventDate": ("event_date", _validate_event_date),
    "rentalPrice": ("rental_price", lambda v: _validate_number_or_none(v, "rentalPrice")),
    "depositAmount": ("deposit_amount", lambda v: _validate_number_or_none(v, "depositAmount")),
    "statusOverride": ("status_override", _validate_status_override),
    "notes": ("notes", lambda v: v if isinstance(v, str) else _raise("notes must be a string")),
    "snapshot": ("snapshot", lambda v: json.dumps(_require_json_object(v, "snapshot"))),
}


def _raise(msg: str) -> Any:
    raise ValueError(msg)


def update_order(conn: sqlite3.Connection, order_id: str, body: Any) -> dict[str, Any] | None:
    """Partial update. Only keys present in `body` are touched.

    `statusOverride` is legitimately nullable ("derive it"), so presence is
    checked with `in body` rather than truthiness — a present-and-null key
    must still clear the column.
    """
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    if _fetch_order_row(conn, order_id) is None:
        return None

    set_clauses: list[str] = []
    params: list[Any] = []
    for json_key, (column, validator) in _PATCHABLE_FIELDS.items():
        if json_key not in body:
            continue
        value = validator(body[json_key])
        set_clauses.append(f"{column} = ?")
        params.append(value)

    set_clauses.append("updated_at = ?")
    params.append(now_iso())
    params.append(order_id)

    conn.execute(
        f"UPDATE orders SET {', '.join(set_clauses)} WHERE id = ?",
        params,
    )
    conn.commit()
    return get_order(conn, order_id)


def delete_order(conn: sqlite3.Connection, order_id: str) -> bool:
    """Delete an order (and its documents, via ON DELETE CASCADE). Returns whether it existed."""
    if _fetch_order_row(conn, order_id) is None:
        return False
    conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    conn.commit()
    return True


def add_document(conn: sqlite3.Connection, order_id: str, body: Any) -> dict[str, Any] | None:
    """Append a document to an order. Returns the updated Order, or None if the order is unknown."""
    if _fetch_order_row(conn, order_id) is None:
        return None
    doc = _validate_document_input(body)

    doc_id = new_id("doc")
    conn.execute(
        """
        INSERT INTO documents (
            id, order_id, kind, number, filename, amount, generated_at, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, order_id, doc["kind"], doc["number"], doc["filename"], doc["amount"], doc["generatedAt"], json.dumps(doc["payload"])),
    )
    conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (now_iso(), order_id))
    conn.commit()
    return get_order(conn, order_id)
