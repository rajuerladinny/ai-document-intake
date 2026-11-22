"""Demo data: two clients and the sample documents from /samples."""

from __future__ import annotations

from pathlib import Path

from .service import IntakeService

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"

DEMO_CLIENTS = [
    {
        "id": "cli_harbour",
        "name": "Harbour & Finch Catering Ltd",
        "contact_email": "accounts@harbourfinch.example",
        "required_types": ["invoice", "receipt", "payslip", "bank_statement"],
        "files": [
            "invoice-brightline.txt",
            "invoice-kestrel-mismatch.txt",
            "payslip-harbour.txt",
            "bank-statement-meridian.txt",
        ],
    },
    {
        "id": "cli_fernhill",
        "name": "Fernhill Design Studio",
        "contact_email": "hello@fernhill.example",
        "required_types": ["receipt", "invoice", "bank_statement"],
        "files": ["receipt-cafe.txt", "receipt-faded.txt", "letter-unknown.txt"],
    },
]


def seed_demo(service: IntakeService, samples_dir: Path = SAMPLES_DIR) -> None:
    if service.store.list_clients():
        return
    # Demo data always uses the free rule-based extractor, so starting the API
    # with EXTRACTOR=llm does not spend tokens on the sample files.
    seeder = IntakeService(service.store)
    for client in DEMO_CLIENTS:
        service.store.add_client(client["name"], client["contact_email"], client["required_types"], client["id"])
        for name in client["files"]:
            path = samples_dir / name
            if path.exists():
                seeder.ingest(client["id"], name, path.read_bytes(), actor=client["contact_email"])
