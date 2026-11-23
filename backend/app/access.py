"""Who may do what. Every API route goes through this layer, so the rules live
in one place and are tested without the web framework.

Client users only ever see their own business: other clients' documents
answer "not found" rather than "forbidden", so ids cannot be probed.
"""

from __future__ import annotations

from typing import Any

from .auth import Auth, Forbidden, User
from .notify import Sender, audit_csv, send_reminders
from .service import IntakeService, NotFound, validate_doc_types


class AccessControl:
    def __init__(self, service: IntakeService, auth: Auth) -> None:
        self.service = service
        self.auth = auth
        self.store = service.store

    # Helpers ------------------------------------------------------------------

    @staticmethod
    def _require(user: User, *roles: str) -> None:
        if user.role not in roles:
            raise Forbidden("You don't have permission to do that.")

    def _check_client(self, user: User, client_id: str) -> None:
        if user.role == "client" and user.client_id != client_id:
            raise NotFound(f"Client {client_id} not found")

    def _document(self, user: User, document_id: str) -> dict[str, Any]:
        doc = self.service.get(document_id)
        if user.role == "client" and doc["client_id"] != user.client_id:
            raise NotFound(f"Document {document_id} not found")
        return doc

    def _with_link(self, doc: dict[str, Any]) -> dict[str, Any]:
        if doc.get("file_media_type"):
            doc = {**doc, "file_query": self.auth.sign_file_link(doc["id"])}
        return doc

    def _for_client(self, user: User, doc: dict[str, Any]) -> dict[str, Any]:
        """Clients see status and the reason for a rejection, not the reviewers' working."""
        doc = self._with_link(doc)
        if user.role != "client":
            return doc
        hidden = {"reviewed_by"}
        return {k: v for k, v in doc.items() if k not in hidden}

    # Clients ------------------------------------------------------------------

    def list_clients(self, user: User) -> list[dict[str, Any]]:
        clients = self.store.list_clients()
        return [c for c in clients if user.is_staff or c["id"] == user.client_id]

    def create_client(self, user: User, name: str, contact_email: str, required_types: list[str]) -> dict[str, Any]:
        self._require(user, "admin")
        client = self.store.add_client(name, contact_email, validate_doc_types(required_types))
        self.store.log(user.email, "client_created", detail=name)
        return client

    def checklist(self, user: User, client_id: str) -> list[dict[str, Any]]:
        self._check_client(user, client_id)
        return self.service.checklist(client_id)

    # Documents ----------------------------------------------------------------

    def list_documents(self, user: User, client_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        if user.role == "client":
            if client_id and client_id != user.client_id:
                return []
            client_id = user.client_id
        docs = self.store.list_documents(client_id=client_id, status=status)
        return [self._for_client(user, d) for d in docs]

    def upload(self, user: User, client_id: str, file_name: str, data: bytes) -> dict[str, Any]:
        self._check_client(user, client_id)
        return self._for_client(user, self.service.ingest(client_id, file_name, data, actor=user.email))

    def get_document(self, user: User, document_id: str) -> dict[str, Any]:
        return self._for_client(user, self._document(user, document_id))

    def original_file(self, document_id: str, expires: str | None, sig: str | None) -> tuple[str, bytes]:
        # Authorised by the signed link, which was only handed to users who could see the document.
        self.auth.check_file_link(document_id, expires, sig)
        return self.service.original_file(document_id)

    def correct_field(self, user: User, document_id: str, key: str, value: str | None) -> dict[str, Any]:
        self._require(user, "admin", "reviewer")
        return self._with_link(self.service.correct_field(document_id, key, value, actor=user.email))

    def approve(self, user: User, document_id: str) -> dict[str, Any]:
        self._require(user, "admin", "reviewer")
        return self._with_link(self.service.approve(document_id, actor=user.email))

    def reject(self, user: User, document_id: str, reason: str) -> dict[str, Any]:
        self._require(user, "admin", "reviewer")
        return self._with_link(self.service.reject(document_id, reason, actor=user.email))

    # Activity, audit and reminders ----------------------------------------------

    def activity(self, user: User, document_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if user.role == "client":
            # A client sees the history of their own documents only.
            own = [d["id"] for d in self.store.list_documents(client_id=user.client_id)]
            if document_id:
                self._document(user, document_id)
            return self.store.activity(document_id=document_id, limit=limit, document_ids=own)
        return self.store.activity(document_id=document_id, limit=limit)

    def audit_export(self, user: User, since: str | None = None, until: str | None = None) -> str:
        self._require(user, "admin")
        rows = self.store.activity(limit=100_000, since=since, until=until)
        self.store.log(user.email, "audit_exported", detail=f"{len(rows)} entries")
        return audit_csv(rows)

    def reminder_previews(self, user: User) -> list[dict[str, Any]]:
        self._require(user, "admin", "reviewer")
        return self.service.reminder_previews()

    def send_reminders(self, user: User, sender: Sender, *, dry_run: bool = False,
                       min_interval_days: int = 7) -> list[dict[str, Any]]:
        self._require(user, "admin")
        return send_reminders(self.service, sender, user.email, dry_run=dry_run, min_interval_days=min_interval_days)

    # Users ----------------------------------------------------------------------

    def list_users(self, user: User) -> list[dict[str, Any]]:
        self._require(user, "admin")
        return self.store.list_users()

    def create_user(self, user: User, email: str, name: str, role: str, password: str,
                    client_id: str | None = None) -> dict[str, Any]:
        self._require(user, "admin")
        created = self.auth.create_user(email, name, role, password, client_id)
        self.store.log(user.email, "user_created", detail=f"{created.email} ({created.role})")
        return created.public()

    def disable_user(self, user: User, user_id: str) -> None:
        self._require(user, "admin")
        if user_id == user.id:
            raise Forbidden("You can't disable your own account.")
        target = self.store.get_user(user_id)
        if not target:
            raise NotFound("User not found")
        self.auth.disable_user(user_id)
        self.store.log(user.email, "user_disabled", detail=target["email"])
