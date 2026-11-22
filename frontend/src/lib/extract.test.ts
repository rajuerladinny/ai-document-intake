import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { classify, extract, moneyOnLine, parseDate } from './extract';

const samplesDir = join(dirname(fileURLToPath(import.meta.url)), '../../../samples');
const expected = JSON.parse(readFileSync(join(samplesDir, 'expected.json'), 'utf8')) as Record<
  string,
  {
    docType: string;
    needsReview: boolean;
    overallConfidence: number;
    lowConfidenceFields: string[];
    fields: Record<string, string | null>;
    failedChecks: string[];
    passedChecks: string[];
  }
>;

test('every sample has an expected result', () => {
  const files = readdirSync(samplesDir).filter((f) => f.endsWith('.txt')).sort();
  assert.deepEqual(files, Object.keys(expected).sort());
});

for (const [file, want] of Object.entries(expected)) {
  test(`extracts ${file}`, () => {
    const got = extract(readFileSync(join(samplesDir, file), 'utf8'));
    assert.equal(got.docType, want.docType);
    assert.equal(got.needsReview, want.needsReview);
    assert.equal(got.overallConfidence, want.overallConfidence);
    assert.deepEqual(got.lowConfidenceFields, want.lowConfidenceFields);
    assert.deepEqual(Object.fromEntries(got.fields.map((f) => [f.key, f.value])), want.fields);
    assert.deepEqual(got.checks.filter((c) => !c.passed).map((c) => c.id), want.failedChecks);
    assert.deepEqual(got.checks.filter((c) => c.passed).map((c) => c.id), want.passedChecks);
  });
}

test('parses UK numeric and written dates', () => {
  assert.equal(parseDate('Invoice Date: 03/11/2026'), '2026-11-03');
  assert.equal(parseDate('Date: 10 Nov 2026'), '2026-11-10');
  assert.equal(parseDate('Date: 4 September 2026'), '2026-09-04');
  assert.equal(parseDate('31/13/2026'), null);
});

test('reads money with and without symbols and ignores 3-decimal unit prices', () => {
  assert.deepEqual(moneyOnLine('Total Due £1,652.20'), [{ amount: 1652.2, symbol: '£' }]);
  assert.deepEqual(moneyOnLine('TOTAL  46.30'), [{ amount: 46.3, symbol: null }]);
  assert.deepEqual(moneyOnLine('32.4 L @ 1.429'), []);
});

test('classifies text with no document keywords as unknown', () => {
  assert.deepEqual(classify('Hello, see you on Monday.'), { docType: 'unknown', confidence: 0 });
});

test('flags missing fields as low confidence and asks for review', () => {
  const got = extract('INVOICE\nInvoice No: A-1\nTotal £10.00');
  assert.equal(got.docType, 'invoice');
  assert.ok(got.needsReview);
  assert.ok(got.lowConfidenceFields.includes('invoice_date'));
  assert.equal(got.fields.find((f) => f.key === 'invoice_date')?.confidence, 0);
});
