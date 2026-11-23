"""HTTP API for document intake and review.

Run locally:  uvicorn app.main:app --reload   (from the backend/ folder)
Docs:         http://localhost:8000/docs
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .ingest import UnsupportedFile
from .seed import seed_demo
from .llm import build_extractor_from_env
from .service import ExtractionUnavailable, IntakeService, InvalidAction, NotFound, validate_doc_types
from .store import Store

Actor = Annotated[str, Header(alias="X-User", description="Who is acting. Real sign-in arrives in milestone 3.")]


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    required_types: list[str] = Field(default_factory=lambda: ["invoice", "receipt", "bank_statement"])


class FieldCorrection(BaseModel):
    value: str | None = Field(default=None, max_length=500)


class Rejection(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def create_app(db_path: str | None = None, seed: bool | None = None, extractor: Any = None) -> FastAPI:
    # DATABASE_URL may be a PostgreSQL URL or a SQLite path; DATABASE_PATH is the older name.
    store = Store(db_path or os.getenv("DATABASE_URL") or os.getenv("DATABASE_PATH", ":memory:"))
    # Fails fast at startup if EXTRACTOR=llm is set without a model or key.
    service = IntakeService(store, extractor or build_extractor_from_env())
    should_seed = seed if seed is not None else os.getenv("SEED_DEMO", "1") == "1"
    if should_seed:
        seed_demo(service)

    app = FastAPI(
        title="AI Document Intake API",
        version="0.2.0",
        description="Upload documents, extract fields with confidence scores, review and approve.",
    )
    app.state.service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidAction)
    async def _invalid(_: Request, exc: InvalidAction) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ExtractionUnavailable)
    async def _unavailable(_: Request, exc: ExtractionUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(UnsupportedFile)
    async def _unsupported(_: Request, exc: UnsupportedFile) -> JSONResponse:
        return JSONResponse(status_code=415, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "extractor": service.extractor.name, "database": store.backend}

    @app.get("/clients")
    def list_clients() -> list[dict[str, Any]]:
        return store.list_clients()

    @app.post("/clients", status_code=201)
    def create_client(body: ClientIn, x_user: Actor = "admin@demo") -> dict[str, Any]:
        client = store.add_client(body.name, body.contact_email, validate_doc_types(body.required_types))
        store.log(x_user, "client_created", detail=body.name)
        return client

    @app.get("/clients/{client_id}/checklist")
    def checklist(client_id: str) -> list[dict[str, Any]]:
        return service.checklist(client_id)

    @app.get("/documents")
    def list_documents(client_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        return store.list_documents(client_id=client_id, status=status)

    @app.post("/documents", status_code=201)
    async def upload(
        client_id: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
        x_user: Actor = "client@demo",
    ) -> dict[str, Any]:
        data = await file.read()
        return service.ingest(client_id, file.filename or "upload", data, actor=x_user)

    @app.get("/documents/{document_id}")
    def get_document(document_id: str) -> dict[str, Any]:
        return service.get(document_id)

    @app.get("/documents/{document_id}/file")
    def original_file(document_id: str) -> Response:
        media_type, data = service.original_file(document_id)
        if media_type.startswith("text/"):
            media_type = "text/plain; charset=utf-8"  # never render uploads as HTML
        headers = {"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
                   "Cache-Control": "private, max-age=300"}
        if media_type != "application/pdf":  # the browser's PDF viewer does not run in a sandbox
            headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
        return Response(data, media_type=media_type, headers=headers)

    @app.patch("/documents/{document_id}/fields/{key}")
    def correct_field(document_id: str, key: str, body: FieldCorrection,
                      x_user: Actor = "reviewer@demo") -> dict[str, Any]:
        return service.correct_field(document_id, key, body.value, actor=x_user)

    @app.post("/documents/{document_id}/approve")
    def approve(document_id: str, x_user: Actor = "reviewer@demo") -> dict[str, Any]:
        return service.approve(document_id, actor=x_user)

    @app.post("/documents/{document_id}/reject")
    def reject(document_id: str, body: Rejection, x_user: Actor = "reviewer@demo") -> dict[str, Any]:
        return service.reject(document_id, body.reason, actor=x_user)

    @app.get("/activity")
    def activity(document_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
        return store.activity(document_id=document_id, limit=limit)

    @app.get("/reminders/preview")
    def reminders() -> list[dict[str, Any]]:
        return service.reminder_previews()

    return app


app = create_app()
