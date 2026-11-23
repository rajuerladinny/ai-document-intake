import csv
import io
import smtplib
from datetime import datetime, timedelta, timezone

import pytest

from app.access import AccessControl
from app.auth import Auth, User
from app.notify import ConsoleSender, NotifyConfigError, SmtpSender, audit_csv, send_reminders, sender_from_env
from app.seed import seed_demo
from app.service import IntakeService
from conftest import make_store

ADMIN = User("u1", "sam@firm.com", "Sam", "admin")


@pytest.fixture
def service():
    svc = IntakeService(make_store())
    seed_demo(svc)
    yield svc
    svc.store.close()


def test_reminders_send_once_per_interval(service):
    sender = ConsoleSender()
    first = send_reminders(service, sender, "sam@firm.com")
    assert {r["status"] for r in first} == {"sent"}
    assert {e.to for e in sender.sent} == {"accounts@harbourfinch.example", "hello@fernhill.example"}
    assert "still waiting for" in sender.sent[0].body

    second = send_reminders(service, sender, "sam@firm.com")
    assert {r["status"] for r in second} == {"skipped"}
    assert len(sender.sent) == 2

    later = datetime.now(timezone.utc) + timedelta(days=8)
    third = send_reminders(service, sender, "sam@firm.com", now=later)
    assert {r["status"] for r in third} == {"sent"}


def test_dry_run_sends_nothing(service):
    sender = ConsoleSender()
    results = send_reminders(service, sender, "sam", dry_run=True)
    assert {r["status"] for r in results} == {"would_send"} and sender.sent == []
    assert not [a for a in service.store.activity(limit=500) if a["action"] == "reminder_sent"]


def test_a_failed_email_is_reported_and_retried_next_time(service):
    class Broken:
        name = "smtp"

        def send(self, email):
            raise smtplib.SMTPServerDisconnected("gone")

    results = send_reminders(service, Broken(), "sam")
    assert {r["status"] for r in results} == {"failed"}
    retry = send_reminders(service, ConsoleSender(), "sam")
    assert {r["status"] for r in retry} == {"sent"}


def test_sender_config():
    assert isinstance(sender_from_env({}), ConsoleSender)
    smtp = sender_from_env({"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "books@firm.com", "SMTP_PORT": "2525"})
    assert isinstance(smtp, SmtpSender) and smtp.port == 2525 and smtp.starttls
    with pytest.raises(NotifyConfigError, match="SMTP_FROM"):
        sender_from_env({"SMTP_HOST": "smtp.example.com"})


def test_smtp_sender_builds_a_proper_message(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["server"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = user

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    from app.notify import Email

    SmtpSender("smtp.example.com", 587, "books@firm.com", "user", "pw").send(Email("c@x.com", "Hi", "Body"))
    assert sent["server"] == ("smtp.example.com", 587) and sent["tls"] and sent["login"] == "user"
    assert sent["msg"]["To"] == "c@x.com" and sent["msg"]["From"] == "books@firm.com"


def test_audit_csv_is_ordered_and_injection_safe():
    rows = [
        {"at": "2026-11-02", "actor": "ana", "action": "approved", "document_id": "d1", "detail": None},
        {"at": "2026-11-01", "actor": "=HYPERLINK(\"evil\")", "action": "uploaded", "document_id": "d1",
         "detail": "+cmd, \"quoted\""},
    ]
    parsed = list(csv.reader(io.StringIO(audit_csv(rows))))
    assert parsed[0] == ["at", "actor", "action", "document_id", "detail"]
    assert parsed[1][0] == "2026-11-01" and parsed[1][1].startswith("'=") and parsed[1][4] == "'+cmd, \"quoted\""
    assert parsed[2][4] == ""


def test_audit_export_filters_by_date_and_logs_itself(service):
    access = AccessControl(service, Auth(service.store, "s"))
    everything = access.audit_export(ADMIN)
    assert len(everything.splitlines()) > 5
    none = access.audit_export(ADMIN, since="2999-01-01")
    assert none.splitlines() == ["at,actor,action,document_id,detail"]
    assert service.store.activity(limit=1)[0]["action"] == "audit_exported"
