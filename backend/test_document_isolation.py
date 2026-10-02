"""Document ownership tests use temporary archives and controlled PDF generation."""
import copy
import sqlite3
import pytest
from test_orders import client, auth_headers
import store


def create(client, headers, context="scope-A"):
    snapshot = {"documentContextId": context, "clubs": ["Test Club"], "eventDate": "2026-10-16", "rentalPrice": "1400", "depositAmount": "300", "numGuests": "250", "contractSigners": []}
    response = client.post("/api/orders", json={"clubName": "Test Club", "eventDate": "2026-10-16", "rentalPrice": 1400, "depositAmount": 300, "snapshot": snapshot}, headers=headers)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["order"]


def document(order):
    payload = {"club_names": ["Test Club"], "club_name": "Test Club", "event_date": "October 16, 2026", "amount": "300"}
    doc = {"kind": "deposit_invoice", "number": "DEP-TEST", "filename": "DEP-TEST.pdf", "amount": 300, "generatedAt": "2026-09-30T00:00:00Z", "payload": payload, "sourceSnapshot": copy.deepcopy(order["snapshot"]), "expectedUpdatedAt": order["updatedAt"]}
    doc["generationReceipt"] = store.document_receipt(doc["kind"], payload, doc["sourceSnapshot"], doc["filename"])
    return doc


def attach(client, headers, order, doc):
    return client.post(f"/api/orders/{order['id']}/documents", json=doc, headers=headers)


def test_same_parties_and_date_cannot_share_a_document(client, auth_headers):
    a = create(client, auth_headers)
    b = create(client, auth_headers, "scope-B")
    doc = document(a)
    doc["expectedUpdatedAt"] = b["updatedAt"]
    assert attach(client, auth_headers, b, doc).status_code == 400
    # Relabeling the source fails even though the other event has identical terms.
    doc["sourceSnapshot"] = b["snapshot"]
    assert attach(client, auth_headers, b, doc).status_code == 400
    assert attach(client, auth_headers, a, document(a)).status_code == 201


@pytest.mark.parametrize("change", ["payload", "amount", "null_amount", "filename", "number", "receipt", "source"])
def test_receipt_and_content_cannot_be_relabelled(client, auth_headers, change):
    order = create(client, auth_headers)
    doc = document(order)
    if change == "payload": doc["payload"]["amount"] = "900"
    elif change == "amount": doc["amount"] = 900
    elif change == "null_amount": doc["amount"] = None
    elif change == "filename": doc["filename"] = "OTHER.pdf"
    elif change == "number": doc["number"] = "OTHER"
    elif change == "receipt": doc.pop("generationReceipt")
    else: doc.pop("sourceSnapshot")
    assert attach(client, auth_headers, order, doc).status_code == 400
    current = client.get(f"/api/orders/{order['id']}", headers=auth_headers).get_json()["order"]
    assert current["documents"] == []


def test_owner_cannot_be_reused_or_removed(client, auth_headers):
    order = create(client, auth_headers)
    same = client.post("/api/orders", json={"clubName": order["clubName"], "eventDate": order["eventDate"], "snapshot": order["snapshot"]}, headers=auth_headers)
    assert same.status_code == 400
    source = {k: v for k, v in order["snapshot"].items() if k != "documentContextId"}
    changed = client.patch(f"/api/orders/{order['id']}", json={"snapshot": source, "expectedUpdatedAt": order["updatedAt"]}, headers=auth_headers)
    assert changed.status_code == 400


def test_stale_saves_and_attachments_fail_without_replacing_current_docs(client, auth_headers):
    order = create(client, auth_headers)
    doc = document(order)
    first = attach(client, auth_headers, order, doc)
    assert first.status_code == 201
    assert attach(client, auth_headers, order, doc).status_code == 400
    patch = client.patch(f"/api/orders/{order['id']}", json={"snapshot": order["snapshot"], "expectedUpdatedAt": order["updatedAt"]}, headers=auth_headers)
    assert patch.status_code == 400
    missing = client.patch(f"/api/orders/{order['id']}", json={"snapshot": order["snapshot"]}, headers=auth_headers)
    assert missing.status_code == 400


def test_signer_changes_preserve_financial_documents(client, auth_headers):
    order = create(client, auth_headers)
    saved = attach(client, auth_headers, order, document(order)).get_json()["order"]
    snapshot = {**saved["snapshot"], "contractSigners": [{"fullName": "Controlled Person", "email": "controlled@example.test", "club": "Test Club", "id": "person"}], "contractPresign": True}
    updated = client.patch(f"/api/orders/{order['id']}", json={"snapshot": snapshot, "expectedUpdatedAt": saved["updatedAt"]}, headers=auth_headers)
    assert updated.status_code == 200
    current = updated.get_json()["order"]
    assert current["documents"][0]["stale"] is False
    assert current["documents"][0]["amount"] == 300
    assert current["rentalPrice"] == 1400


def test_generator_receipt_round_trip_and_fallback_party(client, auth_headers, monkeypatch):
    import app
    monkeypatch.setattr(app, "generate_invoice", lambda body: (b"%PDF-controlled", body["invoice_number"]))
    order = create(client, auth_headers)
    doc = document(order)
    doc["payload"]["invoice_number"] = "DEP-TEST"
    body = {**doc["payload"], "_document_source": doc["sourceSnapshot"]}
    response = client.post("/api/generate/invoice/deposit", json=body, headers=auth_headers)
    assert response.status_code == 200
    doc["generationReceipt"] = response.headers["X-Document-Receipt"]
    assert attach(client, auth_headers, order, doc).status_code == 201
    body.update(club_names=[], club_name="Other Club")
    assert client.post("/api/generate/invoice/deposit", json=body, headers=auth_headers).status_code == 400


def test_legacy_mismatches_are_retained_but_never_current(client, auth_headers):
    order = create(client, auth_headers)
    saved = attach(client, auth_headers, order, document(order)).get_json()["order"]
    with sqlite3.connect(store._db_path()) as conn:
        conn.execute("UPDATE documents SET source_snapshot = NULL, payload = ? WHERE order_id = ?", ('{"club_name":"Other Club","event_date":"October 16, 2026","amount":"300"}', order["id"]))
    current = client.get(f"/api/orders/{order['id']}", headers=auth_headers).get_json()["order"]
    assert current["documents"][0]["stale"] is True
    assert current["documents"][0]["id"] == saved["documents"][0]["id"]
    assert current["documents"][0]["amount"] == 300
    with sqlite3.connect(store._db_path()) as conn:
        conn.execute("UPDATE documents SET payload = ? WHERE order_id = ?", ('{"club_name":null}', order["id"]))
    assert client.get(f"/api/orders/{order['id']}", headers=auth_headers).status_code == 200


def test_signing_guard_accepts_formatted_amounts_and_rejects_foreign_representatives(client, auth_headers):
    import signing
    order = create(client, auth_headers)
    payload = {"club_name": "Test Club", "club_names": ["Test Club"], "date": "October 16, 2026", "price": "$1,400.00", "deposit": "$300", "max_guests": "250", "signers": []}
    signing._assert_order_terms(order, payload)
    payload["signers"] = [{"fullName": "Foreign Person", "email": "foreign@example.test", "club": "Test Club", "role": "club"}]
    with pytest.raises(signing.SigningError, match="recipients"):
        signing._assert_order_terms(order, payload)


def test_ambiguous_display_names_do_not_hide_different_parties(client, auth_headers):
    order = create(client, auth_headers)
    snapshot = {**order["snapshot"], "clubs": ["Alpha and Beta"]}
    payload = {"club_name": "Alpha and Beta", "club_names": ["Alpha", "Beta"], "event_date": "October 16, 2026"}
    with pytest.raises(ValueError, match="parties"):
        store._validate_document_owner({"kind": "deposit_invoice", "payload": payload, "sourceSnapshot": snapshot}, snapshot, "Alpha and Beta", "2026-10-16")
