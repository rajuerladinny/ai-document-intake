import pytest

from app.ingest import UnsupportedFile, to_text
from app.seed import seed_demo
from app.service import IntakeService, InvalidAction, NotFound, validate_doc_types
from app.store import Store
from conftest import SAMPLES_DIR


@pytest.fixture
def service():
    svc = IntakeService(Store(":memory:"))
    svc.store.add_client("Acme Ltd", "acme@example.com", ["invoice", "receipt"], client_id="cli_acme")
    yield svc
    svc.store.close()


def upload(service, name, client="cli_acme"):
    return service.ingest(client, name, (SAMPLES_DIR / name).read_bytes(), actor="client@acme")


def test_clean_invoice_is_ready_to_approve(service):
    doc = upload(service, "invoice-brightline.txt")
    assert doc["status"] == "ready"
    assert doc["extraction"]["doc_type"] == "invoice"
    assert [a["action"] for a in service.store.activity(doc["id"])] == ["extracted", "uploaded"]


def test_vat_mismatch_is_flagged_for_review(service):
    doc = upload(service, "invoice-kestrel-mismatch.txt")
    assert doc["status"] == "needs_review"
    failed = [c["id"] for c in doc["extraction"]["checks"] if not c["passed"]]
    assert failed == ["vat_rate"]


def test_correcting_a_field_records_who_and_what(service):
    doc = upload(service, "invoice-kestrel-mismatch.txt")
    doc = service.correct_field(doc["id"], "due_date", "2026-11-24", actor="ana@firm")
    due = next(f for f in doc["extraction"]["fields"] if f["key"] == "due_date")
    assert due["value"] == "2026-11-24"
    assert due["confidence"] == 1.0
    assert due["source"] == "Corrected by ana@firm"
    assert "due_date" not in doc["extraction"]["low_confidence_fields"]
    assert service.store.activity(doc["id"])[0]["action"] == "corrected"


def test_cannot_approve_with_missing_required_fields(service):
    doc = upload(service, "receipt-faded.txt")
    with pytest.raises(InvalidAction, match="Date"):
        service.approve(doc["id"], actor="ana@firm")
    service.correct_field(doc["id"], "date", "2026-11-07", actor="ana@firm")
    approved = service.approve(doc["id"], actor="ana@firm")
    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == "ana@firm"


def test_cannot_approve_unknown_document(service):
    doc = upload(service, "letter-unknown.txt")
    with pytest.raises(InvalidAction):
        service.approve(doc["id"], actor="ana@firm")


def test_reject_needs_a_reason(service):
    doc = upload(service, "receipt-cafe.txt")
    with pytest.raises(InvalidAction):
        service.reject(doc["id"], "  ", actor="ana@firm")
    rejected = service.reject(doc["id"], "Wrong client", actor="ana@firm")
    assert rejected["status"] == "rejected"
    assert rejected["reject_reason"] == "Wrong client"


def test_reviewed_documents_cannot_be_edited(service):
    doc = upload(service, "invoice-brightline.txt")
    service.approve(doc["id"], actor="ana@firm")
    with pytest.raises(InvalidAction):
        service.correct_field(doc["id"], "total", "1.00", actor="ana@firm")


def test_checklist_tracks_each_required_type(service):
    assert [i["state"] for i in service.checklist("cli_acme")] == ["missing", "missing"]
    doc = upload(service, "invoice-brightline.txt")
    assert service.checklist("cli_acme")[0]["state"] == "in_review"
    service.approve(doc["id"], actor="ana@firm")
    assert service.checklist("cli_acme")[0]["state"] == "approved"


def test_reminders_list_only_clients_with_outstanding_documents(service):
    upload(service, "invoice-brightline.txt")
    previews = service.reminder_previews()
    assert len(previews) == 1
    assert previews[0]["outstanding"] == ["receipt"]
    assert previews[0]["to"] == "acme@example.com"


def test_unknown_client_and_document(service):
    with pytest.raises(NotFound):
        service.ingest("nope", "a.txt", b"INVOICE", actor="x")
    with pytest.raises(NotFound):
        service.get("doc_missing")


def test_ingest_rejects_unsupported_files():
    with pytest.raises(UnsupportedFile):
        to_text("photo.jpg", b"\xff\xd8")
    with pytest.raises(UnsupportedFile):
        to_text("empty.txt", b"")
    assert to_text("a.txt", "Total £1.00".encode()) == "Total £1.00"


def test_validate_doc_types():
    assert validate_doc_types(["invoice"]) == ["invoice"]
    with pytest.raises(InvalidAction):
        validate_doc_types(["passport"])


def test_seed_demo_loads_two_clients_once():
    svc = IntakeService(Store(":memory:"))
    seed_demo(svc)
    seed_demo(svc)
    assert len(svc.store.list_clients()) == 2
    assert len(svc.store.list_documents()) == 7
    statuses = {d["file_name"]: d["status"] for d in svc.store.list_documents()}
    assert statuses["invoice-brightline.txt"] == "ready"
    assert statuses["receipt-faded.txt"] == "needs_review"
