"""Demo data: two clients and the sample documents from /samples."""

from __future__ import annotations

import os
from pathlib import Path

from .auth import hash_password
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


# Demo sign-ins. The password is public (it is shown on the demo's sign-in
# screen), so never run with SEED_DEMO=1 on a server holding real data.
DEMO_PASSWORD_DEFAULT = "DemoPass2026"
DEMO_USERS = [
    {"email": "admin@demo.example", "name": "Sam (admin)", "role": "admin", "client_id": None},
    {"email": "ana@demo.example", "name": "Ana (reviewer)", "role": "reviewer", "client_id": None},
    {"email": "accounts@harbourfinch.example", "name": "Harbour & Finch", "role": "client", "client_id": "cli_harbour"},
    {"email": "hello@fernhill.example", "name": "Fernhill Design", "role": "client", "client_id": "cli_fernhill"},
]


def demo_password() -> str:
    return os.getenv("DEMO_PASSWORD") or DEMO_PASSWORD_DEFAULT


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
    # One hash for all demo users keeps start-up fast (PBKDF2 is deliberately slow).
    password_hash = hash_password(demo_password())
    for user in DEMO_USERS:
        service.store.add_user(user["email"], user["name"], user["role"], password_hash, user["client_id"])
