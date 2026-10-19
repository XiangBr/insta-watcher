"""Log redaction. Query strings and bearer tokens never reach a log line.

The uvicorn access log prints the request path with its query, and the webhook challenge
carries `hub.verify_token` there. httpx logs full request URLs. Both are filtered here.
"""

import logging
import re

_BEARER = re.compile(r"(?i)bearer\s+[\w.\-~+/=]+")
_QUERY = re.compile(r"\?[\w.%\-\[\]]+=[^\s\"']*")


def redact(text: str) -> str:
    text = _BEARER.sub("Bearer [redacted]", text)
    return _QUERY.sub("?[redacted]", text)


def _redact_arg(value: object) -> object:
    return redact(value) if isinstance(value, str) else value


class RedactingFilter(logging.Filter):
    """Redacts the message and each argument in place.

    The arguments keep their shape because formatters such as uvicorn's AccessFormatter
    unpack `record.args` positionally.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        return True


_WATCHED = ("", "uvicorn", "uvicorn.error", "uvicorn.access")


def install_redaction() -> None:
    """Attach the filter to every handler that can emit records.

    Logger-level filters do not see records propagated from child loggers, so the filter
    goes on the handlers of the root and uvicorn loggers. Called after uvicorn has set up
    its own handlers (at app import time).
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    redacting = RedactingFilter()
    for name in _WATCHED:
        logger = logging.getLogger(name)
        for target in (logger, *logger.handlers):
            if not any(isinstance(f, RedactingFilter) for f in target.filters):
                target.addFilter(redacting)

    # httpx logs every request URL at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
