"""
Tests for the /api/generate/* routes: auth gating, input validation, and
template-injection resistance. PDF-rendering tests are skipped when `typst`
isn't on PATH; text-extraction assertions additionally need pypdf.
"""
from __future__ import annotations

import importlib
import shutil
from io import BytesIO

import pytest

ADMIN_KEY = "test-admin-key"

GENERATE_ROUTES = [
    "/api/generate/contract",
    "/api/generate/invoice/deposit",
    "/api/generate/invoice/rental",
    "/api/generate/credit-memo",
]

HAS_TYPST = shutil.which("typst") is not None
needs_typst = pytest.mark.skipif(not HAS_TYPST, reason="typst not installed")

pypdf = pytest.importorskip("pypdf", reason="pypdf not installed")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Fresh app per test, wired to a temp DB and a known ADMIN_KEY —
    same pattern as test_orders.py."""
    db_path = tmp_path / "orders-generate-test.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db_path))
    monkeypatch.setenv("ADMIN_KEY", ADMIN_KEY)

    import store as store_module
    import app as app_module

    importlib.reload(store_module)
    importlib.reload(app_module)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def auth_headers():
    return {"X-Admin-Key": ADMIN_KEY}


def _contract_payload(**overrides):
    body = {
        "club_name": "Pi Sigma Delta",
        "date": "March 15, 2026",
        "start_time": "22:00",
        "end_time": "02:00",
        "price": "1500",
        "deposit": "500",
        "max_guests": "150",
        "monitors": "4",
        "areas": ["living_room"],
        "cleared": {"living_room": True},
        "guest_list": True,
        "sound_system": True,
        "lighting_system": False,
        "cleanup_tier": "basic",
        "sign": False,
    }
    body.update(overrides)
    return body


def _invoice_payload(**overrides):
    body = {
        "club_name": "Pi Sigma Delta",
        "event_date": "2026-03-15",
        "issue_date": "March 1, 2026",
        "due_date": "March 15, 2026",
        "amount": 500,
    }
    body.update(overrides)
    return body


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = pypdf.PdfReader(BytesIO(pdf_bytes))
    text = " ".join((page.extract_text() or "") for page in reader.pages)
    # Extraction inserts odd whitespace; normalize for substring checks.
    return " ".join(text.split())


# ── auth ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("route", GENERATE_ROUTES)
def test_generate_routes_reject_missing_key(client, route):
    r = client.post(route, json={})
    assert r.status_code == 401
    assert r.get_json() == {"error": "unauthorized"}


@pytest.mark.parametrize("route", GENERATE_ROUTES)
def test_generate_routes_reject_wrong_key(client, route):
    r = client.post(route, json={}, headers={"X-Admin-Key": "nope"})
    assert r.status_code == 401


@pytest.mark.parametrize("route", GENERATE_ROUTES)
def test_generate_routes_reject_non_ascii_key_without_500(client, route):
    """hmac.compare_digest raises TypeError on non-ASCII str — an exotic key
    must be a clean 401, never a 500."""
    r = client.post(route, json={}, headers={"X-Admin-Key": "kéy-ü"})
    assert r.status_code == 401


def test_health_stays_open(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


# ── CORS ────────────────────────────────────────────────────────────────

def test_generate_routes_emit_no_cors_headers(client, auth_headers):
    """Server-to-server only: browsers must not be able to call these routes
    cross-origin even with the key (the key lives in the Next backend)."""
    r = client.post(
        "/api/generate/contract",
        json={},
        headers={**auth_headers, "Origin": "https://evil.example"},
    )
    assert "Access-Control-Allow-Origin" not in r.headers


def test_health_keeps_cors_open(client):
    r = client.get("/api/health", headers={"Origin": "https://example.com"})
    # flask_cors may echo the origin rather than send a literal "*".
    assert r.headers.get("Access-Control-Allow-Origin") in ("*", "https://example.com")


# ── request-size cap ────────────────────────────────────────────────────

def test_oversized_payload_rejected(client, auth_headers):
    r = client.post(
        "/api/generate/contract",
        json=_contract_payload(club_name="A" * (300 * 1024)),
        headers=auth_headers,
    )
    assert r.status_code == 413
    assert r.get_json()["error"] == "payload_too_large"


# ── validation (no typst needed — these fail before rendering) ──────────

def test_contract_missing_club_rejected(client, auth_headers):
    body = _contract_payload()
    del body["club_name"]
    r = client.post("/api/generate/contract", json=body, headers=auth_headers)
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_input"


def test_contract_garbage_price_rejected(client, auth_headers):
    r = client.post(
        "/api/generate/contract",
        json=_contract_payload(price="abc"),
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_input"


def test_contract_garbage_max_guests_rejected(client, auth_headers):
    r = client.post(
        "/api/generate/contract",
        json=_contract_payload(max_guests="abc"),
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_contract_negative_price_rejected(client, auth_headers):
    r = client.post(
        "/api/generate/contract",
        json=_contract_payload(price="-50"),
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_contract_bad_time_rejected(client, auth_headers):
    r = client.post(
        "/api/generate/contract",
        json=_contract_payload(start_time="25:00"),
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_invoice_negative_amount_rejected(client, auth_headers):
    r = client.post(
        "/api/generate/invoice/deposit",
        json=_invoice_payload(amount=-100),
        headers=auth_headers,
    )
    assert r.status_code == 400


# ── rendering (needs typst) ─────────────────────────────────────────────

@needs_typst
def test_contract_renders_pdf(client, auth_headers):
    r = client.post(
        "/api/generate/contract", json=_contract_payload(), headers=auth_headers
    )
    assert r.status_code == 200
    assert r.data.startswith(b"%PDF")
    assert r.mimetype == "application/pdf"


@needs_typst
def test_deposit_invoice_renders_pdf(client, auth_headers):
    r = client.post(
        "/api/generate/invoice/deposit",
        json=_invoice_payload(),
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.data.startswith(b"%PDF")


@needs_typst
def test_single_org_contract_text(client, auth_headers):
    r = client.post(
        "/api/generate/contract", json=_contract_payload(), headers=auth_headers
    )
    text = _pdf_text(r.data)
    assert "Pi Sigma Delta hereby agrees" in text
    assert "Pi Sigma Delta is solely responsible" in text


@needs_typst
def test_multi_org_contract_text(client, auth_headers):
    """Two organizations: each is introduced as Club 1 / Club 2, and the body
    refers to them collectively as the Renter (singular verb agreement)."""
    r = client.post(
        "/api/generate/contract",
        json=_contract_payload(club_names=["Alpha Club", "Beta Club"]),
        headers=auth_headers,
    )
    assert r.status_code == 200
    text = _pdf_text(r.data)
    assert 'Alpha Club ("Club 1")' in text
    assert 'Beta Club ("Club 2")' in text
    assert 'collectively referred to as the "Renter"' in text
    assert "the Renter is solely responsible" in text
    # Both organizations get their own signature block.
    assert text.count("Club 1") >= 2  # opening + signature area


@needs_typst
def test_club_names_empty_list_falls_back_to_error(client, auth_headers):
    r = client.post(
        "/api/generate/contract",
        json=_contract_payload(club_names=[]),
        headers=auth_headers,
    )
    assert r.status_code == 400


# ── injection resistance (needs typst + pypdf) ──────────────────────────

@needs_typst
def test_typst_markup_in_club_name_is_inert(client, auth_headers):
    """A club name containing typst markup must render as literal text, not
    execute — no forged clauses in a signed contract. The tell: the raw
    markup source is visible in the PDF, meaning typst treated it as text."""
    r = client.post(
        "/api/generate/contract",
        json=_contract_payload(club_name='ACME #subclause("9z.")[Forged clause.] Club'),
        headers=auth_headers,
    )
    assert r.status_code == 200
    text = _pdf_text(r.data)
    assert '#subclause("9z.")[Forged clause.]' in text  # rendered literally
    assert "ACME" in text


@needs_typst
def test_placeholder_token_in_club_name_not_expanded(client, auth_headers):
    """Regression for the cascade bug: sequential str.replace re-expanded
    «TOKENS» inside user values. Single-pass substitution must leave them
    literal."""
    r = client.post(
        "/api/generate/contract",
        json=_contract_payload(club_name="ACME «DEPOSIT» Club", deposit="500"),
        headers=auth_headers,
    )
    assert r.status_code == 200
    text = _pdf_text(r.data)
    # The token survives literally; the deposit value is not spliced into
    # the club name.
    assert "«DEPOSIT»" in text
    assert "ACME 500 Club" not in text


@needs_typst
def test_typst_markup_in_invoice_club_name_is_inert(client, auth_headers):
    """The invoice terms block references #CLUB_NAME — an interpolated raw
    name there used to be a markup-injection hole."""
    r = client.post(
        "/api/generate/invoice/deposit",
        json=_invoice_payload(club_name='ACME #subclause("9z.")[Forged clause.] Club'),
        headers=auth_headers,
    )
    assert r.status_code == 200
    text = _pdf_text(r.data)
    assert '#subclause("9z.")[Forged clause.]' in text  # rendered literally
    assert "ACME" in text


@needs_typst
def test_deposit_invoice_cites_hard_cap(client, auth_headers):
    """Clause 3c cites max(200, max_guests) — the contract's 4a cap."""
    r = client.post(
        "/api/generate/invoice/deposit",
        json=_invoice_payload(max_guests="250"),
        headers=auth_headers,
    )
    assert r.status_code == 200
    text = _pdf_text(r.data)
    assert "250 guests" in text


@needs_typst
def test_deposit_invoice_unparseable_max_guests_defaults_200(client, auth_headers):
    """Archived payloads predate max_guests and must keep replaying."""
    r = client.post(
        "/api/generate/invoice/deposit",
        json=_invoice_payload(max_guests="not-a-number"),
        headers=auth_headers,
    )
    assert r.status_code == 200
    text = _pdf_text(r.data)
    assert "200 guests" in text
