import pytest

from app.auth import (
    LOCKOUT_SECONDS,
    Auth,
    AuthError,
    auth_from_env,
    check_password_strength,
    hash_password,
    verify_password,
)
from conftest import make_store

PASSWORD = "Str0ngPassword"


@pytest.fixture
def auth():
    store = make_store()
    store.add_client("Acme", "a@example.com", ["invoice"], client_id="c1")
    yield Auth(store, "test-secret")
    store.close()


def test_password_hashing():
    stored = hash_password(PASSWORD, iterations=1000)
    assert stored.startswith("pbkdf2_sha256$1000$") and PASSWORD not in stored
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong", stored)
    assert not verify_password(PASSWORD, "garbage")
    assert hash_password(PASSWORD, iterations=1000) != stored  # salted


@pytest.mark.parametrize("weak", ["short1A", "alllowercase1", "NoNumbersHere"])
def test_weak_passwords_are_refused(weak):
    with pytest.raises(ValueError):
        check_password_strength(weak)


def test_create_user_validates_input(auth):
    with pytest.raises(ValueError, match="valid email"):
        auth.create_user("nope", "N", "admin", PASSWORD)
    with pytest.raises(ValueError, match="Role"):
        auth.create_user("a@x.com", "N", "owner", PASSWORD)
    with pytest.raises(ValueError, match="existing client"):
        auth.create_user("a@x.com", "N", "client", PASSWORD, "missing")
    staff = auth.create_user("Staff@X.com", "Staff", "reviewer", PASSWORD, client_id="c1")
    assert staff.email == "staff@x.com" and staff.client_id is None  # staff are not tied to a client
    with pytest.raises(ValueError, match="already exists"):
        auth.create_user("staff@x.com", "Again", "admin", PASSWORD)


def test_login_session_and_logout(auth):
    auth.create_user("ana@x.com", "Ana", "reviewer", PASSWORD)
    token, user = auth.login(" ANA@x.com ", PASSWORD)
    assert user.role == "reviewer"
    assert auth.user_for_token(token).email == "ana@x.com"
    assert token not in str(auth.store._all("SELECT token_hash FROM sessions"))  # only the hash is stored
    auth.logout(token)
    with pytest.raises(AuthError):
        auth.user_for_token(token)


def test_wrong_password_unknown_email_and_disabled_user_look_the_same(auth):
    user = auth.create_user("ana@x.com", "Ana", "reviewer", PASSWORD)
    messages = set()
    for email, password in [("ana@x.com", "wrong"), ("ghost@x.com", PASSWORD)]:
        with pytest.raises(AuthError) as err:
            auth.login(email, password)
        messages.add(str(err.value))
    auth.disable_user(user.id)
    with pytest.raises(AuthError) as err:
        auth.login("ana@x.com", PASSWORD)
    messages.add(str(err.value))
    assert messages == {"Email or password is incorrect."}


def test_disabling_or_resetting_password_ends_sessions(auth):
    user = auth.create_user("ana@x.com", "Ana", "reviewer", PASSWORD)
    token, _ = auth.login("ana@x.com", PASSWORD)
    auth.set_password(user.id, "An0therPassword")
    with pytest.raises(AuthError):
        auth.user_for_token(token)
    token, _ = auth.login("ana@x.com", "An0therPassword")
    auth.disable_user(user.id)
    with pytest.raises(AuthError):
        auth.user_for_token(token)


def test_repeated_failures_lock_the_account_for_a_while(auth):
    auth.create_user("ana@x.com", "Ana", "reviewer", PASSWORD)
    for _ in range(5):
        with pytest.raises(AuthError, match="incorrect"):
            auth.login("ana@x.com", "wrong", now=1000.0)
    with pytest.raises(AuthError, match="Too many"):
        auth.login("ana@x.com", PASSWORD, now=1001.0)
    token, _ = auth.login("ana@x.com", PASSWORD, now=1000.0 + LOCKOUT_SECONDS + 1)
    assert token


def test_expired_sessions_are_refused(auth):
    auth.create_user("ana@x.com", "Ana", "reviewer", PASSWORD)
    token, _ = auth.login("ana@x.com", PASSWORD)
    auth.store._write("UPDATE sessions SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))
    with pytest.raises(AuthError, match="expired"):
        auth.user_for_token(token)


def test_signed_file_links(auth):
    query = auth.sign_file_link("doc_1", now=1000)
    params = dict(p.split("=") for p in query.split("&"))
    auth.check_file_link("doc_1", params["expires"], params["sig"], now=1100)
    with pytest.raises(AuthError, match="not valid"):
        auth.check_file_link("doc_2", params["expires"], params["sig"], now=1100)
    with pytest.raises(AuthError, match="not valid"):
        auth.check_file_link("doc_1", str(int(params["expires"]) + 1000), params["sig"], now=1100)
    with pytest.raises(AuthError, match="expired"):
        auth.check_file_link("doc_1", params["expires"], params["sig"], now=1000 + 301)
    with pytest.raises(AuthError):
        auth.check_file_link("doc_1", None, None)


def test_auth_can_be_switched_off_for_local_demos(auth):
    off = auth_from_env(auth.store, {"AUTH_REQUIRED": "0"})
    assert off.user_for_token(None).role == "admin"
    off.check_file_link("doc_1", None, None)
    assert auth_from_env(auth.store, {}).required is True
