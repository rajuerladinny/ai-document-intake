"""AI extractor tests. A fake transport stands in for the OpenAI and Anthropic APIs."""

import json

import pytest

from app.extraction import extract
from app.ingest import UnsupportedFile, UploadedDocument
from app.llm import (
    AnthropicProvider,
    ConfigError,
    HybridExtractor,
    LLMError,
    LLMExtractor,
    OpenAIProvider,
    build_extractor_from_env,
    normalise,
)
from app.service import ExtractionUnavailable, IntakeService

from conftest import SAMPLES_DIR, make_store

PNG = b"\x89PNG\r\n\x1a\nfake"


def sample(name):
    return (SAMPLES_DIR / name).read_text()


def answer_from_rules(text, **overrides):
    """A plausible model answer: what the rule-based extractor finds, quoted from the document."""
    rules = extract(text)
    fields = [
        {"key": f.key, "value": f.value, "confidence": 0.93 if f.value else 0.0, "source_quote": f.source}
        for f in rules.fields
    ]
    for f in fields:
        if f["key"] in overrides:
            f.update(overrides[f["key"]])
    return {"doc_type": rules.doc_type, "doc_type_confidence": 0.96, "transcript": None, "fields": fields}


class FakeTransport:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        if self.error:
            raise self.error
        return self.reply


def openai_reply(answer):
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(answer)}}]}


def anthropic_reply(answer):
    return {"content": [{"type": "text", "text": "Recording."}, {"type": "tool_use", "name": "record_extraction", "input": answer}]}


def openai_extractor(answer=None, error=None):
    transport = FakeTransport(openai_reply(answer) if answer else None, error)
    return LLMExtractor(OpenAIProvider("test-model", "sk-test", transport)), transport


# ---------------------------------------------------------------- providers


def test_openai_request_uses_strict_json_schema_and_parses_reply():
    text = sample("invoice-brightline.txt")
    extractor, transport = openai_extractor(answer_from_rules(text))
    got = extractor.extract(text)

    url, headers, body = transport.calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["authorization"] == "Bearer sk-test"
    assert body["model"] == "test-model" and body["temperature"] == 0
    assert body["response_format"]["json_schema"]["strict"] is True
    assert text in body["messages"][1]["content"]
    assert got.doc_type == "invoice"
    assert not got.needs_review
    assert extractor.name == "llm:openai/test-model"


def test_anthropic_request_forces_the_tool_and_reads_its_input():
    text = sample("payslip-harbour.txt")
    transport = FakeTransport(anthropic_reply(answer_from_rules(text)))
    got = LLMExtractor(AnthropicProvider("claude-test", "key-test", transport)).extract(text)

    url, headers, body = transport.calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers == {"x-api-key": "key-test", "anthropic-version": "2023-06-01"}
    assert body["tool_choice"] == {"type": "tool", "name": "record_extraction"}
    assert body["tools"][0]["input_schema"]["required"] == ["doc_type", "doc_type_confidence", "transcript", "fields"]
    assert got.doc_type == "payslip"
    assert {c.id: c.passed for c in got.checks} == {"net_pay": True}


def test_image_is_sent_as_base64_to_each_provider():
    doc = UploadedDocument("receipt.png", None, PNG, "image/png")
    answer = answer_from_rules(sample("receipt-cafe.txt"))
    answer["transcript"] = sample("receipt-cafe.txt")

    oa = FakeTransport(openai_reply(answer))
    LLMExtractor(OpenAIProvider("m", "k", oa)).extract_document(doc)
    part = oa.calls[0][2]["messages"][1]["content"][1]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/png;base64,")

    an = FakeTransport(anthropic_reply(answer))
    LLMExtractor(AnthropicProvider("m", "k", an)).extract_document(doc)
    block = an.calls[0][2]["messages"][0]["content"][0]
    assert block["type"] == "image" and block["source"]["media_type"] == "image/png"


def test_scanned_pdf_is_sent_as_a_document_block():
    doc = UploadedDocument("scan.pdf", None, b"%PDF-1.4 fake", "application/pdf")
    answer = answer_from_rules(sample("receipt-cafe.txt"))
    answer["transcript"] = sample("receipt-cafe.txt")
    an = FakeTransport(anthropic_reply(answer))
    LLMExtractor(AnthropicProvider("m", "k", an)).extract_document(doc)
    assert an.calls[0][2]["messages"][0]["content"][0]["type"] == "document"


# ---------------------------------------------------------------- verification


def test_value_without_a_matching_quote_is_capped_and_flagged():
    text = sample("invoice-brightline.txt")
    answer = answer_from_rules(text, invoice_number={"source_quote": "Invoice no. INV-9999 (not in the document)"})
    got, _ = openai_extractor(answer)
    result = got.extract(text)
    field = next(f for f in result.fields if f.key == "invoice_number")
    assert field.confidence == 0.5
    assert result.needs_review and "invoice_number" in result.low_confidence_fields


def test_checks_are_recomputed_on_model_values():
    """The model may say everything is fine; the arithmetic still catches the VAT error."""
    text = sample("invoice-kestrel-mismatch.txt")
    extractor, _ = openai_extractor(answer_from_rules(text))
    got = extractor.extract(text)
    assert {c.id: c.passed for c in got.checks}["vat_rate"] is False
    assert got.needs_review


def test_missing_fields_are_filled_in_as_empty():
    text = sample("invoice-brightline.txt")
    answer = answer_from_rules(text)
    answer["fields"] = [f for f in answer["fields"] if f["key"] != "due_date"]
    extractor, _ = openai_extractor(answer)
    got = extractor.extract(text)
    due = next(f for f in got.fields if f.key == "due_date")
    assert due.value is None and due.confidence == 0
    assert got.needs_review


def test_unknown_type_from_model_is_safe():
    extractor, _ = openai_extractor({"doc_type": "contract", "doc_type_confidence": 2, "transcript": None, "fields": []})
    got = extractor.extract(sample("letter-unknown.txt"))
    assert got.doc_type == "unknown" and got.fields == [] and got.needs_review


@pytest.mark.parametrize(
    ("kind", "raw", "want"),
    [
        ("amount", "£1,234.5", "1234.50"),
        ("amount", "(12.00)", "-12.00"),
        ("amount", "n/a", None),
        ("amount", "about", None),
        ("date", "2026-11-10", "2026-11-10"),
        ("date", "05/03/2026", "2026-03-05"),
        ("date", "10 Nov 2026", "2026-11-10"),
        ("date", "soon", None),
        ("currency", "£", "GBP"),
        ("currency", "eur", "EUR"),
        ("currency", "pounds", None),
        ("account", "12345678", "****5678"),
        ("text", "  Kestrel Logistics Ltd ", "Kestrel Logistics Ltd"),
        ("text", None, None),
    ],
)
def test_normalise(kind, raw, want):
    assert normalise(kind, raw) == want


# ---------------------------------------------------------------- failure handling


def test_text_document_falls_back_to_rules_when_the_api_fails():
    text = sample("invoice-brightline.txt")
    extractor, _ = openai_extractor(error=LLMError("HTTP 500"))
    result = extractor.extract_document(UploadedDocument("a.txt", text))
    assert result.extractor == "rules (AI unavailable)"
    assert result.extraction.to_dict() == extract(text).to_dict()


def test_malformed_reply_also_falls_back():
    text = sample("receipt-cafe.txt")
    transport = FakeTransport({"choices": [{"message": {"content": "not json"}}]})
    result = LLMExtractor(OpenAIProvider("m", "k", transport)).extract_document(UploadedDocument("a.txt", text))
    assert result.extractor.startswith("rules")


def test_image_errors_are_raised_because_there_is_nothing_to_fall_back_to():
    extractor, _ = openai_extractor(error=LLMError("timeout"))
    with pytest.raises(LLMError):
        extractor.extract_document(UploadedDocument("r.png", None, PNG, "image/png"))


def test_image_without_transcript_is_rejected():
    answer = answer_from_rules(sample("receipt-cafe.txt"))
    extractor, _ = openai_extractor(answer)
    with pytest.raises(LLMError):
        extractor.extract_document(UploadedDocument("r.png", None, PNG, "image/png"))


# ---------------------------------------------------------------- hybrid


def hybrid(answer):
    llm, _ = openai_extractor(answer)
    return HybridExtractor(llm)


def test_hybrid_agreement_raises_confidence():
    text = sample("invoice-brightline.txt")
    got = hybrid(answer_from_rules(text)).extract(text)
    assert all(f.confidence >= 0.95 for f in got.fields if f.value is not None)
    assert not got.needs_review


def test_hybrid_disagreement_caps_confidence_and_shows_both_values():
    text = sample("invoice-brightline.txt")
    answer = answer_from_rules(text, total={"value": "999.00"})
    got = hybrid(answer).extract(text)
    total = next(f for f in got.fields if f.key == "total")
    assert total.value == "999.00" and total.confidence == 0.6
    assert "[rules read:" in (total.source or "")
    assert got.needs_review


def test_hybrid_fills_a_gap_from_rules_with_low_confidence():
    text = sample("invoice-brightline.txt")
    answer = answer_from_rules(text, invoice_number={"value": None, "source_quote": None})
    got = hybrid(answer).extract(text)
    number = next(f for f in got.fields if f.key == "invoice_number")
    assert number.value is not None and number.confidence <= 0.6


def test_hybrid_type_disagreement_is_flagged():
    text = sample("invoice-brightline.txt")
    answer = answer_from_rules(sample("receipt-cafe.txt"))
    got = hybrid(answer).extract(text)
    assert got.type_confidence <= 0.6 and got.needs_review


# ---------------------------------------------------------------- service and config


def make_service(extractor=None):
    svc = IntakeService(make_store(), extractor)
    svc.store.add_client("Acme", "a@example.com", ["receipt"], client_id="c1")
    return svc


def test_service_stores_transcript_and_extractor_for_photos():
    answer = answer_from_rules(sample("receipt-cafe.txt"))
    answer["transcript"] = sample("receipt-cafe.txt")
    extractor, _ = openai_extractor(answer)
    doc = make_service(extractor).ingest("c1", "receipt.jpg", PNG, actor="a@example.com")
    assert doc["text"] == sample("receipt-cafe.txt")
    assert doc["extractor"] == "llm:openai/test-model"
    assert doc["extraction"]["doc_type"] == "receipt"


def test_service_reports_ai_outage_for_photos():
    extractor, _ = openai_extractor(error=LLMError("timeout"))
    with pytest.raises(ExtractionUnavailable):
        make_service(extractor).ingest("c1", "receipt.jpg", PNG, actor="a@example.com")


def test_rules_only_service_explains_how_to_read_photos():
    with pytest.raises(UnsupportedFile, match="EXTRACTOR=llm"):
        make_service().ingest("c1", "receipt.jpg", PNG, actor="a@example.com")


def test_config_defaults_to_rules():
    assert build_extractor_from_env({}).name == "rules"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"EXTRACTOR": "magic"}, "rules, llm or hybrid"),
        ({"EXTRACTOR": "llm"}, "LLM_MODEL"),
        ({"EXTRACTOR": "llm", "LLM_MODEL": "m"}, "OPENAI_API_KEY"),
        ({"EXTRACTOR": "llm", "LLM_MODEL": "m", "LLM_PROVIDER": "anthropic"}, "ANTHROPIC_API_KEY"),
        ({"EXTRACTOR": "llm", "LLM_MODEL": "m", "LLM_PROVIDER": "other"}, "openai or anthropic"),
    ],
)
def test_config_errors_are_clear(env, message):
    with pytest.raises(ConfigError, match=message):
        build_extractor_from_env(env)


def test_config_builds_each_mode():
    fake = FakeTransport()
    llm = build_extractor_from_env({"EXTRACTOR": "llm", "LLM_MODEL": "m", "OPENAI_API_KEY": "k"}, fake)
    assert llm.name == "llm:openai/m"
    hy = build_extractor_from_env(
        {"EXTRACTOR": "hybrid", "LLM_PROVIDER": "anthropic", "LLM_MODEL": "c", "ANTHROPIC_API_KEY": "k"}, fake
    )
    assert hy.name == "hybrid:anthropic/c"
