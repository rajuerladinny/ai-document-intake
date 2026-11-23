# Roadmap

The project is built in four milestones. Each one ends with something that works end to end and can be shown to a client.

## Milestone 1: Intake and review workflow ✅ (this release)

- Document classification: invoice, receipt, payslip, bank statement, or unknown.
- Field extraction with a confidence score and the source line for every field.
- Validation checks: net + VAT = total, VAT rate, line items vs total, gross − deductions = net pay, bank balances reconcile.
- Review dashboard: queue sorted by risk, side-by-side document and fields, correct, approve or reject with a reason.
- Client portal: checklist of documents still needed, upload, status of each document.
- Reminder email previews for clients with missing documents.
- Activity history for every upload, extraction, correction and decision.
- FastAPI backend with SQLite, the same extraction rules as the browser demo, and tests for both.
- Live demo on GitHub Pages that runs entirely in the browser.

## Milestone 2: Real AI extraction and file handling

- `LLMExtractor` implementing the existing `Extractor` protocol (OpenAI or Claude), returning JSON that is validated against a schema before it is stored.
- Rule-based checks kept as a safety net on top of the LLM output, so arithmetic errors are still caught.
- OCR for photos and scanned PDFs (AWS Textract or a vision model).
- PDF and image upload in the web app through the API, with the original file shown next to the fields.
- PostgreSQL in place of SQLite.
- The web app switches between demo mode and API mode with one setting.

## Milestone 3: Accounts, security and reminders

- Sign-in for clients and staff, with roles (client, reviewer, admin) and per-client data isolation.
- File storage in an EU region (S3) with encryption at rest and short-lived download links.
- Scheduled reminder emails for missing documents.
- Audit log export.

## Milestone 4: Production and integrations

- Docker images and a one-command deploy (Render, Fly.io or AWS).
- Monitoring, error tracking and backups.
- Accuracy dashboard: how often reviewers correct each field, per document type.
- Export approved data to CSV, and to accounting tools such as Xero or QuickBooks.
