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
import hmac
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
    approved_order_patch TEXT,
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
        if "source_snapshot" not in {row[1] for row in conn.execute("PRAGMA table_info(documents)")}:
            conn.execute("ALTER TABLE documents ADD COLUMN source_snapshot TEXT")
        if "approved_order_patch" not in {row[1] for row in conn.execute("PRAGMA table_info(signing_revisions)")}:
            conn.execute("ALTER TABLE signing_revisions ADD COLUMN approved_order_patch TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_order_document_context ON orders(json_extract(snapshot, '$.documentContextId')) WHERE json_extract(snapshot, '$.documentContextId') IS NOT NULL AND json_extract(snapshot, '$.documentContextId') != ''")
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
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{field} must contain finite JSON values") from exc
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
        "sourceSnapshot": doc.get("sourceSnapshot"),
        "generationReceipt": doc.get("generationReceipt"),
    }


# Browser bookkeeping is not part of approved order terms.
_BOOKKEEPING = {"currentOrderId", "orderCreateRequestKey", "loadedOrderIdentity", "orderDraftIntent", "lastDepositInvoiceNumber"}

def _terms(snapshot):
    return {key: value for key, value in snapshot.items() if key not in _BOOKKEEPING}

def _clubs_display(clubs):
    if not isinstance(clubs, list):
        raise ValueError("organizations must be an array")
    names = [normalize_org_name(name) for name in clubs if isinstance(name, str) and name.strip()]
    if len(names) < 2:
        return "".join(names)
    if len(names) == 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]

def _validate_snapshot_owner(snapshot, order_id, club_name, event_date, rental_price=None, deposit_amount=None):
    if isinstance(snapshot.get("contractSigners"), list):
        for signer in snapshot["contractSigners"]:
            if not isinstance(signer, dict) or not all(isinstance(signer.get(key), str) for key in ("fullName", "email", "club")):
                raise ValueError("snapshot representatives must include a name, email, and club")
    owner = snapshot.get("currentOrderId")
    if owner and owner != order_id:
        raise ValueError("snapshot belongs to another order")
    if "clubs" in snapshot and _clubs_display(snapshot["clubs"]) != club_name:
        raise ValueError("snapshot organizations do not match the order")
    if snapshot.get("eventDate") and snapshot["eventDate"] != event_date:
        raise ValueError("snapshot event date does not match the order")
    for field, expected in (("rentalPrice", rental_price), ("depositAmount", deposit_amount)):
        if snapshot.get(field) not in (None, "") and (expected is None or _money(snapshot[field]) != _money(expected)):
            raise ValueError("snapshot pricing does not match the order")


def _money(value):
    result = float(str(value).replace("$", "").replace(",", "").strip())
    if not math.isfinite(result):
        raise ValueError("document amount must be finite")
    return result

def document_receipt(kind, payload, source, filename):
    key = os.environ.get("ADMIN_KEY")
    if not key:
        raise ValueError("document receipt signing is not configured")
    message = json.dumps({"kind": kind, "payload": payload, "source": _terms(source), "filename": filename}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()

def assert_contract_snapshot(payload, snapshot):
    for field, key in (("guest_list", "guestList"), ("sound_system", "soundSystem"), ("lighting_system", "lightingSystem"), ("sign", "contractPresign")):
        if key in snapshot and payload.get(field, False) != snapshot[key]:
            raise ValueError("contract options do not match approved terms")
    if "numGuests" in snapshot and payload.get("max_guests") is not None:
        if _money(payload["max_guests"]) != max(200, _money(snapshot["numGuests"])):
            raise ValueError("contract guest limit does not match approved terms")
    if isinstance(snapshot.get("areas"), dict):
        areas = sorted(key for key, enabled in snapshot["areas"].items() if enabled)
        if sorted(payload.get("areas", [])) != areas:
            raise ValueError("contract areas do not match approved terms")
        if "cleared" in snapshot and {key: payload.get("cleared", {}).get(key, False) for key in areas} != {key: snapshot["cleared"].get(key, False) for key in areas}:
            raise ValueError("contract clearing terms do not match approved terms")
    if isinstance(snapshot.get("pricingSelections"), dict) and "cleanup" in snapshot["pricingSelections"]:
        tier = "full" if min(max(snapshot["pricingSelections"]["cleanup"], 0), 1) == 1 else "basic"
        if payload.get("cleanup_tier", "basic") != tier:
            raise ValueError("contract cleanup does not match approved terms")
    if isinstance(snapshot.get("contractSigners"), list):
        expected = [{"fullName": s["fullName"].strip(), "email": s["email"].strip(), "club": normalize_org_name(s["club"]), "role": "club"} for s in snapshot["contractSigners"]]
        if expected and not snapshot.get("contractPresign"):
            expected.append({"fullName": str(snapshot.get("chapterSignerName", "")).strip(), "email": str(snapshot.get("chapterSignerEmail", "")).strip(), "club": "Theta Xi Fraternity", "role": "chapter"})
        if payload.get("signers", []) != expected:
            raise ValueError("contract recipients do not match approved terms")

def _validate_document_owner(doc, snapshot, club_name, event_date, verify_receipt=False, current_read=False):
    payload = doc["payload"]
    listed = _clubs_display(payload["club_names"]) if isinstance(payload.get("club_names"), list) else ""
    fallback = payload.get("club_name", "")
    if not isinstance(fallback, str):
        raise ValueError("document organization must be a string")
    party = listed or normalize_org_name(fallback)
    if verify_receipt and not party:
        raise ValueError("document requires contracting organizations")
    if isinstance(snapshot.get("clubs"), list) and isinstance(payload.get("club_names"), list) and payload["club_names"]:
        approved_parties = [normalize_org_name(name) for name in snapshot["clubs"] if isinstance(name, str) and name.strip()]
        actual_parties = [normalize_org_name(name) for name in payload["club_names"] if isinstance(name, str) and name.strip()]
        if approved_parties != actual_parties:
            raise ValueError("document contracting parties do not match the order")
    if party and party != club_name:
        raise ValueError("document organizations do not match the order")
    date = payload.get("date" if doc["kind"] == "contract" else "event_date")
    if (verify_receipt or doc.get("sourceSnapshot") is not None) and not date:
        raise ValueError("document requires its event date")
    if date:
        try:
            actual = datetime.strptime(date, "%B %d, %Y").strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            actual = date
        if actual != event_date:
            raise ValueError("document event date does not match the order")
    # Existing unscoped PDFs must also agree with the current approved money/terms.
    if doc["kind"] == "contract":
        assert_contract_snapshot(payload, snapshot)
        for field, saved_field in (("price", "rentalPrice"), ("deposit", "depositAmount")):
            expected = snapshot.get(saved_field)
            if expected is not None and payload.get(field) is not None:
                if _money(payload[field]) != _money(expected):
                    raise ValueError("contract pricing does not match the order")
        for field, saved_field in (("start_time", "startTime"), ("end_time", "endTime"), ("monitors", "monitors")):
            if saved_field in snapshot and field in payload and str(snapshot[saved_field]) != str(payload[field]):
                raise ValueError("contract terms do not match the order")
    if verify_receipt:
        if doc["kind"] != "contract" and doc["number"] != doc["filename"].removesuffix(".pdf"):
            raise ValueError("document number does not match the generated filename")
        if doc["kind"] == "rental_invoice" and isinstance(payload.get("line_items"), list):
            amount = sum(_money(item.get("amount", 0)) for item in payload["line_items"])
        else:
            amount = payload.get("price" if doc["kind"] == "contract" else "amount")
        if amount is not None and (doc.get("amount") is None or round(_money(amount), 2) != round(_money(doc["amount"]), 2)):
            raise ValueError("document amount does not match the generated PDF")
    if verify_receipt and not isinstance(doc.get("sourceSnapshot"), dict):
        raise ValueError("new documents require their approved source snapshot")
    source = doc.get("sourceSnapshot")
    if source is not None or snapshot.get("documentContextId"):
        if not isinstance(source, dict) or not source.get("documentContextId"):
            raise ValueError("document requires its approved source snapshot and owner")
        approved = _terms(source)
        current = _terms(snapshot)
        if current_read and doc["kind"] != "contract":
            keys = {"documentContextId", "clubs", "eventDate"}
            if doc["kind"] == "rental_invoice":
                keys |= {"rentalPrice", "numGuests", "pricingSelections", "pricingBreakdown"}
            else:
                keys |= {"depositAmount", "numGuests"}
            approved = {key: approved.get(key) for key in keys}
            current = {key: current.get(key) for key in keys}
        if approved != current:
            raise ValueError("document belongs to another order or an outdated revision")
        if verify_receipt and not hmac.compare_digest(str(doc.get("generationReceipt", "")), document_receipt(doc["kind"], payload, source, doc["filename"])):
            raise ValueError("document generation receipt is missing or does not match its owner and inputs")

def _document_is_stale(doc, row):
    try:
        snapshot = json.loads(row["snapshot"])
        # Retain unscoped legacy entries for ledger/history, but construct current
        # unsigned PDFs from this order's authoritative snapshot instead.
        if doc.get("sourceSnapshot") is None:
            return True
        if doc.get("sourceSnapshot") is not None:
            snapshot = {**snapshot, "documentContextId": snapshot.get("documentContextId") or row["id"]}
        _validate_document_owner(doc, snapshot, row["club_name"], row["event_date"], current_read=True)
        return False
    except (ValueError, TypeError, KeyError, AttributeError):
        return True

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
        "sourceSnapshot": json.loads(row["source_snapshot"]) if row["source_snapshot"] else None,
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
        "documents": [{**_document_to_json(d), "stale": _document_is_stale(_document_to_json(d), row)} for d in documents],
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
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
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
    _validate_snapshot_owner(snapshot, None, club_name, event_date, rental_price, deposit_amount)
    snapshot = _terms(snapshot)
    context = snapshot.get("documentContextId")
    if context is not None and (not isinstance(context, str) or not context.strip()):
        raise ValueError("documentContextId must be a non-empty string")
    if isinstance(context, str) and context.startswith("ord_"):
        raise ValueError("new document owners cannot use a reserved order identity")
    if context and conn.execute("SELECT 1 FROM orders WHERE json_extract(snapshot, '$.documentContextId') = ?", (context,)).fetchone():
        raise ValueError("document owner already belongs to an order; reload its original save")

    raw_documents = body.get("documents") or []
    if not isinstance(raw_documents, list):
        raise ValueError("documents must be an array")
    # Last of each kind wins — see the unique index on (order_id, kind).
    by_kind: dict[str, dict[str, Any]] = {}
    for d in raw_documents:
        validated = _validate_document_input(d)
        _validate_document_owner(validated, snapshot, club_name, event_date, verify_receipt=True)
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
                id, order_id, kind, number, filename, amount, generated_at, payload, source_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id, order_id, doc["kind"], doc["number"], doc["filename"],
                doc["amount"], doc["generatedAt"], json.dumps(doc["payload"]), json.dumps(doc["sourceSnapshot"]) if doc["sourceSnapshot"] else None,
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


def update_order(conn: sqlite3.Connection, order_id: str, body: Any, *, activation_revision_id: str | None = None) -> dict[str, Any] | None:
    """Partial update. Only keys present in `body` are touched.

    `statusOverride` is legitimately nullable ("derive it"), so presence is
    checked with `in body` rather than truthiness — a present-and-null key
    must still clear the column.
    """
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    current = _fetch_order_row(conn, order_id)
    if current is None:
        return None

    if body.get("expectedUpdatedAt") is not None and body["expectedUpdatedAt"] != current["updated_at"]:
        raise ValueError("order changed elsewhere; reload before saving")
    old_snapshot = json.loads(current["snapshot"])
    new_snapshot = body.get("snapshot", old_snapshot)
    _require_json_object(new_snapshot, "snapshot")
    target_club = _PATCHABLE_FIELDS["clubName"][1](body.get("clubName", current["club_name"]))
    _validate_snapshot_owner(new_snapshot, order_id, target_club, body.get("eventDate", current["event_date"]), body.get("rentalPrice", current["rental_price"]), body.get("depositAmount", current["deposit_amount"]))
    if (old_snapshot.get("documentContextId") or new_snapshot.get("documentContextId")) and any(key in body for key in ("snapshot", "clubName", "eventDate", "rentalPrice", "depositAmount")) and not body.get("expectedUpdatedAt"):
        raise ValueError("saving order terms requires the version that was reviewed")
    if "snapshot" in body:
        new_snapshot = _terms(new_snapshot)
        owner = old_snapshot.get("documentContextId") or order_id
        if old_snapshot.get("documentContextId") and new_snapshot.get("documentContextId") != owner:
            raise ValueError("document owner cannot be removed or changed")
        if new_snapshot.get("documentContextId") not in (None, "", owner):
            raise ValueError("document owner cannot change between orders")
        body = {**body, "snapshot": new_snapshot}

    protected = conn.execute("SELECT 1 FROM signing_revisions WHERE order_id = ? AND state IN ('awaiting_signatures', 'preparing_completed_copy', 'signed', 'activating', 'creating', 'created', 'creation_uncertain')", (order_id,)).fetchone()
    approved_activation = False
    if activation_revision_id:
        revision = conn.execute("SELECT state, approved_order_patch FROM signing_revisions WHERE id = ? AND order_id = ?", (activation_revision_id, order_id)).fetchone()
        if revision and revision["state"] == "activating" and revision["approved_order_patch"]:
            approved = json.loads(revision["approved_order_patch"])
            approved_activation = all(body.get(key) == approved.get(key) for key in ("clubName", "eventDate", "rentalPrice", "depositAmount")) and _terms(body.get("snapshot", {})) == _terms(approved.get("snapshot", {}))
        if not approved_activation:
            raise ValueError("order update does not match the explicitly approved signing revision")
    if protected and not approved_activation:
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
            raise ValueError("prepare a new contract revision and approve its links before changing signed or pending terms")

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
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    current = _fetch_order_row(conn, order_id)
    if current is None:
        return None
    doc = _validate_document_input(body)
    if doc["kind"] == "contract" and conn.execute(
        "SELECT 1 FROM signing_revisions WHERE order_id = ? AND state IN ('awaiting_signatures', 'preparing_completed_copy', 'signed', 'activating', 'creating', 'created', 'creation_uncertain') LIMIT 1",
        (order_id,),
    ).fetchone():
        raise ValueError("contract documents with signing history cannot be replaced; prepare a new revision")
    if body.get("expectedUpdatedAt") != current["updated_at"]:
        raise ValueError("order changed elsewhere; reload before attaching documents")
    snapshot = json.loads(current["snapshot"])
    snapshot = {**snapshot, "documentContextId": snapshot.get("documentContextId") or order_id}
    _validate_document_owner(doc, snapshot, current["club_name"], current["event_date"], verify_receipt=True)

    conn.execute(
        "DELETE FROM documents WHERE order_id = ? AND kind = ?",
        (order_id, doc["kind"]),
    )
    doc_id = new_id("doc")
    conn.execute(
        """
        INSERT INTO documents (
            id, order_id, kind, number, filename, amount, generated_at, payload, source_snapshot
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, order_id, doc["kind"], doc["number"], doc["filename"], doc["amount"], doc["generatedAt"], json.dumps(doc["payload"]), json.dumps(doc["sourceSnapshot"]) if doc["sourceSnapshot"] else None),
    )
    conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (now_iso(), order_id))
    conn.commit()
    return get_order(conn, order_id)
