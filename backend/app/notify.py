"""Sending reminder emails, and exporting the audit log.

Email goes through a small ``Sender`` interface:

* ``ConsoleSender`` (default) writes emails to the log, for development.
* ``SmtpSender`` sends real email through any SMTP provider (Postmark,
  SendGrid, Amazon SES, Microsoft 365...) using the standard library.

Run ``python -m app.manage send-reminders`` from cron (or a scheduled job on your host)
to send reminders on a schedule.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Protocol

log = logging.getLogger("intake.notify")


@dataclass
class Email:
    to: str
    subject: str
    body: str


class Sender(Protocol):
    name: str

    def send(self, email: Email) -> None: ...


@dataclass
class ConsoleSender:
    name: str = "console"
    sent: list[Email] = field(default_factory=list)

    def send(self, email: Email) -> None:
        self.sent.append(email)
        log.info("Email to %s: %s", email.to, email.subject)


@dataclass
class SmtpSender:
    host: str
    port: int
    from_address: str
    username: str | None = None
    password: str | None = None
    starttls: bool = True
    timeout: float = 20.0
    name: str = "smtp"

    def send(self, email: Email) -> None:
        msg = EmailMessage()
        msg["From"] = self.from_address
        msg["To"] = email.to
        msg["Subject"] = email.subject
        msg.set_content(email.body)
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            if self.starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self.username:
                smtp.login(self.username, self.password or "")
            smtp.send_message(msg)


class NotifyConfigError(ValueError):
    pass


def sender_from_env(env: dict[str, str] | None = None) -> Sender:
    env = dict(os.environ) if env is None else env
    host = env.get("SMTP_HOST", "").strip()
    if not host:
        return ConsoleSender()
    from_address = env.get("SMTP_FROM", "").strip()
    if not from_address:
        raise NotifyConfigError("Set SMTP_FROM, the address reminder emails come from.")
    try:
        port = int(env.get("SMTP_PORT", "587"))
    except ValueError as exc:
        raise NotifyConfigError("SMTP_PORT must be a number") from exc
    return SmtpSender(
        host=host,
        port=port,
        from_address=from_address,
        username=env.get("SMTP_USERNAME") or None,
        password=env.get("SMTP_PASSWORD") or None,
        starttls=env.get("SMTP_STARTTLS", "1") != "0",
    )


def send_reminders(service: Any, sender: Sender, actor: str, *, min_interval_days: int = 7,
                   dry_run: bool = False, now: datetime | None = None) -> list[dict[str, Any]]:
    """Email every client with missing documents, at most once per ``min_interval_days``."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=min_interval_days)).isoformat(timespec="seconds")
    recent = {
        (a["detail"] or "").split(" ", 1)[0]
        for a in service.store.activity(limit=500, since=since)
        if a["action"] == "reminder_sent"
    }
    results = []
    for preview in service.reminder_previews():
        cid = preview["client_id"]
        entry = {"client_id": cid, "to": preview["to"], "outstanding": preview["outstanding"]}
        if cid in recent:
            results.append({**entry, "status": "skipped", "reason": f"reminded in the last {min_interval_days} days"})
            continue
        if dry_run:
            results.append({**entry, "status": "would_send"})
            continue
        try:
            sender.send(Email(preview["to"], preview["subject"], preview["body"]))
        except (OSError, smtplib.SMTPException) as exc:
            log.warning("Reminder to %s failed: %s", preview["to"], exc)
            results.append({**entry, "status": "failed", "reason": "The email server refused or could not be reached."})
            continue
        service.store.log(actor, "reminder_sent", None, f"{cid} to {preview['to']} via {sender.name}")
        results.append({**entry, "status": "sent"})
    return results


AUDIT_COLUMNS = ("at", "actor", "action", "document_id", "detail")


def _safe_cell(value: Any) -> str:
    """Stop spreadsheet apps treating a cell as a formula (CSV injection)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def audit_csv(rows: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(AUDIT_COLUMNS)
    for row in sorted(rows, key=lambda r: r["at"]):
        writer.writerow([_safe_cell(row.get(c)) for c in AUDIT_COLUMNS])
    return out.getvalue()
