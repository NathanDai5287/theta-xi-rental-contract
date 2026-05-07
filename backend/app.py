"""
Flask backend serving PDF generation endpoints for the Theta Xi tools.

Routes:
  POST /api/generate/contract        -> contract PDF
  POST /api/generate/invoice/deposit -> security deposit invoice PDF
  POST /api/generate/invoice/rental  -> rental invoice PDF
  POST /api/generate/credit-memo     -> credit memo PDF

Each accepts JSON, returns application/pdf with a sensible filename.

Dev:
    pip install -r requirements.txt
    python app.py
"""
from __future__ import annotations

from io import BytesIO

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from generators import generate_contract, generate_credit_memo, generate_invoice
from generators.base import (
    TypstCompileError,
    TypstNotFoundError,
    slug,
)

app = Flask(__name__)
# Allow the Next.js dev server (default :3000) and any other local origin to call us.
CORS(app, resources={r"/api/*": {"origins": "*"}})


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
