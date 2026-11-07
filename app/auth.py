from __future__ import annotations

import hashlib
import hmac
import time


def password_ok(provided: str, expected: str) -> bool:
    left = hashlib.sha256(provided.encode()).digest()
    right = hashlib.sha256(expected.encode()).digest()
    return hmac.compare_digest(left, right)


def issue_session(secret: bytes, password: str, ttl: int = 60 * 60 * 24 * 30) -> str:
    expires = int(time.time()) + ttl
    message = str(expires).encode()
    signature = hmac.new(secret, message + b"\0" + password.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def session_valid(token: str | None, secret: bytes, password: str) -> bool:
    if not token or not password or "." not in token:
        return False
    expires_text, signature = token.split(".", 1)
    try:
        expires = int(expires_text)
    except ValueError:
        return False
    if expires < int(time.time()):
        return False
    expected = hmac.new(secret, expires_text.encode() + b"\0" + password.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)
