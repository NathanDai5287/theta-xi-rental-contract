"""Signing orchestration against a fake Documenso v2 API; never real mail."""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

import signing
import store
from test_generate import _contract_payload


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SIGNING_STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("DOCUMENSO_ORIGIN", "https://sign.cal.taxi")
    store.init_db()
    conn = store._connect()
    order = store.create_order(conn, {"clubName": "Alpha Club and Beta Club", "eventDate": "2026-10-16",
                                      "rentalPrice": 1400, "depositAmount": 300, "snapshot": {}})
    yield conn, order
    conn.close()


class FakeDocumenso:
    def __init__(self):
        self.created = []
        self.envelopes = {}
        self.downloads = []
        self.originals = {}

    def __call__(self, method, path, *, body=None, pdf=None):
        if path.startswith("/envelope?type=DOCUMENT"):
            return {"data": list(self.envelopes.values()), "totalPages": 1}
        if path == "/envelope/create":
            self.created.append((body, pdf))
            envelope_id = f"envelope_{len(self.created)}"
            people = [{"id": i + 1, "email": p["email"], "name": p["name"],
                       "role": "SIGNER", "token": f"private{i + 1}", "signingStatus": "NOT_SIGNED"}
                      for i, p in enumerate(body["recipients"])]
            item_id = f"item_{len(self.created)}"
            self.originals[item_id] = pdf
            fields = [{**field, "recipientId": i + 1, "envelopeItemId": item_id}
                      for i, person in enumerate(body["recipients"]) for field in person["fields"]]
            self.envelopes[envelope_id] = {"id": envelope_id, "externalId": body["externalId"],
                                            "status": "DRAFT", "recipients": people,
                                            "fields": fields, "documentMeta": body["meta"],
                                            "envelopeItems": [{"id": item_id}]}
            return {"id": envelope_id}
        if path == "/envelope/update":
            envelope = self.envelopes[body["envelopeId"]]
            envelope["documentMeta"].update(body["meta"])
            return envelope
        if path == "/envelope/distribute":
            envelope = self.envelopes[body["envelopeId"]]
            envelope["documentMeta"].update(body.get("meta", {}))
            envelope["status"] = "PENDING"
            return {"recipients": [{**p, "signingUrl": f"https://sign.cal.taxi/sign/{p['token']}"}
                                   for p in envelope["recipients"]]}
        if path == "/envelope/cancel":
            self.envelopes[body["envelopeId"]]["status"] = "CANCELLED"
            return {"success": True}
        if "download" in path:
            self.downloads.append(path)
            if "version=original" in path:
                return self.originals[path.split("/")[3]]
            return b"%PDF-1.4\ncontrolled-test-file\n"
        if path.startswith("/envelope/"):
            return self.envelopes[path.split("/")[2]]
        raise AssertionError((method, path))


def payload(presign, count=1):
    signers = [{"fullName": f"Alexandra Rivera {i}", "email": f"alex{i}@example.test",
                "club": "Alpha Club", "role": "club"} for i in range(count)]
    signers.append({"fullName": "Benjamin Longname With Several Middle Names", "email": "ben@example.test",
                    "club": "Beta Club", "role": "club"})
    if not presign:
        signers.append({"fullName": "Taylor Xi", "email": "taylor@example.test",
                        "club": "Theta Xi Fraternity", "role": "chapter"})
    return _contract_payload(club_name="Alpha Club and Beta Club", club_names=["Alpha Club", "Beta Club"],
                             date="October 16, 2026", price="1400", deposit="300",
                             sign=presign, signers=signers)


def test_prepare_rejects_stale_signing_history(archive):
    conn, order = archive
    first = signing.prepare(conn, order["id"], payload(True), "history-first-key-123", "")
    changed = payload(True, 2)
    with pytest.raises(signing.SigningError, match="history changed elsewhere"):
        signing.prepare(conn, order["id"], changed, "history-stale-key-123", "")
    assert signing.prepare(conn, order["id"], changed, "history-fresh-key-123", first["id"])["revision"] == 2


def test_simultaneous_prepares_make_one_revision(archive):
    _, order = archive

    def attempt(count):
        conn = store._connect()
        try:
            return signing.prepare(conn, order["id"], payload(True, count), f"parallel-key-{count:03d}-123", "")
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda count: _capture_prepare(attempt, count), (1, 2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, signing.SigningError) for result in results) == 1
    assert "history changed elsewhere" in str(next(result for result in results if isinstance(result, signing.SigningError)))


def _capture_prepare(attempt, count):
    try:
        return attempt(count)
    except signing.SigningError as exc:
        return exc


def test_create_cannot_distribute_superseded_preview(archive, monkeypatch):
    conn, order = archive
    first = signing.prepare(conn, order["id"], payload(True), "create-race-first-key-123", "")
    started, proceed = Event(), Event()
    render = signing.generate_contract

    def paused_render(contract):
        started.set()
        assert proceed.wait(10)
        return render(contract)

    monkeypatch.setattr(signing, "generate_contract", paused_render)
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)

    def revise():
        other = store._connect()
        try:
            return signing.prepare(other, order["id"], payload(True, 2), "create-race-next-key-123", first["id"])
        finally:
            other.close()

    def create_old():
        other = store._connect()
        try:
            return signing.create_links(other, first["id"], first["original_sha256"])
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        revision = pool.submit(revise)
        assert started.wait(10)
        old_request = pool.submit(create_old)
        proceed.set()
        assert revision.result()["revision"] == 2
        with pytest.raises(signing.SigningError, match="superseded"):
            old_request.result()
    assert fake.created == []


@pytest.mark.parametrize("presign,count", [(True, 1), (False, 3)])
def test_one_envelope_all_people_and_exact_pdf(archive, monkeypatch, presign, count):
    conn, order = archive
    initial_order = store.get_order(conn, order["id"])
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    contract = payload(presign, count)
    revision = signing.prepare(conn, order["id"], contract, "stable-request-key-123")
    original = signing._path(revision["id"], "original").read_bytes()
    assert hashlib.sha256(original).hexdigest() == revision["original_sha256"]
    assert revision["totalCount"] == count + 1 + (not presign)
    assert len({f["page"] for f in revision["fields"]}) == revision["totalCount"]
    assert all({f["type"] for f in item["fields"]} == {"SIGNATURE", "NAME", "DATE"} for item in revision["fields"])
    for item in revision["fields"]:
        assert all(0 < f["positionX"] < 100 and 0 < f["positionY"] < 100 for f in item["fields"])

    created = signing.create_links(conn, revision["id"], revision["original_sha256"])
    assert len(fake.created) == 1
    request, uploaded_pdf = fake.created[0]
    assert uploaded_pdf == original
    assert request["meta"]["distributionMethod"] == "NONE"
    assert request["meta"]["signingOrder"] == "PARALLEL"
    assert request["meta"]["timezone"] == "America/Los_Angeles"
    assert request["meta"]["typedSignatureEnabled"] and request["meta"]["drawSignatureEnabled"]
    assert {p["email"] for p in request["recipients"]} == {p["email"] for p in contract["signers"]}
    assert all(len(p["fields"]) == 3 for p in request["recipients"])
    assert all(p["link"].startswith("https://sign.cal.taxi/sign/") for p in created["recipients"])
    assert all(person["sentAt"] is None for person in created["recipients"])
    marked = signing.mark_link_sent(conn, revision["id"], created["recipients"][0]["email"], True)
    assert marked["recipients"][0]["sentAt"]
    assert marked["recipients"][1]["sentAt"] is None
    assert signing.mark_link_sent(conn, revision["id"], created["recipients"][0]["email"], True)["recipients"][0]["sentAt"] == marked["recipients"][0]["sentAt"]
    assert signing.create_links(conn, revision["id"], revision["original_sha256"])["id"] == revision["id"]
    assert len(fake.created) == 1

    envelope = fake.envelopes[created["envelope_id"]]
    envelope["recipients"][0]["signingStatus"] = "SIGNED"
    partial = signing.sync(conn, revision["id"])
    assert partial["signedCount"] == 1 and partial["state"] == "awaiting_signatures"
    assert partial["recipients"][0]["sentAt"] == marked["recipients"][0]["sentAt"]
    assert not partial["files"]["completed"]
    for person in envelope["recipients"]:
        person["signingStatus"] = "SIGNED"
    envelope["status"] = "COMPLETED"
    done = signing.sync(conn, revision["id"])
    assert done["state"] == "signed"
    assert done["files"]["completed"] and done["files"]["audit"]
    assert signing._path(revision["id"], "completed").read_bytes() == b"%PDF-1.4\ncontrolled-test-file\n"
    assert signing.completed_copy_for_token(conn, done["recipients"][0]["copyToken"]).exists()
    assert signing.sync(conn, revision["id"])["state"] == "signed"
    assert len([path for path in fake.downloads if "version=original" not in path]) == 2
    after_signing = store.get_order(conn, order["id"])
    assert after_signing["statusOverride"] == initial_order["statusOverride"]
    assert after_signing["documents"] == initial_order["documents"]
    assert after_signing["rentalPrice"] == initial_order["rentalPrice"]
    assert after_signing["depositAmount"] == initial_order["depositAmount"]
    assert signing.delete_order(conn, order["id"])
    assert store.get_order(conn, order["id"]) is None
    assert signing.get(conn, revision["id"])["state"] == "signed"
    assert signing.completed_copy_for_token(conn, done["recipients"][0]["copyToken"]).exists()


def test_revision_cancels_old_request_and_old_links(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    first = signing.prepare(conn, order["id"], payload(True), "first-request-key-123")
    first = signing.create_links(conn, first["id"], first["original_sha256"])
    changed = payload(True)
    changed["price"] = "1600"
    second = signing.prepare(conn, order["id"], changed, "second-request-key-123")
    assert second["revision"] == 2 and second["state"] == "preview"
    assert signing.get(conn, first["id"])["state"] == "cancelled"
    assert fake.envelopes[first["envelope_id"]]["status"] == "CANCELLED"
    assert signing._path(first["id"], "original").exists()
    assert signing.prepare(conn, order["id"], changed, "second-request-key-123")["id"] == second["id"]


def test_completed_previous_request_is_archived_before_new_revision(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    first = signing.prepare(conn, order["id"], payload(True), "completed-first-key-123")
    first = signing.create_links(conn, first["id"], first["original_sha256"])
    envelope = fake.envelopes[first["envelope_id"]]
    envelope["status"] = "COMPLETED"
    for person in envelope["recipients"]:
        person["signingStatus"] = "SIGNED"
    changed = payload(True)
    changed["monitors"] = "5"
    second = signing.prepare(conn, order["id"], changed, "completed-second-key-123")
    assert second["state"] == "preview"
    prior = signing.get(conn, first["id"])
    assert prior["state"] == "signed"
    assert prior["files"]["completed"] and prior["files"]["audit"]


@pytest.mark.parametrize("provider_status", ["DRAFT", "PENDING"])
def test_legacy_timezone_recovery(archive, monkeypatch, provider_status):
    conn, order = archive
    fake = FakeDocumenso()
    original = fake.__call__

    def interrupted_legacy_request(method, path, **kwargs):
        result = original(method, path, **kwargs)
        if path == "/envelope/create":
            envelope = fake.envelopes[result["id"]]
            envelope["documentMeta"]["timezone"] = "UTC"
            envelope["status"] = provider_status
        return result

    monkeypatch.setattr(signing, "_documenso", interrupted_legacy_request)
    revision = signing.prepare(conn, order["id"], payload(True), "legacy-timezone-key-123")
    if provider_status == "PENDING":
        with pytest.raises(signing.SigningError, match="timezone corrected"):
            signing.create_links(conn, revision["id"], revision["original_sha256"])
        assert signing.get(conn, revision["id"])["state"] == "created"
        assert not signing.get(conn, revision["id"])["recipients"]
        # An operator can correct the unsigned envelope and resume without a duplicate.
        fake.envelopes["envelope_1"]["documentMeta"]["timezone"] = signing.SIGNING_TIMEZONE
    linked = signing.create_links(conn, revision["id"], revision["original_sha256"])
    assert linked["state"] == "awaiting_signatures"
    assert fake.envelopes[linked["envelope_id"]]["documentMeta"]["timezone"] == signing.SIGNING_TIMEZONE
    assert len(fake.created) == 1


def test_provider_field_change_blocks_distribution(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    original = fake.__call__

    def shifted(method, path, **kwargs):
        result = original(method, path, **kwargs)
        if path == "/envelope/create":
            fake.envelopes[result["id"]]["fields"][0]["recipientId"] = 999
        return result

    monkeypatch.setattr(signing, "_documenso", shifted)
    revision = signing.prepare(conn, order["id"], payload(True), "field-check-key-123")
    with pytest.raises(signing.SigningError, match="field ownership"):
        signing.create_links(conn, revision["id"], revision["original_sha256"])
    assert fake.envelopes[signing.get(conn, revision["id"])["envelope_id"]]["status"] == "DRAFT"


def test_provider_original_change_blocks_distribution(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    original = fake.__call__

    def changed(method, path, **kwargs):
        result = original(method, path, **kwargs)
        if path == "/envelope/create":
            fake.originals[f"item_{len(fake.created)}"] = b"%PDF-1.4\nchanged\n"
        return result

    monkeypatch.setattr(signing, "_documenso", changed)
    revision = signing.prepare(conn, order["id"], payload(True), "pdf-check-key-123")
    with pytest.raises(signing.SigningError, match="different PDF"):
        signing.create_links(conn, revision["id"], revision["original_sha256"])
    assert fake.envelopes[signing.get(conn, revision["id"])["envelope_id"]]["status"] == "DRAFT"


def test_provider_edit_after_distribution_blocks_completion(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    revision = signing.prepare(conn, order["id"], payload(True), "edit-check-key-123")
    linked = signing.create_links(conn, revision["id"], revision["original_sha256"])
    envelope = fake.envelopes[linked["envelope_id"]]
    envelope["fields"][0]["page"] += 1
    for person in envelope["recipients"]:
        person["signingStatus"] = "SIGNED"
    envelope["status"] = "COMPLETED"
    with pytest.raises(signing.SigningError, match="field page"):
        signing.sync(conn, revision["id"])
    assert signing.get(conn, revision["id"])["state"] == "awaiting_signatures"
    assert not signing._path(revision["id"], "completed").exists()


def test_signed_contract_metadata_and_terms_are_guarded(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    revision = signing.prepare(conn, order["id"], payload(True), "order-guard-key-123")
    signing.create_links(conn, revision["id"], revision["original_sha256"])
    with pytest.raises(ValueError, match="new contract revision"):
        store.update_order(conn, order["id"], {"snapshot": {"numGuests": "450"}})
    contract_doc = {"kind": "contract", "number": "CTR-1", "filename": "CTR-1.pdf", "amount": 1400,
                    "generatedAt": "2026-10-01T00:00:00+00:00", "payload": payload(True)}
    with pytest.raises(ValueError, match="signing history"):
        store.add_document(conn, order["id"], contract_doc)
    monkeypatch.setenv("ADMIN_KEY", "controlled-test-key")
    source = {**order["snapshot"], "documentContextId": order["id"]}
    invoice_doc = {**contract_doc, "kind": "deposit_invoice", "number": "DEP-1", "filename": "DEP-1.pdf",
                   "expectedUpdatedAt": store.get_order(conn, order["id"])["updatedAt"], "sourceSnapshot": source}
    invoice_doc["payload"] = {**invoice_doc["payload"], "event_date": "October 16, 2026"}
    invoice_doc["generationReceipt"] = store.document_receipt(invoice_doc["kind"], invoice_doc["payload"], source, invoice_doc["filename"])
    assert store.add_document(conn, order["id"], invoice_doc)["documents"][0]["kind"] == "deposit_invoice"


def test_unknown_create_outcome_does_not_retry(archive, monkeypatch):
    conn, order = archive
    revision = signing.prepare(conn, order["id"], payload(True), "unknown-request-key-123")
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise signing.SigningError("provider timeout", 503)

    monkeypatch.setattr(signing, "_documenso", fail)
    with pytest.raises(signing.SigningError):
        signing.create_links(conn, revision["id"], revision["original_sha256"])
    with pytest.raises(signing.SigningError, match="no duplicate"):
        signing.create_links(conn, revision["id"], revision["original_sha256"])
    assert len(attempts) == 1
    assert signing.get(conn, revision["id"])["state"] == "creation_uncertain"


def test_order_creation_retries_keep_same_order(archive):
    conn, _ = archive
    body = {"clubName": "Gamma Club", "eventDate": "2026-10-17", "rentalPrice": 1000,
            "depositAmount": 250, "snapshot": {}, "requestKey": "stable-order-key-123"}
    first = store.create_order(conn, body)
    second = store.create_order(conn, body)
    assert first["id"] == second["id"]
    assert len(store.list_orders(conn)) == 2
    with pytest.raises(ValueError, match="different order details"):
        store.create_order(conn, {**body, "rentalPrice": 1200})


def test_first_revision_must_match_saved_order(archive):
    conn, order = archive
    changed = payload(True)
    changed["price"] = "1600"
    with pytest.raises(signing.SigningError, match="do not match the saved order"):
        signing.prepare(conn, order["id"], changed, "mismatch-request-key-123")


def test_obsolete_preview_cannot_issue_links(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    first = signing.prepare(conn, order["id"], payload(True), "old-preview-key-123")
    changed = payload(True)
    changed["monitors"] = "5"
    signing.prepare(conn, order["id"], changed, "new-preview-key-123")
    with pytest.raises(signing.SigningError, match="superseded"):
        signing.create_links(conn, first["id"], first["original_sha256"])
    assert not fake.created


def test_stale_provider_sync_cannot_regress_signed_state(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    revision = signing.prepare(conn, order["id"], payload(True), "stale-sync-key-123")
    linked = signing.create_links(conn, revision["id"], revision["original_sha256"])
    envelope = fake.envelopes[linked["envelope_id"]]
    stale = {**envelope, "recipients": [dict(person) for person in envelope["recipients"]]}
    for person in envelope["recipients"]:
        person["signingStatus"] = "SIGNED"
    envelope["status"] = "COMPLETED"
    original = fake.__call__
    once = True

    def interleaved(method, path, **kwargs):
        nonlocal once
        if path == f"/envelope/{linked['envelope_id']}" and once:
            once = False
            signing.sync(conn, revision["id"])
            return stale
        return original(method, path, **kwargs)

    monkeypatch.setattr(signing, "_documenso", interleaved)
    assert signing.sync(conn, revision["id"])["state"] == "signed"


def test_old_preview_does_not_shorten_uncertain_create_grace(archive, monkeypatch):
    conn, order = archive
    revision = signing.prepare(conn, order["id"], payload(True), "old-preview-time-key-123")
    conn.execute("UPDATE signing_revisions SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (revision["id"],))
    conn.commit()

    def unavailable(method, path, **kwargs):
        if path == "/envelope/create":
            raise signing.SigningError("timeout", 503)
        return {"data": [], "totalPages": 1}

    monkeypatch.setattr(signing, "_documenso", unavailable)
    with pytest.raises(signing.SigningError):
        signing.create_links(conn, revision["id"], revision["original_sha256"])
    with pytest.raises(signing.SigningError, match="15 minutes"):
        signing.reconcile(conn, revision["id"])


def test_reconcile_lost_create_response(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    revision = signing.prepare(conn, order["id"], payload(True), "lost-response-key-123")
    attempted = []

    def lost(method, path, **kwargs):
        if path == "/envelope/create" and not attempted:
            attempted.append(1)
            fake(method, path, **kwargs)
            raise signing.SigningError("connection dropped", 503)
        return fake(method, path, **kwargs)

    monkeypatch.setattr(signing, "_documenso", lost)
    with pytest.raises(signing.SigningError):
        signing.create_links(conn, revision["id"], revision["original_sha256"])
    assert signing.get(conn, revision["id"])["state"] == "creation_uncertain"
    recovered = signing.reconcile(conn, revision["id"])
    assert recovered["state"] == "awaiting_signatures"
    assert len(fake.created) == 1
