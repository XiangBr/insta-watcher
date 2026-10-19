"""Writes the admin API's OpenAPI schema without a running server or real secrets.

`uv run python -m app.export_openapi ../frontend/openapi.json`. The secrets below are random
and thrown away: building the schema needs valid settings, not working ones.
"""

import json
import secrets
import sys
from pathlib import Path

from app.config import Settings
from app.factory import create_app


def build_schema() -> dict[str, object]:
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        meta_app_secret=secrets.token_hex(16),
        instagram_app_secret=secrets.token_hex(16),
        webhook_verify_token=secrets.token_hex(24),
        encryption_key=secrets.token_hex(32),
        api_admin_token=secrets.token_hex(32),
        click_hash_salt=secrets.token_hex(32),
        enable_api_docs=True,
    )
    return create_app(settings).openapi()


def main(argv: list[str]) -> int:
    text = json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n"
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
