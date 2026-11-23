"""Accounts, sign-in and permissions.

* Passwords: PBKDF2-HMAC-SHA256 with a per-user salt (standard library, no extra packages).
* Sessions: random bearer tokens. Only their SHA-256 is stored, so a leaked
  database does not leak usable tokens.
* File links: <img> and <iframe> tags cannot send an Authorization header, so
  original files are fetched with short-lived signed URLs instead.
* Roles:
    - client:   their own business only. Upload, see their documents and checklist.
    - reviewer: every client. Review, correct, approve, reject, see activity.
    - admin:    reviewer rights plus users, clients, audit export and sending reminders.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import Store

ROLES = ("admin", "reviewer", "client")
PBKDF2_ITERATIONS = 310_000
SESSION_HOURS = 12
FILE_LINK_SECONDS = 300
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
MIN_PASSWORD_LENGTH = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Spent on unknown emails too, so response time does not reveal which emails exist.
_DUMMY_HASH = None


class AuthError(Exception):
    """Not signed in, or the session has expired (HTTP 401)."""


class Forbidden(Exception):
    """Signed in, but not allowed to do this (HTTP 403)."""


@dataclass(frozen=True)
class User:
    id: str
    email: str
    name: str
    role: str
    client_id: str | None = None

    @property
    def is_staff(self) -> bool:
        return self.role in ("admin", "reviewer")

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "User":
        return cls(row["id"], row["email"], row["name"], row["role"], row["client_id"])

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email, "name": self.name, "role": self.role, "client_id": self.client_id}


# Used when AUTH_REQUIRED=0 (local demo only): every request acts as this admin.
DEMO_ADMIN = User("usr_demo", "admin@demo", "Demo admin", "admin")


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters.")
    if password.lower() == password or not any(c.isdigit() for c in password):
        raise ValueError("Use upper and lower case letters and at least one number.")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class Auth:
    def __init__(self, store: Store, secret_key: str | None = None, *, required: bool = True) -> None:
        self.store = store
        self.required = required
        # Without a fixed SECRET_KEY, file links stop working after a restart (that is all).
        self._secret = (secret_key or secrets.token_hex(32)).encode()
        # Failed sign-ins per email, to slow down password guessing. In memory, per process.
        self._failures: dict[str, list[float]] = {}
        self._failures_lock = threading.Lock()

    # Accounts ---------------------------------------------------------------

    def create_user(self, email: str, name: str, role: str, password: str,
                    client_id: str | None = None, *, check_strength: bool = True) -> User:
        email = email.strip().lower()
        if not EMAIL_RE.match(email):
            raise ValueError("Enter a valid email address.")
        if role not in ROLES:
            raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
        if role == "client":
            if not client_id or not self.store.get_client(client_id):
                raise ValueError("Client users must belong to an existing client.")
        else:
            client_id = None
        if self.store.user_with_password(email):
            raise ValueError("A user with that email already exists.")
        if check_strength:
            check_password_strength(password)
        row = self.store.add_user(email, name.strip() or email, role, hash_password(password), client_id)
        return User.from_row(row)

    def set_password(self, user_id: str, password: str) -> None:
        check_password_strength(password)
        self.store.update_user(user_id, password_hash=hash_password(password))
        self.store.delete_sessions(user_id=user_id)  # sign out everywhere

    def disable_user(self, user_id: str) -> None:
        if not self.store.get_user(user_id):
            raise LookupError("User not found")
        self.store.update_user(user_id, disabled=True)
        self.store.delete_sessions(user_id=user_id)

    # Sessions ---------------------------------------------------------------

    def _recent_failures(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < LOCKOUT_SECONDS]
        self._failures[key] = recent
        return recent

    def login(self, email: str, password: str, *, now: float | None = None) -> tuple[str, User]:
        global _DUMMY_HASH
        key = (email or "").strip().lower()
        moment = now or time.time()
        with self._failures_lock:
            if len(self._recent_failures(key, moment)) >= MAX_FAILED_LOGINS:
                raise AuthError("Too many failed attempts. Try again in 15 minutes.")
        found = self.store.user_with_password(key)
        if found is None:
            _DUMMY_HASH = _DUMMY_HASH or hash_password("not-a-real-password")
            verify_password(password or "", _DUMMY_HASH)
            ok, row = False, None
        else:
            row, stored = found
            ok = verify_password(password or "", stored) and not row["disabled"]
        if not ok or row is None:
            with self._failures_lock:
                self._failures.setdefault(key, []).append(moment)
            raise AuthError("Email or password is incorrect.")
        with self._failures_lock:
            self._failures.pop(key, None)
        token = secrets.token_urlsafe(32)
        issued = datetime.now(timezone.utc)
        self.store.delete_expired_sessions(_iso(issued))
        self.store.add_session(_token_hash(token), row["id"], _iso(issued + timedelta(hours=SESSION_HOURS)))
        return token, User.from_row(row)

    def logout(self, token: str) -> None:
        self.store.delete_sessions(token_hash=_token_hash(token))

    def user_for_token(self, token: str | None) -> User:
        if not self.required:
            return DEMO_ADMIN
        if not token:
            raise AuthError("Please sign in.")
        row = self.store.session_user(_token_hash(token), _iso(datetime.now(timezone.utc)))
        if row is None:
            raise AuthError("Your session has expired. Please sign in again.")
        return User.from_row(row)

    # Signed file links --------------------------------------------------------

    def sign_file_link(self, document_id: str, *, now: float | None = None) -> str:
        expires = int((now or time.time()) + FILE_LINK_SECONDS)
        sig = hmac.new(self._secret, f"{document_id}:{expires}".encode(), hashlib.sha256).hexdigest()
        return f"expires={expires}&sig={sig}"

    def check_file_link(self, document_id: str, expires: str | None, sig: str | None, *,
                        now: float | None = None) -> None:
        if not self.required:
            return
        try:
            exp = int(expires or "")
        except ValueError:
            raise AuthError("This file link is not valid.") from None
        expected = hmac.new(self._secret, f"{document_id}:{exp}".encode(), hashlib.sha256).hexdigest()
        if not sig or not hmac.compare_digest(expected, sig):
            raise AuthError("This file link is not valid.")
        if exp < (now or time.time()):
            raise AuthError("This file link has expired. Reload the page.")


def auth_from_env(store: Store, env: dict[str, str] | None = None) -> Auth:
    env = dict(os.environ) if env is None else env
    required = env.get("AUTH_REQUIRED", "1") != "0"
    return Auth(store, env.get("SECRET_KEY") or None, required=required)
