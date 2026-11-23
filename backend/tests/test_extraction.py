import json

import pytest

from app.extraction import classify, extract, money_on_line, parse_date
from conftest import SAMPLES_DIR

EXPECTED = json.loads((SAMPLES_DIR / "expected.json").read_text())


def test_every_sample_has_an_expected_result():
    files = sorted(p.name for p in SAMPLES_DIR.glob("*.txt"))
    assert files == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_sample_matches_shared_expectation(name):
    want = EXPECTED[name]
    got = extract((SAMPLES_DIR / name).read_text())
    assert got.doc_type == want["docType"]
    assert got.needs_review == want["needsReview"]
    assert got.overall_confidence == want["overallConfidence"]
    assert got.low_confidence_fields == want["lowConfidenceFields"]
    assert {f.key: f.value for f in got.fields} == want["fields"]
    assert [c.id for c in got.checks if not c.passed] == want["failedChecks"]
    assert [c.id for c in got.checks if c.passed] == want["passedChecks"]


def test_parses_uk_numeric_and_written_dates():
    assert parse_date("Invoice Date: 03/11/2026") == "2026-11-03"
    assert parse_date("Date: 10 Nov 2026") == "2026-11-10"
    assert parse_date("Date: 4 September 2026") == "2026-09-04"
    assert parse_date("31/13/2026") is None


def test_reads_money_and_ignores_three_decimal_unit_prices():
    assert money_on_line("Total Due £1,652.20") == [(1652.2, "£")]
    assert money_on_line("TOTAL  46.30") == [(46.3, None)]
    assert money_on_line("32.4 L @ 1.429") == []


def test_unknown_text():
    assert classify("Hello, see you on Monday.") == ("unknown", 0.0)


def test_missing_fields_are_flagged_for_review():
    got = extract("INVOICE\nInvoice No: A-1\nTotal £10.00")
    assert got.doc_type == "invoice"
    assert got.needs_review
    assert "invoice_date" in got.low_confidence_fields


def test_bank_account_number_is_masked():
    got = extract((SAMPLES_DIR / "bank-statement-meridian.txt").read_text())
    account = next(f for f in got.fields if f.key == "account_number")
    assert account.value == "****2819"
    assert "70442819" not in json.dumps(got.to_dict())
