# AI Document Intake

**Turn piles of invoices, receipts, payslips and bank statements into checked, structured data, with a person approving every record.**

Clients upload their documents. The system works out what each one is, pulls out the key fields with a confidence score for each, and runs arithmetic checks. A reviewer then sees the original document next to the extracted data, fixes anything flagged and approves it. Every step is logged.

**[▶ Try the live demo](https://rajuerladinny.github.io/ai-document-intake/)**. No sign-up: it runs in your browser with sample documents.

![Review screen: the original invoice on the left, extracted fields with confidence scores and checks on the right](docs/screenshots/review-document.png)

## The problem it solves

Accounting firms, insurers, lenders and back offices still retype numbers from documents by hand. It is slow, and the mistakes are expensive. Fully automatic extraction isn't trusted either, because nobody can see why a number was read the way it was.

This project takes a human-in-the-loop approach:

- **Automate the reading, not the decision.** The system does the typing; a person approves.
- **Show the evidence.** Every field links back to the exact line it came from.
- **Flag what needs attention.** Low-confidence fields and failed checks rise to the top of the queue, so reviewers spend their time on the documents that actually need it.

## Features

| | |
|---|---|
| **Classification** | Invoice, receipt, payslip, bank statement, or unknown (with a confidence score). |
| **Field extraction** | Supplier, invoice number, dates, net, VAT, total, pay figures, balances and more. Each field has a confidence score and the source line. |
| **Validation checks** | Net + VAT = total · VAT matches the stated rate · receipt items add up · gross − deductions = net pay · bank running balances reconcile to the closing balance. |
| **Review queue** | Sorted by risk: flagged documents first, then lowest confidence. Filters for flagged, ready, approved and rejected. |
| **Side-by-side review** | Hover a field to highlight where it came from in the document. Correct a value inline; the correction is recorded with who made it. |
| **Approve / reject** | Approval is blocked while required fields are missing. A rejection needs a reason, which the client sees. |
| **Client portal** | A checklist of documents still needed this month, upload, and the status of each document. |
| **Reminders** | Reminder emails to every client with missing documents, sent from the dashboard or on a schedule, at most once a week per client. |
| **Activity history** | Every upload, extraction, correction, approval and rejection, with time and person. |
| **AI extraction (API)** | Optional OpenAI or Claude extractor, including photos and scanned PDFs. Every AI value must quote the document line it came from, or it is flagged. The same arithmetic checks run on the AI output, and **hybrid** mode cross-checks the AI against the rules. |
| **Accounts and roles (API)** | Sign-in for admins, reviewers and clients. Clients only ever see their own business; other clients' documents answer "not found". Original files use short-lived signed links. Admins add and disable users from a Users screen. |
| **Audit export (API)** | Download the full activity log as CSV for compliance, safe to open in Excel. |
| **Privacy by default** | Extracted data keeps only the last 4 digits of bank account numbers, and account and National Insurance numbers are masked in the document viewer. |

<table>
  <tr>
    <td><img src="docs/screenshots/review-queue.png" alt="Review queue with confidence bars and status badges" /></td>
    <td><img src="docs/screenshots/client-portal.png" alt="Client portal with document checklist and upload" /></td>
  </tr>
  <tr>
    <td align="center"><sub>Review queue</sub></td>
    <td align="center"><sub>Client portal</sub></td>
  </tr>
</table>

## How it works

```mermaid
flowchart LR
    A[Client uploads<br/>document] --> B[Read text<br/>.txt / PDF / photo]
    B --> C[Classify<br/>document type]
    C --> D[Extract fields<br/>+ confidence + source line]
    D --> E[Validation checks]
    E -->|all good| F[Ready to approve]
    E -->|low confidence or failed check| G[Flagged for review]
    F --> H[Reviewer approves<br/>or corrects]
    G --> H
    H --> I[Approved data<br/>+ activity history]
```

The extractor sits behind a small interface (`Extractor` in `backend/app/extraction.py`). The rule-based one is fast, free and predictable. The AI one (OpenAI or Claude, in `backend/app/llm.py`) plugs in behind the same interface, so the confidence scores, checks, API and review screen stay the same, and the rule-based checks keep catching arithmetic errors in the AI's output.

<table>
  <tr>
    <td><img src="docs/screenshots/portal-upload-photo.png" alt="Client portal with a receipt photo ready to send" /></td>
    <td><img src="docs/screenshots/review-photo-api.png" alt="Reviewing a receipt photo: the original image beside the fields the AI read" /></td>
  </tr>
  <tr>
    <td align="center"><sub>API mode: a client sends a photo of a receipt</sub></td>
    <td align="center"><sub>The reviewer sees the photo next to what the AI read</sub></td>
  </tr>
</table>

<table>
  <tr>
    <td><img src="docs/screenshots/sign-in.png" alt="Sign-in screen with one-click demo roles" /></td>
    <td><img src="docs/screenshots/admin-reminders.png" alt="Admin sending reminder emails to clients with missing documents" /></td>
  </tr>
  <tr>
    <td align="center"><sub>Sign-in, with one-click demo roles on a demo server</sub></td>
    <td align="center"><sub>Admin sends reminders; each send is logged</sub></td>
  </tr>
</table>

<p align="center"><img src="docs/screenshots/users.png" alt="Admin Users screen: add a user with a role, disable access" width="80%" /><br /><sub>Admins add clients, reviewers and admins, and can disable access</sub></p>

## Tech stack

- **Frontend:** React 19, TypeScript (strict), esbuild, hand-written CSS (no UI framework), fully responsive.
- **Backend:** Python, FastAPI, PostgreSQL or SQLite (same queries, chosen by `DATABASE_URL`), pypdf for PDF text.
- **Testing:** Node's test runner for the TypeScript logic, pytest for the backend. Both extraction engines are checked against the same expected results in `samples/expected.json`, so the browser demo and the API always agree.
- **CI/CD:** GitHub Actions runs type checks, tests and the build on every push and deploys the demo to GitHub Pages.

## Project layout

```
ai-document-intake/
├── frontend/                 React review dashboard and client portal
│   ├── src/lib/extract.ts    Classification, extraction, confidence, checks
│   ├── src/lib/demoStore.ts  In-browser workflow (mirrors the backend service)
│   ├── src/lib/api.ts        API client used in API mode
│   └── src/components/       Review queue, review screen, portal, clients, activity
├── backend/
│   ├── app/extraction.py     Same extraction rules in Python + Extractor interface
│   ├── app/llm.py            OpenAI / Claude extractor, hybrid mode, config
│   ├── app/service.py        Intake, correction, approval, checklist, reminders
│   ├── app/store.py          PostgreSQL / SQLite persistence
│   ├── app/auth.py           Passwords, sessions, signed file links
│   ├── app/access.py         Who may do what (roles, per-client isolation)
│   ├── app/notify.py         Reminder emails (SMTP) and audit CSV
│   ├── app/storage.py        File storage: database, local folder or S3 (EU)
│   ├── app/manage.py         Admin commands: create-user, send-reminders
│   ├── app/main.py           FastAPI routes
│   └── tests/
├── samples/                  Sample documents + expected extraction results
└── docs/                     Roadmap and screenshots
```

## Run it locally

**Frontend demo** (Node 20+):

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm test           # extraction and workflow tests
npm run typecheck
```

**API** (Python 3.11+):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload     # http://localhost:8000/docs
pytest
```

The API starts with the demo clients and sample documents loaded (always read with the free rules extractor). Set `SEED_DEMO=0` to start empty and `CORS_ORIGINS` to allow your frontend's URL.

**Database:** `DATABASE_URL` picks it. Leave it unset for in-memory SQLite (resets on restart), set a file path such as `intake.db` for SQLite on disk, or a PostgreSQL URL for real use:

```bash
docker run -d --name intake-db -e POSTGRES_USER=intake -e POSTGRES_PASSWORD=change-me -e POSTGRES_DB=intake -p 5432:5432 postgres:16
DATABASE_URL=postgresql://intake:change-me@localhost:5432/intake uvicorn app.main:app --reload
```

Tables are created on first start. CI runs the store, service and AI test suites against PostgreSQL 16 as well as SQLite.

**Use the web app with the API:** start both, then click **Demo data** in the top bar, choose **Connect to the API** and enter `http://localhost:8000`. You can also open `http://localhost:5173/?api=http://localhost:8000`. In API mode clients can upload PDFs and photos, reviewers see the original file next to the extracted fields, and everything is stored by the API. The choice is remembered; switch back to the browser demo at any time.

### Choosing the extractor

The API reads documents with one of three extractors, set by environment variables (see [`backend/.env.example`](backend/.env.example)):

| `EXTRACTOR` | What it does | Reads photos and scans |
|---|---|---|
| `rules` (default) | Rule-based extraction. Free, offline, the same logic as the browser demo. | No |
| `llm` | OpenAI or Claude (`LLM_PROVIDER`, `LLM_MODEL`, API key) with a strict JSON schema. | Yes |
| `hybrid` | Runs both on text documents. Agreement raises confidence; disagreement caps it at 60% and shows the reviewer both readings. | Yes |

The AI is never trusted on its own word:

1. **Evidence required.** Each value comes with a quote from the document. If the quote is not actually in the document, confidence is capped at 50% and the field goes to review.
2. **Same checks.** Net + VAT = total and the other checks are recalculated on the AI's numbers, so a misread figure is still caught.
3. **Normalised output.** Amounts, dates, currencies and masked account numbers come out in the same format whichever extractor ran.
4. **Graceful failure.** If the AI API is down, text documents fall back to the rules extractor (marked "AI unavailable"); photos return `503` so the client can retry.

For photos and scans, the model also returns a transcript. Fields are checked against that transcript, which is stored as the document text so the reviewer can see exactly what the model read.

### Accounts, roles and security

With the API, everyone signs in (`AUTH_REQUIRED=1`, the default):

| Role | Can |
|---|---|
| **Client** | Upload, see their own checklist, documents and history. Nothing from other clients. |
| **Reviewer** | Every client: review, correct, approve, reject, activity, reminder previews. |
| **Admin** | Reviewer rights plus users, clients, sending reminders and audit export. |

- Passwords are hashed with PBKDF2-SHA256 (310,000 rounds, per-user salt). They need 10+ characters, mixed case and a number.
- Sessions are random bearer tokens that last 12 hours. Only a SHA-256 of each token is stored. Signing out, a password reset or disabling a user ends their sessions.
- After 5 wrong passwords, sign-in for that email is paused for 15 minutes. Unknown emails get the same answer and timing as wrong passwords.
- Original files are fetched with signed links that expire after 5 minutes, so images and PDFs load without exposing a token.
- The web app keeps the session in `sessionStorage`, so it ends when the tab closes.

Create the first admin on a new server:

```bash
python -m app.manage create-user --email you@yourfirm.com --name "Your Name" --role admin
```

With `SEED_DEMO=1`, demo accounts for each role are created and shown on the sign-in screen (password `DemoPass2026`, or `DEMO_PASSWORD`). **Never use `SEED_DEMO=1` on a server with real data.** Set `SECRET_KEY` so file links survive restarts. `AUTH_REQUIRED=0` turns sign-in off for local demos only.

### Where original files are stored

`FILE_STORAGE` picks it:

| Value | Where | Settings |
|---|---|---|
| `database` (default) | In the database, next to the extracted data | none |
| `local` | A folder on the server | `FILE_STORAGE_DIR` |
| `s3` | Any S3-compatible bucket: AWS S3 in an EU region (`eu-west-2` London, `eu-central-1` Frankfurt), Cloudflare R2, Hetzner, Scaleway, MinIO | `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, optional `S3_ENDPOINT`, `S3_PREFIX` |

- S3 objects are written with server-side encryption (AES-256). The bucket should block all public access, because files reach users only through the API's signed links.
- The S3 client signs requests itself (AWS Signature Version 4, checked against AWS's published test signatures), so no SDK is needed.
- Files already stored in the database keep working after you switch.
- The file is saved before the document is created, so a storage outage never leaves a half-saved document.

### Reminder emails

`POST /reminders/send` (admin) or the **Send reminders** button emails every client with missing documents, skipping anyone reminded in the last 7 days. Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` and `SMTP_FROM` to use any SMTP provider. Without them, emails are written to the log. To send on a schedule, run this from cron or your host's scheduler:

```bash
python -m app.manage send-reminders            # --dry-run to preview
```

### API

| Method | Path | What it does |
|---|---|---|
| `GET` | `/health` | Status, extractor, database and whether sign-in is required |
| `POST` | `/auth/login` · `/auth/logout` · `GET /auth/me` | Sign in (returns a bearer token), sign out, current user |
| `GET` / `POST` | `/clients` | List or create clients and the document types they must send |
| `GET` | `/clients/{id}/checklist` | Approved / in review / missing, per required document type |
| `GET` | `/documents?client_id=&status=` | List documents |
| `POST` | `/documents` | Upload `.txt`, `.pdf`, or (with the AI extractor) `.jpg` / `.png` / `.webp` / scanned PDF (multipart: `client_id`, `file`) |
| `GET` | `/documents/{id}` | Document with extracted fields, confidence and checks |
| `PATCH` | `/documents/{id}/fields/{key}` | Correct a field (`{"value": "..."}`) |
| `POST` | `/documents/{id}/approve` | Approve (409 if required fields are missing) |
| `POST` | `/documents/{id}/reject` | Reject with a reason |
| `GET` | `/activity?document_id=` | Activity history |
| `GET` | `/documents/{id}/file?expires=&sig=` | Original upload, through the signed link included with each document |
| `GET` | `/reminders/preview` · `POST /reminders/send` | Reminder emails that would go out today · send them (admin) |
| `GET` | `/audit/export?since=&until=` | Activity log as CSV (admin) |
| `GET` / `POST` | `/users` · `POST /users/{id}/disable` | Manage sign-ins (admin; also on the web app's Users screen) |

Every endpoint except `/health`, `/auth/login` and signed file links needs `Authorization: Bearer <token>`. Changes are recorded under the signed-in user.

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| 1. Intake and review workflow | Extraction with confidence and checks, review dashboard, client portal, reminders preview, activity history, API, tests, live demo | ✅ Done |
| 2. Real AI extraction | OpenAI / Claude extractor with schema-validated JSON, quote verification, hybrid cross-check, photos and scans, API mode in the web app with PDF and photo upload, PostgreSQL | ✅ Done |
| 3. Accounts and security | Sign-in and roles, per-client data isolation, signed file links, reminder emails (manual and scheduled), audit export, EU object storage, user management | ✅ Done |
| 4. Production | Docker, deploy, monitoring, accuracy dashboard, export to CSV / Xero / QuickBooks | Planned |

Details are in [docs/ROADMAP.md](docs/ROADMAP.md).

## Current limitations

- The rules extractor is tuned to UK-style documents in English. Unusual layouts get a low confidence score and go to review; they are not silently accepted. The AI extractor handles other layouts but costs per document.
- The hosted demo runs in the browser with the rules extractor and text files. PDFs, photos and AI extraction need the API running (see above).
- With the AI extractor, document content is sent to the provider you choose. Check their data-retention terms (and use an EU endpoint if needed) before using real client data.
- Sessions and the failed-sign-in limit live in one API process. Running several API processes needs a shared limit (milestone 4).
- Get an independent security review before handling real client data.

## License

MIT
