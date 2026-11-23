/**
 * In-browser version of the backend's IntakeService (backend/app/service.py),
 * so the live demo works without a server. Every function is pure: it takes
 * the current state and returns a new one.
 */
import { extract, REVIEW_THRESHOLD, TYPE_LABELS } from './extract';
import type { ActivityEntry, Client, DocType, IntakeDocument } from './types';

export interface DemoState {
  clients: Client[];
  documents: IntakeDocument[];
  activity: ActivityEntry[];
}

export type ChecklistState = 'approved' | 'in_review' | 'rejected' | 'missing';

export interface ChecklistItem {
  docType: DocType;
  state: ChecklistState;
  documentIds: string[];
}

export interface ReminderPreview {
  clientId: string;
  to: string;
  subject: string;
  body: string;
  outstanding: DocType[];
}

export class ActionError extends Error {}

let counter = 0;
export function newId(prefix: string): string {
  counter += 1;
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);
  return `${prefix}_${random}${counter}`;
}

function log(state: DemoState, entry: Omit<ActivityEntry, 'id' | 'at'>, at: Date): DemoState {
  return { ...state, activity: [{ id: newId('act'), at: at.toISOString(), ...entry }, ...state.activity] };
}

function requireDoc(state: DemoState, id: string): IntakeDocument {
  const doc = state.documents.find((d) => d.id === id);
  if (!doc) throw new ActionError('Document not found.');
  return doc;
}

function replaceDoc(state: DemoState, doc: IntakeDocument): DemoState {
  return { ...state, documents: state.documents.map((d) => (d.id === doc.id ? doc : d)) };
}

export function ingest(
  state: DemoState,
  clientId: string,
  fileName: string,
  text: string,
  actor: string,
  at: Date = new Date(),
): { state: DemoState; doc: IntakeDocument } {
  if (!state.clients.some((c) => c.id === clientId)) throw new ActionError('Client not found.');
  if (!text.trim()) throw new ActionError('The document is empty.');
  const extraction = extract(text);
  const doc: IntakeDocument = {
    id: newId('doc'),
    clientId,
    fileName,
    text,
    uploadedAt: at.toISOString(),
    status: extraction.needsReview ? 'needs_review' : 'ready',
    extraction,
  };
  let next: DemoState = { ...state, documents: [doc, ...state.documents] };
  next = log(next, { actor, action: 'uploaded', documentId: doc.id, detail: fileName }, at);
  next = log(
    next,
    {
      actor: 'system',
      action: 'extracted',
      documentId: doc.id,
      detail: `${TYPE_LABELS[extraction.docType]} at ${Math.round(extraction.overallConfidence * 100)}% confidence${
        doc.status === 'needs_review' ? ', flagged for review' : ''
      }`,
    },
    at,
  );
  return { state: next, doc };
}

export function correctField(
  state: DemoState,
  documentId: string,
  key: string,
  value: string,
  actor: string,
  at: Date = new Date(),
): DemoState {
  const doc = requireDoc(state, documentId);
  if (doc.status === 'approved' || doc.status === 'rejected') throw new ActionError('This document has already been reviewed.');
  const target = doc.extraction.fields.find((f) => f.key === key);
  if (!target) throw new ActionError('Field not found.');
  const cleaned = value.trim() || null;
  if (cleaned === target.value) return state;
  const fields = doc.extraction.fields.map((f) =>
    f.key === key ? { ...f, value: cleaned, confidence: cleaned === null ? 0 : 1, source: `Corrected by ${actor}` } : f,
  );
  const lowConfidenceFields = fields.filter((f) => f.required && f.confidence < REVIEW_THRESHOLD).map((f) => f.key);
  const updated: IntakeDocument = { ...doc, extraction: { ...doc.extraction, fields, lowConfidenceFields } };
  return log(
    replaceDoc(state, updated),
    { actor, action: 'corrected', documentId, detail: `${target.label}: ${target.value ?? 'empty'} → ${cleaned ?? 'empty'}` },
    at,
  );
}

export function missingRequired(doc: IntakeDocument): string[] {
  return doc.extraction.fields.filter((f) => f.required && f.value === null).map((f) => f.label);
}

export function approve(state: DemoState, documentId: string, actor: string, at: Date = new Date()): DemoState {
  const doc = requireDoc(state, documentId);
  if (doc.status === 'approved') throw new ActionError('Already approved.');
  if (doc.extraction.docType === 'unknown') throw new ActionError('This is not a recognised document type. Reject it instead.');
  const missing = missingRequired(doc);
  if (missing.length) throw new ActionError(`Fill in the missing fields first: ${missing.join(', ')}.`);
  const updated: IntakeDocument = { ...doc, status: 'approved', reviewedBy: actor, reviewedAt: at.toISOString(), rejectReason: undefined };
  return log(replaceDoc(state, updated), { actor, action: 'approved', documentId }, at);
}

export function reject(state: DemoState, documentId: string, reason: string, actor: string, at: Date = new Date()): DemoState {
  const doc = requireDoc(state, documentId);
  if (doc.status === 'rejected') throw new ActionError('Already rejected.');
  const why = reason.trim();
  if (!why) throw new ActionError('Give a reason so the client knows what to resend.');
  const updated: IntakeDocument = { ...doc, status: 'rejected', reviewedBy: actor, reviewedAt: at.toISOString(), rejectReason: why };
  return log(replaceDoc(state, updated), { actor, action: 'rejected', documentId, detail: why }, at);
}

export function checklist(state: DemoState, clientId: string): ChecklistItem[] {
  const client = state.clients.find((c) => c.id === clientId);
  if (!client) return [];
  const docs = state.documents.filter((d) => d.clientId === clientId);
  return client.required.map((docType) => {
    const ofType = docs.filter((d) => d.extraction.docType === docType);
    const stateFor: ChecklistState = ofType.some((d) => d.status === 'approved')
      ? 'approved'
      : ofType.some((d) => d.status === 'needs_review' || d.status === 'ready')
        ? 'in_review'
        : ofType.length
          ? 'rejected'
          : 'missing';
    return { docType, state: stateFor, documentIds: ofType.map((d) => d.id) };
  });
}

export function reminderPreviews(state: DemoState): ReminderPreview[] {
  return state.clients.flatMap((client) => {
    const outstanding = checklist(state, client.id)
      .filter((i) => i.state === 'missing' || i.state === 'rejected')
      .map((i) => i.docType);
    if (!outstanding.length) return [];
    const names = outstanding.map((t) => TYPE_LABELS[t].toLowerCase()).join(', ');
    return [
      {
        clientId: client.id,
        to: client.contactEmail,
        subject: 'Documents still needed for your bookkeeping',
        body: `Hi ${client.name}, we are still waiting for: ${names}. Please upload them through your client portal.`,
        outstanding,
      },
    ];
  });
}

/** Builds the starting demo: clients plus their sample files, uploaded a few minutes apart. */
export function createDemoState(
  clients: Array<Client & { files: string[] }>,
  files: Record<string, string>,
  start: Date = new Date(Date.now() - 1000 * 60 * 90),
): DemoState {
  let state: DemoState = {
    clients: clients.map((c) => ({ id: c.id, name: c.name, contactEmail: c.contactEmail, required: c.required })),
    documents: [],
    activity: [],
  };
  let minutes = 0;
  for (const client of clients) {
    for (const name of client.files) {
      const text = files[name];
      if (text === undefined) continue;
      minutes += 7;
      state = ingest(state, client.id, name, text, client.contactEmail, new Date(start.getTime() + minutes * 60_000)).state;
    }
  }
  return state;
}
