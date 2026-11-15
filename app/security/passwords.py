"""Password hashing with the standard library's memory-hard scrypt."""

import base64
import hashlib
import hmac
import secrets
from functools import cache

# OWASP minimum for scrypt: N=2^17, r=8, p=1 (128 MiB). maxmem must exceed 128 * N * r.
N, R, P = 2**17, 8, 1
MAXMEM = 256 * 1024 * 1024
PREFIX = "scrypt"


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=32
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, N, R, P)
    encode = base64.urlsafe_b64encode
    return f"{PREFIX}${N}${R}${P}${encode(salt).decode()}${encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        prefix, n, r, p, salt, digest = stored.split("$")
        if prefix != PREFIX:
            return False
        expected = base64.urlsafe_b64decode(digest)
        actual = _derive(password, base64.urlsafe_b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


@cache
def dummy_hash() -> str:
    """Verified against when the email is unknown, so timing does not reveal registration."""
    return hash_password(secrets.token_urlsafe(16))
