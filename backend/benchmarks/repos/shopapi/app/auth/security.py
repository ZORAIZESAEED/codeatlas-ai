"""Password hashing and token helpers."""
import base64
import hashlib
import hmac
import json
import os
import time

from app.config import get_settings

PBKDF2_ROUNDS = 260_000


def hash_password(password: str) -> str:
    """Derive a salted PBKDF2 hash for storing a user's password."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return base64.b64encode(salt + digest).decode()


def verify_password(password: str, stored: str) -> bool:
    """Check a plain-text password against a stored PBKDF2 hash in constant time."""
    raw = base64.b64decode(stored.encode())
    salt, expected = raw[:16], raw[16:]
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return hmac.compare_digest(candidate, expected)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def create_access_token(subject: str, scopes: list[str] | None = None) -> str:
    """Issue a signed JWT (HS256) for the given subject that expires after the configured TTL."""
    settings = get_settings()
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": subject,
        "scopes": scopes or [],
        "exp": int(time.time()) + settings.token_expiry_minutes * 60,
    }
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    signature = hmac.new(settings.jwt_secret.encode(), signing_input.encode(), "sha256").digest()
    return f"{signing_input}.{_b64(signature)}"


def decode_access_token(token: str) -> dict:
    """Verify the signature and expiry of a JWT and return its claims."""
    settings = get_settings()
    signing_input, _, signature = token.rpartition(".")
    expected = hmac.new(settings.jwt_secret.encode(), signing_input.encode(), "sha256").digest()
    if not hmac.compare_digest(_b64(expected), signature):
        raise ValueError("bad token signature")
    payload_part = signing_input.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload_part + "=="))
    if claims["exp"] < time.time():
        raise ValueError("token expired")
    return claims
