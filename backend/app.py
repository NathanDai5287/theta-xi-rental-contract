"""
Flask backend serving PDF generation endpoints for the Theta Xi tools, plus
a server-to-server order archive API.

Routes:
  POST /api/generate/contract        -> contract PDF
  POST /api/generate/invoice/deposit -> security deposit invoice PDF
  POST /api/generate/invoice/rental  -> rental invoice PDF
  POST /api/generate/credit-memo     -> credit memo PDF

  GET    /api/orders                 -> list orders (summaries)
  POST   /api/orders                 -> create an order
  GET    /api/orders/<id>            -> fetch one order
  PATCH  /api/orders/<id>            -> partial update
  DELETE /api/orders/<id>            -> delete an order
  POST   /api/orders/<id>/documents  -> append a generated document to an order

Every route except /api/health requires the `X-Admin-Key` header — see
`_require_admin_key`. The generate routes are called server-to-server from
the Next.js admin app's own backend (the browser never talks to this service
directly), so no CORS headers are emitted for them; only /api/health is
CORS-open so it can be probed from anywhere.

Each generate/* route accepts JSON, returns application/pdf with a sensible
filename.

Dev:
    pip install -r requirements.txt
    python app.py
"""
from __future__ import annotations

import hmac
import logging
import os
from functools import wraps
from io import BytesIO
from typing import Any, Callable

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

import store
import signing
from generators import generate_contract, generate_credit_memo, generate_invoice
from generators.base import (
    TypstCompileError,
    TypstNotFoundError,
    TypstTimeoutError,
    UnknownPlaceholderError,
    slug,
)

log = logging.getLogger(__name__)

app = Flask(__name__)
store.init_app(app)

# Hard cap on request bodies. PDF payloads are a few KB of JSON at most;
# anything bigger is either a mistake or an attempt to exhaust compile
# resources. 413 is handled below.
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024

# Only the health probe is callable cross-origin from a browser. Generate and
# orders routes are server-to-server (the Next app's backend holds the admin
# key), so they get no Access-Control-Allow-Origin header at all.
CORS(app, resources={r"/api/health": {"origins": "*"}})


@app.errorhandler(TypstNotFoundError)
def _handle_no_typst(e: TypstNotFoundError):
    return jsonify(error="typst_not_installed"), 500


@app.errorhandler(TypstCompileError)
def _handle_typst_compile(e: TypstCompileError):
    # stderr can embed attacker-controlled input; log it server-side only.
    log.error("typst compile failed (rc=%s):\n%s", e.returncode, e.stderr)
    return jsonify(error="typst_compile_failed"), 500


@app.errorhandler(TypstTimeoutError)
def _handle_typst_timeout(e: TypstTimeoutError):
    log.error("typst compile timed out: %s", e)
    return jsonify(error="typst_timeout"), 500


@app.errorhandler(UnknownPlaceholderError)
def _handle_unknown_placeholder(e: UnknownPlaceholderError):
    # Indicates a template/generator mismatch — a bug on our side, not the
    # caller's. Log the token, return a generic 500.
    log.error("unresolved template placeholder: %s", e)
    return jsonify(error="template_error"), 500


@app.errorhandler(ValueError)
def _handle_value_error(e: ValueError):
    return jsonify(error="invalid_input", detail=str(e)), 400


@app.errorhandler(signing.SigningError)
def _handle_signing_error(e: signing.SigningError):
    return jsonify(error="signing_error", detail=str(e)), e.status


@app.errorhandler(413)
def _handle_too_large(e):
    return jsonify(error="payload_too_large"), 413


@app.errorhandler(Exception)
def _handle_unexpected(e: Exception):
    # Routing-level errors (404/405/…) are HTTPExceptions with their own
    # status — pass them through. Anything else is a bug: log it with a
    # traceback server-side, return a generic JSON 500 (never HTML, never
    # internals).
    if isinstance(e, HTTPException):
        return e
    log.exception("unhandled error")
    return jsonify(error="internal_error"), 500


def _json_body() -> dict[str, Any]:
    """Request JSON, enforced to be an object — a top-level list/scalar
    would otherwise surface as an AttributeError deep inside a generator."""
    body = request.get_json(force=True, silent=False)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    return body


def _pdf_response(pdf_bytes: bytes, filename: str):
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


def _require_admin_key(fn: Callable[..., Any]) -> Callable[..., Any]:
    """
    Reject unless `X-Admin-Key` matches env `ADMIN_KEY`, using a constant-time
    comparison. Fails closed: if ADMIN_KEY isn't configured on the server,
    every request is rejected rather than treated as "no auth required" —
    mirrors the fail-closed check the Next app itself does in front of us.
    """

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        expected = os.environ.get("ADMIN_KEY")
        provided = request.headers.get("X-Admin-Key")
        if not expected or not provided:
            return jsonify(error="unauthorized"), 401
        # compare_digest raises TypeError on non-ASCII str; encode both sides
        # so an exotic header value yields a 401 instead of a 500.
        if not hmac.compare_digest(provided.encode("utf-8", "ignore"),
                                   expected.encode("utf-8", "ignore")):
            return jsonify(error="unauthorized"), 401
        return fn(*args, **kwargs)

    return wrapper


@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.post("/api/generate/contract")
@_require_admin_key
def contract():
    payload = _json_body()
    pdf = generate_contract(payload)
    club = slug(str(payload.get("club_name", "partner")))
    return _pdf_response(pdf, f"theta_xi_{club}_contract.pdf")


def _invoice_route(kind: str):
    payload = _json_body()
    payload["kind"] = kind
    pdf, number = generate_invoice(payload)
    return _pdf_response(pdf, f"{number}.pdf")


@app.post("/api/generate/invoice/deposit")
@_require_admin_key
def invoice_deposit():
    return _invoice_route("deposit")


@app.post("/api/generate/invoice/rental")
@_require_admin_key
def invoice_rental():
    return _invoice_route("rental")


@app.post("/api/generate/credit-memo")
@_require_admin_key
def credit_memo():
    payload = _json_body()
    pdf, number = generate_credit_memo(payload)
    return _pdf_response(pdf, f"{number}.pdf")


# ── Orders API ──────────────────────────────────────────────────────────


@app.get("/api/orders")
@_require_admin_key
def list_orders():
    conn = store.get_conn()
    return jsonify(orders=store.list_orders(conn))


@app.post("/api/orders")
@_require_admin_key
def create_order():
    conn = store.get_conn()
    body = request.get_json(force=True, silent=False) or {}
    order = store.create_order(conn, body)
    return jsonify(order=order), 201


@app.get("/api/orders/<order_id>")
@_require_admin_key
def get_order(order_id: str):
    conn = store.get_conn()
    order = store.get_order(conn, order_id)
    if order is None:
        return jsonify(error="not_found"), 404
    return jsonify(order=order)


@app.patch("/api/orders/<order_id>")
@_require_admin_key
def update_order(order_id: str):
    conn = store.get_conn()
    body = request.get_json(force=True, silent=False) or {}
    order = store.update_order(conn, order_id, body)
    if order is None:
        return jsonify(error="not_found"), 404
    return jsonify(order=order)


@app.delete("/api/orders/<order_id>")
@_require_admin_key
def delete_order(order_id: str):
    conn = store.get_conn()
    if not store.delete_order(conn, order_id):
        return jsonify(error="not_found"), 404
    return jsonify(ok=True)


@app.post("/api/orders/<order_id>/documents")
@_require_admin_key
def add_order_document(order_id: str):
    conn = store.get_conn()
    body = request.get_json(force=True, silent=False) or {}
    order = store.add_document(conn, order_id, body)
    if order is None:
        return jsonify(error="not_found"), 404
    return jsonify(order=order), 201


@app.get("/api/orders/<order_id>/signing")
@_require_admin_key
def list_signing_revisions(order_id: str):
    conn = store.get_conn()
    if not store.get_order(conn, order_id):
        return jsonify(error="not_found"), 404
    return jsonify(revisions=signing.list_for_order(conn, order_id))


@app.post("/api/orders/<order_id>/signing/prepare")
@_require_admin_key
def prepare_signing(order_id: str):
    body = _json_body()
    revision = signing.prepare(store.get_conn(), order_id, body.get("payload"), body.get("requestKey"), body.get("expectedLatestRevisionId"))
    return jsonify(revision=revision)


@app.get("/api/orders/<order_id>/signing/<revision_id>")
@_require_admin_key
def get_signing_revision(order_id: str, revision_id: str):
    revision = signing.get(store.get_conn(), revision_id)
    if not revision or revision["order_id"] != order_id:
        return jsonify(error="not_found"), 404
    return jsonify(revision=revision)


@app.post("/api/orders/<order_id>/signing/<revision_id>/create-links")
@_require_admin_key
def create_signing_links(order_id: str, revision_id: str):
    revision = signing.get(store.get_conn(), revision_id)
    if not revision or revision["order_id"] != order_id:
        return jsonify(error="not_found"), 404
    created = signing.create_links(store.get_conn(), revision_id, _json_body().get("approvedSha256"))
    return jsonify(revision=created)


@app.post("/api/orders/<order_id>/signing/<revision_id>/sync")
@_require_admin_key
def sync_signing(order_id: str, revision_id: str):
    revision = signing.get(store.get_conn(), revision_id)
    if not revision or revision["order_id"] != order_id:
        return jsonify(error="not_found"), 404
    return jsonify(revision=signing.sync(store.get_conn(), revision_id))


@app.post("/api/orders/<order_id>/signing/<revision_id>/link-delivery")
@_require_admin_key
def mark_signing_link_delivery(order_id: str, revision_id: str):
    revision = signing.get(store.get_conn(), revision_id)
    if not revision or revision["order_id"] != order_id:
        return jsonify(error="not_found"), 404
    body = _json_body()
    return jsonify(revision=signing.mark_link_sent(store.get_conn(), revision_id, body.get("email"), body.get("sent")))


@app.post("/api/orders/<order_id>/signing/<revision_id>/reconcile")
@_require_admin_key
def reconcile_signing(order_id: str, revision_id: str):
    revision = signing.get(store.get_conn(), revision_id)
    if not revision or revision["order_id"] != order_id:
        return jsonify(error="not_found"), 404
    return jsonify(revision=signing.reconcile(store.get_conn(), revision_id))


@app.get("/api/orders/<order_id>/signing/<revision_id>/<kind>.pdf")
@_require_admin_key
def download_signing_file(order_id: str, revision_id: str, kind: str):
    revision = signing.get(store.get_conn(), revision_id)
    if not revision or revision["order_id"] != order_id:
        return jsonify(error="not_found"), 404
    if kind not in ("original", "completed", "audit"):
        return jsonify(error="not_found"), 404
    path = signing._path(revision_id, kind)
    if not path.exists() or (kind != "original" and revision["state"] != "signed"):
        return jsonify(error="not_ready"), 404
    return send_file(path, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{revision_id}-{kind}.pdf")


@app.post("/api/signing/notifications/<revision_id>")
@_require_admin_key
def signing_notification(revision_id: str):
    body = _json_body()
    revision = signing.get(store.get_conn(), revision_id)
    if not revision or not revision["envelope_id"] or body.get("envelopeId") != revision["envelope_id"]:
        return jsonify(error="not_found"), 404
    # The incoming event is only a hint. sync() fetches current state from
    # Documenso, so duplicate and delayed events cannot regress progress.
    signing.sync(store.get_conn(), revision_id)
    return jsonify(ok=True)


@app.post("/api/signing/completed-copy")
@_require_admin_key
def completed_signing_copy():
    path = signing.completed_copy_for_token(store.get_conn(), _json_body().get("token"))
    return send_file(path, mimetype="application/pdf", as_attachment=True,
                     download_name="completed-hosting-contract.pdf")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
