import hashlib
import json

from app.backup import BackupStore, content_hash
from app.db import Database
from app.discovery import DeviceFacts


def make_store(tmp_path):
    db = Database(tmp_path / "fleet.db")
    db.upsert_device(
        DeviceFacts(mac="aa:bb:cc:dd:ee:ff", ip="127.0.0.1", port=80, name="Shelf", arch="esp32")
    )
    return BackupStore(db, tmp_path), db


def test_key_order_does_not_create_a_second_version(tmp_path):
    store, db = make_store(tmp_path)
    mac = "aa:bb:cc:dd:ee:ff"
    info = {"ver": "16.0.1", "name": "Shelf", "leds": {"count": 150}}
    first = store.save(mac, b'{"b":1,"a":2}', b'{"1":{"n":"Warm"}}', info, "manual", 30)
    second = store.save(mac, b'{"a": 2, "b": 1}', b'{"1":{"n":"Warm"}}', info, "schedule", 30)
    assert first.created
    assert not second.created
    assert second.backup_id == first.backup_id
    assert len(db.list_backups(mac)) == 1
    assert content_hash(b'{"b":1,"a":2}', b"{}") == content_hash(b'{"a":2,"b":1}', b"{}")


def test_change_and_revert_keeps_history(tmp_path):
    store, db = make_store(tmp_path)
    mac = "aa:bb:cc:dd:ee:ff"
    info = {"ver": "16.0.1", "name": "Shelf", "leds": {"count": 10}}
    store.save(mac, b'{"n":"A"}', b"{}", info, "discover", 30)
    store.save(mac, b'{"n":"B"}', b"{}", info, "change", 30)
    third = store.save(mac, b'{"n":"A"}', b"{}", info, "change", 30)
    assert third.created
    rows = db.list_backups(mac)
    assert len(rows) == 3
    newest = json.loads(store.read_blob("cfg", rows[0]["cfg_sha"]))
    assert newest["n"] == "A"


def test_retention_drops_oldest_and_unreferenced_blobs(tmp_path):
    store, db = make_store(tmp_path)
    mac = "aa:bb:cc:dd:ee:ff"
    info = {"ver": "1", "name": "Shelf", "leds": {"count": 1}}
    for index in range(5):
        store.save(mac, json.dumps({"n": index}).encode(), b"{}", info, "change", retention=3)
    rows = db.list_backups(mac)
    assert len(rows) == 3
    kept = [json.loads(store.read_blob("cfg", row["cfg_sha"]))["n"] for row in rows]
    assert kept == [4, 3, 2]
    oldest = hashlib.sha256(json.dumps({"n": 0}).encode()).hexdigest()
    assert not (tmp_path / "blobs" / "cfg" / oldest).exists()
