"""Optional single-user protection for the research section.

Set RESEARCH_PASSWORD on the API service. Then every /api/research route (except /health and
/auth/*) needs `Authorization: Bearer <token>` from POST /api/research/auth/login. Tokens are
HMAC-signed with RESEARCH_SECRET (or a hash of the password) and expire after 30 days.
Without RESEARCH_PASSWORD the section is open — fine on your own machine, not on a public URL.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from fastapi import Header, HTTPException

TOKEN_DAYS = 30


def password() -> str:
    return os.getenv("RESEARCH_PASSWORD", "")


def protected() -> bool:
    return bool(password())


def _secret() -> bytes:
    return (os.getenv("RESEARCH_SECRET") or hashlib.sha256(("swing-research:" + password()).encode()).hexdigest()).encode()


def issue_token(now: float | None = None) -> tuple[str, int]:
    exp = int((now or time.time()) + TOKEN_DAYS * 86400)
    payload = str(exp).encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + sig, exp


def check_token(token: str, now: float | None = None) -> bool:
    try:
        p64, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4))
        exp = int(payload)
    except (ValueError, TypeError):
        return False
    good = hmac.new(_secret(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig) and exp > (now or time.time())


def login(given: str) -> tuple[str, int] | None:
    if not protected():
        return issue_token()
    if hmac.compare_digest(given.encode(), password().encode()):
        return issue_token()
    time.sleep(1.0)            # slow down guessing
    return None


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not protected():
        return
    if not authorization or not authorization.startswith("Bearer ") or not check_token(authorization[7:]):
        raise HTTPException(status_code=401, detail="Sign in to the research section.")
