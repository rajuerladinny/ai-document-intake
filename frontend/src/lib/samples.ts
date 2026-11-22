import invoiceBrightline from '../../../samples/invoice-brightline.txt';
import invoiceKestrel from '../../../samples/invoice-kestrel-mismatch.txt';
import receiptCafe from '../../../samples/receipt-cafe.txt';
import receiptFaded from '../../../samples/receipt-faded.txt';
import payslip from '../../../samples/payslip-harbour.txt';
import bankStatement from '../../../samples/bank-statement-meridian.txt';
import letter from '../../../samples/letter-unknown.txt';
import type { Client, DocType } from './types';

export interface Sample {
  fileName: string;
  title: string;
  text: string;
}

export const SAMPLES: Sample[] = [
  { fileName: 'invoice-brightline.txt', title: 'Supplier invoice (clean)', text: invoiceBrightline },
  { fileName: 'invoice-kestrel-mismatch.txt', title: 'Invoice with a VAT error', text: invoiceKestrel },
  { fileName: 'payslip-harbour.txt', title: 'Payslip', text: payslip },
  { fileName: 'bank-statement-meridian.txt', title: 'Bank statement', text: bankStatement },
  { fileName: 'receipt-cafe.txt', title: 'Café receipt', text: receiptCafe },
  { fileName: 'receipt-faded.txt', title: 'Faded fuel receipt', text: receiptFaded },
  { fileName: 'letter-unknown.txt', title: 'Letter (not a financial document)', text: letter },
];

export const DEMO_CLIENTS: Array<Client & { files: string[] }> = [
  {
    id: 'cli_harbour',
    name: 'Harbour & Finch Catering Ltd',
    contactEmail: 'accounts@harbourfinch.example',
    required: ['invoice', 'receipt', 'payslip', 'bank_statement'] as DocType[],
    files: ['invoice-brightline.txt', 'invoice-kestrel-mismatch.txt', 'payslip-harbour.txt', 'bank-statement-meridian.txt'],
  },
  {
    id: 'cli_fernhill',
    name: 'Fernhill Design Studio',
    contactEmail: 'hello@fernhill.example',
    required: ['receipt', 'invoice', 'bank_statement'] as DocType[],
    files: ['receipt-cafe.txt', 'receipt-faded.txt', 'letter-unknown.txt'],
  },
];
