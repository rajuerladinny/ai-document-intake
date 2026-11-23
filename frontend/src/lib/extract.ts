/**
 * Rule-based document extraction used by the in-browser demo.
 *
 * It mirrors backend/app/extraction.py so the demo and the API give the same
 * results for the same text. Both are checked against samples/expected.json.
 * In production the backend can swap in an LLM provider; the output shape
 * (fields + confidence + validation checks) stays the same, so the review
 * dashboard does not change.
 */
import type { DocType, Extraction, ExtractedField, ValidationCheck } from './types';

export const REVIEW_THRESHOLD = 0.8;

const LABELLED = 0.95;
const HEURISTIC = 0.75;
const WEAK = 0.55;

const KEYWORDS: Record<Exclude<DocType, 'unknown'>, Array<[RegExp, number]>> = {
  invoice: [
    [/\binvoice\b/i, 3],
    [/\binvoice\s*(no|number|#)/i, 3],
    [/\bbill\s+to\b/i, 2],
    [/\bdue\s+date\b/i, 2],
    [/\bvat\s+(reg|number|no)/i, 1],
    [/\b(subtotal|net amount)\b/i, 1],
  ],
  receipt: [
    [/\breceipt\b/i, 3],
    [/\bpaid\s+by\b/i, 2],
    [/\btill\b/i, 2],
    [/\bthank\s+you\b/i, 1],
    [/\bcash\b/i, 1],
    [/\bpump\b/i, 1],
    [/\bchange\b/i, 1],
  ],
  payslip: [
    [/\bpayslip\b/i, 3],
    [/\bgross\s+pay\b/i, 3],
    [/\bnet\s+pay\b/i, 3],
    [/\btax\s+code\b/i, 2],
    [/\bnational\s+insurance\b|\bni\s+number\b/i, 2],
    [/\bemployee\b/i, 1],
  ],
  bank_statement: [
    [/\bstatement\b/i, 2],
    [/\bopening\s+balance\b/i, 3],
    [/\bclosing\s+balance\b/i, 3],
    [/\bmoney\s+(in|out)\b/i, 2],
    [/\bsort\s+code\b/i, 1],
    [/\baccount\s+number\b/i, 1],
  ],
};

export const TYPE_LABELS: Record<DocType, string> = {
  invoice: 'Invoice',
  receipt: 'Receipt',
  payslip: 'Payslip',
  bank_statement: 'Bank statement',
  unknown: 'Unknown',
};

const MONTHS: Record<string, number> = {
  jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
  jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
};

const MONEY_RE = /([£€$])?\s?(\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})(?!\d)/g;
const DATE_RE = /\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b|\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\b/;

export const round2 = (n: number): number => Math.round(n * 100) / 100;

export function classify(text: string): { docType: DocType; confidence: number } {
  const scores = (Object.keys(KEYWORDS) as Array<Exclude<DocType, 'unknown'>>).map((type) => ({
    type,
    score: KEYWORDS[type].reduce((sum, [re, weight]) => sum + (re.test(text) ? weight : 0), 0),
  }));
  scores.sort((a, b) => b.score - a.score);
  const [top, second] = scores;
  if (top.score < 2) return { docType: 'unknown', confidence: 0 };
  const share = top.score / (top.score + second.score);
  const strength = Math.min(1, top.score / 6);
  return { docType: top.type, confidence: round2(share * strength) };
}

interface MoneyMatch {
  amount: number;
  symbol: string | null;
}

export function moneyOnLine(line: string): MoneyMatch[] {
  const out: MoneyMatch[] = [];
  for (const m of line.matchAll(MONEY_RE)) {
    out.push({ amount: Number(m[2].replace(/,/g, '')), symbol: m[1] ?? null });
  }
  return out;
}

export function parseDate(line: string): string | null {
  const m = DATE_RE.exec(line);
  if (!m) return null;
  let day: number;
  let month: number;
  let year: number;
  if (m[1]) {
    day = Number(m[1]);
    month = Number(m[2]);
    year = Number(m[3]);
  } else {
    day = Number(m[4]);
    month = MONTHS[m[5].slice(0, 3).toLowerCase()] ?? 0;
    year = Number(m[6]);
  }
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function linesOf(text: string): string[] {
  return text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
}

function findLine(lines: string[], re: RegExp): string | null {
  return lines.find((l) => re.test(l)) ?? null;
}

function field(
  key: string,
  label: string,
  value: string | null,
  confidence: number,
  source: string | null,
  required = true,
): ExtractedField {
  return { key, label, value, confidence: value === null ? 0 : round2(confidence), source, required };
}

/** Last amount on the first line matching `re`. Missing currency symbol lowers confidence. */
function amountField(lines: string[], key: string, label: string, re: RegExp, required = true): ExtractedField {
  const line = findLine(lines, re);
  const amounts = line ? moneyOnLine(line) : [];
  if (!line || amounts.length === 0) return field(key, label, null, 0, null, required);
  const last = amounts[amounts.length - 1];
  const conf = last.symbol ? LABELLED : LABELLED - 0.2;
  return field(key, label, last.amount.toFixed(2), conf, line, required);
}

function dateField(lines: string[], key: string, label: string, re: RegExp, required = true): ExtractedField {
  const line = findLine(lines, re);
  const value = line ? parseDate(line) : null;
  return field(key, label, value, LABELLED, value ? line : null, required);
}

function labelValue(lines: string[], re: RegExp): { value: string; line: string } | null {
  for (const line of lines) {
    const m = re.exec(line);
    if (m && m[1] && m[1].trim()) return { value: m[1].trim(), line };
  }
  return null;
}

function firstLineField(lines: string[], key: string, label: string): ExtractedField {
  const first = lines[0] ?? null;
  if (!first) return field(key, label, null, 0, null);
  // A company suffix or an all-caps letterhead line is a strong sign this is the issuer's name.
  const looksLikeCompany = /\b(ltd|limited|plc|llp|inc|gmbh|bank)\b/i.test(first);
  const isLetterhead = /[A-Z]{3}/.test(first) && first === first.toUpperCase();
  return field(key, label, titleCase(first), looksLikeCompany || isLetterhead ? 0.85 : HEURISTIC, first);
}

function titleCase(s: string): string {
  if (s !== s.toUpperCase()) return s;
  return s
    .toLowerCase()
    .replace(/\b([a-z])/g, (c) => c.toUpperCase())
    .replace(/\bPlc\b/, 'plc');
}

function currencyField(text: string): ExtractedField {
  const symbol = /£/.test(text) ? 'GBP' : /€/.test(text) ? 'EUR' : /\$/.test(text) ? 'USD' : null;
  return field('currency', 'Currency', symbol, LABELLED, null, false);
}

function check(id: string, label: string, passed: boolean, detail: string): ValidationCheck {
  return { id, label, passed, detail };
}

const num = (f: ExtractedField | undefined): number | null =>
  f && f.value !== null && !Number.isNaN(Number(f.value)) ? Number(f.value) : null;

function extractInvoice(lines: string[], text: string): { fields: ExtractedField[]; checks: ValidationCheck[] } {
  const number = labelValue(lines, /\binvoice\s*(?:no|number|#)\.?\s*:?\s*([A-Z0-9][A-Z0-9/-]*)/i);
  const invoiceDate = dateField(lines, 'invoice_date', 'Invoice date', /\b(invoice\s+date|^date)\b/i);
  let dueDate = dateField(lines, 'due_date', 'Due date', /\bdue\s+date\b/i);
  if (dueDate.value === null && invoiceDate.value) {
    const terms = labelValue(lines, /\b(?:within|terms:?)\s*(\d{1,3})\s*days\b/i)
      ?? labelValue(lines, /\b(\d{1,3})\s*days\b/i);
    if (terms) {
      const d = new Date(`${invoiceDate.value}T00:00:00Z`);
      d.setUTCDate(d.getUTCDate() + Number(terms.value));
      dueDate = field('due_date', 'Due date', d.toISOString().slice(0, 10), WEAK, terms.line);
    }
  }
  const fields = [
    firstLineField(lines, 'supplier', 'Supplier'),
    number
      ? field('invoice_number', 'Invoice number', number.value, LABELLED, number.line)
      : field('invoice_number', 'Invoice number', null, 0, null),
    invoiceDate,
    dueDate,
    amountField(lines, 'net_amount', 'Net amount', /\b(subtotal|net\s+amount|net)\b/i),
    amountField(lines, 'vat_amount', 'VAT', /^vat\b(?!\s*(reg|number|no))/i),
    amountField(lines, 'total', 'Total', /\b(total\s+due|amount\s+due|^total)\b/i),
    currencyField(text),
  ];

  const net = num(fields[4]);
  const vat = num(fields[5]);
  const total = num(fields[6]);
  const checks: ValidationCheck[] = [];
  if (net !== null && vat !== null && total !== null) {
    const ok = Math.abs(net + vat - total) < 0.01;
    checks.push(check('sum', 'Net + VAT = total', ok, `${net.toFixed(2)} + ${vat.toFixed(2)} = ${round2(net + vat).toFixed(2)} (document says ${total.toFixed(2)})`));
  }
  const rateLine = findLine(lines, /^vat\b.*?(\d{1,2}(?:\.\d+)?)\s*%/i);
  const rateMatch = rateLine ? /(\d{1,2}(?:\.\d+)?)\s*%/.exec(rateLine) : null;
  if (rateMatch && net !== null && vat !== null) {
    const rate = Number(rateMatch[1]);
    const expected = round2((net * rate) / 100);
    const ok = Math.abs(expected - vat) <= 0.02;
    checks.push(check('vat_rate', `VAT is ${rate}% of net`, ok, `Expected ${expected.toFixed(2)}, document says ${vat.toFixed(2)}`));
  }
  return { fields, checks };
}

function extractReceipt(lines: string[], text: string): { fields: ExtractedField[]; checks: ValidationCheck[] } {
  const totalIdx = lines.findIndex((l) => /^total\b/i.test(l));
  const payment = /\b(card|visa|mastercard|amex|contactless)\b/i.test(text)
    ? 'Card'
    : /\bcash\b/i.test(text)
      ? 'Cash'
      : null;
  const paymentLine = payment ? findLine(lines, payment === 'Card' ? /\b(card|visa|mastercard|amex|contactless)\b/i : /\bcash\b/i) : null;
  const fields = [
    firstLineField(lines, 'merchant', 'Merchant'),
    dateField(lines, 'date', 'Date', /\bdate\b|\d{1,2}\/\d{1,2}\/\d{4}/i),
    amountField(lines, 'total', 'Total', /^total\b/i),
    field('payment_method', 'Payment method', payment, LABELLED, paymentLine, false),
    amountField(lines, 'vat_amount', 'VAT', /\bvat\b/i, false),
    currencyField(text),
  ];
  const checks: ValidationCheck[] = [];
  const total = num(fields[2]);
  if (totalIdx > 0 && total !== null) {
    const items = lines
      .slice(0, totalIdx)
      .filter((l) => !/\b(vat|tel|subtotal)\b/i.test(l))
      .flatMap((l) => {
        const m = moneyOnLine(l);
        return m.length ? [m[m.length - 1].amount] : [];
      });
    if (items.length > 0) {
      const sum = round2(items.reduce((a, b) => a + b, 0));
      checks.push(check('items_sum', 'Line items add up to total', Math.abs(sum - total) < 0.01, `Items ${sum.toFixed(2)}, total ${total.toFixed(2)}`));
    }
  }
  return { fields, checks };
}

function extractPayslip(lines: string[], text: string): { fields: ExtractedField[]; checks: ValidationCheck[] } {
  const employee = labelValue(lines, /^employee(?:\s+name)?\s*:\s*(.+)$/i);
  const period = labelValue(lines, /^pay\s+period\s*:\s*(.+)$/i);
  const taxCode = labelValue(lines, /\btax\s+code\s*:?\s*([0-9]{2,4}[A-Z]{1,2})\b/i);
  const fields = [
    firstLineField(lines, 'employer', 'Employer'),
    employee ? field('employee', 'Employee', employee.value, LABELLED, employee.line) : field('employee', 'Employee', null, 0, null),
    dateField(lines, 'pay_date', 'Pay date', /\bpay\s+date\b/i),
    period ? field('pay_period', 'Pay period', period.value, LABELLED, period.line, false) : field('pay_period', 'Pay period', null, 0, null, false),
    taxCode ? field('tax_code', 'Tax code', taxCode.value, LABELLED, taxCode.line, false) : field('tax_code', 'Tax code', null, 0, null, false),
    amountField(lines, 'gross_pay', 'Gross pay', /\bgross\s+pay\b/i),
    amountField(lines, 'total_deductions', 'Total deductions', /\btotal\s+deductions\b/i),
    amountField(lines, 'net_pay', 'Net pay', /\bnet\s+pay\b/i),
    currencyField(text),
  ];
  const gross = num(fields[5]);
  const deductions = num(fields[6]);
  const net = num(fields[7]);
  const checks: ValidationCheck[] = [];
  if (gross !== null && deductions !== null && net !== null) {
    checks.push(check('net_pay', 'Gross − deductions = net pay', Math.abs(gross - deductions - net) < 0.01, `${gross.toFixed(2)} − ${deductions.toFixed(2)} = ${round2(gross - deductions).toFixed(2)} (document says ${net.toFixed(2)})`));
  }
  return { fields, checks };
}

function extractBankStatement(lines: string[], text: string): { fields: ExtractedField[]; checks: ValidationCheck[] } {
  const holder = labelValue(lines, /^account\s+name\s*:\s*(.+)$/i);
  const sortCode = labelValue(lines, /\bsort\s+code\s*:?\s*(\d{2}-\d{2}-\d{2})\b/i);
  const account = labelValue(lines, /\baccount\s+number\s*:?\s*(\d{6,10})\b/i);
  const periodLine = findLine(lines, /\bstatement\s+period\b/i);
  const periodDates = periodLine ? [...periodLine.matchAll(/\d{1,2}\/\d{1,2}\/\d{4}/g)].map((m) => parseDate(m[0])) : [];
  const fields = [
    firstLineField(lines, 'bank', 'Bank'),
    holder ? field('account_holder', 'Account holder', holder.value, LABELLED, holder.line) : field('account_holder', 'Account holder', null, 0, null),
    sortCode ? field('sort_code', 'Sort code', sortCode.value, LABELLED, sortCode.line, false) : field('sort_code', 'Sort code', null, 0, null, false),
    // Only the last four digits are kept: reviewers do not need the full number.
    account
      ? field('account_number', 'Account number', `****${account.value.slice(-4)}`, LABELLED, '(masked)', false)
      : field('account_number', 'Account number', null, 0, null, false),
    field('period_start', 'Period start', periodDates[0] ?? null, LABELLED, periodLine),
    field('period_end', 'Period end', periodDates[1] ?? null, LABELLED, periodLine),
    amountField(lines, 'opening_balance', 'Opening balance', /\bopening\s+balance\b/i),
    amountField(lines, 'closing_balance', 'Closing balance', /\bclosing\s+balance\b/i),
    currencyField(text),
  ];

  const checks: ValidationCheck[] = [];
  const opening = num(fields[6]);
  const closing = num(fields[7]);
  const txLines = lines.filter((l) => /^\d{1,2}\/\d{1,2}\/\d{4}\s/.test(l) && !/opening\s+balance/i.test(l));
  if (opening !== null && closing !== null && txLines.length > 0) {
    let balance = opening;
    let reconciled = true;
    for (const l of txLines) {
      const amounts = moneyOnLine(l).map((m) => m.amount);
      if (amounts.length < 2) {
        reconciled = false;
        break;
      }
      const change = amounts[amounts.length - 2];
      const next = amounts[amounts.length - 1];
      if (Math.abs(balance - change - next) < 0.01 || Math.abs(balance + change - next) < 0.01) {
        balance = next;
      } else {
        reconciled = false;
        break;
      }
    }
    const ok = reconciled && Math.abs(balance - closing) < 0.01;
    checks.push(check('reconcile', 'Running balances reconcile to closing balance', ok, `${txLines.length} transactions, ends at ${balance.toFixed(2)}, closing ${closing.toFixed(2)}`));
  }
  return { fields, checks };
}

export function extract(text: string): Extraction {
  const lines = linesOf(text);
  const { docType, confidence } = classify(text);
  const result =
    docType === 'invoice'
      ? extractInvoice(lines, text)
      : docType === 'receipt'
        ? extractReceipt(lines, text)
        : docType === 'payslip'
          ? extractPayslip(lines, text)
          : docType === 'bank_statement'
            ? extractBankStatement(lines, text)
            : { fields: [] as ExtractedField[], checks: [] as ValidationCheck[] };

  const required = result.fields.filter((f) => f.required);
  const avg = required.length ? required.reduce((s, f) => s + f.confidence, 0) / required.length : 0;
  const lowConfidenceFields = required.filter((f) => f.confidence < REVIEW_THRESHOLD).map((f) => f.key);
  const failedCheck = result.checks.some((c) => !c.passed);
  const overallConfidence = round2(Math.min(confidence, avg) * (failedCheck ? 0.8 : 1));
  return {
    docType,
    typeConfidence: confidence,
    fields: result.fields,
    checks: result.checks,
    overallConfidence,
    needsReview: docType === 'unknown' || confidence < 0.7 || lowConfidenceFields.length > 0 || failedCheck,
    lowConfidenceFields,
  };
}
