import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ApiClient, ApiError, normaliseBaseUrl, toDocument } from './api';
import { checkUpload, initialConnection, saveConnection, sourceMatchesLine, CONNECTION_KEY } from './connection';
import { checklist } from './demoStore';

const apiDoc = {
  id: 'doc_1',
  client_id: 'cli_harbour',
  file_name: 'receipt.jpg',
  text: 'CAFE\nTotal £4.50',
  uploaded_at: '2026-11-22T10:00:00+00:00',
  status: 'needs_review',
  extractor: 'llm:openai/m',
  reviewed_by: null,
  reviewed_at: null,
  reject_reason: null,
  file_media_type: 'image/jpeg',
  extraction: {
    doc_type: 'receipt',
    type_confidence: 0.9,
    overall_confidence: 0.5,
    needs_review: true,
    low_confidence_fields: ['date'],
    fields: [
      { key: 'merchant', label: 'Merchant', value: 'Cafe', confidence: 0.9, source: 'CAFE', required: true, original_value: 'x' },
      { key: 'date', label: 'Date', value: null, confidence: 0, source: null, required: true },
    ],
    checks: [{ id: 'items_sum', label: 'Items add up', passed: false, detail: 'Items 4.00, total 4.50' }],
  },
};

type Call = { url: string; init: RequestInit };

function fakeFetch(routes: Record<string, { status?: number; body: unknown }>, calls: Call[] = []) {
  return (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    calls.push({ url, init });
    const key = Object.keys(routes).find((k) => url.endsWith(k));
    if (!key) throw new TypeError('Failed to fetch');
    const { status = 200, body } = routes[key];
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  }) as typeof fetch;
}

test('API documents map onto the demo types, with a link to the original file', () => {
  const doc = toDocument(apiDoc, 'http://localhost:8000');
  assert.equal(doc.clientId, 'cli_harbour');
  assert.equal(doc.extraction.docType, 'receipt');
  assert.equal(doc.extraction.fields[1].value, null);
  assert.deepEqual(doc.extraction.lowConfidenceFields, ['date']);
  assert.equal(doc.reviewedBy, undefined);
  assert.equal(doc.extractor, 'llm:openai/m');
  assert.deepEqual(doc.file, { mediaType: 'image/jpeg', url: 'http://localhost:8000/documents/doc_1/file' });
});

test('loadState builds a state the existing screens can use', async () => {
  const api = new ApiClient(
    'http://localhost:8000/',
    fakeFetch({
      '/clients': { body: [{ id: 'cli_harbour', name: 'Harbour', contact_email: 'h@x.com', required_types: ['receipt', 'invoice'] }] },
      '/documents': { body: [apiDoc] },
      '/activity?limit=500': { body: [{ id: 'act_1', at: '2026-11-22T10:00:00+00:00', actor: 'system', action: 'extracted', document_id: 'doc_1', detail: null }] },
    }),
  );
  const state = await api.loadState();
  assert.equal(state.clients[0].contactEmail, 'h@x.com');
  assert.equal(state.activity[0].documentId, 'doc_1');
  assert.deepEqual(
    checklist(state, 'cli_harbour').map((i) => i.state),
    ['in_review', 'missing'],
  );
});

test('actions send the reviewer and the right method and body', async () => {
  const calls: Call[] = [];
  const api = new ApiClient(
    'http://api.test',
    fakeFetch({ '/fields/vat_amount': { body: apiDoc }, '/approve': { body: apiDoc }, '/reject': { body: apiDoc }, '/documents': { body: apiDoc } }, calls),
  );
  await api.correctField('doc_1', 'vat_amount', '89.60', 'ana@demo');
  await api.approve('doc_1', 'ana@demo');
  await api.reject('doc_1', 'Blurry', 'ana@demo');
  await api.upload('cli_harbour', new File(['Total £1.00'], 'r.txt', { type: 'text/plain' }), 'client@x');

  assert.equal(calls[0].init.method, 'PATCH');
  assert.equal(calls[0].init.body, JSON.stringify({ value: '89.60' }));
  assert.equal(new Headers(calls[0].init.headers).get('X-User'), 'ana@demo');
  assert.ok(calls[1].url.endsWith('/documents/doc_1/approve'));
  assert.equal(calls[2].init.body, JSON.stringify({ reason: 'Blurry' }));
  const form = calls[3].init.body as FormData;
  assert.equal(form.get('client_id'), 'cli_harbour');
  assert.equal((form.get('file') as File).name, 'r.txt');
});

test('API error details reach the user', async () => {
  const api = new ApiClient('http://api.test', fakeFetch({ '/approve': { status: 409, body: { detail: 'Fill in the required fields first: Date.' } } }));
  await assert.rejects(api.approve('doc_1', 'ana'), (e: unknown) => e instanceof ApiError && e.status === 409 && /required fields/.test(e.message));
});

test('an unreachable API gives a helpful message', async () => {
  const api = new ApiClient('http://api.test', fakeFetch({}));
  await assert.rejects(api.health(), /Can't reach the API at http:\/\/api\.test/);
});

test('API addresses are checked', () => {
  assert.equal(normaliseBaseUrl(' http://localhost:8000// '), 'http://localhost:8000');
  assert.throws(() => normaliseBaseUrl('localhost:8000'), ApiError);
  assert.throws(() => normaliseBaseUrl('javascript:alert(1)'), ApiError);
});

test('connection comes from the query, then storage, then defaults to demo', () => {
  const store = new Map<string, string>();
  const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
  assert.deepEqual(initialConnection('', storage), { mode: 'demo' });
  saveConnection({ mode: 'api', url: 'http://localhost:8000' }, storage);
  assert.ok(store.has(CONNECTION_KEY));
  assert.deepEqual(initialConnection('', storage), { mode: 'api', url: 'http://localhost:8000' });
  assert.deepEqual(initialConnection('?api=https://intake.example.com', storage), { mode: 'api', url: 'https://intake.example.com' });
  store.set(CONNECTION_KEY, '{broken');
  assert.deepEqual(initialConnection('', storage), { mode: 'demo' });
  assert.deepEqual(initialConnection('', null), { mode: 'demo' });
});

test('upload rules differ between demo and API mode', () => {
  assert.match(checkUpload('scan.pdf', 100, 'demo') ?? '', /Connect to the API/);
  assert.equal(checkUpload('scan.pdf', 100, 'api'), null);
  assert.equal(checkUpload('photo.JPG', 100, 'api'), null);
  assert.match(checkUpload('photo.heic', 100, 'api') ?? '', /HEIC/);
  assert.match(checkUpload('a.txt', 2 * 1024 * 1024, 'demo') ?? '', /1 MB/);
  assert.equal(checkUpload('a.txt', 2 * 1024 * 1024, 'api'), null);
  assert.match(checkUpload('a.txt', 0, 'api') ?? '', /empty/);
});

test('AI quotes and hybrid sources highlight the right line', () => {
  assert.ok(sourceMatchesLine('VAT 20%                                      £86.60', 'VAT 20%  £86.60'));
  assert.ok(sourceMatchesLine('Invoice number: KL-7782', '  Invoice number: KL-7782'));
  assert.ok(sourceMatchesLine('KL-7782 [rules read: KL-7783]', 'Invoice number: KL-7782'));
  assert.ok(!sourceMatchesLine('Total', ''));
  assert.ok(!sourceMatchesLine(null, 'Total'));
  assert.ok(!sourceMatchesLine('£86.60', 'Net amount £448.00'));
});
