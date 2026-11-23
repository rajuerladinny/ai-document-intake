"""Business rules for intake and review, independent of the web framework."""

from __future__ import annotations

from typing import Any

from .extraction import DOC_TYPES, REVIEW_THRESHOLD, ExtractedField, Extractor, RuleBasedExtractor, run_checks
from .ingest import UnsupportedFile, read_upload
from .llm import LLMError
from .storage import DatabaseStorage, FileStorage, StorageError
from .store import Store, new_id, utc_now


class NotFound(LookupError):
    pass


class InvalidAction(ValueError):
    pass


class ExtractionUnavailable(RuntimeError):
    """The AI extractor was needed (photo or scan) but could not be reached."""


class StorageUnavailable(RuntimeError):
    """The file store (local folder or S3) refused or could not be reached."""


class IntakeService:
    def __init__(self, store: Store, extractor: Extractor | None = None, storage: FileStorage | None = None) -> None:
        self.store = store
        self.extractor = extractor or RuleBasedExtractor()
        self.storage = storage or DatabaseStorage()

    # Intake ------------------------------------------------------------------

    def ingest(self, client_id: str, file_name: str, data: bytes, actor: str) -> dict[str, Any]:
        if not self.store.get_client(client_id):
            raise NotFound(f"Client {client_id} not found")
        upload = read_upload(file_name, data)
        extract_document = getattr(self.extractor, "extract_document", None)
        if extract_document is not None:
            try:
                result = extract_document(upload)
            except LLMError as exc:  # only raised for photos and scans; text falls back to rules
                raise ExtractionUnavailable(f"The AI extractor could not read this file: {exc}") from exc
            text, extraction, used = result.text, result.extraction.to_dict(), result.extractor
        else:
            if upload.text is None:
                raise UnsupportedFile(
                    "Photos and scanned PDFs need the AI extractor (set EXTRACTOR=llm). "
                    "Upload a text-based PDF or .txt instead."
                )
            text, extraction, used = upload.text, self.extractor.extract(upload.text).to_dict(), self.extractor.name
        status = "needs_review" if extraction["needs_review"] else "ready"
        media_type = upload.media_type or "application/octet-stream"
        key = None
        if not isinstance(self.storage, DatabaseStorage):
            # Save the original first, so a storage failure leaves no half-created document.
            key = f"{client_id}/{new_id('file')}"
            try:
                self.storage.put(key, data, media_type)
            except StorageError as exc:
                raise StorageUnavailable("The file could not be saved to storage. Please try again.") from exc
        doc = self.store.add_document(client_id, file_name, text, status, extraction, used)
        if key is None:
            self.store.save_file(doc["id"], media_type, data)
        else:
            self.store.save_file(doc["id"], media_type, b"", storage_key=key)
        doc = self.get(doc["id"])
        self.store.log(actor, "uploaded", doc["id"], file_name)
        self.store.log(
            "system", "extracted", doc["id"],
            f"{extraction['doc_type']} at {round(extraction['overall_confidence'] * 100)}% confidence"
            + (", flagged for review" if status == "needs_review" else ""),
        )
        return doc

    def original_file(self, document_id: str) -> tuple[str, bytes]:
        found = self.store.get_file_record(document_id)
        if found is None:
            self.get(document_id)  # 404 for unknown documents
            raise NotFound("The original file is not stored for this document")
        media_type, data, key = found
        if key is None:
            return media_type, data
        if isinstance(self.storage, DatabaseStorage):
            raise StorageUnavailable("This file is in external storage, but FILE_STORAGE is set to database.")
        try:
            return media_type, self.storage.get(key)
        except StorageError as exc:
            raise StorageUnavailable(str(exc)) from exc

    # Review ------------------------------------------------------------------

    def get(self, document_id: str) -> dict[str, Any]:
        doc = self.store.get_document(document_id)
        if not doc:
            raise NotFound(f"Document {document_id} not found")
        return doc

    def correct_field(self, document_id: str, key: str, value: str | None, actor: str) -> dict[str, Any]:
        doc = self.get(document_id)
        if doc["status"] in ("approved", "rejected"):
            raise InvalidAction("This document has already been reviewed.")
        extraction = doc["extraction"]
        target = next((f for f in extraction["fields"] if f["key"] == key), None)
        if target is None:
            raise NotFound(f"Field {key} not found on this document")
        cleaned = (value or "").strip() or None
        before = target["value"]
        target.setdefault("original_value", before)
        target["value"] = cleaned
        target["confidence"] = 1.0 if cleaned is not None else 0.0
        target["source"] = f"Corrected by {actor}"
        extraction["low_confidence_fields"] = [
            f["key"] for f in extraction["fields"] if f["required"] and f["confidence"] < REVIEW_THRESHOLD
        ]
        # Re-run the arithmetic checks so a corrected VAT figure clears (or raises) the warning.
        fields = [
            ExtractedField(f["key"], f["label"], f["value"], f["confidence"], f["source"], f["required"])
            for f in extraction["fields"]
        ]
        extraction["checks"] = [c.__dict__ for c in run_checks(extraction["doc_type"], fields, doc["text"])]
        self.store.update_document(document_id, extraction=extraction)
        self.store.log(actor, "corrected", document_id, f"{target['label']}: {before or 'empty'} → {cleaned or 'empty'}")
        return self.get(document_id)

    def approve(self, document_id: str, actor: str) -> dict[str, Any]:
        doc = self.get(document_id)
        if doc["status"] == "approved":
            raise InvalidAction("Already approved.")
        missing = [f["label"] for f in doc["extraction"]["fields"] if f["required"] and f["value"] is None]
        if doc["extraction"]["doc_type"] == "unknown":
            raise InvalidAction("Set the document type before approving, or reject it.")
        if missing:
            raise InvalidAction(f"Fill in the missing fields first: {', '.join(missing)}.")
        updated = self.store.update_document(
            document_id, status="approved", reviewed_by=actor, reviewed_at=utc_now(), reject_reason=None
        )
        self.store.log(actor, "approved", document_id)
        return updated

    def reject(self, document_id: str, reason: str, actor: str) -> dict[str, Any]:
        doc = self.get(document_id)
        if doc["status"] == "rejected":
            raise InvalidAction("Already rejected.")
        reason = (reason or "").strip()
        if not reason:
            raise InvalidAction("Give a reason so the client knows what to resend.")
        updated = self.store.update_document(
            document_id, status="rejected", reviewed_by=actor, reviewed_at=utc_now(), reject_reason=reason
        )
        self.store.log(actor, "rejected", document_id, reason)
        return updated

    # Client checklist and reminders -----------------------------------------

    def checklist(self, client_id: str) -> list[dict[str, Any]]:
        client = self.store.get_client(client_id)
        if not client:
            raise NotFound(f"Client {client_id} not found")
        docs = self.store.list_documents(client_id=client_id)
        items = []
        for doc_type in client["required_types"]:
            of_type = [d for d in docs if d["extraction"]["doc_type"] == doc_type]
            if any(d["status"] == "approved" for d in of_type):
                state = "approved"
            elif any(d["status"] in ("needs_review", "ready") for d in of_type):
                state = "in_review"
            elif of_type:
                state = "rejected"
            else:
                state = "missing"
            items.append({"doc_type": doc_type, "state": state, "documents": [d["id"] for d in of_type]})
        return items

    def reminder_previews(self) -> list[dict[str, Any]]:
        """Who would get a 'documents still missing' email today. Sending arrives in milestone 3."""
        previews = []
        for client in self.store.list_clients():
            outstanding = [i["doc_type"] for i in self.checklist(client["id"]) if i["state"] in ("missing", "rejected")]
            if outstanding:
                names = ", ".join(t.replace("_", " ") for t in outstanding)
                previews.append({
                    "client_id": client["id"],
                    "to": client["contact_email"],
                    "subject": "Documents still needed for your bookkeeping",
                    "body": f"Hi {client['name']}, we are still waiting for: {names}. "
                            "Please upload them through your client portal.",
                    "outstanding": outstanding,
                })
        return previews


def validate_doc_types(types: list[str]) -> list[str]:
    bad = [t for t in types if t not in DOC_TYPES or t == "unknown"]
    if bad:
        raise InvalidAction(f"Unknown document types: {', '.join(bad)}")
    return types
