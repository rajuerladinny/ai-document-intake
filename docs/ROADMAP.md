# Roadmap

The project is built in four milestones. Each one ends with something that works end to end and can be shown to a client.

## Milestone 1: Intake and review workflow ✅

- Document classification: invoice, receipt, payslip, bank statement, or unknown.
- Field extraction with a confidence score and the source line for every field.
- Validation checks: net + VAT = total, VAT rate, line items vs total, gross − deductions = net pay, bank balances reconcile.
- Review dashboard: queue sorted by risk, side-by-side document and fields, correct, approve or reject with a reason.
- Client portal: checklist of documents still needed, upload, status of each document.
- Reminder email previews for clients with missing documents.
- Activity history for every upload, extraction, correction and decision.
- FastAPI backend with SQLite, the same extraction rules as the browser demo, and tests for both.
- Live demo on GitHub Pages that runs entirely in the browser.

## Milestone 2: Real AI extraction and file handling ✅

Done:

- `LLMExtractor` implementing the existing `Extractor` protocol, for OpenAI (strict JSON schema) or Claude (forced tool call). Standard-library HTTP only, so no SDK lock-in.
- Every AI value must quote the document; unverified values are capped at 50% confidence.
- Rule-based checks re-run on the AI output, so arithmetic errors are still caught.
- `HybridExtractor`: runs AI and rules together and adjusts confidence by whether they agree.
- Photos and scanned PDFs read through the model's vision input, with a transcript stored for review.
- Automatic fallback to the rules extractor when the AI API is unavailable.
- Corrections re-run the checks, so fixing a VAT figure clears the warning.
- Config through environment variables, with clear errors at startup.
- The web app switches between the browser demo and the API from the top bar (or a `?api=` link).
- PDF and photo upload in the client portal through the API.
- Original files are stored and shown to the reviewer next to the extracted fields, with the text that was read one click away.

- PostgreSQL support alongside SQLite, chosen by `DATABASE_URL`, with the same queries. CI tests both.

Moved to milestone 4: an accuracy report that runs each extractor over a labelled sample set.

## Milestone 3: Accounts, security and reminders (in progress)

Done:

- Sign-in for clients, reviewers and admins: PBKDF2 password hashing, 12-hour bearer sessions stored only as hashes, a failed sign-in limit, and sign-out everywhere on a password reset or when a user is disabled.
- Per-client data isolation in one access-control layer. Client users only see their own business, and other clients' documents answer "not found".
- Short-lived signed links for original files.
- Reminder emails through SMTP, from the dashboard or `python -m app.manage send-reminders` on a schedule, at most once per client per week.
- Audit log export as CSV, safe to open in spreadsheet apps.
- Sign-in screen and role-based views in the web app.

Next:

- File storage in EU object storage (S3-compatible) with encryption at rest.
- User management screens in the web app (the API already supports it).

## Milestone 4: Production and integrations

- Docker images and a one-command deploy (Render, Fly.io or AWS).
- Monitoring, error tracking and backups.
- Accuracy dashboard: how often reviewers correct each field, per document type.
- Export approved data to CSV, and to accounting tools such as Xero or QuickBooks.
