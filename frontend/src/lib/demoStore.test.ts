import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ActionError,
  approve,
  checklist,
  correctField,
  createDemoState,
  ingest,
  reject,
  reminderPreviews,
  type DemoState,
} from './demoStore';
import type { Client, DocType } from './types';

const samplesDir = join(dirname(fileURLToPath(import.meta.url)), '../../../samples');
const files = Object.fromEntries(
  readdirSync(samplesDir)
    .filter((f) => f.endsWith('.txt'))
    .map((f) => [f, readFileSync(join(samplesDir, f), 'utf8')]),
);

const acme: Client = { id: 'cli_acme', name: 'Acme Ltd', contactEmail: 'acme@example.com', required: ['invoice', 'receipt'] as DocType[] };
const empty = (): DemoState => ({ clients: [acme], documents: [], activity: [] });

test('a clean invoice is ready to approve and logs upload + extraction', () => {
  const { state, doc } = ingest(empty(), 'cli_acme', 'inv.txt', files['invoice-brightline.txt'], 'owner@acme');
  assert.equal(doc.status, 'ready');
  assert.deepEqual(state.activity.map((a) => a.action), ['extracted', 'uploaded']);
});

test('missing required fields block approval until corrected', () => {
  const up = ingest(empty(), 'cli_acme', 'fuel.txt', files['receipt-faded.txt'], 'owner@acme');
  assert.equal(up.doc.status, 'needs_review');
  assert.throws(() => approve(up.state, up.doc.id, 'ana@firm'), ActionError);
  const fixed = correctField(up.state, up.doc.id, 'date', '2026-11-07', 'ana@firm');
  const field = fixed.documents[0].extraction.fields.find((f) => f.key === 'date');
  assert.equal(field?.confidence, 1);
  assert.equal(field?.source, 'Corrected by ana@firm');
  const done = approve(fixed, up.doc.id, 'ana@firm');
  assert.equal(done.documents[0].status, 'approved');
  assert.deepEqual(done.activity.map((a) => a.action).slice(0, 2), ['approved', 'corrected']);
});

test('rejecting needs a reason and reviewed documents are locked', () => {
  const up = ingest(empty(), 'cli_acme', 'cafe.txt', files['receipt-cafe.txt'], 'owner@acme');
  assert.throws(() => reject(up.state, up.doc.id, '  ', 'ana@firm'), ActionError);
  const rejected = reject(up.state, up.doc.id, 'Personal expense', 'ana@firm');
  assert.equal(rejected.documents[0].rejectReason, 'Personal expense');
  assert.throws(() => correctField(rejected, up.doc.id, 'total', '1.00', 'ana@firm'), ActionError);
});

test('unknown documents cannot be approved', () => {
  const up = ingest(empty(), 'cli_acme', 'letter.txt', files['letter-unknown.txt'], 'owner@acme');
  assert.throws(() => approve(up.state, up.doc.id, 'ana@firm'), ActionError);
});

test('checklist and reminders follow the documents', () => {
  let state = empty();
  assert.deepEqual(checklist(state, 'cli_acme').map((i) => i.state), ['missing', 'missing']);
  const up = ingest(state, 'cli_acme', 'inv.txt', files['invoice-brightline.txt'], 'owner@acme');
  state = approve(up.state, up.doc.id, 'ana@firm');
  assert.deepEqual(checklist(state, 'cli_acme').map((i) => i.state), ['approved', 'missing']);
  const previews = reminderPreviews(state);
  assert.equal(previews.length, 1);
  assert.deepEqual(previews[0].outstanding, ['receipt']);
});

test('the demo seeds two clients and seven documents, newest first', () => {
  const clients = [
    { ...acme, files: ['invoice-brightline.txt', 'receipt-faded.txt'] },
    { id: 'cli_b', name: 'B Ltd', contactEmail: 'b@example.com', required: ['payslip'] as DocType[], files: ['payslip-harbour.txt'] },
  ];
  const state = createDemoState(clients, files, new Date('2026-11-20T09:00:00Z'));
  assert.equal(state.clients.length, 2);
  assert.deepEqual(state.documents.map((d) => d.fileName), ['payslip-harbour.txt', 'receipt-faded.txt', 'invoice-brightline.txt']);
  assert.equal(state.documents[0].uploadedAt, '2026-11-20T09:21:00.000Z');
});
