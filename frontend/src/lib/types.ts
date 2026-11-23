export type DocType = 'invoice' | 'receipt' | 'payslip' | 'bank_statement' | 'unknown';

export interface ExtractedField {
  key: string;
  label: string;
  value: string | null;
  /** 0..1. How sure the extractor is about this value. */
  confidence: number;
  /** The line of the document the value came from, shown to the reviewer. */
  source: string | null;
  required: boolean;
}

export interface ValidationCheck {
  id: string;
  label: string;
  passed: boolean;
  detail: string;
}

export interface Extraction {
  docType: DocType;
  typeConfidence: number;
  fields: ExtractedField[];
  checks: ValidationCheck[];
  overallConfidence: number;
  needsReview: boolean;
  /** Keys of required fields below the review threshold. */
  lowConfidenceFields: string[];
}

export type DocStatus = 'needs_review' | 'ready' | 'approved' | 'rejected';

export interface IntakeDocument {
  id: string;
  clientId: string;
  fileName: string;
  text: string;
  uploadedAt: string;
  status: DocStatus;
  extraction: Extraction;
  reviewedBy?: string;
  reviewedAt?: string;
  rejectReason?: string;
  /** Which extractor read the document (API mode), e.g. "rules" or "llm:openai/…". */
  extractor?: string;
  /** The original upload, when the API keeps it (API mode). */
  file?: { url: string; mediaType: string };
}

export interface Client {
  id: string;
  name: string;
  contactEmail: string;
  /** Document types this client still has to send for the current period. */
  required: DocType[];
}

export type Role = 'client' | 'reviewer';

export interface ActivityEntry {
  id: string;
  at: string;
  actor: string;
  action: string;
  documentId?: string;
  detail?: string;
}

export interface UploadInput {
  fileName: string;
  /** Text to send, for pasted text, samples and .txt files. */
  text: string;
  /** The picked file itself, for PDFs and photos (API mode only). */
  file?: File;
}
