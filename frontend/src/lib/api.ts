/**
 * Client for the FastAPI backend (backend/app/main.py). Converts the API's
 * snake_case JSON into the same types the browser demo uses, so every screen
 * works the same in demo mode and API mode.
 */
import type { DemoState } from './demoStore';
import type { ActivityEntry, Client, DocStatus, DocType, Extraction, IntakeDocument } from './types';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

type Json = Record<string, unknown>;
type Fetch = typeof fetch;

const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const optStr = (v: unknown): string | undefined => (typeof v === 'string' && v ? v : undefined);
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

export function toExtraction(raw: Json): Extraction {
  const fields = Array.isArray(raw.fields) ? (raw.fields as Json[]) : [];
  const checks = Array.isArray(raw.checks) ? (raw.checks as Json[]) : [];
  return {
    docType: (str(raw.doc_type) || 'unknown') as DocType,
    typeConfidence: num(raw.type_confidence),
    fields: fields.map((f) => ({
      key: str(f.key),
      label: str(f.label),
      value: typeof f.value === 'string' ? f.value : null,
      confidence: num(f.confidence),
      source: typeof f.source === 'string' ? f.source : null,
      required: f.required !== false,
    })),
    checks: checks.map((c) => ({ id: str(c.id), label: str(c.label), passed: c.passed === true, detail: str(c.detail) })),
    overallConfidence: num(raw.overall_confidence),
    needsReview: raw.needs_review !== false,
    lowConfidenceFields: Array.isArray(raw.low_confidence_fields) ? (raw.low_confidence_fields as string[]) : [],
  };
}

export function toDocument(raw: Json, baseUrl: string): IntakeDocument {
  const id = str(raw.id);
  const mediaType = optStr(raw.file_media_type);
  return {
    id,
    clientId: str(raw.client_id),
    fileName: str(raw.file_name),
    text: str(raw.text),
    uploadedAt: str(raw.uploaded_at),
    status: str(raw.status) as DocStatus,
    extraction: toExtraction((raw.extraction ?? {}) as Json),
    reviewedBy: optStr(raw.reviewed_by),
    reviewedAt: optStr(raw.reviewed_at),
    rejectReason: optStr(raw.reject_reason),
    extractor: optStr(raw.extractor),
    file: mediaType ? { mediaType, url: `${baseUrl}/documents/${encodeURIComponent(id)}/file` } : undefined,
  };
}

export function toClient(raw: Json): Client {
  return {
    id: str(raw.id),
    name: str(raw.name),
    contactEmail: str(raw.contact_email),
    required: (Array.isArray(raw.required_types) ? raw.required_types : []) as DocType[],
  };
}

export function toActivity(raw: Json): ActivityEntry {
  return {
    id: str(raw.id),
    at: str(raw.at),
    actor: str(raw.actor),
    action: str(raw.action),
    documentId: optStr(raw.document_id),
    detail: optStr(raw.detail),
  };
}

/** Trims spaces and trailing slashes, and checks the URL is http(s). */
export function normaliseBaseUrl(url: string): string {
  const trimmed = url.trim().replace(/\/+$/, '');
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new ApiError('Enter the full API address, for example http://localhost:8000', 0);
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new ApiError('The API address must start with http:// or https://', 0);
  }
  return trimmed;
}

export class ApiClient {
  readonly baseUrl: string;

  constructor(
    baseUrl: string,
    private readonly fetchImpl: Fetch = (...args) => fetch(...args),
  ) {
    this.baseUrl = normaliseBaseUrl(baseUrl);
  }

  private async request<T>(path: string, init: RequestInit = {}, actor?: string): Promise<T> {
    const headers = new Headers(init.headers);
    if (actor) headers.set('X-User', actor);
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.baseUrl}${path}`, { ...init, headers });
    } catch {
      throw new ApiError(`Can't reach the API at ${this.baseUrl}. Is it running, and does CORS_ORIGINS include this page?`, 0);
    }
    if (!res.ok) {
      let message = `The API returned an error (${res.status}).`;
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body.detail === 'string') message = body.detail;
        else if (Array.isArray(body.detail)) message = 'Some of the details sent were not valid.';
      } catch {
        // Not JSON: keep the generic message.
      }
      throw new ApiError(message, res.status);
    }
    return (await res.json()) as T;
  }

  private json(method: string, body: unknown): RequestInit {
    return { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) };
  }

  async health(): Promise<{ status: string; extractor: string }> {
    return this.request('/health');
  }

  async loadState(): Promise<DemoState> {
    const [clients, documents, activity] = await Promise.all([
      this.request<Json[]>('/clients'),
      this.request<Json[]>('/documents'),
      this.request<Json[]>('/activity?limit=500'),
    ]);
    return {
      clients: clients.map(toClient),
      documents: documents.map((d) => toDocument(d, this.baseUrl)),
      activity: activity.map(toActivity),
    };
  }

  async upload(clientId: string, file: File, actor: string): Promise<IntakeDocument> {
    const form = new FormData();
    form.append('client_id', clientId);
    form.append('file', file, file.name);
    return toDocument(await this.request<Json>('/documents', { method: 'POST', body: form }, actor), this.baseUrl);
  }

  async correctField(documentId: string, key: string, value: string, actor: string): Promise<IntakeDocument> {
    const path = `/documents/${encodeURIComponent(documentId)}/fields/${encodeURIComponent(key)}`;
    return toDocument(await this.request<Json>(path, this.json('PATCH', { value }), actor), this.baseUrl);
  }

  async approve(documentId: string, actor: string): Promise<IntakeDocument> {
    const path = `/documents/${encodeURIComponent(documentId)}/approve`;
    return toDocument(await this.request<Json>(path, { method: 'POST' }, actor), this.baseUrl);
  }

  async reject(documentId: string, reason: string, actor: string): Promise<IntakeDocument> {
    const path = `/documents/${encodeURIComponent(documentId)}/reject`;
    return toDocument(await this.request<Json>(path, this.json('POST', { reason }), actor), this.baseUrl);
  }
}
