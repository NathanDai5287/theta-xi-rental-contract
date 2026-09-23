"""
Tests for the orders archive API (backend/store.py + the /api/orders* routes
in app.py). Runs against a temp-file SQLite DB — never `backend/orders.db`.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

ADMIN_KEY = "test-admin-key"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """
    A Flask test client wired to a fresh temp-file DB per test, with a known
    ADMIN_KEY. app.py reads ORDERS_DB_PATH/ADMIN_KEY from the environment at
    import time (store.init_db is called from store.init_app during app
    creation) and at request time (the auth check), so both are set before
    (re-)importing `app` and `store` fresh for each test.
    """
    db_path = tmp_path / "orders-test.db"
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


def _sample_order(**overrides):
    body = {
        "clubName": "Pi Sigma Delta",
        "eventDate": "2026-05-05",
        "rentalPrice": 2000.0,
        "depositAmount": 500.0,
        "notes": "",
        "snapshot": {"guests": 150, "areas": ["patio", "hall"]},
    }
    body.update(overrides)
    return body


def _sample_document(**overrides):
    doc = {
        "kind": "deposit_invoice",
        "number": "DEP-2026-0505-PISIGM",
        "filename": "DEP-2026-0505-PISIGM.pdf",
        "amount": 500.0,
        "generatedAt": "2026-01-01T00:00:00+00:00",
        "payload": {"club_name": "Pi Sigma Delta", "amount": 500},
    }
    doc.update(overrides)
    return doc


def test_create_order_normalizes_club_name(client, auth_headers):
    """The archive (and the finance ledger it feeds) stores the tidy,
    print-normalized name — not whatever casing was typed."""
    r = client.post(
        "/api/orders", json=_sample_order(clubName="alpha alpha"), headers=auth_headers
    )
    assert r.status_code == 201
    assert r.get_json()["order"]["clubName"] == "Alpha Alpha"


def test_patch_order_normalizes_club_name(client, auth_headers):
    created = client.post(
        "/api/orders", json=_sample_order(), headers=auth_headers
    ).get_json()["order"]
    r = client.patch(
        f"/api/orders/{created['id']}",
        json={"clubName": "beta beta"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.get_json()["order"]["clubName"] == "Beta Beta"


# ── auth ─────────────────────────────────────────────────────────────────

def test_missing_key_rejected(client):
    r = client.get("/api/orders")
    assert r.status_code == 401
    assert r.get_json() == {"error": "unauthorized"}


def test_wrong_key_rejected(client):
    r = client.get("/api/orders", headers={"X-Admin-Key": "nope"})
    assert r.status_code == 401
    assert r.get_json() == {"error": "unauthorized"}


def test_unset_admin_key_rejects_everything(tmp_path, monkeypatch):
    """Fail closed: if ADMIN_KEY isn't configured, no key can possibly match."""
    db_path = tmp_path / "orders-unset.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db_path))
    monkeypatch.delenv("ADMIN_KEY", raising=False)

    import store as store_module
    import app as app_module

    importlib.reload(store_module)
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True

    with app_module.app.test_client() as c:
        r = c.get("/api/orders", headers={"X-Admin-Key": "anything"})
        assert r.status_code == 401
        assert r.get_json() == {"error": "unauthorized"}

        r2 = c.get("/api/orders")
        assert r2.status_code == 401


# ── CRUD round-trip ──────────────────────────────────────────────────────

def test_create_and_get_order(client, auth_headers):
    r = client.post("/api/orders", json=_sample_order(), headers=auth_headers)
    assert r.status_code == 201
    order = r.get_json()["order"]
    assert order["id"].startswith("ord_")
    assert order["clubName"] == "Pi Sigma Delta"
    assert order["eventDate"] == "2026-05-05"
    assert order["rentalPrice"] == 2000.0
    assert order["depositAmount"] == 500.0
    assert order["statusOverride"] is None
    assert order["notes"] == ""
    assert order["snapshot"] == {"guests": 150, "areas": ["patio", "hall"]}
    assert order["documents"] == []
    assert order["createdAt"] == order["updatedAt"]

    r2 = client.get(f"/api/orders/{order['id']}", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.get_json()["order"] == order


def test_get_unknown_order_404(client, auth_headers):
    r = client.get("/api/orders/ord_doesnotexist", headers=auth_headers)
    assert r.status_code == 404


def test_patch_updates_only_given_fields(client, auth_headers):
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]

    r = client.patch(
        f"/api/orders/{created['id']}",
        json={"rentalPrice": 2500.0},
        headers=auth_headers,
    )
    assert r.status_code == 200
    updated = r.get_json()["order"]
    assert updated["rentalPrice"] == 2500.0
    # untouched fields survive exactly
    assert updated["clubName"] == created["clubName"]
    assert updated["eventDate"] == created["eventDate"]
    assert updated["depositAmount"] == created["depositAmount"]
    assert updated["notes"] == created["notes"]
    assert updated["snapshot"] == created["snapshot"]
    assert updated["statusOverride"] == created["statusOverride"]
    assert updated["updatedAt"] >= created["updatedAt"]


def test_patch_can_set_status_override_explicitly_null(client, auth_headers):
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]

    r1 = client.patch(
        f"/api/orders/{created['id']}",
        json={"statusOverride": "contracted"},
        headers=auth_headers,
    )
    assert r1.get_json()["order"]["statusOverride"] == "contracted"

    # Explicitly present-and-null must clear it back to "derive it",
    # distinct from simply omitting the key.
    r2 = client.patch(
        f"/api/orders/{created['id']}",
        json={"statusOverride": None},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.get_json()["order"]["statusOverride"] is None


def test_patch_unknown_order_404(client, auth_headers):
    r = client.patch("/api/orders/ord_nope", json={"notes": "x"}, headers=auth_headers)
    assert r.status_code == 404


def test_patch_invalid_status_override_rejected(client, auth_headers):
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]
    r = client.patch(
        f"/api/orders/{created['id']}",
        json={"statusOverride": "not-a-real-status"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_input"


def test_delete_order_and_cascade(client, auth_headers):
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]
    client.post(
        f"/api/orders/{created['id']}/documents",
        json=_sample_document(),
        headers=auth_headers,
    )

    r = client.delete(f"/api/orders/{created['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}

    r2 = client.get(f"/api/orders/{created['id']}", headers=auth_headers)
    assert r2.status_code == 404

    # cascade: no orphaned document rows left behind for a re-used id is
    # hard to observe from the API directly, so check via the store module.
    import store as store_module
    conn = store_module._connect()
    try:
        rows = conn.execute(
            "SELECT * FROM documents WHERE order_id = ?", (created["id"],)
        ).fetchall()
        assert rows == []
    finally:
        conn.close()


def test_delete_unknown_order_404(client, auth_headers):
    r = client.delete("/api/orders/ord_nope", headers=auth_headers)
    assert r.status_code == 404


# ── documents ────────────────────────────────────────────────────────────

def test_add_document_appends_and_returns_order(client, auth_headers):
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]

    r = client.post(
        f"/api/orders/{created['id']}/documents",
        json=_sample_document(),
        headers=auth_headers,
    )
    assert r.status_code == 201
    order = r.get_json()["order"]
    assert len(order["documents"]) == 1
    doc = order["documents"][0]
    assert doc["id"].startswith("doc_")
    assert doc["kind"] == "deposit_invoice"
    assert doc["number"] == "DEP-2026-0505-PISIGM"
    assert doc["amount"] == 500.0
    assert doc["payload"] == {"club_name": "Pi Sigma Delta", "amount": 500}
    # adding a document bumps the order's updatedAt
    assert order["updatedAt"] >= created["updatedAt"]


def test_documents_ordered_oldest_first(client, auth_headers):
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]

    client.post(
        f"/api/orders/{created['id']}/documents",
        json=_sample_document(kind="contract", number="C-1", filename="c1.pdf", amount=None, generatedAt="2026-01-01T00:00:00+00:00"),
        headers=auth_headers,
    )
    r = client.post(
        f"/api/orders/{created['id']}/documents",
        json=_sample_document(kind="deposit_invoice", number="D-1", filename="d1.pdf", generatedAt="2026-01-02T00:00:00+00:00"),
        headers=auth_headers,
    )
    order = r.get_json()["order"]
    kinds = [d["kind"] for d in order["documents"]]
    assert kinds == ["contract", "deposit_invoice"]


def test_add_document_unknown_order_404(client, auth_headers):
    r = client.post(
        "/api/orders/ord_nope/documents", json=_sample_document(), headers=auth_headers
    )
    assert r.status_code == 404


def test_add_document_invalid_kind_rejected(client, auth_headers):
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]
    r = client.post(
        f"/api/orders/{created['id']}/documents",
        json=_sample_document(kind="not_a_kind"),
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_input"


def test_create_order_with_inline_documents(client, auth_headers):
    body = _sample_order(documents=[_sample_document(), _sample_document(kind="contract", number="C-1", filename="c1.pdf", amount=None)])
    r = client.post("/api/orders", json=body, headers=auth_headers)
    assert r.status_code == 201
    order = r.get_json()["order"]
    assert len(order["documents"]) == 2
    ids = [d["id"] for d in order["documents"]]
    assert len(set(ids)) == 2  # minted ids are unique
    assert all(d["id"].startswith("doc_") for d in order["documents"])


# ── validation ───────────────────────────────────────────────────────────

def test_create_order_missing_required_field(client, auth_headers):
    body = _sample_order()
    del body["clubName"]
    r = client.post("/api/orders", json=body, headers=auth_headers)
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_input"


def test_create_order_bad_event_date(client, auth_headers):
    r = client.post("/api/orders", json=_sample_order(eventDate="05/05/2026"), headers=auth_headers)
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_input"


def test_create_order_snapshot_must_be_object(client, auth_headers):
    r = client.post("/api/orders", json=_sample_order(snapshot=["not", "an", "object"]), headers=auth_headers)
    assert r.status_code == 400


def test_create_order_numeric_field_must_be_number_or_null(client, auth_headers):
    r = client.post("/api/orders", json=_sample_order(rentalPrice="two thousand"), headers=auth_headers)
    assert r.status_code == 400


def test_create_order_numeric_fields_accept_null(client, auth_headers):
    r = client.post(
        "/api/orders",
        json=_sample_order(rentalPrice=None, depositAmount=None),
        headers=auth_headers,
    )
    assert r.status_code == 201
    order = r.get_json()["order"]
    assert order["rentalPrice"] is None
    assert order["depositAmount"] is None


def test_create_order_numeric_field_rejects_non_finite(client, auth_headers):
    # Python's json.loads accepts NaN/Infinity literals; SQLite would store
    # them as NULL/Inf — silent corruption in the archive.
    for bad in (float("nan"), float("inf"), float("-inf")):
        r = client.post("/api/orders", json=_sample_order(rentalPrice=bad), headers=auth_headers)
        assert r.status_code == 400, bad


# ── snapshot/payload round-trip, unicode + nesting ────────────────────────

def test_snapshot_and_payload_round_trip_verbatim(client, auth_headers):
    snapshot = {
        "club": "Théta Χι Événement",
        "nested": {"a": [1, 2, {"b": "c"}], "flag": True, "none": None},
        "unicode": "café \U0001F600",
    }
    payload = {
        "line_items": [{"description": "Dépôt", "amount": "$1,234.56"}],
        "nested": {"deep": {"deeper": [1, 2, 3]}},
        "emoji": "\U0001F389",
    }

    created = client.post(
        "/api/orders", json=_sample_order(snapshot=snapshot), headers=auth_headers
    ).get_json()["order"]
    assert created["snapshot"] == snapshot

    doc_body = _sample_document(payload=payload)
    r = client.post(
        f"/api/orders/{created['id']}/documents", json=doc_body, headers=auth_headers
    )
    order = r.get_json()["order"]
    assert order["documents"][0]["payload"] == payload

    # re-fetch to make sure it survives a full DB round-trip, not just the
    # in-memory response of the write itself
    refetched = client.get(f"/api/orders/{created['id']}", headers=auth_headers).get_json()["order"]
    assert refetched["snapshot"] == snapshot
    assert refetched["documents"][0]["payload"] == payload


# ── listing ──────────────────────────────────────────────────────────────

def test_list_orders_newest_event_first(client, auth_headers):
    client.post("/api/orders", json=_sample_order(clubName="Early Club", eventDate="2026-01-01"), headers=auth_headers)
    client.post("/api/orders", json=_sample_order(clubName="Late Club", eventDate="2026-12-31"), headers=auth_headers)
    client.post("/api/orders", json=_sample_order(clubName="Mid Club", eventDate="2026-06-15"), headers=auth_headers)

    r = client.get("/api/orders", headers=auth_headers)
    assert r.status_code == 200
    orders = r.get_json()["orders"]
    assert [o["clubName"] for o in orders] == ["Late Club", "Mid Club", "Early Club"]


def test_list_orders_summary_shape_and_document_fields(client, auth_headers):
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]
    client.post(
        f"/api/orders/{created['id']}/documents",
        json=_sample_document(kind="deposit_invoice"),
        headers=auth_headers,
    )
    # Same kind again — supersedes the first rather than adding a second.
    client.post(
        f"/api/orders/{created['id']}/documents",
        json=_sample_document(kind="deposit_invoice", number="DEP-2", filename="dep2.pdf"),
        headers=auth_headers,
    )
    client.post(
        f"/api/orders/{created['id']}/documents",
        json=_sample_document(kind="contract", number="C-1", filename="c1.pdf", amount=None),
        headers=auth_headers,
    )

    r = client.get("/api/orders", headers=auth_headers)
    summary = r.get_json()["orders"][0]

    assert "snapshot" not in summary
    assert "documents" not in summary
    assert summary["documentCount"] == 2
    # Distinct, and in DOCUMENT_KINDS order regardless of when each was issued
    # here the deposit invoice was generated first, but contract sorts ahead.
    assert summary["documentKinds"] == ["contract", "deposit_invoice"]


def test_document_kinds_ignore_generation_order(client, auth_headers):
    """The list view renders documentKinds as "which of the four exist", so the
    sequence must not depend on the order the documents happened to be issued."""
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]
    # Issue them in reverse display order.
    for kind, number in [
        ("credit_memo", "CM-1"),
        ("rental_invoice", "RNT-1"),
        ("contract", "CTR-1"),
    ]:
        client.post(
            f"/api/orders/{created['id']}/documents",
            json=_sample_document(kind=kind, number=number, filename=f"{number}.pdf"),
            headers=auth_headers,
        )

    summary = client.get("/api/orders", headers=auth_headers).get_json()["orders"][0]
    assert summary["documentKinds"] == ["contract", "rental_invoice", "credit_memo"]


@pytest.mark.parametrize("bad_date", ["2026-5-5", "2026-05-5", "26-05-05", "2026-5-05"])
def test_event_date_must_be_zero_padded(client, auth_headers, bad_date):
    """`%Y-%m-%d` accepts "2026-5-5", but event_date is a TEXT column sorted
    lexicographically — an unpadded value silently sorts into the wrong place
    in the archive, so it has to be rejected at the door."""
    body = _sample_order()
    body["eventDate"] = bad_date
    r = client.post("/api/orders", json=body, headers=auth_headers)
    assert r.status_code == 400, f"{bad_date!r} should be rejected"
    assert r.get_json()["error"] == "invalid_input"


def test_event_date_padding_enforced_on_patch(client, auth_headers):
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]
    r = client.patch(f"/api/orders/{created['id']}", json={"eventDate": "2026-5-5"}, headers=auth_headers)
    assert r.status_code == 400


def test_list_orders_sorts_dates_correctly_across_month_boundary(client, auth_headers):
    """Regression for the padding bug: December must outrank May."""
    for club, date in [("May Club", "2026-05-05"), ("Dec Club", "2026-12-01")]:
        body = _sample_order()
        body["clubName"], body["eventDate"] = club, date
        client.post("/api/orders", json=body, headers=auth_headers)

    orders = client.get("/api/orders", headers=auth_headers).get_json()["orders"]
    assert [o["clubName"] for o in orders] == ["Dec Club", "May Club"]


def test_regenerating_a_document_replaces_it(client, auth_headers):
    """A rental has at most one of each document. Re-posting the same kind
    supersedes the earlier one — two deposit invoices on an order would
    double-count in the archive's ledger and misreport the balance."""
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]

    first = client.post(
        f"/api/orders/{created['id']}/documents",
        json=_sample_document(kind="deposit_invoice", number="DEP-1", filename="dep1.pdf", amount=500),
        headers=auth_headers,
    ).get_json()["order"]
    assert len(first["documents"]) == 1

    second = client.post(
        f"/api/orders/{created['id']}/documents",
        json=_sample_document(kind="deposit_invoice", number="DEP-2", filename="dep2.pdf", amount=600),
        headers=auth_headers,
    ).get_json()["order"]

    assert len(second["documents"]) == 1, "regeneration must replace, not append"
    assert second["documents"][0]["number"] == "DEP-2"
    assert second["documents"][0]["amount"] == 600
    # Total across deposit invoices must be 600, not 1100.
    assert sum(d["amount"] for d in second["documents"] if d["kind"] == "deposit_invoice") == 600


def test_saving_is_idempotent(client, auth_headers):
    """The documents step re-sends every generated document on each save, so
    pressing save repeatedly must converge rather than accumulate."""
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]
    docs = [
        _sample_document(kind="contract", number="CTR-1", filename="ctr.pdf", amount=2000),
        _sample_document(kind="deposit_invoice", number="DEP-1", filename="dep.pdf", amount=500),
    ]
    for _ in range(3):
        for d in docs:
            r = client.post(f"/api/orders/{created['id']}/documents", json=d, headers=auth_headers)
            assert r.status_code == 201

    order = client.get(f"/api/orders/{created['id']}", headers=auth_headers).get_json()["order"]
    assert len(order["documents"]) == 2
    summary = client.get("/api/orders", headers=auth_headers).get_json()["orders"][0]
    assert summary["documentCount"] == 2


def test_create_dedupes_inline_documents_by_kind(client, auth_headers):
    body = _sample_order()
    body["documents"] = [
        _sample_document(kind="contract", number="CTR-old", filename="a.pdf", amount=1),
        _sample_document(kind="contract", number="CTR-new", filename="b.pdf", amount=2),
    ]
    order = client.post("/api/orders", json=body, headers=auth_headers).get_json()["order"]
    assert len(order["documents"]) == 1
    assert order["documents"][0]["number"] == "CTR-new", "last of each kind should win"


def test_different_kinds_still_coexist(client, auth_headers):
    """The uniqueness rule is per (order, kind) — not one document per order."""
    created = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]
    for kind in ("contract", "deposit_invoice", "rental_invoice", "credit_memo"):
        client.post(
            f"/api/orders/{created['id']}/documents",
            json=_sample_document(kind=kind, number=f"{kind}-1", filename=f"{kind}.pdf"),
            headers=auth_headers,
        )
    order = client.get(f"/api/orders/{created['id']}", headers=auth_headers).get_json()["order"]
    assert len(order["documents"]) == 4


def test_same_kind_on_different_orders_is_fine(client, auth_headers):
    a = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]
    b = client.post("/api/orders", json=_sample_order(), headers=auth_headers).get_json()["order"]
    for oid in (a["id"], b["id"]):
        r = client.post(
            f"/api/orders/{oid}/documents",
            json=_sample_document(kind="contract", number="CTR-1", filename="c.pdf"),
            headers=auth_headers,
        )
        assert r.status_code == 201
