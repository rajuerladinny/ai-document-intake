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
  // With sign-in on, the API adds a short-lived signed query for the original file.
  const fileQuery = optStr(raw.file_query);
  const fileUrl = `${baseUrl}/documents/${encodeURIComponent(id)}/file${fileQuery ? `?${fileQuery}` : ''}`;
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
    file: mediaType ? { mediaType, url: fileUrl } : undefined,
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

export type Role = 'admin' | 'reviewer' | 'client';

export interface SessionUser {
  id: string;
  email: string;
  name: string;
  role: Role;
  clientId: string | null;
}

export interface Health {
  status: string;
  extractor: string;
  database?: string;
  authRequired: boolean;
  demoAccounts?: { password: string; emails: string[] };
}

export interface ReminderResult {
  clientId: string;
  to: string;
  status: 'sent' | 'skipped' | 'failed' | 'would_send';
  reason?: string;
}

export interface ManagedUser extends SessionUser {
  createdAt: string;
  disabled: boolean;
}

export interface NewUser {
  email: string;
  name: string;
  role: Role;
  password: string;
  clientId: string | null;
}

export function toUser(raw: Json): SessionUser {
  const role = str(raw.role);
  return {
    id: str(raw.id),
    email: str(raw.email),
    name: str(raw.name),
    role: (role === 'admin' || role === 'reviewer' ? role : 'client') as Role,
    clientId: optStr(raw.client_id) ?? null,
  };
}

export class ApiClient {
  readonly baseUrl: string;

  constructor(
    baseUrl: string,
    private readonly fetchImpl: Fetch = (...args) => fetch(...args),
    /** Bearer token from sign-in, when the API requires it. */
    private readonly token: string | null = null,
  ) {
    this.baseUrl = normaliseBaseUrl(baseUrl);
  }

  /** The same API, signed in with this token. */
  withToken(token: string | null): ApiClient {
    return new ApiClient(this.baseUrl, this.fetchImpl, token);
  }

  private async raw(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    if (this.token) headers.set('Authorization', `Bearer ${this.token}`);
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.baseUrl}${path}`, { ...init, headers });
    } catch {
      throw new ApiError(`Can't reach the API at ${this.baseUrl}. Is it running, and does CORS_ORIGINS include this page?`, 0);
    }
    if (!res.ok) {
      let message = res.status === 403 ? "You don't have permission to do that." : `The API returned an error (${res.status}).`;
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body.detail === 'string') message = body.detail;
        else if (Array.isArray(body.detail)) {
          const first = body.detail[0] as { loc?: unknown[]; msg?: unknown } | undefined;
          const field = Array.isArray(first?.loc) ? String(first?.loc[first.loc.length - 1] ?? '') : '';
          message = field ? `Check the ${field.replace(/_/g, ' ')}: ${String(first?.msg ?? 'not valid')}.` : 'Some of the details sent were not valid.';
        }
      } catch {
        // Not JSON: keep the generic message.
      }
      throw new ApiError(message, res.status);
    }
    return res;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await this.raw(path, init);
    return (await res.json()) as T;
  }

  private json(method: string, body: unknown): RequestInit {
    return { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) };
  }

  async health(): Promise<Health> {
    const raw = await this.request<Json>('/health');
    const demo = raw.demo_accounts as { password?: unknown; emails?: unknown } | undefined;
    return {
      status: str(raw.status),
      extractor: str(raw.extractor),
      database: optStr(raw.database),
      // Older API versions had no sign-in.
      authRequired: raw.auth_required === true,
      demoAccounts:
        demo && typeof demo.password === 'string' && Array.isArray(demo.emails)
          ? { password: demo.password, emails: demo.emails.filter((e): e is string => typeof e === 'string') }
          : undefined,
    };
  }

  async login(email: string, password: string): Promise<{ token: string; user: SessionUser }> {
    const raw = await this.request<Json>('/auth/login', this.json('POST', { email, password }));
    return { token: str(raw.token), user: toUser((raw.user ?? {}) as Json) };
  }

  async logout(): Promise<void> {
    await this.raw('/auth/logout', { method: 'POST' });
  }

  async me(): Promise<SessionUser> {
    return toUser(await this.request<Json>('/auth/me'));
  }

  async listUsers(): Promise<ManagedUser[]> {
    const rows = await this.request<Json[]>('/users');
    return rows.map((r) => ({ ...toUser(r), createdAt: str(r.created_at), disabled: r.disabled === true }));
  }

  async createUser(user: NewUser): Promise<SessionUser> {
    const body = {
      email: user.email,
      name: user.name,
      role: user.role,
      password: user.password,
      client_id: user.role === 'client' ? user.clientId : null,
    };
    return toUser(await this.request<Json>('/users', this.json('POST', body)));
  }

  async disableUser(userId: string): Promise<void> {
    await this.raw(`/users/${encodeURIComponent(userId)}/disable`, { method: 'POST' });
  }

  async auditCsv(): Promise<string> {
    return (await this.raw('/audit/export')).text();
  }

  async sendReminders(dryRun = false): Promise<ReminderResult[]> {
    const raw = await this.request<{ results?: Json[] }>('/reminders/send', this.json('POST', { dry_run: dryRun }));
    return (raw.results ?? []).map((r) => ({
      clientId: str(r.client_id),
      to: str(r.to),
      status: str(r.status) as ReminderResult['status'],
      reason: optStr(r.reason),
    }));
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

  // The API records who acted from the signed-in user, so no actor is sent.

  async upload(clientId: string, file: File): Promise<IntakeDocument> {
    const form = new FormData();
    form.append('client_id', clientId);
    form.append('file', file, file.name);
    return toDocument(await this.request<Json>('/documents', { method: 'POST', body: form }), this.baseUrl);
  }

  async correctField(documentId: string, key: string, value: string): Promise<IntakeDocument> {
    const path = `/documents/${encodeURIComponent(documentId)}/fields/${encodeURIComponent(key)}`;
    return toDocument(await this.request<Json>(path, this.json('PATCH', { value })), this.baseUrl);
  }

  async approve(documentId: string): Promise<IntakeDocument> {
    const path = `/documents/${encodeURIComponent(documentId)}/approve`;
    return toDocument(await this.request<Json>(path, { method: 'POST' }), this.baseUrl);
  }

  async reject(documentId: string, reason: string): Promise<IntakeDocument> {
    const path = `/documents/${encodeURIComponent(documentId)}/reject`;
    return toDocument(await this.request<Json>(path, this.json('POST', { reason })), this.baseUrl);
  }
}
