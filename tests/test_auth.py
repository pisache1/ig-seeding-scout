import importlib
import os

import pytest

from app import auth


@pytest.fixture(autouse=True)
def clean():
    auth._attempts.clear()
    yield
    auth._attempts.clear()


def test_public_paths_bypass_the_gate():
    for p in ("/login", "/logout", "/healthz", "/static/app.css",
              "/static/fonts/PretendardVariable.woff2"):
        assert auth.needs_auth(p) is False


def test_webhook_bypasses_session_but_has_its_own_signature_check():
    # Instagram cannot carry a session cookie; /webhook verifies X-Hub-Signature-256.
    assert auth.needs_auth("/webhook") is False


def test_everything_else_is_gated():
    for p in ("/", "/economics", "/audience", "/intake", "/creator/x",
              "/api/roster", "/intake/followers"):
        assert auth.needs_auth(p) is True


def test_static_prefix_is_not_a_bare_substring_match():
    # "/staticky" must not sneak through the /static/ prefix.
    assert auth.needs_auth("/staticky") is True


def test_password_check_requires_configuration(monkeypatch):
    monkeypatch.delenv("SCOUT_PASSWORD", raising=False)
    assert auth.password_matches("") is False
    assert auth.password_matches("anything") is False


def test_password_check_is_exact(monkeypatch):
    monkeypatch.setenv("SCOUT_PASSWORD", "correct horse battery staple")
    assert auth.password_matches("correct horse battery staple") is True
    assert auth.password_matches("correct horse battery stapl") is False
    assert auth.password_matches("") is False


def test_lockout_after_repeated_failures():
    ip = "203.0.113.9"
    for _ in range(auth.MAX_ATTEMPTS - 1):
        auth.record_failure(ip, now=0.0)
    assert auth.locked_out(ip, now=0.0) == 0.0
    auth.record_failure(ip, now=0.0)
    assert auth.locked_out(ip, now=0.0) == pytest.approx(auth.LOCKOUT_SECONDS)


def test_lockout_expires_and_counter_resets():
    ip = "203.0.113.10"
    for _ in range(auth.MAX_ATTEMPTS):
        auth.record_failure(ip, now=0.0)
    after = auth.LOCKOUT_SECONDS + 1
    assert auth.locked_out(ip, now=after) == 0.0
    auth.record_failure(ip, now=after)
    assert auth.locked_out(ip, now=after) == 0.0  # counting started over


def test_success_clears_the_counter():
    ip = "203.0.113.11"
    for _ in range(auth.MAX_ATTEMPTS):
        auth.record_failure(ip, now=0.0)
    auth.clear_failures(ip)
    assert auth.locked_out(ip, now=0.0) == 0.0


def test_lockouts_are_per_ip():
    for _ in range(auth.MAX_ATTEMPTS):
        auth.record_failure("198.51.100.1", now=0.0)
    assert auth.locked_out("198.51.100.1", now=0.0) > 0
    assert auth.locked_out("198.51.100.2", now=0.0) == 0.0


def test_local_host_detection():
    assert auth.is_local("127.0.0.1") and auth.is_local("::1")
    assert not auth.is_local("203.0.113.5")
    assert not auth.is_local(None)


# --- the gate as actually wired -------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_SECRET_KEY", "test-key")
    monkeypatch.setenv("SCOUT_PASSWORD", "s3cret-passphrase")
    from fastapi.testclient import TestClient
    import app.main as m
    monkeypatch.setattr(m.cfg, "db_path", str(tmp_path / "t.db"))
    return TestClient(m.app, follow_redirects=False)


def test_gated_pages_redirect_to_login(client):
    for path in ("/", "/economics", "/audience", "/intake", "/api/roster"):
        r = client.get(path)
        assert r.status_code == 303, path
        assert r.headers["location"] == "/login"


def test_public_pages_stay_reachable(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_wrong_password_does_not_create_a_session(client):
    r = client.post("/login", data={"password": "wrong"})
    assert r.status_code == 200 and "맞지 않습니다" in r.text
    assert client.get("/").status_code == 303


def test_correct_password_opens_the_app_then_logout_closes_it(client):
    r = client.post("/login", data={"password": "s3cret-passphrase"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/logout"' in page.text
    client.get("/logout")
    assert client.get("/").status_code == 303


def test_lockout_is_enforced_through_the_route(client):
    auth._attempts.clear()
    for _ in range(auth.MAX_ATTEMPTS):
        client.post("/login", data={"password": "nope"})
    r = client.post("/login", data={"password": "s3cret-passphrase"})
    assert "시도가 너무 많습니다" in r.text
    assert client.get("/").status_code == 303   # correct password still refused
