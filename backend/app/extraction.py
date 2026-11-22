"""Rule-based document extraction.

Mirrors frontend/src/lib/extract.ts so the browser demo and the API agree on the
same text. Both implementations are tested against samples/expected.json.

The output shape (document type, fields with confidence and source line, and
validation checks) is what the review dashboard needs. An LLM-backed extractor
can implement the same ``Extractor`` protocol and be swapped in without
changing the API or the UI.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Protocol

REVIEW_THRESHOLD = 0.8

LABELLED = 0.95
HEURISTIC = 0.75
WEAK = 0.55

DOC_TYPES = ("invoice", "receipt", "payslip", "bank_statement", "unknown")

_I = re.IGNORECASE

KEYWORDS: dict[str, list[tuple[re.Pattern[str], int]]] = {
    "invoice": [
        (re.compile(r"\binvoice\b", _I), 3),
        (re.compile(r"\binvoice\s*(no|number|#)", _I), 3),
        (re.compile(r"\bbill\s+to\b", _I), 2),
        (re.compile(r"\bdue\s+date\b", _I), 2),
        (re.compile(r"\bvat\s+(reg|number|no)", _I), 1),
        (re.compile(r"\b(subtotal|net amount)\b", _I), 1),
    ],
    "receipt": [
        (re.compile(r"\breceipt\b", _I), 3),
        (re.compile(r"\bpaid\s+by\b", _I), 2),
        (re.compile(r"\btill\b", _I), 2),
        (re.compile(r"\bthank\s+you\b", _I), 1),
        (re.compile(r"\bcash\b", _I), 1),
        (re.compile(r"\bpump\b", _I), 1),
        (re.compile(r"\bchange\b", _I), 1),
    ],
    "payslip": [
        (re.compile(r"\bpayslip\b", _I), 3),
        (re.compile(r"\bgross\s+pay\b", _I), 3),
        (re.compile(r"\bnet\s+pay\b", _I), 3),
        (re.compile(r"\btax\s+code\b", _I), 2),
        (re.compile(r"\bnational\s+insurance\b|\bni\s+number\b", _I), 2),
        (re.compile(r"\bemployee\b", _I), 1),
    ],
    "bank_statement": [
        (re.compile(r"\bstatement\b", _I), 2),
        (re.compile(r"\bopening\s+balance\b", _I), 3),
        (re.compile(r"\bclosing\s+balance\b", _I), 3),
        (re.compile(r"\bmoney\s+(in|out)\b", _I), 2),
        (re.compile(r"\bsort\s+code\b", _I), 1),
        (re.compile(r"\baccount\s+number\b", _I), 1),
    ],
}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
)}

MONEY_RE = re.compile(r"([£€$])?\s?(\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})(?!\d)")
DATE_RE = re.compile(
    r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b|\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\b"
)


@dataclass
class ExtractedField:
    key: str
    label: str
    value: str | None
    confidence: float
    source: str | None
    required: bool = True


@dataclass
class ValidationCheck:
    id: str
    label: str
    passed: bool
    detail: str


@dataclass
class Extraction:
    doc_type: str
    type_confidence: float
    fields: list[ExtractedField] = field(default_factory=list)
    checks: list[ValidationCheck] = field(default_factory=list)
    overall_confidence: float = 0.0
    needs_review: bool = True
    low_confidence_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Extractor(Protocol):
    """Anything that turns document text into an Extraction."""

    name: str

    def extract(self, text: str) -> Extraction: ...


def round2(n: float) -> float:
    """Round half away from zero, matching JavaScript's Math.round for positive values."""
    return math.floor(n * 100 + 0.5) / 100


def classify(text: str) -> tuple[str, float]:
    scores = sorted(
        ((sum(w for rx, w in rules if rx.search(text)), t) for t, rules in KEYWORDS.items()),
        key=lambda s: -s[0],
    )
    (top_score, top_type), (second_score, _) = scores[0], scores[1]
    if top_score < 2:
        return "unknown", 0.0
    share = top_score / (top_score + second_score)
    strength = min(1.0, top_score / 6)
    return top_type, round2(share * strength)


def money_on_line(line: str) -> list[tuple[float, str | None]]:
    return [(float(m.group(2).replace(",", "")), m.group(1)) for m in MONEY_RE.finditer(line)]


def parse_date(line: str) -> str | None:
    m = DATE_RE.search(line)
    if not m:
        return None
    if m.group(1):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        day = int(m.group(4))
        month = MONTHS.get(m.group(5)[:3].lower(), 0)
        year = int(m.group(6))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _find_line(lines: list[str], rx: re.Pattern[str]) -> str | None:
    return next((line for line in lines if rx.search(line)), None)


def _field(key: str, label: str, value: str | None, confidence: float, source: str | None,
           required: bool = True) -> ExtractedField:
    return ExtractedField(key, label, value, 0.0 if value is None else round2(confidence), source, required)


def _amount_field(lines: list[str], key: str, label: str, pattern: str, required: bool = True) -> ExtractedField:
    line = _find_line(lines, re.compile(pattern, _I))
    amounts = money_on_line(line) if line else []
    if not line or not amounts:
        return _field(key, label, None, 0, None, required)
    amount, symbol = amounts[-1]
    conf = LABELLED if symbol else LABELLED - 0.2
    return _field(key, label, f"{amount:.2f}", conf, line, required)


def _date_field(lines: list[str], key: str, label: str, pattern: str, required: bool = True) -> ExtractedField:
    line = _find_line(lines, re.compile(pattern, _I))
    value = parse_date(line) if line else None
    return _field(key, label, value, LABELLED, line if value else None, required)


def _label_value(lines: list[str], pattern: str) -> tuple[str, str] | None:
    rx = re.compile(pattern, _I)
    for line in lines:
        m = rx.search(line)
        if m and m.group(1) and m.group(1).strip():
            return m.group(1).strip(), line
    return None


def _title_case(s: str) -> str:
    if s != s.upper():
        return s
    out = re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), s.lower())
    return re.sub(r"\bPlc\b", "plc", out, count=1)


def _first_line_field(lines: list[str], key: str, label: str) -> ExtractedField:
    if not lines:
        return _field(key, label, None, 0, None)
    first = lines[0]
    looks_like_company = bool(re.search(r"\b(ltd|limited|plc|llp|inc|gmbh|bank)\b", first, _I))
    is_letterhead = bool(re.search(r"[A-Z]{3}", first)) and first == first.upper()
    conf = 0.85 if looks_like_company or is_letterhead else HEURISTIC
    return _field(key, label, _title_case(first), conf, first)


def _currency_field(text: str) -> ExtractedField:
    symbol = "GBP" if "£" in text else "EUR" if "€" in text else "USD" if "$" in text else None
    return _field("currency", "Currency", symbol, LABELLED, None, required=False)


def _num(f: ExtractedField) -> float | None:
    try:
        return float(f.value) if f.value is not None else None
    except ValueError:
        return None


def _extract_invoice(lines: list[str], text: str) -> tuple[list[ExtractedField], list[ValidationCheck]]:
    number = _label_value(lines, r"\binvoice\s*(?:no|number|#)\.?\s*:?\s*([A-Z0-9][A-Z0-9/-]*)")
    invoice_date = _date_field(lines, "invoice_date", "Invoice date", r"\b(invoice\s+date|^date)\b")
    due_date = _date_field(lines, "due_date", "Due date", r"\bdue\s+date\b")
    if due_date.value is None and invoice_date.value:
        terms = _label_value(lines, r"\b(?:within|terms:?)\s*(\d{1,3})\s*days\b") or _label_value(
            lines, r"\b(\d{1,3})\s*days\b"
        )
        if terms:
            due = date.fromisoformat(invoice_date.value) + timedelta(days=int(terms[0]))
            due_date = _field("due_date", "Due date", due.isoformat(), WEAK, terms[1])
    fields = [
        _first_line_field(lines, "supplier", "Supplier"),
        _field("invoice_number", "Invoice number", number[0], LABELLED, number[1])
        if number else _field("invoice_number", "Invoice number", None, 0, None),
        invoice_date,
        due_date,
        _amount_field(lines, "net_amount", "Net amount", r"\b(subtotal|net\s+amount|net)\b"),
        _amount_field(lines, "vat_amount", "VAT", r"^vat\b(?!\s*(reg|number|no))"),
        _amount_field(lines, "total", "Total", r"\b(total\s+due|amount\s+due|^total)\b"),
        _currency_field(text),
    ]
    net, vat, total = _num(fields[4]), _num(fields[5]), _num(fields[6])
    checks: list[ValidationCheck] = []
    if net is not None and vat is not None and total is not None:
        checks.append(ValidationCheck(
            "sum", "Net + VAT = total", abs(net + vat - total) < 0.01,
            f"{net:.2f} + {vat:.2f} = {round2(net + vat):.2f} (document says {total:.2f})",
        ))
    rate_line = _find_line(lines, re.compile(r"^vat\b.*?(\d{1,2}(?:\.\d+)?)\s*%", _I))
    rate_match = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", rate_line) if rate_line else None
    if rate_match and net is not None and vat is not None:
        rate = float(rate_match.group(1))
        expected = round2(net * rate / 100)
        rate_label = f"{rate:g}"
        checks.append(ValidationCheck(
            "vat_rate", f"VAT is {rate_label}% of net", abs(expected - vat) <= 0.02,
            f"Expected {expected:.2f}, document says {vat:.2f}",
        ))
    return fields, checks


_CARD_RE = re.compile(r"\b(card|visa|mastercard|amex|contactless)\b", _I)
_CASH_RE = re.compile(r"\bcash\b", _I)


def _extract_receipt(lines: list[str], text: str) -> tuple[list[ExtractedField], list[ValidationCheck]]:
    total_idx = next((i for i, line in enumerate(lines) if re.match(r"total\b", line, _I)), -1)
    if _CARD_RE.search(text):
        payment, payment_line = "Card", _find_line(lines, _CARD_RE)
    elif _CASH_RE.search(text):
        payment, payment_line = "Cash", _find_line(lines, _CASH_RE)
    else:
        payment, payment_line = None, None
    fields = [
        _first_line_field(lines, "merchant", "Merchant"),
        _date_field(lines, "date", "Date", r"\bdate\b|\d{1,2}/\d{1,2}/\d{4}"),
        _amount_field(lines, "total", "Total", r"^total\b"),
        _field("payment_method", "Payment method", payment, LABELLED, payment_line, required=False),
        _amount_field(lines, "vat_amount", "VAT", r"\bvat\b", required=False),
        _currency_field(text),
    ]
    checks: list[ValidationCheck] = []
    total = _num(fields[2])
    if total_idx > 0 and total is not None:
        items = []
        for line in lines[:total_idx]:
            if re.search(r"\b(vat|tel|subtotal)\b", line, _I):
                continue
            found = money_on_line(line)
            if found:
                items.append(found[-1][0])
        if items:
            s = round2(sum(items))
            checks.append(ValidationCheck(
                "items_sum", "Line items add up to total", abs(s - total) < 0.01,
                f"Items {s:.2f}, total {total:.2f}",
            ))
    return fields, checks


def _extract_payslip(lines: list[str], text: str) -> tuple[list[ExtractedField], list[ValidationCheck]]:
    employee = _label_value(lines, r"^employee(?:\s+name)?\s*:\s*(.+)$")
    period = _label_value(lines, r"^pay\s+period\s*:\s*(.+)$")
    tax_code = _label_value(lines, r"\btax\s+code\s*:?\s*([0-9]{2,4}[A-Z]{1,2})\b")
    fields = [
        _first_line_field(lines, "employer", "Employer"),
        _field("employee", "Employee", employee[0], LABELLED, employee[1])
        if employee else _field("employee", "Employee", None, 0, None),
        _date_field(lines, "pay_date", "Pay date", r"\bpay\s+date\b"),
        _field("pay_period", "Pay period", period[0], LABELLED, period[1], required=False)
        if period else _field("pay_period", "Pay period", None, 0, None, required=False),
        _field("tax_code", "Tax code", tax_code[0], LABELLED, tax_code[1], required=False)
        if tax_code else _field("tax_code", "Tax code", None, 0, None, required=False),
        _amount_field(lines, "gross_pay", "Gross pay", r"\bgross\s+pay\b"),
        _amount_field(lines, "total_deductions", "Total deductions", r"\btotal\s+deductions\b"),
        _amount_field(lines, "net_pay", "Net pay", r"\bnet\s+pay\b"),
        _currency_field(text),
    ]
    gross, deductions, net = _num(fields[5]), _num(fields[6]), _num(fields[7])
    checks: list[ValidationCheck] = []
    if gross is not None and deductions is not None and net is not None:
        checks.append(ValidationCheck(
            "net_pay", "Gross − deductions = net pay", abs(gross - deductions - net) < 0.01,
            f"{gross:.2f} − {deductions:.2f} = {round2(gross - deductions):.2f} (document says {net:.2f})",
        ))
    return fields, checks


def _extract_bank_statement(lines: list[str], text: str) -> tuple[list[ExtractedField], list[ValidationCheck]]:
    holder = _label_value(lines, r"^account\s+name\s*:\s*(.+)$")
    sort_code = _label_value(lines, r"\bsort\s+code\s*:?\s*(\d{2}-\d{2}-\d{2})\b")
    account = _label_value(lines, r"\baccount\s+number\s*:?\s*(\d{6,10})\b")
    period_line = _find_line(lines, re.compile(r"\bstatement\s+period\b", _I))
    period_dates = (
        [parse_date(d) for d in re.findall(r"\d{1,2}/\d{1,2}/\d{4}", period_line)] if period_line else []
    )
    fields = [
        _first_line_field(lines, "bank", "Bank"),
        _field("account_holder", "Account holder", holder[0], LABELLED, holder[1])
        if holder else _field("account_holder", "Account holder", None, 0, None),
        _field("sort_code", "Sort code", sort_code[0], LABELLED, sort_code[1], required=False)
        if sort_code else _field("sort_code", "Sort code", None, 0, None, required=False),
        # Only the last four digits are kept: reviewers do not need the full number.
        _field("account_number", "Account number", f"****{account[0][-4:]}", LABELLED, "(masked)", required=False)
        if account else _field("account_number", "Account number", None, 0, None, required=False),
        _field("period_start", "Period start", period_dates[0] if period_dates else None, LABELLED, period_line),
        _field("period_end", "Period end", period_dates[1] if len(period_dates) > 1 else None, LABELLED, period_line),
        _amount_field(lines, "opening_balance", "Opening balance", r"\bopening\s+balance\b"),
        _amount_field(lines, "closing_balance", "Closing balance", r"\bclosing\s+balance\b"),
        _currency_field(text),
    ]
    checks: list[ValidationCheck] = []
    opening, closing = _num(fields[6]), _num(fields[7])
    tx_lines = [
        line for line in lines
        if re.match(r"\d{1,2}/\d{1,2}/\d{4}\s", line) and not re.search(r"opening\s+balance", line, _I)
    ]
    if opening is not None and closing is not None and tx_lines:
        balance, reconciled = opening, True
        for line in tx_lines:
            amounts = [a for a, _ in money_on_line(line)]
            if len(amounts) < 2:
                reconciled = False
                break
            change, nxt = amounts[-2], amounts[-1]
            if abs(balance - change - nxt) < 0.01 or abs(balance + change - nxt) < 0.01:
                balance = nxt
            else:
                reconciled = False
                break
        ok = reconciled and abs(balance - closing) < 0.01
        checks.append(ValidationCheck(
            "reconcile", "Running balances reconcile to closing balance", ok,
            f"{len(tx_lines)} transactions, ends at {balance:.2f}, closing {closing:.2f}",
        ))
    return fields, checks


_EXTRACTORS = {
    "invoice": _extract_invoice,
    "receipt": _extract_receipt,
    "payslip": _extract_payslip,
    "bank_statement": _extract_bank_statement,
}


def extract(text: str) -> Extraction:
    lines = _lines(text)
    doc_type, confidence = classify(text)
    fields, checks = _EXTRACTORS[doc_type](lines, text) if doc_type in _EXTRACTORS else ([], [])

    required = [f for f in fields if f.required]
    avg = sum(f.confidence for f in required) / len(required) if required else 0.0
    low = [f.key for f in required if f.confidence < REVIEW_THRESHOLD]
    failed = any(not c.passed for c in checks)
    overall = round2(min(confidence, avg) * (0.8 if failed else 1.0))
    return Extraction(
        doc_type=doc_type,
        type_confidence=confidence,
        fields=fields,
        checks=checks,
        overall_confidence=overall,
        needs_review=doc_type == "unknown" or confidence < 0.7 or bool(low) or failed,
        low_confidence_fields=low,
    )


class RuleBasedExtractor:
    name = "rules"

    def extract(self, text: str) -> Extraction:
        return extract(text)
