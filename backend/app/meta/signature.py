"""Webhook authenticity: HMAC-SHA256 over the raw body, compared in constant time."""

import hashlib
import hmac
from collections.abc import Iterable

_PREFIX = "sha256="


def verify_signature(body: bytes, header: str | None, secrets: Iterable[bytes]) -> bool:
    """Return True if `header` (X-Hub-Signature-256) matches `body` under any secret.

    Every secret is checked even after a match, so timing does not reveal which one matched.
    """
    if not header or not header.startswith(_PREFIX):
        return False
    received = header[len(_PREFIX) :].strip().lower().encode()
    matched = False
    for secret in secrets:
        if not secret:
            continue
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest().encode()
        matched |= hmac.compare_digest(expected, received)
    return matched


def tokens_equal(given: str | None, expected: str) -> bool:
    """Constant-time comparison for verify tokens and admin tokens."""
    if given is None:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())
