"""AI extraction with OpenAI or Claude, checked against the document.

The model never gets the last word on its own. Every value it returns is:

* normalised (amounts to 2 dp, dates to ISO, currency to a code);
* verified: its ``source_quote`` must actually appear in the document text,
  otherwise confidence is capped at 0.5 so a person looks at it;
* re-checked with the same arithmetic rules the rule-based extractor uses.

If the API fails, text documents fall back to the rule-based extractor, so an
outage slows reviewers down instead of stopping intake.

Only the Python standard library is used for HTTP. Tests inject a fake
transport, so nothing here needs a network or an API key to test.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .extraction import (
    DOC_TYPES,
    FIELD_SPECS,
    Extraction,
    ExtractedField,
    RuleBasedExtractor,
    parse_date,
    summarise,
)
from .ingest import UploadedDocument

Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]

UNVERIFIED_CAP = 0.5
DISAGREE_CAP = 0.6
MAX_CONFIDENCE = 0.98


class LLMError(RuntimeError):
    """The model could not be reached or returned something unusable."""


def urllib_transport(timeout: float = 60.0) -> Transport:
    def send(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URLs
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise LLMError(f"HTTP {exc.code} from model API: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMError(f"Model API unreachable: {exc}") from exc

    return send


# --------------------------------------------------------------------------- prompt


def _all_keys() -> list[str]:
    keys: list[str] = []
    for spec in FIELD_SPECS.values():
        keys += [k for k, *_ in spec if k not in keys]
    return keys


RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["doc_type", "doc_type_confidence", "transcript", "fields"],
    "properties": {
        "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
        "doc_type_confidence": {"type": "number"},
        "transcript": {
            "type": ["string", "null"],
            "description": "Plain-text transcription of the document when it was given as an image; otherwise null.",
        },
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "value", "confidence", "source_quote"],
                "properties": {
                    "key": {"type": "string", "enum": _all_keys()},
                    "value": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "source_quote": {"type": ["string", "null"]},
                },
            },
        },
    },
}


def system_prompt() -> str:
    lines = [
        "You extract data from financial documents for an accounting firm's review queue.",
        "Classify the document as one of: invoice, receipt, payslip, bank_statement, unknown.",
        "Then return every field listed for that type, in this order:",
    ]
    for doc_type, spec in FIELD_SPECS.items():
        listed = ", ".join(f"{k} ({kind})" for k, _, _, kind in spec)
        lines.append(f"- {doc_type}: {listed}")
    lines += [
        "Rules:",
        "- value: amounts as plain numbers like 1234.50; dates as YYYY-MM-DD; currency as a 3-letter code.",
        "- If a value is not printed on the document, use null. Never guess or calculate a missing value,"
        " except a due date stated as payment terms (e.g. 'within 14 days'), which you may compute with low confidence.",
        "- source_quote: copy the exact line or phrase from the document that shows the value.",
        "- confidence: 0 to 1, how sure you are the value is correct.",
        "- For account numbers return only the last four digits.",
        "- If the document is an image, put a faithful plain-text transcription in transcript.",
        "- For unknown documents return an empty fields list.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- providers


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    def complete(self, text: str | None, doc: UploadedDocument | None) -> dict[str, Any]: ...


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@dataclass
class OpenAIProvider:
    model: str
    api_key: str
    transport: Transport
    base_url: str = "https://api.openai.com/v1"

    @property
    def name(self) -> str:
        return f"openai/{self.model}"

    def complete(self, text: str | None, doc: UploadedDocument | None) -> dict[str, Any]:
        if text is not None:
            user: Any = f"Document:\n\n{text}"
        else:
            assert doc and doc.binary and doc.media_type
            if doc.media_type == "application/pdf":
                part = {"type": "file", "file": {"filename": doc.file_name,
                                                 "file_data": f"data:application/pdf;base64,{_b64(doc.binary)}"}}
            else:
                part = {"type": "image_url", "image_url": {"url": f"data:{doc.media_type};base64,{_b64(doc.binary)}"}}
            user = [{"type": "text", "text": "Extract the fields from this document."}, part]
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system_prompt()}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "strict": True, "schema": RESPONSE_SCHEMA},
            },
        }
        resp = self.transport(f"{self.base_url}/chat/completions", {"authorization": f"Bearer {self.api_key}"}, body)
        try:
            message = resp["choices"][0]["message"]
            if message.get("refusal"):
                raise LLMError(f"Model refused: {message['refusal']}")
            return json.loads(message["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError("Unexpected response shape from OpenAI") from exc


@dataclass
class AnthropicProvider:
    model: str
    api_key: str
    transport: Transport
    base_url: str = "https://api.anthropic.com/v1"

    @property
    def name(self) -> str:
        return f"anthropic/{self.model}"

    def complete(self, text: str | None, doc: UploadedDocument | None) -> dict[str, Any]:
        if text is not None:
            content: list[dict[str, Any]] = [{"type": "text", "text": f"Document:\n\n{text}"}]
        else:
            assert doc and doc.binary and doc.media_type
            block_type = "document" if doc.media_type == "application/pdf" else "image"
            content = [
                {"type": block_type, "source": {"type": "base64", "media_type": doc.media_type, "data": _b64(doc.binary)}},
                {"type": "text", "text": "Extract the fields from this document."},
            ]
        body = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0,
            "system": system_prompt(),
            "messages": [{"role": "user", "content": content}],
            "tools": [{
                "name": "record_extraction",
                "description": "Record the document type and extracted fields.",
                "input_schema": RESPONSE_SCHEMA,
            }],
            "tool_choice": {"type": "tool", "name": "record_extraction"},
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        resp = self.transport(f"{self.base_url}/messages", headers, body)
        for block in resp.get("content", []) if isinstance(resp, dict) else []:
            if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                return block["input"]
        raise LLMError("Unexpected response shape from Anthropic")


# --------------------------------------------------------------------------- normalising

_CURRENCY = {"£": "GBP", "€": "EUR", "$": "USD", "gbp": "GBP", "eur": "EUR", "usd": "USD"}


def normalise(kind: str, value: Any) -> str | None:
    """Bring a model value into the same format the rule-based extractor uses. None if unusable."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"null", "none", "n/a", "-"}:
        return None
    if kind == "amount":
        negative = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
        digits = re.sub(r"[^\d.]", "", s.replace(",", ""))
        try:
            n = float(digits)
        except ValueError:
            return None
        return f"{-n if negative else n:.2f}"
    if kind == "date":
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return s
        return parse_date(s)
    if kind == "currency":
        code = _CURRENCY.get(s.lower(), s.upper())
        return code if re.fullmatch(r"[A-Z]{3}", code) else None
    if kind == "account":
        last4 = re.sub(r"\D", "", s)[-4:]
        return f"****{last4}" if len(last4) == 4 else None
    return s


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_found(quote: str | None, text: str) -> bool:
    return bool(quote) and len(_squash(quote or "")) >= 2 and _squash(quote or "") in _squash(text)


def _clamp(n: Any) -> float:
    try:
        return max(0.0, min(MAX_CONFIDENCE, float(n)))
    except (TypeError, ValueError):
        return 0.0


def build_extraction(raw: dict[str, Any], text: str) -> Extraction:
    """Turn the model's JSON into an Extraction, verifying each value against ``text``."""
    raw_type = raw.get("doc_type")
    doc_type: str = raw_type if isinstance(raw_type, str) and raw_type in DOC_TYPES else "unknown"
    type_conf = _clamp(raw.get("doc_type_confidence", 0))
    by_key = {f.get("key"): f for f in raw.get("fields") or [] if isinstance(f, dict)}
    fields: list[ExtractedField] = []
    for key, label, required, kind in FIELD_SPECS.get(doc_type, []):
        item = by_key.get(key, {})
        value = normalise(kind, item.get("value"))
        quote = item.get("source_quote")
        confidence = _clamp(item.get("confidence", 0)) if value is not None else 0.0
        if value is not None and kind != "account" and not quote_found(quote, text):
            confidence = min(confidence, UNVERIFIED_CAP)
        source = "(masked)" if kind == "account" and value else (quote if value is not None else None)
        fields.append(ExtractedField(key, label, value, round(confidence, 2), source, required))
    return summarise(doc_type, round(type_conf, 2), fields, text)


# --------------------------------------------------------------------------- extractors


@dataclass
class DocumentResult:
    """What the service stores: the extraction plus the text it was checked against."""

    extraction: Extraction
    text: str
    extractor: str


class LLMExtractor:
    supports_vision = True

    def __init__(self, provider: Provider, fallback: RuleBasedExtractor | None = None) -> None:
        self.provider = provider
        self.fallback = fallback or RuleBasedExtractor()
        self.name = f"llm:{provider.name}"

    def extract(self, text: str) -> Extraction:
        return self.extract_document(UploadedDocument("document.txt", text)).extraction

    def extract_document(self, doc: UploadedDocument) -> DocumentResult:
        if doc.text is not None:
            try:
                raw = self.provider.complete(doc.text, None)
            except LLMError:
                return DocumentResult(self.fallback.extract(doc.text), doc.text, f"{self.fallback.name} (AI unavailable)")
            return DocumentResult(build_extraction(raw, doc.text), doc.text, self.name)
        # A photo or scan has no text to fall back on, so errors go to the caller.
        raw = self.provider.complete(None, doc)
        transcript = raw.get("transcript") or ""
        if not transcript.strip():
            raise LLMError("The model returned no transcript for this image.")
        return DocumentResult(build_extraction(raw, transcript), transcript, self.name)


class HybridExtractor:
    """Runs both extractors on text documents and lets them check each other.

    Where they agree, confidence goes up. Where they disagree, the AI value is
    kept but capped at 0.6 and the rules value is shown in the source, so the
    reviewer sees both.
    """

    supports_vision = True

    def __init__(self, llm: LLMExtractor) -> None:
        self.llm = llm
        self.rules = llm.fallback
        self.name = f"hybrid:{llm.provider.name}"

    def extract(self, text: str) -> Extraction:
        return self.extract_document(UploadedDocument("document.txt", text)).extraction

    def extract_document(self, doc: UploadedDocument) -> DocumentResult:
        result = self.llm.extract_document(doc)
        if doc.text is None or result.extractor != self.llm.name:
            return result  # image (rules cannot read it) or AI already fell back to rules
        ai, rules = result.extraction, self.rules.extract(doc.text)
        type_conf = ai.type_confidence
        if rules.doc_type != ai.doc_type:
            type_conf = min(type_conf, DISAGREE_CAP)
            return DocumentResult(summarise(ai.doc_type, type_conf, ai.fields, doc.text), doc.text, self.name)
        rule_values = {f.key: f.value for f in rules.fields}
        merged: list[ExtractedField] = []
        for f in ai.fields:
            other = rule_values.get(f.key)
            if f.value is None and other is not None:
                rf = next(r for r in rules.fields if r.key == f.key)
                merged.append(ExtractedField(f.key, f.label, rf.value, min(rf.confidence, DISAGREE_CAP), rf.source, f.required))
            elif f.value is not None and other is not None and f.value != other:
                merged.append(ExtractedField(f.key, f.label, f.value, min(f.confidence, DISAGREE_CAP),
                                             f"{f.source or ''} [rules read: {other}]".strip(), f.required))
            elif f.value is not None and f.value == other:
                merged.append(ExtractedField(f.key, f.label, f.value,
                                             round(min(MAX_CONFIDENCE, max(f.confidence, 0.9) + 0.05), 2),
                                             f.source, f.required))
            else:
                merged.append(f)
        type_conf = round(min(MAX_CONFIDENCE, max(type_conf, rules.type_confidence)), 2)
        return DocumentResult(summarise(ai.doc_type, type_conf, merged, doc.text), doc.text, self.name)


# --------------------------------------------------------------------------- config


class ConfigError(ValueError):
    pass


def build_extractor_from_env(env: dict[str, str] | None = None, transport: Transport | None = None):
    """EXTRACTOR=rules|llm|hybrid, LLM_PROVIDER=openai|anthropic, LLM_MODEL, and the provider's API key."""
    env = dict(os.environ) if env is None else env
    mode = env.get("EXTRACTOR", "rules").strip().lower()
    if mode == "rules":
        return RuleBasedExtractor()
    if mode not in {"llm", "hybrid"}:
        raise ConfigError("EXTRACTOR must be rules, llm or hybrid")
    provider_name = env.get("LLM_PROVIDER", "openai").strip().lower()
    model = env.get("LLM_MODEL", "").strip()
    if not model:
        raise ConfigError("Set LLM_MODEL to the model id you want to use")
    send = transport or urllib_transport(float(env.get("LLM_TIMEOUT_SECONDS", "60")))
    provider: Provider
    if provider_name == "openai":
        key = env.get("OPENAI_API_KEY", "")
        if not key:
            raise ConfigError("Set OPENAI_API_KEY")
        provider = OpenAIProvider(model, key, send, env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    elif provider_name == "anthropic":
        key = env.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ConfigError("Set ANTHROPIC_API_KEY")
        provider = AnthropicProvider(model, key, send)
    else:
        raise ConfigError("LLM_PROVIDER must be openai or anthropic")
    llm = LLMExtractor(provider)
    return HybridExtractor(llm) if mode == "hybrid" else llm
