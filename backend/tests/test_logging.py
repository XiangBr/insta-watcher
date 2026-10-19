import logging

import pytest
from uvicorn.logging import AccessFormatter

from app.logging_setup import RedactingFilter, redact


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        (
            '"GET /webhook?hub.mode=subscribe&hub.verify_token=s3cr3tv4lu3 HTTP/1.1" 200',
            "s3cr3tv4lu3",
        ),
        (
            "GET https://graph.instagram.com/refresh_access_token?access_token=IGQabc123",
            "IGQabc123",
        ),
        ("Authorization: Bearer IGQVJxyz.987-_", "IGQVJxyz.987-_"),
    ],
)
def test_secrets_are_redacted(raw: str, secret: str) -> None:
    assert secret not in redact(raw)


def test_plain_text_is_untouched() -> None:
    assert redact("worker started, is it ok?") == "worker started, is it ok?"


def test_filter_rewrites_formatted_record() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '"%s %s"',
        ("GET", "/webhook?hub.verify_token=abc"),
        None,
    )
    RedactingFilter().filter(record)
    assert "abc" not in record.getMessage()


def test_uvicorn_access_formatter_still_works_after_redaction() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/webhook?hub.verify_token=s3cr3t", "1.1", 200),
        None,
    )
    RedactingFilter().filter(record)
    line = AccessFormatter(fmt="%(message)s", use_colors=False).format(record)
    assert "s3cr3t" not in line
    assert "GET /webhook?[redacted]" in line
