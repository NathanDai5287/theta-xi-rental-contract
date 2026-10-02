"""Deleting orders must retire links without destroying signed evidence."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

import signing
import store
from test_signing import FakeDocumenso, archive, payload


@pytest.mark.parametrize("stage", ["preview", "draft", "pending", "cancelled_locally", "completed"])
def test_delete_retains_history_and_prevents_reactivation(archive, monkeypatch, stage):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    revision = signing.prepare(conn, order["id"], payload(True), "delete-history-key-123")
    original = signing._path(revision["id"], "original").read_bytes()
    if stage != "preview":
        revision = signing.create_links(conn, revision["id"], revision["original_sha256"])
        envelope = fake.envelopes[revision["envelope_id"]]
        if stage == "draft":
            envelope["status"] = "DRAFT"
            conn.execute("UPDATE signing_revisions SET state = 'created' WHERE id = ?", (revision["id"],))
            conn.commit()
        else:
            envelope["recipients"][0]["signingStatus"] = "SIGNED"
        if stage == "completed":
            envelope["status"] = "COMPLETED"
            for person in envelope["recipients"]:
                person["signingStatus"] = "SIGNED"
        if stage == "cancelled_locally":
            conn.execute("UPDATE signing_revisions SET state = 'cancelled' WHERE id = ?", (revision["id"],))
            conn.commit()

    assert signing.delete_order(conn, order["id"])
    assert signing.delete_order(conn, order["id"])
    assert store.get_order(conn, order["id"]) is None
    assert store.list_orders(conn) == []
    assert store.update_order(conn, order["id"], {"notes": "revive"}) is None
    retained = signing.get(conn, revision["id"])
    assert retained["state"] == ("signed" if stage == "completed" else "cancelled")
    assert signing._path(revision["id"], "original").read_bytes() == original
    assert conn.execute("SELECT deleted_at FROM orders WHERE id = ?", (order["id"],)).fetchone()[0]
    if stage in ("pending", "cancelled_locally"):
        assert fake.envelopes[revision["envelope_id"]]["status"] == "CANCELLED"
        assert retained["signedCount"] == 1
    if stage == "draft":
        assert fake.envelopes[revision["envelope_id"]]["status"] == "DRAFT"
    if stage == "completed":
        assert retained["files"]["completed"] and retained["files"]["audit"]
        assert signing.completed_copy_for_token(conn, revision["recipients"][0]["copyToken"]).exists()
    with pytest.raises(signing.SigningError, match="order not found"):
        signing.create_links(conn, revision["id"], revision["original_sha256"])
    with pytest.raises(signing.SigningError, match="order not found"):
        signing.prepare(conn, order["id"], payload(True), "delete-after-key-123")
    with pytest.raises(signing.SigningError, match="order not found"):
        signing.reconcile(conn, revision["id"])


@pytest.mark.parametrize("cancel_succeeded", [False, True])
def test_delete_cancellation_failure_is_retryable(archive, monkeypatch, cancel_succeeded):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    revision = signing.prepare(conn, order["id"], payload(True), "delete-timeout-key-123")
    revision = signing.create_links(conn, revision["id"], revision["original_sha256"])

    def timeout(method, path, **kwargs):
        if path == "/envelope/cancel":
            if cancel_succeeded:
                fake(method, path, **kwargs)
            raise signing.SigningError("provider timeout", 503)
        return fake(method, path, **kwargs)

    monkeypatch.setattr(signing, "_documenso", timeout)
    with pytest.raises(signing.SigningError, match="timeout"):
        signing.delete_order(conn, order["id"])
    assert store.get_order(conn, order["id"])
    monkeypatch.setattr(signing, "_documenso", fake)
    assert signing.delete_order(conn, order["id"])
    assert fake.envelopes[revision["envelope_id"]]["status"] == "CANCELLED"
    assert len(fake.created) == 1


def test_delete_waits_for_completed_files(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    revision = signing.prepare(conn, order["id"], payload(True), "delete-files-key-123")
    revision = signing.create_links(conn, revision["id"], revision["original_sha256"])
    envelope = fake.envelopes[revision["envelope_id"]]
    envelope["status"] = "COMPLETED"
    for person in envelope["recipients"]:
        person["signingStatus"] = "SIGNED"

    def unavailable(method, path, **kwargs):
        if "audit-log/download" in path:
            raise signing.SigningError("audit temporarily unavailable", 503)
        return fake(method, path, **kwargs)

    monkeypatch.setattr(signing, "_documenso", unavailable)
    with pytest.raises(signing.SigningError, match="still being stored"):
        signing.delete_order(conn, order["id"])
    assert store.get_order(conn, order["id"])
    assert signing.get(conn, revision["id"])["state"] == "preparing_completed_copy"
    monkeypatch.setattr(signing, "_documenso", fake)
    assert signing.delete_order(conn, order["id"])
    assert signing.get(conn, revision["id"])["state"] == "signed"


def test_delete_unknown_creation_requires_reconciliation(archive):
    conn, order = archive
    revision = signing.prepare(conn, order["id"], payload(True), "delete-unknown-key-123")
    conn.execute("UPDATE signing_revisions SET state = 'creation_uncertain' WHERE id = ?", (revision["id"],))
    conn.commit()
    with pytest.raises(signing.SigningError, match="Check and resume"):
        signing.delete_order(conn, order["id"])
    assert store.get_order(conn, order["id"])


@pytest.mark.parametrize("when_signed", ["before_cancel", "during_cancel"])
def test_delete_waits_for_sealing_when_everyone_has_signed(archive, monkeypatch, when_signed):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    revision = signing.prepare(conn, order["id"], payload(True), "delete-sealing-key-123")
    revision = signing.create_links(conn, revision["id"], revision["original_sha256"])
    envelope = fake.envelopes[revision["envelope_id"]]

    def sign_all():
        for person in envelope["recipients"]:
            person["signingStatus"] = "SIGNED"

    if when_signed == "before_cancel":
        sign_all()

    def race(method, path, **kwargs):
        if path == "/envelope/cancel":
            sign_all()
        return fake(method, path, **kwargs)

    monkeypatch.setattr(signing, "_documenso", race)
    with pytest.raises(signing.SigningError, match="completed contract"):
        signing.delete_order(conn, order["id"])
    assert store.get_order(conn, order["id"])
    if when_signed == "before_cancel":
        assert envelope["status"] == "PENDING"
    envelope["status"] = "COMPLETED"
    assert signing.delete_order(conn, order["id"])
    assert signing.get(conn, revision["id"])["state"] == "signed"


def test_late_completion_after_delete_keeps_completed_copy(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    revision = signing.prepare(conn, order["id"], payload(True), "delete-late-seal-key-123")
    revision = signing.create_links(conn, revision["id"], revision["original_sha256"])
    assert signing.delete_order(conn, order["id"])
    envelope = fake.envelopes[revision["envelope_id"]]
    assert signing.sync(conn, revision["id"])["state"] == "cancelled"
    envelope["status"] = "COMPLETED"
    for person in envelope["recipients"]:
        person["signingStatus"] = "SIGNED"
    assert signing.sync(conn, revision["id"])["state"] == "signed"
    assert signing.sync(conn, revision["id"])["state"] == "signed"
    assert signing.completed_copy_for_token(conn, revision["recipients"][0]["copyToken"]).exists()
    assert signing._path(revision["id"], "audit").exists()
    assert store.get_order(conn, order["id"]) is None


def test_delete_serializes_with_link_creation(archive, monkeypatch):
    conn, order = archive
    fake = FakeDocumenso()
    monkeypatch.setattr(signing, "_documenso", fake)
    revision = signing.prepare(conn, order["id"], payload(True), "delete-race-key-123")
    entered, proceed = Event(), Event()
    original_delete = store.delete_order

    def paused_delete(connection, order_id):
        entered.set()
        assert proceed.wait(10)
        return original_delete(connection, order_id)

    monkeypatch.setattr(store, "delete_order", paused_delete)

    def remove():
        other = store._connect()
        try:
            return signing.delete_order(other, order["id"])
        finally:
            other.close()

    def create():
        other = store._connect()
        try:
            return signing.create_links(other, revision["id"], revision["original_sha256"])
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(remove)
        assert entered.wait(10)
        creating = pool.submit(create)
        proceed.set()
        assert deleting.result()
        with pytest.raises(signing.SigningError, match="order not found"):
            creating.result()
    assert not fake.created
