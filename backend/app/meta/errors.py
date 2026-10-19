"""How a call to Meta can fail, and what each failure allows us to do next.

The rule: only an explicit rejection proves a message did not leave. Anything else after the
request was sent (timeout, 5xx, code 1/2, a 2xx without an id) is ambiguous and must not be
retried automatically, because a duplicate DM is worse than a missing one.
"""

from enum import StrEnum

from app.logging_setup import redact

_MAX_MESSAGE = 300


class RejectionKind(StrEnum):
    TOKEN_INVALID = "TOKEN_INVALID"  # noqa: S105 (kind name, not a secret)
    RATE_LIMITED = "RATE_LIMITED"
    PERMANENT = "PERMANENT"


# Codes from the Graph API docs and from how OpenReply treats them (docs/architecture.md).
TOKEN_CODES = frozenset({190})
RATE_LIMIT_CODES = frozenset({4, 17, 32, 368, 613})
AMBIGUOUS_CODES = frozenset({1, 2})


def safe_text(text: str) -> str:
    return redact(text)[:_MAX_MESSAGE]


class MetaError(Exception):
    """Base class. `str()` is always safe to store and log."""


class NotSent(MetaError):
    """The request never reached Meta (connection failed). Retrying cannot duplicate."""


class AmbiguousFailure(MetaError):
    """The request may have been processed. Never retry automatically."""


class ConfirmedRejection(MetaError):
    """Meta answered with an explicit error: nothing was sent."""

    def __init__(
        self,
        kind: RejectionKind,
        *,
        http_status: int,
        code: int | None,
        subcode: int | None,
        message: str,
    ) -> None:
        self.kind = kind
        self.http_status = http_status
        self.code = code
        self.subcode = subcode
        self.message = safe_text(message)
        super().__init__(
            f"{kind} (http={http_status} code={code} subcode={subcode}): {self.message}"
        )


def classify_error_response(http_status: int, body: object) -> MetaError:
    """Turn a non-2xx response into the right error class."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        # No Graph error envelope: a proxy or an outage. We cannot tell what happened.
        return AmbiguousFailure(f"http {http_status} without a Graph error body")
    code = error.get("code") if isinstance(error.get("code"), int) else None
    subcode = error.get("error_subcode") if isinstance(error.get("error_subcode"), int) else None
    message = str(error.get("message", ""))
    if http_status >= 500 or code in AMBIGUOUS_CODES:
        return AmbiguousFailure(
            f"http {http_status} code={code} subcode={subcode}: {safe_text(message)}"
        )
    if code in TOKEN_CODES:
        kind = RejectionKind.TOKEN_INVALID
    elif code in RATE_LIMIT_CODES:
        kind = RejectionKind.RATE_LIMITED
    else:
        kind = RejectionKind.PERMANENT
    return ConfirmedRejection(
        kind, http_status=http_status, code=code, subcode=subcode, message=message
    )
