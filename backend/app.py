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

The generate/health routes accept unauthenticated requests from any origin
(they're called directly from the browser). The orders routes are called
server-to-server from the Next.js admin app only, so they require an
`X-Admin-Key` header and are deliberately left out of the CORS allowlist
below — see `_require_admin_key`.

Each generate/* route accepts JSON, returns application/pdf with a sensible
filename.

Dev:
    pip install -r requirements.txt
    python app.py
"""
from __future__ import annotations

import hmac
import os
from functools import wraps
from io import BytesIO
from typing import Any, Callable

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

import store
from generators import generate_contract, generate_credit_memo, generate_invoice
from generators.base import (
    TypstCompileError,
    TypstNotFoundError,
    slug,
)

app = Flask(__name__)
store.init_app(app)

# Allow the Next.js dev server (default :3000) and any other local origin to
# call the PDF-generation + health routes. Deliberately scoped to just those
# two path groups (rather than `r"/api/*"`) so `/api/orders*` gets no
# Access-Control-Allow-Origin header at all — those routes are for
# server-to-server calls from the Next app's own backend, never a browser.
CORS(app, resources={r"/api/generate/*": {"origins": "*"}, r"/api/health": {"origins": "*"}})


@app.errorhandler(TypstNotFoundError)
def _handle_no_typst(e: TypstNotFoundError):
    return jsonify(error="typst_not_installed", detail=str(e)), 500


@app.errorhandler(TypstCompileError)
def _handle_typst_compile(e: TypstCompileError):
    return jsonify(error="typst_compile_failed", detail=e.stderr, code=e.returncode), 500


@app.errorhandler(ValueError)
def _handle_value_error(e: ValueError):
    return jsonify(error="invalid_input", detail=str(e)), 400


def _pdf_response(pdf_bytes: bytes, filename: str):
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.post("/api/generate/contract")
def contract():
    payload = request.get_json(force=True, silent=False) or {}
    pdf = generate_contract(payload)
    club = slug(str(payload.get("club_name", "partner")))
    return _pdf_response(pdf, f"theta_xi_{club}_contract.pdf")


def _invoice_route(kind: str):
    payload = request.get_json(force=True, silent=False) or {}
    payload["kind"] = kind
    pdf, number = generate_invoice(payload)
    return _pdf_response(pdf, f"{number}.pdf")


@app.post("/api/generate/invoice/deposit")
def invoice_deposit():
    return _invoice_route("deposit")


@app.post("/api/generate/invoice/rental")
def invoice_rental():
    return _invoice_route("rental")


@app.post("/api/generate/credit-memo")
def credit_memo():
    payload = request.get_json(force=True, silent=False) or {}
    pdf, number = generate_credit_memo(payload)
    return _pdf_response(pdf, f"{number}.pdf")


# ── Orders API ──────────────────────────────────────────────────────────
# Server-to-server only: gated by X-Admin-Key, and excluded from CORS above.

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
        if not expected or not provided or not hmac.compare_digest(provided, expected):
            return jsonify(error="unauthorized"), 401
        return fn(*args, **kwargs)

    return wrapper


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
