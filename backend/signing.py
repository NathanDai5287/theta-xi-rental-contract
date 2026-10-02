"""Immutable hosting contract revisions and Documenso v2.19 envelopes.

All writes run behind the backend admin key. PDF files are stored separately
from the regenerable order-document metadata; deleted orders retain their
signing history and exact files. Never log recipient links or tokens.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import hmac
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse
from pathlib import Path
import sqlite3
from typing import Any

import requests

import store
from generators.contract import _resolve_clubs, _signing_representatives, generate_contract, signing_field_pages

SIGNING_TIMEZONE = "America/Los_Angeles"


class SigningError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _root() -> Path:
    root = Path(os.environ.get("SIGNING_STORAGE_DIR") or store.BACKEND_ROOT / "contract-files").resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root


@contextmanager
def _order_lock(order_id: str):
    """Serialize revisions across gunicorn workers before provider side effects."""
    directory = _root() / ".locks"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = hashlib.sha256(order_id.encode()).hexdigest() + ".lock"
    with os.fdopen(os.open(directory / name, os.O_RDWR | os.O_CREAT, 0o600), "r+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _path(revision_id: str, kind: str) -> Path:
    if kind not in ("original", "completed", "audit") or not re.fullmatch(r"sig_[0-9a-f]{16}", revision_id):
        raise SigningError("invalid file request", 400)
    return _root() / revision_id / f"{kind}.pdf"


def _save_file(revision_id: str, kind: str, content: bytes) -> None:
    if not content.startswith(b"%PDF-"):
        raise SigningError("signing provider did not return a PDF", 502)
    target = _path(revision_id, kind)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target.parent, 0o700)
    temp = target.with_name(f".{kind}.{secrets.token_hex(12)}.tmp")
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
            file.write(content)
        os.replace(temp, target)
        os.chmod(target, 0o600)
    finally:
        temp.unlink(missing_ok=True)


def _row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("payload", "signers", "fields", "recipients"):
        result[key] = json.loads(result[key])
    sent = {item["recipient_email"]: item["sent_at"] for item in conn.execute(
        "SELECT recipient_email, sent_at FROM signing_link_delivery WHERE revision_id = ?", (result["id"],)
    ).fetchall()}
    for person in result["recipients"]:
        person["sentAt"] = sent.get(person["email"].casefold())
    result["signedCount"] = sum(p.get("status") == "SIGNED" for p in result["recipients"])
    result["totalCount"] = len(result["signers"])
    result["files"] = {key: _path(result["id"], key).exists() for key in ("original", "completed", "audit")}
    return result


def get(conn: sqlite3.Connection, revision_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM signing_revisions WHERE id = ?", (revision_id,)).fetchone()
    return _row(conn, row) if row else None


def list_for_order(conn: sqlite3.Connection, order_id: str) -> list[dict[str, Any]]:
    return [_row(conn, row) for row in conn.execute(
        "SELECT * FROM signing_revisions WHERE order_id = ? ORDER BY revision DESC", (order_id,)
    ).fetchall()]


def mark_link_sent(conn: sqlite3.Connection, revision_id: str, email: str, sent: bool) -> dict[str, Any]:
    """Record an administrator's explicit delivery acknowledgement only."""
    revision = get(conn, revision_id)
    if not revision:
        raise SigningError("revision not found", 404)
    if not isinstance(email, str) or not isinstance(sent, bool):
        raise SigningError("recipient email and sent flag are required", 400)
    normalized = email.strip().casefold()
    if normalized not in {person["email"].casefold() for person in revision["recipients"]}:
        raise SigningError("recipient not found on this signing request", 404)
    if sent:
        conn.execute("INSERT OR IGNORE INTO signing_link_delivery (revision_id, recipient_email, sent_at) VALUES (?, ?, ?)",
                     (revision_id, normalized, store.now_iso()))
    else:
        conn.execute("DELETE FROM signing_link_delivery WHERE revision_id = ? AND recipient_email = ?",
                     (revision_id, normalized))
    conn.commit()
    return get(conn, revision_id)  # type: ignore[return-value]


def _documenso(method: str, path: str, *, body: dict | None = None,
               pdf: bytes | None = None) -> Any:
    origin = os.environ.get("DOCUMENSO_ORIGIN", "").rstrip("/")
    token = os.environ.get("DOCUMENSO_API_KEY", "")
    parsed = urlparse(origin)
    local_http = parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if not (parsed.scheme == "https" or local_http) or not token:
        raise SigningError("Documenso is not configured", 503)
    headers = {"Authorization": token}
    kwargs: dict[str, Any] = {"headers": headers, "timeout": 45}
    if pdf is not None:
        kwargs["data"] = {"payload": json.dumps(body)}
        kwargs["files"] = [("files", ("hosting-contract.pdf", pdf, "application/pdf"))]
    elif body is not None:
        kwargs["json"] = body
    try:
        response = requests.request(method, f"{origin}/api/v2{path}", **kwargs)
    except requests.RequestException as exc:
        # A timeout after POST/create has unknown outcome. The caller persists
        # its 'creating' state and never automatically retries that POST.
        raise SigningError("Documenso is unavailable; inspect the request before retrying", 503) from exc
    if not response.ok:
        raise SigningError(f"Documenso returned {response.status_code}; review the request", 502)
    if "download" in path:
        return response.content
    try:
        return response.json()
    except ValueError as exc:
        raise SigningError("Documenso returned an invalid response", 502) from exc


def _assert_order_terms(order: dict[str, Any], payload: dict[str, Any]) -> None:
    try:
        event_date = datetime.strptime(str(payload.get("date", "")), "%B %d, %Y").date().isoformat()
        price = store._money(payload.get("price", ""))
        deposit = store._money(payload.get("deposit", ""))
    except ValueError as exc:
        raise SigningError("contract terms do not match the saved order", 409) from exc
    if (event_date != order["eventDate"] or
            store.normalize_org_name(str(payload.get("club_name", ""))) != order["clubName"] or
            price != order["rentalPrice"] or deposit != order["depositAmount"]):
        raise SigningError("contract terms do not match the saved order", 409)
    try:
        store._validate_document_owner({"kind": "contract", "payload": payload, **({"sourceSnapshot": order["snapshot"]} if order["snapshot"].get("documentContextId") else {})}, order["snapshot"], order["clubName"], order["eventDate"])
    except (ValueError, TypeError, KeyError) as exc:
        raise SigningError("contract terms or recipients do not match the saved order", 409) from exc


def prepare(conn: sqlite3.Connection, order_id: str, payload: dict, request_key: str, expected_latest_id: str | None = None) -> dict[str, Any]:
    with _order_lock(order_id):
        return _prepare_locked(conn, order_id, payload, request_key, expected_latest_id)


def _prepare_locked(conn: sqlite3.Connection, order_id: str, payload: dict, request_key: str, expected_latest_id: str | None) -> dict[str, Any]:
    order = store.get_order(conn, order_id)
    if not order:
        raise SigningError("order not found", 404)
    if not isinstance(request_key, str) or not 12 <= len(request_key) <= 100:
        raise SigningError("a stable request key is required", 400)
    if not isinstance(payload, dict):
        raise SigningError("contract payload is required", 400)
    clubs = _resolve_clubs(payload)
    signers = _signing_representatives(payload, clubs, payload.get("sign") is True)
    if not signers:
        raise SigningError("at least one signer is required", 400)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    existing = conn.execute("SELECT * FROM signing_revisions WHERE request_key = ?", (request_key,)).fetchone()
    if existing:
        if existing["order_id"] != order_id or existing["payload_hash"] != digest:
            raise SigningError("request key already belongs to a different contract")
        return _row(conn, existing)

    latest = conn.execute("SELECT * FROM signing_revisions WHERE order_id = ? ORDER BY revision DESC LIMIT 1", (order_id,)).fetchone()
    if expected_latest_id is not None and (not isinstance(expected_latest_id, str) or expected_latest_id != (latest["id"] if latest else "")):
        raise SigningError("signing history changed elsewhere; reload this order before preparing a new contract")
    if not latest:
        _assert_order_terms(order, payload)
    if latest and latest["payload_hash"] == digest and latest["state"] not in ("cancelled", "failed"):
        return _row(conn, latest)
    if conn.execute("SELECT 1 FROM signing_revisions WHERE order_id = ? AND state IN ('activating', 'creating', 'creation_uncertain', 'created', 'preparing_completed_copy')", (order_id,)).fetchone():
        raise SigningError("prior revision is still processing; refresh its status before revising")
    # A preview is inert: rendering never changes an existing envelope or
    # the saved order. Retire outstanding links only during confirmed activation.
    pdf = generate_contract(payload)
    fields = signing_field_pages(pdf, signers)
    revision_id = store.new_id("sig")
    revision_number = (latest["revision"] if latest else 0) + 1
    _save_file(revision_id, "original", pdf)
    stamp = store.now_iso()
    conn.execute("""INSERT INTO signing_revisions
        (id, order_id, revision, request_key, payload_hash, payload, signers, fields,
         state, original_sha256, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'preview', ?, ?, ?)""",
        (revision_id, order_id, revision_number, request_key, digest, canonical,
         json.dumps(signers), json.dumps(fields), hashlib.sha256(pdf).hexdigest(), stamp, stamp))
    conn.commit()
    return get(conn, revision_id)  # type: ignore[return-value]


def create_links(conn: sqlite3.Connection, revision_id: str, approved_sha256: str, activation: dict | None = None) -> dict[str, Any]:
    revision = get(conn, revision_id)
    if not revision:
        raise SigningError("revision not found", 404)
    with _order_lock(revision["order_id"]):
        return _create_links_locked(conn, revision_id, approved_sha256, activation)


def _create_links_locked(conn: sqlite3.Connection, revision_id: str, approved_sha256: str, activation: dict | None = None) -> dict[str, Any]:
    revision = get(conn, revision_id)
    if not revision:
        raise SigningError("revision not found", 404)
    latest = conn.execute("SELECT id FROM signing_revisions WHERE order_id = ? ORDER BY revision DESC LIMIT 1",
                          (revision["order_id"],)).fetchone()
    if not latest or latest["id"] != revision_id:
        raise SigningError("this contract revision has been superseded")
    order = store.get_order(conn, revision["order_id"])
    if not order:
        raise SigningError("order not found", 404)
    if revision["original_sha256"] != approved_sha256:
        raise SigningError("preview has changed; review the current PDF before signing")
    if revision["state"] == "awaiting_signatures" or revision["state"] == "signed":
        return revision
    if revision["state"] not in ("preview", "activating", "created"):
        raise SigningError("request is processing or needs reconciliation; no duplicate will be created")

    if revision["state"] == "preview":
        pending = conn.execute("SELECT * FROM signing_revisions WHERE order_id = ? AND id != ? AND state = 'awaiting_signatures' ORDER BY revision", (revision["order_id"], revision_id)).fetchall()
        processing = conn.execute("SELECT 1 FROM signing_revisions WHERE order_id = ? AND id != ? AND state IN ('activating', 'creating', 'created', 'creation_uncertain', 'preparing_completed_copy')", (revision["order_id"], revision_id)).fetchone()
        if processing:
            raise SigningError("a previous request is still processing; refresh before activating a replacement")
        if pending and activation is None:
            raise SigningError("confirm replacement of outstanding signing links before activating this revision")
        if activation is not None:
            if not isinstance(activation, dict) or activation.get("expectedUpdatedAt") != order["updatedAt"]:
                raise SigningError("order changed since review; reload before creating signing links")
            if not isinstance(activation.get("replacesRevisionIds"), list) or sorted(activation["replacesRevisionIds"]) != sorted(row["id"] for row in pending):
                raise SigningError("outstanding signing requests changed; review replacement again")
            keys = ("clubName", "eventDate", "rentalPrice", "depositAmount", "snapshot")
            if any(key not in activation for key in keys):
                raise SigningError("approved order terms are required", 400)
            candidate = {**order, **{key: activation[key] for key in keys}}
            _assert_order_terms(candidate, revision["payload"])
            store._validate_snapshot_owner(activation["snapshot"], revision["order_id"], activation["clubName"], activation["eventDate"], activation["rentalPrice"], activation["depositAmount"])
            if order["snapshot"].get("documentContextId") and activation["snapshot"].get("documentContextId") != order["snapshot"]["documentContextId"]:
                raise SigningError("document owner cannot change during contract approval")
        else:
            _assert_order_terms(order, revision["payload"])
            activation = {key: order[key] for key in ("clubName", "eventDate", "rentalPrice", "depositAmount", "snapshot")}
            activation.update(expectedUpdatedAt=order["updatedAt"], replacesRevisionIds=[])
        activation = {**activation, "baselineOrderTerms": {key: order[key] for key in ("clubName", "eventDate", "rentalPrice", "depositAmount", "snapshot")}}
        # Persist approval before side effects; an interrupted activation resumes
        # this exact approved revision rather than recancelling or creating anew.
        conn.execute("UPDATE signing_revisions SET state = 'activating', approved_order_patch = ?, updated_at = ? WHERE id = ? AND state = 'preview'", (json.dumps(activation), store.now_iso(), revision_id))
        conn.commit()
        revision = get(conn, revision_id)

    if revision["state"] == "activating":
        activation = json.loads(revision["approved_order_patch"])
        same_terms = all(order[key] == activation[key] for key in ("clubName", "eventDate", "rentalPrice", "depositAmount")) and store._terms(order["snapshot"]) == store._terms(activation["snapshot"])
        baseline = activation["baselineOrderTerms"]
        same_baseline = all(order[key] == baseline[key] for key in ("clubName", "eventDate", "rentalPrice", "depositAmount")) and store._terms(order["snapshot"]) == store._terms(baseline["snapshot"])
        if order["updatedAt"] != activation["expectedUpdatedAt"] and not same_terms and not same_baseline:
            raise SigningError("order changed since approval; review before resuming activation")
        for prior_id in activation["replacesRevisionIds"]:
            prior = get(conn, prior_id)
            if not prior or prior["order_id"] != revision["order_id"]:
                raise SigningError("previous signing request identity changed")
            if prior["state"] in ("cancelled", "signed"):
                continue
            if not prior["envelope_id"]:
                raise SigningError("prior request outcome is unknown; reconcile it first")
            envelope = _documenso("GET", f"/envelope/{prior['envelope_id']}")
            _verify_provider_envelope(prior, envelope)
            if envelope.get("status") == "PENDING" and envelope.get("recipients") and all(person.get("signingStatus") == "SIGNED" for person in envelope["recipients"]):
                raise SigningError("everyone has signed the previous contract; wait for its completed copy before replacing it")
            if envelope.get("status") == "PENDING":
                _documenso("POST", "/envelope/cancel", body={"envelopeId": prior["envelope_id"], "reason": "Contract revised after administrator approval"})
                envelope = _documenso("GET", f"/envelope/{prior['envelope_id']}")
                _verify_provider_envelope(prior, envelope)
            if envelope.get("status") == "COMPLETED":
                if sync(conn, prior["id"])["state"] != "signed":
                    raise SigningError("store the completed previous contract before replacing it")
            elif envelope.get("status") == "CANCELLED" and not all(person.get("signingStatus") == "SIGNED" for person in envelope.get("recipients", [])):
                conn.execute("UPDATE signing_revisions SET state = 'cancelled', updated_at = ? WHERE id = ?", (store.now_iso(), prior["id"]))
                conn.commit()
            else:
                raise SigningError("could not confirm old links are inactive; check and resume before creating a replacement")
        # Apply approved terms only after older links are confirmed inactive.
        if not same_terms:
            fresh_order = store.get_order(conn, revision["order_id"])
            keys = ("clubName", "eventDate", "rentalPrice", "depositAmount", "snapshot")
            if not all(fresh_order[key] == baseline[key] for key in keys[:-1]) or store._terms(fresh_order["snapshot"]) != store._terms(baseline["snapshot"]):
                raise SigningError("saved terms changed during activation; inspect before resuming")
            store.update_order(conn, revision["order_id"], {**{key: activation[key] for key in keys}, "expectedUpdatedAt": fresh_order["updatedAt"]}, activation_revision_id=revision_id)
            order = store.get_order(conn, revision["order_id"])
        _assert_order_terms(order, revision["payload"])
        # Atomic claim prevents concurrent clicks from issuing two envelopes.
        claimed = conn.execute("UPDATE signing_revisions SET state = 'creating', updated_at = ? WHERE id = ? AND state = 'activating'",
                               (store.now_iso(), revision_id))
        conn.commit()
        if claimed.rowcount != 1:
            raise SigningError("request is already processing")
        recipients = []
        fields_by_email = {item["email"].casefold(): item["fields"] for item in revision["fields"]}
        for signer in revision["signers"]:
            recipients.append({"email": signer["email"], "name": signer["fullName"], "role": "SIGNER",
                               "accessAuth": [], "actionAuth": [], "fields": fields_by_email[signer["email"].casefold()]})
        payload = {"type": "DOCUMENT", "title": f"Hosting Contract {revision['order_id']} · revision {revision['revision']}",
                   "externalId": revision_id, "recipients": recipients,
                   "globalAccessAuth": [], "globalActionAuth": [],
                   "meta": {"distributionMethod": "NONE", "signingOrder": "PARALLEL", "timezone": SIGNING_TIMEZONE,
                            "typedSignatureEnabled": True, "drawSignatureEnabled": True,
                            "uploadSignatureEnabled": False}}
        try:
            response = _documenso("POST", "/envelope/create", body=payload, pdf=_path(revision_id, "original").read_bytes())
        except SigningError as exc:
            conn.execute("UPDATE signing_revisions SET state = 'creation_uncertain', error = ?, updated_at = ? WHERE id = ?",
                         (str(exc), store.now_iso(), revision_id))
            conn.commit()
            raise
        envelope_id = response.get("id")
        if not isinstance(envelope_id, str):
            raise SigningError("Documenso returned no envelope ID; reconcile this request", 502)
        conn.execute("UPDATE signing_revisions SET envelope_id = ?, state = 'created', updated_at = ? WHERE id = ?",
                     (envelope_id, store.now_iso(), revision_id))
        conn.commit()
        revision = get(conn, revision_id)

    # If distribute was interrupted, check the actual state before retrying.
    assert revision and revision["envelope_id"]
    envelope = _documenso("GET", f"/envelope/{revision['envelope_id']}")
    _verify_provider_envelope(revision, envelope)
    _verify_provider_original(revision, envelope)
    if envelope.get("status") == "PENDING":
        people = envelope.get("recipients", [])
        links = [{"id": p["id"], "email": p["email"], "name": p["name"],
                  "status": p["signingStatus"], "link": _link_from_token(p["token"])} for p in people]
    elif envelope.get("status") == "DRAFT":
        if (envelope.get("documentMeta") or {}).get("timezone") != SIGNING_TIMEZONE:
            # Resume older drafts safely without activating links until the setting is verified.
            _documenso("POST", "/envelope/update", body={"envelopeId": revision["envelope_id"],
                        "meta": {"timezone": SIGNING_TIMEZONE}})
            envelope = _documenso("GET", f"/envelope/{revision['envelope_id']}")
            _verify_provider_envelope(revision, envelope)
            _verify_provider_original(revision, envelope)
            if envelope.get("status") != "DRAFT" or (envelope.get("documentMeta") or {}).get("timezone") != SIGNING_TIMEZONE:
                raise SigningError("Could not verify Pacific signing time before distributing links", 502)
        _documenso("POST", "/envelope/distribute", body={"envelopeId": revision["envelope_id"],
                    "meta": {"distributionMethod": "NONE", "timezone": SIGNING_TIMEZONE}})
        envelope = _documenso("GET", f"/envelope/{revision['envelope_id']}")
        _verify_provider_envelope(revision, envelope)
        _verify_provider_original(revision, envelope)
        if envelope.get("status") != "PENDING":
            raise SigningError("Documenso did not activate the signing request", 502)
        links = [{"id": p["id"], "email": p["email"], "name": p["name"],
                  "status": p["signingStatus"], "link": _link_from_token(p["token"])} for p in envelope["recipients"]]
    else:
        raise SigningError("envelope state needs reconciliation before links can be shown")
    if (envelope.get("documentMeta") or {}).get("timezone") != SIGNING_TIMEZONE:
        raise SigningError("This request needs its signing timezone corrected to America/Los_Angeles before links can be returned", 502)
    expected = {s["email"].casefold() for s in revision["signers"]}
    if {p["email"].casefold() for p in links} != expected:
        raise SigningError("Documenso recipients differ from the approved contract")
    expected_origin = os.environ["DOCUMENSO_ORIGIN"].rstrip("/") + "/sign/"
    if any(not p["link"].startswith(expected_origin) for p in links):
        raise SigningError("Documenso signing URLs use the wrong host", 502)
    existing_tokens = {p["email"].casefold(): p.get("copyToken") for p in revision["recipients"]}
    for person in links:
        person["copyToken"] = existing_tokens.get(person["email"].casefold()) or secrets.token_urlsafe(32)
    conn.execute("UPDATE signing_revisions SET recipients = ?, item_id = ?, state = 'awaiting_signatures', updated_at = ? WHERE id = ?",
                 (json.dumps(links), envelope["envelopeItems"][0]["id"], store.now_iso(), revision_id))
    conn.commit()
    return get(conn, revision_id)  # type: ignore[return-value]


def _verify_provider_envelope(revision: dict[str, Any], envelope: dict[str, Any]) -> None:
    """Fail closed before distribution if Documenso changed recipients or fields."""
    if envelope.get("id") != revision["envelope_id"] or envelope.get("externalId") != revision["id"]:
        raise SigningError("Documenso envelope identity mismatch", 502)
    meta = envelope.get("documentMeta") or {}
    if meta.get("distributionMethod") != "NONE" or meta.get("signingOrder") != "PARALLEL":
        raise SigningError("Documenso signing or email settings differ from the approved request", 502)
    if not meta.get("typedSignatureEnabled") or not meta.get("drawSignatureEnabled"):
        raise SigningError("Documenso signature methods differ from the approved request", 502)
    items = envelope.get("envelopeItems") or []
    if len(items) != 1:
        raise SigningError("Documenso must contain exactly one contract PDF", 502)
    recipients = envelope.get("recipients") or []
    if len(recipients) != len(revision["signers"]):
        raise SigningError("Documenso recipient count differs from the approved contract", 502)
    by_email = {person.get("email", "").casefold(): person for person in recipients}
    if len(by_email) != len(recipients):
        raise SigningError("Documenso recipient addresses are not unique", 502)
    expected_signers = {person["email"].casefold(): person for person in revision["signers"]}
    if set(by_email) != set(expected_signers):
        raise SigningError("Documenso recipients differ from the approved contract", 502)
    for email, person in by_email.items():
        if person.get("name") != expected_signers[email]["fullName"] or person.get("role") != "SIGNER":
            raise SigningError("Documenso recipient identity differs from the approved contract", 502)
    actual_fields = envelope.get("fields") or []
    expected_fields = {entry["email"].casefold(): entry["fields"] for entry in revision["fields"]}
    if len(actual_fields) != sum(len(fields) for fields in expected_fields.values()):
        raise SigningError("Documenso field count differs from the approved contract", 502)
    for email, fields in expected_fields.items():
        person_id = by_email[email]["id"]
        owned = [field for field in actual_fields if field.get("recipientId") == person_id]
        if len(owned) != len(fields):
            raise SigningError("Documenso field ownership differs from the approved contract", 502)
        for expected in fields:
            matches = [field for field in owned if field.get("type") == expected["type"]]
            if len(matches) != 1:
                raise SigningError("Documenso signer fields differ from the approved contract", 502)
            actual = matches[0]
            if actual.get("page") != expected["page"] or actual.get("envelopeItemId") != items[0]["id"]:
                raise SigningError("Documenso signer field page differs from the approved contract", 502)
            try:
                coordinates_match = all(abs(float(actual[key]) - expected[key]) <= 0.02 for key in
                                        ("positionX", "positionY", "width", "height"))
            except (KeyError, TypeError, ValueError):
                coordinates_match = False
            if not coordinates_match:
                raise SigningError("Documenso signer field position differs from the approved contract", 502)


def _verify_provider_original(revision: dict[str, Any], envelope: dict[str, Any]) -> None:
    item_id = envelope["envelopeItems"][0]["id"]
    provider_original = _documenso("GET", f"/envelope/item/{item_id}/download?version=original")
    if hashlib.sha256(provider_original).hexdigest() != revision["original_sha256"]:
        raise SigningError("Documenso stored a different PDF than the approved preview", 502)


def reconcile(conn: sqlite3.Connection, revision_id: str) -> dict[str, Any]:
    """Recover a lost create response without issuing a second POST/create."""
    revision = get(conn, revision_id)
    if not revision:
        raise SigningError("revision not found", 404)
    with _order_lock(revision["order_id"]):
        if not store.get_order(conn, revision["order_id"]):
            raise SigningError("order not found", 404)
        return _reconcile_locked(conn, revision_id)


def _reconcile_locked(conn: sqlite3.Connection, revision_id: str) -> dict[str, Any]:
    revision = get(conn, revision_id)
    if revision["state"] in ("activating", "created"):
        return _create_links_locked(conn, revision_id, revision["original_sha256"])
    if revision["state"] not in ("creating", "creation_uncertain"):
        return revision
    matches = []
    page = 1
    while True:
        found = _documenso("GET", f"/envelope?type=DOCUMENT&page={page}&perPage=100")
        matches += [item for item in found["data"] if item.get("externalId") == revision_id]
        if page >= found["totalPages"]:
            break
        page += 1
    if len(matches) > 1:
        raise SigningError("multiple provider envelopes match this revision; manual repair required", 502)
    if not matches:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(revision["updated_at"])
        if age.total_seconds() < 15 * 60:
            raise SigningError("provider request outcome is still uncertain; check again after 15 minutes")
        # A full provider listing after the grace period found no request.
        # Retain the failed revision; a new prepare gets a new revision ID.
        conn.execute("UPDATE signing_revisions SET state = 'failed', error = ?, updated_at = ? WHERE id = ?",
                     ("No matching provider request found after 15 minutes", store.now_iso(), revision_id))
        conn.commit()
        return get(conn, revision_id)  # type: ignore[return-value]
    envelope_id = matches[0]["id"]
    conn.execute("UPDATE signing_revisions SET envelope_id = ?, state = 'created', error = NULL, updated_at = ? WHERE id = ?",
                 (envelope_id, store.now_iso(), revision_id))
    conn.commit()
    return _create_links_locked(conn, revision_id, revision["original_sha256"])


def delete_order(conn: sqlite3.Connection, order_id: str) -> bool:
    """Retire signing requests before removing the order from the working archive."""
    with _order_lock(order_id):
        order = conn.execute("SELECT deleted_at FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            return False
        if order["deleted_at"]:
            return True
        for revision in list_for_order(conn, order_id):
            if revision["state"] == "signed":
                continue
            if revision["state"] in ("creating", "creation_uncertain") and not revision["envelope_id"]:
                # Do not hide a request whose links may still become active.
                raise SigningError("The signing request outcome is uncertain. Use Check and resume request, then retry deleting the order.")
            people = revision["recipients"]
            if revision["envelope_id"]:
                envelope = _documenso("GET", f"/envelope/{revision['envelope_id']}")
                if envelope.get("id") != revision["envelope_id"] or envelope.get("externalId") != revision["id"]:
                    raise SigningError("Could not verify the signing request before deleting the order", 502)
                if envelope.get("status") == "PENDING":
                    if envelope.get("recipients") and all(p.get("signingStatus") == "SIGNED" for p in envelope["recipients"]):
                        raise SigningError("Everyone has signed and Documenso is preparing the completed contract. Wait for it to finish, then retry deleting the order.")
                    _documenso("POST", "/envelope/cancel", body={"envelopeId": revision["envelope_id"], "reason": "Order deleted"})
                    envelope = _documenso("GET", f"/envelope/{revision['envelope_id']}")
                _verify_provider_envelope(revision, envelope)
                # Keep the latest per-person progress even for a cancelled request.
                # Distribution may have succeeded before its response was lost.
                existing = {p["email"].casefold(): p for p in revision["recipients"]}
                people = [{"id": p["id"], "email": p["email"], "name": p["name"],
                           "status": p["signingStatus"], "link": _link_from_token(p["token"]),
                           "copyToken": existing.get(p["email"].casefold(), {}).get("copyToken") or secrets.token_urlsafe(32)}
                          for p in envelope["recipients"]]
                if envelope.get("status") == "COMPLETED":
                    _verify_provider_original(revision, envelope)
                    conn.execute("UPDATE signing_revisions SET state = 'preparing_completed_copy', recipients = ?, updated_at = ? WHERE id = ?",
                                 (json.dumps(people), store.now_iso(), revision["id"]))
                    conn.commit()
                    if sync(conn, revision["id"])["state"] != "signed":
                        raise SigningError("The completed contract is still being stored. Retry deleting the order after it finishes.")
                    continue
                # DRAFT has never been activated. v2.19 only cancels PENDING;
                # retain the provider draft and prevent our app from distributing it.
                if envelope.get("status") not in ("DRAFT", "CANCELLED", "REJECTED"):
                    raise SigningError("Could not confirm that signing links are inactive. Retry deleting the order.")
                if envelope.get("status") == "CANCELLED" and all(p["status"] == "SIGNED" for p in people):
                    raise SigningError("Signing finished while cancellation was processing. Wait for the completed contract before retrying deletion.")
            conn.execute("UPDATE signing_revisions SET state = 'cancelled', recipients = ?, error = NULL, updated_at = ? WHERE id = ?",
                         (json.dumps(people), store.now_iso(), revision["id"]))
            conn.commit()
        return store.delete_order(conn, order_id)


def _link_from_token(token: str) -> str:
    origin = os.environ["DOCUMENSO_ORIGIN"].rstrip("/")
    return f"{origin}/sign/{token}"


def completed_copy_for_token(conn: sqlite3.Connection, token: str) -> Path:
    if not isinstance(token, str) or len(token) > 100:
        raise SigningError("invalid link", 404)
    # Tokens are kept with their matching recipients so an administrator can
    # retrieve the same link after a reload. A random 256-bit token makes an
    # online guess impractical; scan is small for this archive's order volume.
    rows = conn.execute("SELECT id, recipients, state FROM signing_revisions WHERE state IN ('awaiting_signatures', 'preparing_completed_copy', 'signed')").fetchall()
    for row in rows:
        for person in json.loads(row["recipients"]):
            candidate = person.get("copyToken", "")
            if candidate and hmac.compare_digest(candidate, token):
                if row["state"] != "signed":
                    raise SigningError("completed contract is not ready yet", 409)
                path = _path(row["id"], "completed")
                if path.exists():
                    return path
    raise SigningError("invalid link", 404)


def sync(conn: sqlite3.Connection, revision_id: str) -> dict[str, Any]:
    revision = get(conn, revision_id)
    if not revision:
        raise SigningError("revision not found", 404)
    if not revision["envelope_id"] or revision["state"] in ("preview", "creating", "created", "signed"):
        return revision
    envelope = _documenso("GET", f"/envelope/{revision['envelope_id']}")
    # Documenso seals asynchronously: a completion can cross cancellation.
    # Retain that completed evidence even after the order was removed, while
    # never reviving a cancelled request on an older pending notification.
    if revision["state"] == "cancelled" and envelope.get("status") != "COMPLETED":
        return revision
    _verify_provider_envelope(revision, envelope)
    _verify_provider_original(revision, envelope)
    by_email = {p["email"].casefold(): p for p in envelope["recipients"]}
    expected = {p["email"].casefold() for p in revision["signers"]}
    if set(by_email) != expected:
        raise SigningError("Documenso recipient list changed", 502)
    people = [{**old, "status": by_email[old["email"].casefold()]["signingStatus"]}
              for old in revision["recipients"]]
    status = envelope["status"]
    if status == "CANCELLED":
        state = "cancelled"
    elif status == "REJECTED":
        state = "failed"
    elif status == "COMPLETED" and all(p["status"] == "SIGNED" for p in people):
        state = "preparing_completed_copy"
    else:
        state = "awaiting_signatures"
    updated = conn.execute("UPDATE signing_revisions SET recipients = ?, state = ?, updated_at = ? WHERE id = ? AND state = ?",
                           (json.dumps(people), state, store.now_iso(), revision_id, revision["state"]))
    conn.commit()
    if updated.rowcount != 1:
        return get(conn, revision_id)  # type: ignore[return-value]
    if state == "preparing_completed_copy":
        item_id = revision["item_id"] or envelope["envelopeItems"][0]["id"]
        try:
            _save_file(revision_id, "completed", _documenso("GET", f"/envelope/item/{item_id}/download?version=signed"))
            _save_file(revision_id, "audit", _documenso("GET", f"/envelope/{revision['envelope_id']}/audit-log/download"))
        except SigningError as exc:
            conn.execute("UPDATE signing_revisions SET error = ?, updated_at = ? WHERE id = ?",
                         (str(exc), store.now_iso(), revision_id))
            conn.commit()
            return get(conn, revision_id)  # type: ignore[return-value]
        conn.execute("UPDATE signing_revisions SET state = 'signed', error = NULL, updated_at = ? WHERE id = ? AND state = 'preparing_completed_copy'",
                     (store.now_iso(), revision_id))
        conn.commit()
    return get(conn, revision_id)  # type: ignore[return-value]
