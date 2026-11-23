import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

from app.service import IntakeService, StorageUnavailable
from app.storage import (
    DatabaseStorage,
    LocalStorage,
    S3Storage,
    StorageConfigError,
    StorageError,
    sigv4_headers,
    storage_from_env,
)
from app.store import Store
from conftest import SAMPLES_DIR, make_store

EMPTY = hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------- signing (AWS's published examples)


def test_sigv4_matches_the_aws_test_suite():
    h = sigv4_headers("GET", "https://example.amazonaws.com/", {}, EMPTY,
                      access_key="AKIDEXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
                      region="us-east-1", service="service", now=datetime(2015, 8, 30, 12, 36, tzinfo=timezone.utc))
    assert h["Authorization"].endswith("Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


def test_sigv4_matches_the_s3_get_object_example():
    h = sigv4_headers("GET", "https://examplebucket.s3.amazonaws.com/test.txt",
                      {"Range": "bytes=0-9", "x-amz-content-sha256": EMPTY}, EMPTY,
                      access_key="AKIAIOSFODNN7EXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                      region="us-east-1", now=datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in h["Authorization"]
    assert h["Authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


# ---------------------------------------------------------------- S3 client with a fake bucket


class FakeBucket:
    def __init__(self, fail_with=None):
        self.objects, self.calls, self.fail_with = {}, [], fail_with

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers))
        if self.fail_with:
            return self.fail_with
        if method == "PUT":
            self.objects[url] = body
            return 200, b""
        return (200, self.objects[url]) if url in self.objects else (404, b"<Code>NoSuchKey</Code>")


def s3(bucket, **kw):
    return S3Storage("intake-files", "eu-west-2", "AKIA", "secret", transport=bucket, **kw)


def test_s3_put_is_signed_encrypted_and_eu_hosted():
    bucket = FakeBucket()
    s3(bucket, prefix="prod/").put("cli_1/file_abc", b"%PDF", "application/pdf")
    method, url, headers = bucket.calls[0]
    assert method == "PUT"
    assert url == "https://intake-files.s3.eu-west-2.amazonaws.com/prod/cli_1/file_abc"
    assert headers["x-amz-server-side-encryption"] == "AES256"
    assert headers["x-amz-content-sha256"] == hashlib.sha256(b"%PDF").hexdigest()
    assert "/eu-west-2/s3/aws4_request" in headers["Authorization"]
    assert "x-amz-server-side-encryption" in headers["Authorization"]  # signed, so it cannot be stripped


def test_s3_round_trip_and_custom_endpoint():
    bucket = FakeBucket()
    store = s3(bucket, endpoint="https://fsn1.your-objectstorage.com", encrypt=False)
    store.put("cli_1/file_abc", b"data", "image/png")
    assert store.get("cli_1/file_abc") == b"data"
    assert bucket.calls[0][1] == "https://fsn1.your-objectstorage.com/intake-files/cli_1/file_abc"
    assert "x-amz-server-side-encryption" not in bucket.calls[0][2]


def test_s3_errors_are_reported_without_secrets():
    with pytest.raises(StorageError, match="missing"):
        s3(FakeBucket()).get("cli_1/nothing")
    with pytest.raises(StorageError, match=r"403 \(AccessDenied\)") as err:
        s3(FakeBucket(fail_with=(403, b"<Error><Code>AccessDenied</Code></Error>"))).put("cli_1/f", b"x", "text/plain")
    assert "secret" not in str(err.value)


@pytest.mark.parametrize("key", ["../etc/passwd", "cli_1/../../x", "/abs", "cli 1/x", ""])
def test_unsafe_keys_are_refused(tmp_path, key):
    with pytest.raises(StorageError):
        LocalStorage(tmp_path).put(key, b"x", "text/plain")
    with pytest.raises(StorageError):
        s3(FakeBucket()).put(key, b"x", "text/plain")


# ---------------------------------------------------------------- local folder


def test_local_storage_round_trip(tmp_path):
    store = LocalStorage(tmp_path / "uploads")
    store.put("cli_1/file_abc", b"hello", "text/plain")
    assert store.get("cli_1/file_abc") == b"hello"
    assert (tmp_path / "uploads" / "cli_1" / "file_abc").read_bytes() == b"hello"
    assert not list((tmp_path / "uploads").rglob("*.part"))
    with pytest.raises(StorageError, match="missing"):
        store.get("cli_1/nope")


# ---------------------------------------------------------------- service integration


def service_with(storage):
    svc = IntakeService(make_store(), storage=storage)
    svc.store.add_client("Acme", "a@example.com", ["receipt"], client_id="cli_acme")
    return svc


def test_service_keeps_originals_outside_the_database(tmp_path):
    svc = service_with(LocalStorage(tmp_path))
    raw = (SAMPLES_DIR / "receipt-cafe.txt").read_bytes()
    doc = svc.ingest("cli_acme", "r.txt", raw, actor="a@example.com")
    media_type, in_db, key = svc.store.get_file_record(doc["id"])
    assert in_db == b"" and key.startswith("cli_acme/file_")
    assert svc.original_file(doc["id"]) == ("text/plain", raw)
    assert doc["file_media_type"] == "text/plain"


def test_storage_failure_leaves_no_document_behind():
    svc = service_with(s3(FakeBucket(fail_with=(500, b""))))
    with pytest.raises(StorageUnavailable):
        svc.ingest("cli_acme", "r.txt", b"TOTAL 1.00", actor="a")
    assert svc.store.list_documents() == []


def test_files_saved_in_the_database_still_open_after_switching_storage(tmp_path):
    store = make_store()
    old = IntakeService(store)
    old.store.add_client("Acme", "a@example.com", ["receipt"], client_id="cli_acme")
    doc = old.ingest("cli_acme", "r.txt", b"TOTAL 1.00", actor="a")
    new = IntakeService(store, storage=LocalStorage(tmp_path))
    assert new.original_file(doc["id"]) == ("text/plain", b"TOTAL 1.00")


def test_external_file_with_database_setting_explains_itself(tmp_path):
    store = make_store()
    ext = IntakeService(store, storage=LocalStorage(tmp_path))
    ext.store.add_client("Acme", "a@example.com", ["receipt"], client_id="cli_acme")
    doc = ext.ingest("cli_acme", "r.txt", b"TOTAL 1.00", actor="a")
    with pytest.raises(StorageUnavailable, match="FILE_STORAGE"):
        IntakeService(store).original_file(doc["id"])


def test_old_sqlite_databases_gain_the_storage_column(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE files (document_id TEXT PRIMARY KEY, media_type TEXT NOT NULL, data BLOB NOT NULL)")
    db.commit()
    db.close()
    store = Store(str(path))
    assert "storage_key" in {r[1] for r in store._db.execute("PRAGMA table_info(files)")}
    store.close()


# ---------------------------------------------------------------- config


def test_storage_config(tmp_path):
    assert isinstance(storage_from_env({}), DatabaseStorage)
    assert isinstance(storage_from_env({"FILE_STORAGE": "local", "FILE_STORAGE_DIR": str(tmp_path)}), LocalStorage)
    s3_store = storage_from_env({"FILE_STORAGE": "s3", "S3_BUCKET": "b", "S3_REGION": "eu-central-1",
                                 "S3_ACCESS_KEY_ID": "k", "S3_SECRET_ACCESS_KEY": "s", "S3_PREFIX": "/intake/"})
    assert isinstance(s3_store, S3Storage) and s3_store.prefix == "intake/" and s3_store.encrypt
    with pytest.raises(StorageConfigError, match="S3_BUCKET"):
        storage_from_env({"FILE_STORAGE": "s3"})
    with pytest.raises(StorageConfigError):
        storage_from_env({"FILE_STORAGE": "dropbox"})
