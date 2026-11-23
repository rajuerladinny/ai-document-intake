"""HTTP API for document intake and review.

Run locally:  uvicorn app.main:app --reload   (from the backend/ folder)
Docs:         http://localhost:8000/docs

Sign in with POST /auth/login, then send ``Authorization: Bearer <token>``.
Set AUTH_REQUIRED=0 only for a local demo: every request then acts as an admin.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .access import AccessControl
from .auth import AuthError, Forbidden, User, auth_from_env
from .ingest import UnsupportedFile
from .llm import build_extractor_from_env
from .notify import sender_from_env
from .seed import DEMO_USERS, demo_password, seed_demo
from .service import ExtractionUnavailable, IntakeService, InvalidAction, NotFound, StorageUnavailable
from .storage import storage_from_env
from .store import Store

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact_email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    required_types: list[str] = Field(default_factory=lambda: ["invoice", "receipt", "bank_statement"])


class FieldCorrection(BaseModel):
    value: str | None = Field(default=None, max_length=500)


class Rejection(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class UserIn(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    name: str = Field(default="", max_length=200)
    role: str
    password: str = Field(min_length=10, max_length=200)
    client_id: str | None = None


class ReminderSend(BaseModel):
    dry_run: bool = False
    min_interval_days: int = Field(default=7, ge=0, le=90)


def bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def current_user(request: Request, authorization: Annotated[str | None, Header()] = None) -> User:
    return request.app.state.auth.user_for_token(bearer_token(authorization))


# Module level, so FastAPI can resolve it from the postponed annotations.
CurrentUser = Annotated[User, Depends(current_user)]


def create_app(db_path: str | None = None, seed: bool | None = None, extractor: Any = None) -> FastAPI:
    # DATABASE_URL may be a PostgreSQL URL or a SQLite path; DATABASE_PATH is the older name.
    store = Store(db_path or os.getenv("DATABASE_URL") or os.getenv("DATABASE_PATH", ":memory:"))
    # Fails fast at startup if EXTRACTOR=llm is set without a model or key.
    service = IntakeService(store, extractor or build_extractor_from_env(), storage_from_env())
    should_seed = seed if seed is not None else os.getenv("SEED_DEMO", "1") == "1"
    if should_seed:
        seed_demo(service)

    auth = auth_from_env(store)
    access = AccessControl(service, auth)
    sender = sender_from_env()

    app = FastAPI(
        title="AI Document Intake API",
        version="0.3.0",
        description="Upload documents, extract fields with confidence scores, review and approve.",
    )
    app.state.service = service
    app.state.auth = auth
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o],
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def error(status: int):
        async def handler(_: Request, exc: Exception) -> JSONResponse:
            headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
            return JSONResponse(status_code=status, content={"detail": str(exc)}, headers=headers)
        return handler

    app.add_exception_handler(AuthError, error(401))
    app.add_exception_handler(Forbidden, error(403))
    app.add_exception_handler(NotFound, error(404))
    app.add_exception_handler(InvalidAction, error(409))
    app.add_exception_handler(UnsupportedFile, error(415))
    app.add_exception_handler(ExtractionUnavailable, error(503))
    app.add_exception_handler(StorageUnavailable, error(503))

    @app.exception_handler(ValueError)
    async def _bad_value(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, Any]:
        info: dict[str, Any] = {
            "status": "ok",
            "extractor": service.extractor.name,
            "database": store.backend,
            "file_storage": service.storage.name,
            "auth_required": auth.required,
        }
        if should_seed and auth.required:
            # Demo only: lets the sign-in screen offer the sample accounts.
            # Only the built-in demo accounts, never users an admin added later.
            demo_emails = {u["email"] for u in DEMO_USERS}
            info["demo_accounts"] = {"password": demo_password(),
                                     "emails": [u["email"] for u in store.list_users()
                                                if u["email"] in demo_emails and not u["disabled"]]}
        return info

    # Sign-in -------------------------------------------------------------------

    @app.post("/auth/login")
    def login(body: LoginIn) -> dict[str, Any]:
        token, user = auth.login(body.email, body.password)
        store.log(user.email, "signed_in")
        return {"token": token, "user": user.public()}

    @app.post("/auth/logout", status_code=204)
    def logout(authorization: Annotated[str | None, Header()] = None) -> Response:
        token = bearer_token(authorization)
        if token:
            auth.logout(token)
        return Response(status_code=204)

    @app.get("/auth/me")
    def me(user: CurrentUser) -> dict[str, Any]:
        return user.public()

    # Clients and documents ---------------------------------------------------------

    @app.get("/clients")
    def list_clients(user: CurrentUser) -> list[dict[str, Any]]:
        return access.list_clients(user)

    @app.post("/clients", status_code=201)
    def create_client(body: ClientIn, user: CurrentUser) -> dict[str, Any]:
        return access.create_client(user, body.name, body.contact_email, body.required_types)

    @app.get("/clients/{client_id}/checklist")
    def checklist(client_id: str, user: CurrentUser) -> list[dict[str, Any]]:
        return access.checklist(user, client_id)

    @app.get("/documents")
    def list_documents(user: CurrentUser, client_id: str | None = None,
                       status: str | None = None) -> list[dict[str, Any]]:
        return access.list_documents(user, client_id, status)

    @app.post("/documents", status_code=201)
    async def upload(
        user: CurrentUser,
        client_id: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        data = await file.read()
        return access.upload(user, client_id, file.filename or "upload", data)

    @app.get("/documents/{document_id}")
    def get_document(document_id: str, user: CurrentUser) -> dict[str, Any]:
        return access.get_document(user, document_id)

    @app.get("/documents/{document_id}/file")
    def original_file(document_id: str, expires: str | None = None, sig: str | None = None) -> Response:
        media_type, data = access.original_file(document_id, expires, sig)
        if media_type.startswith("text/"):
            media_type = "text/plain; charset=utf-8"  # never render uploads as HTML
        headers = {"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
                   "Cache-Control": "private, max-age=300", "Referrer-Policy": "no-referrer"}
        if media_type != "application/pdf":  # the browser's PDF viewer does not run in a sandbox
            headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
        return Response(data, media_type=media_type, headers=headers)

    @app.patch("/documents/{document_id}/fields/{key}")
    def correct_field(document_id: str, key: str, body: FieldCorrection, user: CurrentUser) -> dict[str, Any]:
        return access.correct_field(user, document_id, key, body.value)

    @app.post("/documents/{document_id}/approve")
    def approve(document_id: str, user: CurrentUser) -> dict[str, Any]:
        return access.approve(user, document_id)

    @app.post("/documents/{document_id}/reject")
    def reject(document_id: str, body: Rejection, user: CurrentUser) -> dict[str, Any]:
        return access.reject(user, document_id, body.reason)

    # Activity, audit and reminders ---------------------------------------------------

    @app.get("/activity")
    def activity(user: CurrentUser, document_id: str | None = None,
                 limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
        return access.activity(user, document_id, limit)

    @app.get("/audit/export")
    def audit_export(user: CurrentUser, since: str | None = None, until: str | None = None) -> Response:
        body = access.audit_export(user, since, until)
        return Response(body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'})

    @app.get("/reminders/preview")
    def reminders(user: CurrentUser) -> list[dict[str, Any]]:
        return access.reminder_previews(user)

    @app.post("/reminders/send")
    def send(body: ReminderSend, user: CurrentUser) -> dict[str, Any]:
        results = access.send_reminders(user, sender, dry_run=body.dry_run,
                                        min_interval_days=body.min_interval_days)
        return {"sender": sender.name, "results": results}

    # Users -----------------------------------------------------------------------

    @app.get("/users")
    def list_users(user: CurrentUser) -> list[dict[str, Any]]:
        return access.list_users(user)

    @app.post("/users", status_code=201)
    def create_user(body: UserIn, user: CurrentUser) -> dict[str, Any]:
        return access.create_user(user, body.email, body.name, body.role, body.password, body.client_id)

    @app.post("/users/{user_id}/disable", status_code=204)
    def disable_user(user_id: str, user: CurrentUser) -> Response:
        access.disable_user(user, user_id)
        return Response(status_code=204)

    return app


app = create_app()
