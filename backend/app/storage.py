"""Where original uploads are kept.

* ``database`` (default): inside the database, next to the extracted data. Simple and
  fine for small volumes.
* ``local``: a folder on the server's disk (``FILE_STORAGE_DIR``).
* ``s3``: any S3-compatible object store. AWS S3 in an EU region (e.g. ``eu-west-2``
  London, ``eu-central-1`` Frankfurt), or Cloudflare R2, Hetzner, Scaleway, MinIO via
  ``S3_ENDPOINT``. Objects are written with server-side encryption (AES-256) and the
  bucket should block public access; the API streams files to signed-in users.

The S3 client signs requests itself (AWS Signature Version 4) with the standard
library, so no SDK is needed, and tests run against a fake transport.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

KEY_RE = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-.]+)*$")


class StorageError(RuntimeError):
    """The file store could not save or return a file."""


class FileStorage(Protocol):
    name: str

    def put(self, key: str, data: bytes, media_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...


def _check_key(key: str) -> str:
    if not KEY_RE.match(key) or ".." in key:
        raise StorageError(f"Unsafe storage key: {key!r}")
    return key


class DatabaseStorage:
    """Marker: files stay in the database's files table (handled by the store)."""

    name = "database"

    def put(self, key: str, data: bytes, media_type: str) -> None:  # pragma: no cover - never called
        raise NotImplementedError

    def get(self, key: str) -> bytes:  # pragma: no cover - never called
        raise NotImplementedError


@dataclass
class LocalStorage:
    root: Path
    name: str = "local"

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _check_key(key)).resolve()
        if self.root not in path.parents:
            raise StorageError("Storage key escapes the storage folder")
        return path

    def put(self, key: str, data: bytes, media_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # atomic: readers never see half a file

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("The original file is missing from storage") from exc


# --------------------------------------------------------------------------- S3

S3Transport = Callable[[str, str, dict[str, str], bytes], tuple[int, bytes]]


def urllib_s3_transport(timeout: float = 30.0) -> S3Transport:
    def send(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body if method == "PUT" else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - configured https endpoint
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise StorageError(f"File storage unreachable: {exc}") from exc

    return send


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(method: str, url: str, headers: dict[str, str], payload_hash: str, *,
                  access_key: str, secret_key: str, region: str, service: str = "s3",
                  now: datetime | None = None) -> dict[str, str]:
    """Return ``headers`` plus x-amz-date and Authorization (AWS Signature Version 4)."""
    now = now or datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    parts = urllib.parse.urlsplit(url)
    signed = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    signed["host"] = parts.netloc
    signed["x-amz-date"] = amz_date
    names = sorted(signed)
    query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    )
    canonical = "\n".join([
        method,
        urllib.parse.quote(parts.path or "/", safe="/-_.~"),
        query,
        "".join(f"{n}:{signed[n]}\n" for n in names),
        ";".join(names),
        payload_hash,
    ])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {**headers, "x-amz-date": amz_date}
    out["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return out


@dataclass
class S3Storage:
    bucket: str
    region: str
    access_key: str
    secret_key: str
    endpoint: str | None = None  # set for non-AWS providers (path-style URLs)
    prefix: str = ""
    encrypt: bool = True
    transport: S3Transport | None = None
    name: str = "s3"

    def _url(self, key: str) -> str:
        path = urllib.parse.quote(f"{self.prefix}{_check_key(key)}", safe="/-_.~")
        if self.endpoint:
            return f"{self.endpoint.rstrip('/')}/{self.bucket}/{path}"
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{path}"

    def _send(self, method: str, key: str, body: bytes, extra: dict[str, str]) -> bytes:
        url = self._url(key)
        payload_hash = hashlib.sha256(body).hexdigest()
        headers = sigv4_headers(method, url, {**extra, "x-amz-content-sha256": payload_hash}, payload_hash,
                                access_key=self.access_key, secret_key=self.secret_key, region=self.region)
        status, data = (self.transport or urllib_s3_transport())(method, url, headers, body)
        if status == 404:
            raise StorageError("The original file is missing from storage")
        if not 200 <= status < 300:
            code = re.search(rb"<Code>([^<]+)</Code>", data or b"")
            raise StorageError(f"File storage returned {status}" + (f" ({code.group(1).decode()})" if code else ""))
        return data

    def put(self, key: str, data: bytes, media_type: str) -> None:
        extra = {"content-type": media_type, "content-length": str(len(data))}
        if self.encrypt:
            extra["x-amz-server-side-encryption"] = "AES256"
        self._send("PUT", key, data, extra)

    def get(self, key: str) -> bytes:
        return self._send("GET", key, b"", {})


class StorageConfigError(ValueError):
    pass


def storage_from_env(env: dict[str, str] | None = None, transport: S3Transport | None = None) -> FileStorage:
    env = dict(os.environ) if env is None else env
    kind = env.get("FILE_STORAGE", "database").strip().lower()
    if kind == "database":
        return DatabaseStorage()
    if kind == "local":
        return LocalStorage(Path(env.get("FILE_STORAGE_DIR", "uploads")))
    if kind == "s3":
        missing = [n for n in ("S3_BUCKET", "S3_REGION", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY") if not env.get(n)]
        if missing:
            raise StorageConfigError(f"FILE_STORAGE=s3 needs: {', '.join(missing)}")
        prefix = env.get("S3_PREFIX", "").strip("/")
        return S3Storage(
            bucket=env["S3_BUCKET"],
            region=env["S3_REGION"],
            access_key=env["S3_ACCESS_KEY_ID"],
            secret_key=env["S3_SECRET_ACCESS_KEY"],
            endpoint=env.get("S3_ENDPOINT") or None,
            prefix=f"{prefix}/" if prefix else "",
            encrypt=env.get("S3_SERVER_SIDE_ENCRYPTION", "1") != "0",
            transport=transport,
        )
    raise StorageConfigError("FILE_STORAGE must be database, local or s3")
