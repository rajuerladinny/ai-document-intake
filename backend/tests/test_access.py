"""Role rules, tested without the web framework."""

import pytest

from app.access import AccessControl
from app.auth import Auth, Forbidden, User
from app.notify import ConsoleSender
from app.seed import seed_demo
from app.service import IntakeService, NotFound
from conftest import SAMPLES_DIR, make_store

ADMIN = User("u1", "sam@firm.com", "Sam", "admin")
REVIEWER = User("u2", "ana@firm.com", "Ana", "reviewer")
HARBOUR = User("u3", "accounts@harbourfinch.example", "Harbour", "client", "cli_harbour")


@pytest.fixture
def access():
    svc = IntakeService(make_store())
    seed_demo(svc)
    yield AccessControl(svc, Auth(svc.store, "secret"))
    svc.store.close()


def fernhill_doc(access):
    return access.store.list_documents(client_id="cli_fernhill")[0]


def test_staff_see_every_client(access):
    assert len(access.list_clients(REVIEWER)) == 2
    assert {d["client_id"] for d in access.list_documents(REVIEWER)} == {"cli_harbour", "cli_fernhill"}


def test_client_sees_only_their_business(access):
    assert [c["id"] for c in access.list_clients(HARBOUR)] == ["cli_harbour"]
    docs = access.list_documents(HARBOUR)
    assert docs and {d["client_id"] for d in docs} == {"cli_harbour"}
    assert access.list_documents(HARBOUR, client_id="cli_fernhill") == []
    assert all("reviewed_by" not in d for d in docs)


def test_other_clients_documents_look_missing_not_forbidden(access):
    other = fernhill_doc(access)
    with pytest.raises(NotFound):
        access.get_document(HARBOUR, other["id"])
    with pytest.raises(NotFound):
        access.checklist(HARBOUR, "cli_fernhill")
    with pytest.raises(NotFound):
        access.upload(HARBOUR, "cli_fernhill", "r.txt", b"TOTAL 1.00")
    with pytest.raises(NotFound):
        access.activity(HARBOUR, document_id=other["id"])


def test_client_activity_is_limited_to_their_documents(access):
    own = {d["id"] for d in access.store.list_documents(client_id="cli_harbour")}
    entries = access.activity(HARBOUR, limit=500)
    assert entries and {e["document_id"] for e in entries} <= own
    everything = access.activity(REVIEWER, limit=500)
    assert len(everything) > len(entries)


def test_client_can_upload_for_themselves_and_is_recorded_as_the_actor(access):
    doc = access.upload(HARBOUR, "cli_harbour", "fuel.txt", (SAMPLES_DIR / "receipt-cafe.txt").read_bytes())
    assert doc["client_id"] == "cli_harbour" and doc["file_query"].startswith("expires=")
    assert access.store.activity(doc["id"])[-1]["actor"] == HARBOUR.email


@pytest.mark.parametrize("action", ["correct", "approve", "reject"])
def test_clients_cannot_review(access, action):
    doc = access.store.list_documents(client_id="cli_harbour")[0]
    with pytest.raises(Forbidden):
        if action == "correct":
            access.correct_field(HARBOUR, doc["id"], "total", "1.00")
        elif action == "approve":
            access.approve(HARBOUR, doc["id"])
        else:
            access.reject(HARBOUR, doc["id"], "Blurry")


def test_reviewer_actions_are_signed_with_their_email(access):
    doc = next(d for d in access.store.list_documents() if d["status"] == "ready")
    approved = access.approve(REVIEWER, doc["id"])
    assert approved["reviewed_by"] == REVIEWER.email


@pytest.mark.parametrize("user", [REVIEWER, HARBOUR])
def test_only_admins_administer(access, user):
    for call in (
        lambda: access.list_users(user),
        lambda: access.create_user(user, "x@y.com", "X", "reviewer", "Str0ngPassword"),
        lambda: access.create_client(user, "New", "n@y.com", ["invoice"]),
        lambda: access.audit_export(user),
        lambda: access.send_reminders(user, ConsoleSender()),
    ):
        with pytest.raises(Forbidden):
            call()


def test_reminder_previews_are_staff_only(access):
    assert access.reminder_previews(REVIEWER)
    with pytest.raises(Forbidden):
        access.reminder_previews(HARBOUR)


def test_admin_manages_users(access):
    created = access.create_user(ADMIN, "new@harbourfinch.example", "New", "client", "Str0ngPassword", "cli_harbour")
    assert created["client_id"] == "cli_harbour"
    assert "password_hash" not in str(access.list_users(ADMIN))
    access.disable_user(ADMIN, created["id"])
    assert next(u for u in access.list_users(ADMIN) if u["id"] == created["id"])["disabled"] is True
    with pytest.raises(Forbidden):
        access.disable_user(ADMIN, ADMIN.id)
    with pytest.raises(NotFound):
        access.disable_user(ADMIN, "usr_missing")


def test_demo_users_are_seeded_for_each_role(access):
    roles = {u["email"]: u["role"] for u in access.store.list_users()}
    assert roles["admin@demo.example"] == "admin"
    assert roles["ana@demo.example"] == "reviewer"
    assert roles["accounts@harbourfinch.example"] == "client"
