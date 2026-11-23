"""HTTP-level tests. Needs the packages in requirements.txt (run in CI)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.seed import DEMO_PASSWORD_DEFAULT  # noqa: E402
from conftest import SAMPLES_DIR  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("AUTH_REQUIRED", "DEMO_PASSWORD", "SMTP_HOST", "EXTRACTOR", "DATABASE_URL", "FILE_STORAGE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def api():
    return TestClient(create_app(db_path=":memory:", seed=True))


def sign_in(api, email):
    res = api.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD_DEFAULT})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


@pytest.fixture
def admin(api):
    return sign_in(api, "admin@demo.example")


@pytest.fixture
def reviewer(api):
    return sign_in(api, "ana@demo.example")


@pytest.fixture
def harbour(api):
    return sign_in(api, "accounts@harbourfinch.example")


def test_health_reports_setup_and_demo_accounts(api):
    body = api.get("/health").json()
    assert body["status"] == "ok" and body["extractor"] == "rules" and body["database"] == "sqlite"
    assert body["file_storage"] == "database"
    assert body["auth_required"] is True
    assert "admin@demo.example" in body["demo_accounts"]["emails"]


def test_everything_else_needs_sign_in(api):
    for path in ("/clients", "/documents", "/activity", "/reminders/preview", "/users", "/auth/me"):
        res = api.get(path)
        assert res.status_code == 401, path
        assert res.headers["www-authenticate"] == "Bearer"
    assert api.get("/clients", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_wrong_password_is_refused(api):
    res = api.post("/auth/login", json={"email": "admin@demo.example", "password": "wrong"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Email or password is incorrect."


def test_sign_in_me_and_sign_out(api, admin):
    assert api.get("/auth/me", headers=admin).json()["role"] == "admin"
    assert api.post("/auth/logout", headers=admin).status_code == 204
    assert api.get("/auth/me", headers=admin).status_code == 401


def test_reviewer_flow_upload_correct_approve(api, admin, reviewer):
    created = api.post("/clients", headers=admin,
                       json={"name": "Acme Ltd", "contact_email": "acme@example.com", "required_types": ["receipt"]})
    assert created.status_code == 201
    client_id = created.json()["id"]

    with open(SAMPLES_DIR / "receipt-faded.txt", "rb") as fh:
        res = api.post("/documents", headers=reviewer, data={"client_id": client_id},
                       files={"file": ("fuel.txt", fh, "text/plain")})
    assert res.status_code == 201
    doc = res.json()
    assert doc["status"] == "needs_review"

    blocked = api.post(f"/documents/{doc['id']}/approve", headers=reviewer)
    assert blocked.status_code == 409

    fixed = api.patch(f"/documents/{doc['id']}/fields/date", headers=reviewer, json={"value": "2026-11-12"})
    assert fixed.status_code == 200
    approved = api.post(f"/documents/{doc['id']}/approve", headers=reviewer)
    assert approved.status_code == 200
    assert approved.json()["reviewed_by"] == "ana@demo.example"


def test_client_users_only_see_their_own_business(api, harbour):
    clients = api.get("/clients", headers=harbour).json()
    assert [c["id"] for c in clients] == ["cli_harbour"]
    docs = api.get("/documents", headers=harbour).json()
    assert docs and {d["client_id"] for d in docs} == {"cli_harbour"}
    assert all("reviewed_by" not in d for d in docs)

    other = api.get("/documents", headers=harbour, params={"client_id": "cli_fernhill"}).json()
    assert other == []
    assert api.get("/clients/cli_fernhill/checklist", headers=harbour).status_code == 404
    with open(SAMPLES_DIR / "receipt-cafe.txt", "rb") as fh:
        res = api.post("/documents", headers=harbour, data={"client_id": "cli_fernhill"},
                       files={"file": ("x.txt", fh, "text/plain")})
    assert res.status_code == 404


def test_client_users_cannot_review_or_administer(api, harbour):
    doc = api.get("/documents", headers=harbour).json()[0]
    assert api.post(f"/documents/{doc['id']}/approve", headers=harbour).status_code == 403
    assert api.patch(f"/documents/{doc['id']}/fields/total", headers=harbour, json={"value": "1"}).status_code == 403
    assert api.get("/reminders/preview", headers=harbour).status_code == 403
    assert api.get("/users", headers=harbour).status_code == 403
    assert api.get("/audit/export", headers=harbour).status_code == 403


def test_reviewers_cannot_administer(api, reviewer):
    assert api.get("/users", headers=reviewer).status_code == 403
    assert api.post("/reminders/send", headers=reviewer, json={}).status_code == 403
    assert api.get("/audit/export", headers=reviewer).status_code == 403


def test_admin_creates_a_client_user_who_can_sign_in(api, admin):
    res = api.post("/users", headers=admin, json={
        "email": "Owner@Fernhill.example", "name": "Owner", "role": "client",
        "password": "Str0ngPassword", "client_id": "cli_fernhill",
    })
    assert res.status_code == 201, res.text
    assert res.json()["email"] == "owner@fernhill.example"
    weak = api.post("/users", headers=admin, json={
        "email": "x@fernhill.example", "role": "client", "password": "alllowercase1", "client_id": "cli_fernhill",
    })
    assert weak.status_code == 422
    login = api.post("/auth/login", json={"email": "owner@fernhill.example", "password": "Str0ngPassword"})
    assert login.status_code == 200 and login.json()["user"]["client_id"] == "cli_fernhill"
    assert "owner@fernhill.example" not in api.get("/health").json()["demo_accounts"]["emails"]


def test_original_file_needs_a_valid_signed_link(api, reviewer):
    doc = api.get("/documents", headers=reviewer).json()[0]
    assert doc["file_query"].startswith("expires=")
    ok = api.get(f"/documents/{doc['id']}/file?{doc['file_query']}")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("text/plain")
    assert ok.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in ok.headers["content-security-policy"]
    assert api.get(f"/documents/{doc['id']}/file").status_code == 401
    tampered = doc["file_query"][:-1] + ("0" if doc["file_query"][-1] != "0" else "1")
    assert api.get(f"/documents/{doc['id']}/file?{tampered}").status_code == 401


def test_audit_export_is_csv(api, admin):
    res = api.get("/audit/export", headers=admin)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert res.text.splitlines()[0] == "at,actor,action,document_id,detail"


def test_reminders_dry_run_then_send(api, admin):
    dry = api.post("/reminders/send", headers=admin, json={"dry_run": True}).json()
    assert dry["sender"] == "console"
    assert {r["status"] for r in dry["results"]} == {"would_send"}
    sent = api.post("/reminders/send", headers=admin, json={}).json()["results"]
    assert {r["status"] for r in sent} == {"sent"}
    again = api.post("/reminders/send", headers=admin, json={}).json()["results"]
    assert {r["status"] for r in again} == {"skipped"}


def test_errors_map_to_http_codes(api, reviewer):
    assert api.get("/documents/doc_missing", headers=reviewer).status_code == 404
    res = api.post("/documents", headers=reviewer, data={"client_id": "cli_harbour"},
                   files={"file": ("photo.heic", b"x", "image/heic")})
    assert res.status_code == 415
    assert api.get("/activity", headers=reviewer, params={"limit": 0}).status_code == 422


def test_demo_mode_without_sign_in(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    open_api = TestClient(create_app(db_path=":memory:", seed=True))
    assert open_api.get("/health").json()["auth_required"] is False
    assert open_api.get("/clients").status_code == 200
    doc = open_api.get("/documents").json()[0]
    assert open_api.get(f"/documents/{doc['id']}/file").status_code == 200


def test_ai_extractor_is_reported_and_outages_return_503():
    from app.llm import LLMError, LLMExtractor, OpenAIProvider

    def down(url, headers, body):
        raise LLMError("timeout")

    ai = TestClient(create_app(db_path=":memory:", seed=True, extractor=LLMExtractor(OpenAIProvider("m", "k", down))))
    headers = sign_in(ai, "ana@demo.example")
    assert ai.get("/health").json()["extractor"] == "llm:openai/m"
    photo = ai.post("/documents", headers=headers, data={"client_id": "cli_harbour"},
                    files={"file": ("photo.jpg", b"\xff\xd8\xff", "image/jpeg")})
    assert photo.status_code == 503
    text = ai.post("/documents", headers=headers, data={"client_id": "cli_harbour"},
                   files={"file": ("inv.txt", (SAMPLES_DIR / "invoice-brightline.txt").read_bytes(), "text/plain")})
    assert text.status_code == 201
    assert text.json()["extractor"] == "rules (AI unavailable)"
