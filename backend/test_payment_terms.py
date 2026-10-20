"""Real PDF and archive preservation tests; no live provider calls."""
from io import BytesIO
import hashlib
import pytest
from pypdf import PdfReader
import signing
import store
from generators.contract import generate_contract
from payment_terms import ADVANCE_PAYMENT_TIMING
from test_generate import _contract_payload, client, auth_headers
from test_signing import archive, FakeDocumenso, payload


def text(pdf):
    return " ".join(" ".join(page.extract_text().split()) for page in PdfReader(BytesIO(pdf)).pages)


def test_new_and_legacy_pdf_deadlines():
    legacy = text(generate_contract(_contract_payload()))
    new = text(generate_contract(_contract_payload(rental_payment_timing=ADVANCE_PAYMENT_TIMING)))
    assert "full rental fee is due within 2 days after the conclusion of the event" in legacy
    assert "not be received within 2 days after the event" in legacy
    assert "11:59 p.m. Pacific Time" not in legacy
    assert "full rental fee is due by 11:59 p.m. Pacific Time" in new
    assert "America/Los_Angeles, observing daylight saving time" in new
    assert "calendar day immediately before the event date" in new
    assert "After the event concludes and upon receipt of the full rental fee" in new
    assert "not be received by the deadline specified in Section 02" in new
    assert "within 2 days after" not in new


def test_generation_acknowledges_supported_policy(client, auth_headers):
    response = client.post("/api/generate/contract", json=_contract_payload(rental_payment_timing=ADVANCE_PAYMENT_TIMING), headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["X-Rental-Payment-Timing"] == ADVANCE_PAYMENT_TIMING
    legacy = client.post("/api/generate/contract", json=_contract_payload(), headers=auth_headers)
    assert legacy.status_code == 200
    assert legacy.headers["X-Rental-Payment-Timing"] == "after_conclusion"


def test_unknown_payment_terms_are_rejected():
    with pytest.raises(ValueError, match="unknown rental payment timing"):
        generate_contract(_contract_payload(rental_payment_timing="typo"))


def test_contract_policy_must_match_snapshot():
    with pytest.raises(ValueError, match="payment deadline"):
        store.assert_contract_snapshot({"rental_payment_timing": ADVANCE_PAYMENT_TIMING}, {})
    with pytest.raises(ValueError, match="payment deadline"):
        store.assert_contract_snapshot({}, {"rentalPaymentTiming": ADVANCE_PAYMENT_TIMING})
    store.assert_contract_snapshot({}, {})
    store.assert_contract_snapshot({"rental_payment_timing": ADVANCE_PAYMENT_TIMING}, {"rentalPaymentTiming": ADVANCE_PAYMENT_TIMING})


def test_shutdown_payment_only_appears_in_new_contracts(client, auth_headers):
    legacy = client.post("/api/generate/contract", json=_contract_payload(), headers=auth_headers)
    current = client.post("/api/generate/contract", json=_contract_payload(shutdown_payment_required=True), headers=auth_headers)
    archived = client.post("/api/generate/contract", json=_contract_payload(), headers=auth_headers)
    assert legacy.status_code == current.status_code == archived.status_code == 200
    assert current.headers["X-Shutdown-Payment-Required"] == "true"
    assert legacy.headers["X-Shutdown-Payment-Required"] == "false"
    assert "An early shutdown does not reduce the rental fee" in text(current.data)
    assert "Any unpaid rental fee remains due" in text(current.data)
    assert "An early shutdown" not in text(legacy.data)
    assert text(archived.data) == text(legacy.data)


def test_shutdown_payment_must_match_approved_terms():
    store.assert_contract_snapshot({}, {})
    store.assert_contract_snapshot({"shutdown_payment_required": False}, {})
    store.assert_contract_snapshot({"shutdown_payment_required": True}, {"shutdownPaymentRequired": True})
    with pytest.raises(ValueError, match="shutdown payment"):
        store.assert_contract_snapshot({"shutdown_payment_required": True}, {})
    with pytest.raises(ValueError, match="shutdown payment"):
        store.assert_contract_snapshot({}, {"shutdownPaymentRequired": True})


@pytest.mark.parametrize("completed", [False, True])
def test_new_contract_preserves_pending_and_signed_contracts(archive, monkeypatch, completed):
    conn, old_order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    old = signing.prepare(conn, old_order["id"], payload(True), "preserve-old-key-123", "")
    old = signing.create_links(conn, old["id"], old["original_sha256"])
    if completed:
        envelope = fake.envelopes[old["envelope_id"]]
        for person in envelope["recipients"]:
            person["signingStatus"] = "SIGNED"
        envelope["status"] = "COMPLETED"
        old = signing.sync(conn, old["id"])
        assert old["state"] == "signed"
    before_order = store.get_order(conn, old_order["id"])
    before_revision = signing.get(conn, old["id"])
    before_files = {kind: signing._path(old["id"], kind).read_bytes()
                    for kind, exists in old["files"].items() if exists}
    new_order = store.create_order(conn, {"clubName": old_order["clubName"], "eventDate": old_order["eventDate"],
        "rentalPrice": 1400, "depositAmount": 300, "snapshot": {"rentalPaymentTiming": ADVANCE_PAYMENT_TIMING, "shutdownPaymentRequired": True}})
    new = signing.prepare(conn, new_order["id"], {**payload(True), "rental_payment_timing": ADVANCE_PAYMENT_TIMING, "shutdown_payment_required": True}, "preserve-new-key-123", "")
    assert new["rentalPaymentTiming"] == ADVANCE_PAYMENT_TIMING
    assert new["shutdownPaymentRequired"] is True
    assert old["shutdownPaymentRequired"] is False
    new = signing.create_links(conn, new["id"], new["original_sha256"])
    assert new["state"] == "awaiting_signatures"
    assert "11:59 p.m. Pacific Time" in text(signing._path(new["id"], "original").read_bytes())
    assert "An early shutdown does not reduce the rental fee" in text(signing._path(new["id"], "original").read_bytes())
    assert store.get_order(conn, old_order["id"]) == before_order
    assert signing.get(conn, old["id"]) == before_revision
    for kind, content in before_files.items():
        assert signing._path(old["id"], kind).read_bytes() == content
    assert hashlib.sha256(before_files["original"]).hexdigest() == old["original_sha256"]
    assert "11:59 p.m. Pacific Time" not in text(before_files["original"])
    assert "An early shutdown" not in text(before_files["original"])
    with pytest.raises(ValueError, match="new contract revision"):
        store.update_order(conn, old_order["id"], {"snapshot": {"rentalPaymentTiming": ADVANCE_PAYMENT_TIMING}})
    with pytest.raises(ValueError, match="new contract revision"):
        store.update_order(conn, old_order["id"], {"snapshot": {"shutdownPaymentRequired": True}})


def test_unissued_draft_upgrades_only_on_explicit_activation(archive, monkeypatch):
    conn, order = archive
    before = store.get_order(conn, order["id"])
    revision = signing.prepare(conn, order["id"], {**payload(True), "rental_payment_timing": ADVANCE_PAYMENT_TIMING}, "upgrade-draft-key-123", "")
    assert revision["rentalPaymentTiming"] == ADVANCE_PAYMENT_TIMING
    assert store.get_order(conn, order["id"]) == before
    assert revision["envelope_id"] is None
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    activation = {**{key: before[key] for key in ("clubName", "eventDate", "rentalPrice", "depositAmount")},
                  "expectedUpdatedAt": before["updatedAt"], "replacesRevisionIds": [],
                  "snapshot": {**before["snapshot"], "rentalPaymentTiming": ADVANCE_PAYMENT_TIMING}}
    linked = signing.create_links(conn, revision["id"], revision["original_sha256"], activation)
    assert linked["state"] == "awaiting_signatures"
    assert store.get_order(conn, order["id"])["snapshot"]["rentalPaymentTiming"] == ADVANCE_PAYMENT_TIMING


@pytest.mark.parametrize("kind", ["deposit", "rental"])
def test_invoice_terms_follow_payment_timing(kind):
    from generators.invoice import generate_invoice
    invoice = {"kind": kind, "club_name": "Sample Club", "event_date": "October 16, 2026",
               "issue_date": "October 1, 2026", "due_date": "October 15, 2026", "amount": 300,
               "line_items": [{"description": "Venue rental", "amount": 1400}]}
    legacy = text(generate_invoice(invoice)[0])
    current = text(generate_invoice({**invoice, "rental_payment_timing": "day_before"})[0])
    assert "within 2 days following" in legacy
    assert "11:59 p.m. Pacific Time" in current
    assert "within 2 days following" not in current
    if kind == "deposit":
        assert "After the event concludes and upon receipt" in current
    else:
        assert "Rental Fee · Due Before Event" in current
        assert "October 15, 2026 at 11:59 p.m. Pacific Time" in current
        assert "America/Los_Angeles, observing daylight saving time" in current


def test_deposit_return_deadline_is_explicit_and_preserves_legacy_pdf(client, auth_headers):
    old = _contract_payload(rental_payment_timing=ADVANCE_PAYMENT_TIMING)
    current = {**old, "deposit_return_timing": "two_business_days"}
    legacy_text = text(generate_contract(old))
    response = client.post("/api/generate/contract", json=current, headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["X-Deposit-Return-Timing"] == "two_business_days"
    assert "Within 2 business days (Monday through Friday) after the event concludes and the full rental fee is received, whichever occurs later" in text(response.data)
    assert "2 business days" not in legacy_text
    assert text(generate_contract(old)) == legacy_text
    store.assert_contract_snapshot(current, {"rentalPaymentTiming": ADVANCE_PAYMENT_TIMING, "depositReturnTiming": "two_business_days"})
    with pytest.raises(ValueError, match="deposit return timing"):
        store.assert_contract_snapshot(current, {"rentalPaymentTiming": ADVANCE_PAYMENT_TIMING})
    with pytest.raises(ValueError, match="unknown deposit return timing"):
        generate_contract({**old, "deposit_return_timing": "invalid"})


def test_deposit_invoice_matches_return_deadline():
    from generators.invoice import generate_invoice
    invoice = {"kind": "deposit", "club_name": "Sample Club", "event_date": "October 16, 2026",
               "issue_date": "October 1, 2026", "due_date": "October 9, 2026", "amount": 300,
               "deposit_return_timing": "two_business_days"}
    assert "Within 2 business days (Monday through Friday)" in text(generate_invoice(invoice)[0])
