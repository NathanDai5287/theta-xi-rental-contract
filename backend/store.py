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
import hashlib
import math
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from flask import Flask, g

from generators.base import normalize_org_name

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
    snapshot         TEXT NOT NULL,
    deleted_at       TEXT
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

CREATE TABLE IF NOT EXISTS order_create_keys (
    request_key TEXT PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
    request_hash TEXT NOT NULL
);

-- A rental has at most one of each document. Regenerating a PDF supersedes
-- the previous one rather than adding a second: two deposit invoices on an
-- order would double-count in the archive's ledger and misreport the balance.
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_order_kind ON documents(order_id, kind);

CREATE TABLE IF NOT EXISTS signing_revisions (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL,
    request_key TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    signers TEXT NOT NULL,
    fields TEXT NOT NULL,
    state TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    envelope_id TEXT UNIQUE,
    recipients TEXT NOT NULL DEFAULT '[]',
    item_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(order_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_signing_order ON signing_revisions(order_id, revision);

CREATE TABLE IF NOT EXISTS signing_link_delivery (
    revision_id TEXT NOT NULL REFERENCES signing_revisions(id) ON DELETE CASCADE,
    recipient_email TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (revision_id, recipient_email)
);
"""


def _db_path() -> str:
    return os.environ.get("ORDERS_DB_PATH") or str(DEFAULT_DB_PATH)


def _protect_db_files(path: str) -> None:
    """The archive now holds private signer links as well as order details."""
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(path + suffix)
        if candidate.exists():
            os.chmod(candidate, 0o600)


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
    _protect_db_files(path)
    return conn


def init_db(path: str | None = None) -> None:
    """Create the schema if absent. Safe to call repeatedly (idempotent).

    Also flips on WAL mode, which — unlike `foreign_keys` — is persisted in
    the database file itself, so it only needs to be requested once here
    rather than per-connection.
    """
    target = path or os.environ.get("ORDERS_DB_PATH") or str(DEFAULT_DB_PATH)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        if "request_hash" not in {row[1] for row in conn.execute("PRAGMA table_info(order_create_keys)")}:
            conn.execute("ALTER TABLE order_create_keys ADD COLUMN request_hash TEXT")
        if "deleted_at" not in {row[1] for row in conn.execute("PRAGMA table_info(orders)")}:
            conn.execute("ALTER TABLE orders ADD COLUMN deleted_at TEXT")
        conn.commit()
        _protect_db_files(target)
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
    # Python's json.loads accepts NaN/Infinity literals; SQLite would store
    # them as NULL/Inf and jsonify would emit non-standard tokens later.
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
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
        "SELECT * FROM orders WHERE deleted_at IS NULL ORDER BY event_date DESC, created_at DESC"
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
    return conn.execute("SELECT * FROM orders WHERE id = ? AND deleted_at IS NULL", (order_id,)).fetchone()


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
    request_key = body.get("requestKey")
    if request_key is not None:
        if not isinstance(request_key, str) or not 12 <= len(request_key) <= 100:
            raise ValueError("requestKey must be 12–100 characters")
        # The UI snapshot also contains workspace bookkeeping which changes
        # after the first save without changing the order being requested.
        keyed_body = {key: value for key, value in body.items() if key != "requestKey"}
        if isinstance(keyed_body.get("snapshot"), dict):
            keyed_body["snapshot"] = {key: value for key, value in keyed_body["snapshot"].items()
                                      if key not in ("currentOrderId", "orderCreateRequestKey", "loadedOrderIdentity")}
        request_hash = hashlib.sha256(json.dumps(keyed_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        # Serialize creation for this SQLite database. If the HTTP response
        # gets lost, a retry with the same key returns the already-saved order.
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute("SELECT order_id, request_hash FROM order_create_keys WHERE request_key = ?", (request_key,)).fetchone()
        if prior:
            conn.commit()
            if prior["request_hash"] != request_hash:
                raise ValueError("requestKey belongs to different order details; reconcile the original save")
            saved = get_order(conn, prior["order_id"])
            if not saved:
                raise ValueError("this order was deleted; start a new order instead of retrying its original save")
            return saved

    club_name = body.get("clubName")
    if not isinstance(club_name, str) or not club_name.strip():
        raise ValueError("clubName is required")
    # Print-normalized so the archive, the finance ledger, and any document
    # regenerated from this order all show the same tidy name.
    club_name = normalize_org_name(club_name)
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
    # Last of each kind wins — see the unique index on (order_id, kind).
    by_kind: dict[str, dict[str, Any]] = {}
    for d in raw_documents:
        validated = _validate_document_input(d)
        by_kind[validated["kind"]] = validated
    documents = list(by_kind.values())

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
            order_id, ts, ts, club_name, event_date,
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

    if request_key is not None:
        conn.execute("INSERT INTO order_create_keys (request_key, order_id, request_hash) VALUES (?, ?, ?)",
                     (request_key, order_id, request_hash))

    conn.commit()
    return get_order(conn, order_id)  # type: ignore[return-value]


_PATCHABLE_FIELDS = {
    "clubName": ("club_name", lambda v: normalize_org_name(v) if isinstance(v, str) and v.strip() else _raise("clubName must be a non-empty string")),
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
    current = _fetch_order_row(conn, order_id)
    if current is None:
        return None

    latest = conn.execute("SELECT state FROM signing_revisions WHERE order_id = ? ORDER BY revision DESC LIMIT 1", (order_id,)).fetchone()
    if latest and latest["state"] in ("awaiting_signatures", "preparing_completed_copy", "signed"):
        columns = {"clubName": "club_name", "eventDate": "event_date",
                   "rentalPrice": "rental_price", "depositAmount": "deposit_amount"}
        changed = any(key in body and body[key] != current[column] for key, column in columns.items())
        if "snapshot" in body and isinstance(body["snapshot"], dict):
            old = json.loads(current["snapshot"])
            new = body["snapshot"]
            contract_keys = ("clubs", "eventDate", "numGuests", "startTime", "endTime", "depositAmount",
                             "maxGuests", "monitors", "areas", "cleared", "guestList", "soundSystem",
                             "lightingSystem", "pricingSelections", "finalPrice", "overrides",
                             "pricingBreakdown", "rentalPrice", "contractSigners", "chapterSignerName",
                             "chapterSignerEmail", "contractPresign")
            changed = changed or any(old.get(key) != new.get(key) for key in contract_keys)
        if changed:
            raise ValueError("prepare a new contract revision before changing signed or pending terms")

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
    """Remove an order, retaining exact signing records after requests are retired."""
    row = conn.execute("SELECT deleted_at FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        return False
    if row["deleted_at"]:
        return True
    if conn.execute("SELECT 1 FROM signing_revisions WHERE order_id = ? LIMIT 1", (order_id,)).fetchone():
        if conn.execute("SELECT 1 FROM signing_revisions WHERE order_id = ? AND state NOT IN ('cancelled', 'signed') LIMIT 1", (order_id,)).fetchone():
            raise ValueError("retire outstanding signing requests before deleting this order")
        stamp = now_iso()
        conn.execute("UPDATE orders SET deleted_at = ?, updated_at = ? WHERE id = ?", (stamp, stamp, order_id))
    else:
        conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    conn.commit()
    return True


def add_document(conn: sqlite3.Connection, order_id: str, body: Any) -> dict[str, Any] | None:
    """Record a document against an order, replacing any previous one of the
    same kind. Returns the updated Order, or None if the order is unknown.

    Upsert rather than append so the endpoint is idempotent: the documents
    step re-sends every document it has generated each time the user saves,
    and a regenerated PDF supersedes its predecessor instead of leaving two
    of the same kind on the order (which would double-count in the ledger).
    """
    if _fetch_order_row(conn, order_id) is None:
        return None
    doc = _validate_document_input(body)
    if doc["kind"] == "contract" and conn.execute(
        "SELECT 1 FROM signing_revisions WHERE order_id = ? AND state IN ('awaiting_signatures', 'preparing_completed_copy', 'signed') LIMIT 1",
        (order_id,),
    ).fetchone():
        raise ValueError("contract documents with signing history cannot be replaced; prepare a new revision")

    conn.execute(
        "DELETE FROM documents WHERE order_id = ? AND kind = ?",
        (order_id, doc["kind"]),
    )
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
