"""HTTP-level tests. Needs the packages in requirements.txt (run in CI)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from conftest import SAMPLES_DIR  # noqa: E402


@pytest.fixture
def client():
    return TestClient(create_app(db_path=":memory:", seed=True))


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "extractor": "rules"}


def test_lists_seeded_clients_and_documents(client):
    assert {c["id"] for c in client.get("/clients").json()} == {"cli_harbour", "cli_fernhill"}
    docs = client.get("/documents", params={"status": "needs_review"}).json()
    assert {d["file_name"] for d in docs} >= {"receipt-faded.txt", "invoice-kestrel-mismatch.txt"}


def test_upload_review_and_approve_flow(client):
    created = client.post(
        "/clients",
        json={"name": "Acme Ltd", "contact_email": "acme@example.com", "required_types": ["receipt"]},
    )
    assert created.status_code == 201
    client_id = created.json()["id"]

    with open(SAMPLES_DIR / "receipt-faded.txt", "rb") as fh:
        res = client.post("/documents", data={"client_id": client_id},
                          files={"file": ("fuel.txt", fh, "text/plain")}, headers={"X-User": "owner@acme"})
    assert res.status_code == 201
    doc = res.json()
    assert doc["status"] == "needs_review"

    blocked = client.post(f"/documents/{doc['id']}/approve")
    assert blocked.status_code == 409
    assert "Date" in blocked.json()["detail"]

    fixed = client.patch(f"/documents/{doc['id']}/fields/date", json={"value": "2026-11-07"},
                         headers={"X-User": "ana@firm"})
    assert fixed.status_code == 200

    approved = client.post(f"/documents/{doc['id']}/approve", headers={"X-User": "ana@firm"})
    assert approved.status_code == 200
    assert approved.json()["reviewed_by"] == "ana@firm"

    assert client.get(f"/clients/{client_id}/checklist").json()[0]["state"] == "approved"
    actions = [a["action"] for a in client.get("/activity", params={"document_id": doc["id"]}).json()]
    assert actions == ["approved", "corrected", "extracted", "uploaded"]


def test_errors_map_to_http_codes(client):
    assert client.get("/documents/doc_missing").status_code == 404
    res = client.post("/documents", data={"client_id": "cli_harbour"},
                      files={"file": ("photo.jpg", b"\xff\xd8\xff", "image/jpeg")})
    assert res.status_code == 415
    bad = client.post("/clients", json={"name": "X", "contact_email": "not-an-email"})
    assert bad.status_code == 422


def test_reminder_preview(client):
    previews = client.get("/reminders/preview").json()
    assert any(p["client_id"] == "cli_fernhill" for p in previews)
