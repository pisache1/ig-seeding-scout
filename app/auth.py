"""Password gate.

The tool was written to run on one laptop. Putting it on the internet means
the roster, the follower export — real people's usernames — and eventually
an Instagram access token are all one URL away, so nothing is served until
the visitor has logged in.

One shared password, set in the environment. There are no user accounts
because there is one operator; if that stops being true, this is the file to
replace.

Safe default: with no password configured, requests from anywhere except the
local machine are refused outright rather than served openly. Deploying
without setting SCOUT_PASSWORD therefore fails closed, loudly.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import time

log = logging.getLogger(__name__)

# Paths reachable without a session.
PUBLIC_PATHS = {"/login", "/logout", "/healthz"}
PUBLIC_PREFIXES = ("/static/",)
# Meta signs its webhook calls with the app secret; that is its own gate and
# Instagram cannot carry a session cookie.
WEBHOOK_PATHS = {"/webhook"}

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
_attempts: dict[str, tuple[int, float]] = {}


def configured_password() -> str | None:
    return os.getenv("SCOUT_PASSWORD") or None


def secret_key() -> str:
    """Signs the session cookie.

    Generated per process when unset, which logs everyone out on restart —
    fine locally, which is why deployments should set it explicitly.
    """
    key = os.getenv("SCOUT_SECRET_KEY")
    if key:
        return key
    log.warning("SCOUT_SECRET_KEY 미설정 — 재시작하면 로그인이 풀립니다")
    return secrets.token_urlsafe(32)


def is_local(client_host: str | None) -> bool:
    return (client_host or "") in LOCAL_HOSTS


def needs_auth(path: str) -> bool:
    if path in PUBLIC_PATHS or path in WEBHOOK_PATHS:
        return False
    return not path.startswith(PUBLIC_PREFIXES)


def locked_out(ip: str, now: float | None = None) -> float:
    """Seconds remaining on a lockout, 0 if none."""
    now = now if now is not None else time.monotonic()
    count, until = _attempts.get(ip, (0, 0.0))
    return max(0.0, until - now) if count >= MAX_ATTEMPTS else 0.0


def record_failure(ip: str, now: float | None = None) -> None:
    now = now if now is not None else time.monotonic()
    count, until = _attempts.get(ip, (0, 0.0))
    if until and now > until:
        count = 0  # lockout expired, start over
    count += 1
    _attempts[ip] = (count, now + LOCKOUT_SECONDS if count >= MAX_ATTEMPTS else 0.0)


def clear_failures(ip: str) -> None:
    _attempts.pop(ip, None)


def password_matches(supplied: str) -> bool:
    expected = configured_password()
    if not expected:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())
