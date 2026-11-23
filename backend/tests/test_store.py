"""Store behaviour that both databases must share. Runs on PostgreSQL when TEST_DATABASE_URL is set."""

import pytest

from app.store import PG_SCHEMA, Store, StoreConfigError, _statements
from conftest import make_store


@pytest.fixture
def store():
    s = make_store()
    s.add_client("Acme", "a@example.com", ["invoice"], client_id="c1")
    yield s
    s.close()


def test_documents_and_files_round_trip(store):
    doc = store.add_document("c1", "a.pdf", "text", "ready", {"doc_type": "invoice"}, "rules")
    store.save_file(doc["id"], "application/pdf", b"%PDF\x00\xff")
    store.save_file(doc["id"], "application/pdf", b"%PDF-2")  # replaces, does not duplicate
    assert store.get_file(doc["id"]) == ("application/pdf", b"%PDF-2")
    got = store.get_document(doc["id"])
    assert got["extraction"] == {"doc_type": "invoice"} and got["file_media_type"] == "application/pdf"


def test_newest_first_even_within_the_same_second(store):
    ids = [store.add_document("c1", f"{i}.txt", "t", "ready", {}, "rules")["id"] for i in range(3)]
    assert [d["id"] for d in store.list_documents()] == ids[::-1]
    for i in range(3):
        store.log("ana", f"step{i}")
    assert [a["action"] for a in store.activity()] == ["step2", "step1", "step0"]
    assert set(store.activity()[0]) == {"id", "at", "actor", "action", "document_id", "detail"}


def test_status_is_constrained(store):
    doc = store.add_document("c1", "a.txt", "t", "ready", {}, "rules")
    with pytest.raises(Exception):
        store.update_document(doc["id"], status="lost")


def test_unknown_client_is_rejected(store):
    with pytest.raises(Exception):
        store.add_document("nobody", "a.txt", "t", "ready", {}, "rules")


def test_quotes_and_unicode_survive(store):
    text = "O'Brien & Sons — £1,234.50 \\ \"quoted\" ?"
    doc = store.add_document("c1", "o'brien.txt", text, "ready", {"note": text}, "rules")
    got = store.get_document(doc["id"])
    assert got["text"] == text and got["extraction"]["note"] == text


def test_reset_clears_everything(store):
    store.add_document("c1", "a.txt", "t", "ready", {}, "rules")
    store.reset()
    assert store.list_clients() == [] and store.list_documents() == [] and store.activity() == []


def test_postgres_schema_has_order_columns_and_bytea():
    statements = list(_statements(PG_SCHEMA))
    assert len(statements) == 7
    assert "BYTEA" in PG_SCHEMA and PG_SCHEMA.count("seq BIGSERIAL") == 2


def test_sqlite_url_form_is_accepted(tmp_path):
    s = Store(f"sqlite:///{tmp_path / 'x.db'}")
    assert s.backend == "sqlite"
    s.close()


def test_postgres_url_without_driver_or_server_explains_itself():
    try:
        import psycopg  # noqa: F401
    except ImportError:
        with pytest.raises(StoreConfigError, match="psycopg"):
            Store("postgresql://localhost:1/none")
    else:
        with pytest.raises(StoreConfigError, match="Could not connect"):
            Store("postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")
