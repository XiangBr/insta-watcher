import secrets

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from tests.conftest import strong_secrets

DB = "sqlite+aiosqlite:///:memory:"


def build(**overrides: object) -> Settings:
    values = {"database_url": DB, **strong_secrets(), **overrides}
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_strong_secrets_are_accepted() -> None:
    settings = build()
    assert len(settings.app_secrets()) == 2


@pytest.mark.parametrize("name", list(strong_secrets()))
def test_missing_secret_refuses_to_start(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(name.upper(), raising=False)
    values = {"database_url": DB, **strong_secrets()}
    del values[name]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("webhook_verify_token", "short"),
        ("api_admin_token", secrets.token_hex(16)),  # 32 chars, needs 64
        ("meta_app_secret", "your-app-secret-goes-here-123456"),
        ("click_hash_salt", "changeme" * 5),
        ("instagram_app_secret", "a" * 40),
        ("encryption_key", "z" * 64),
        ("encryption_key", "ab" * 31),
    ],
)
def test_weak_secret_refuses_to_start(name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        build(**{name: value})


def test_startup_error_names_the_variable_but_not_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    almost_real = secrets.token_hex(16)[:31]  # a Meta secret pasted one character short
    for name, value in strong_secrets().items():
        monkeypatch.setenv(name.upper(), value)
    monkeypatch.setenv("META_APP_SECRET", almost_real)
    monkeypatch.setenv("DATABASE_URL", DB)
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as excinfo:
            get_settings()
    finally:
        get_settings.cache_clear()
    message = str(excinfo.value)
    assert "META_APP_SECRET" in message
    assert almost_real not in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_secrets_do_not_leak_in_repr() -> None:
    settings = build()
    token = settings.api_admin_token.get_secret_value()
    assert token not in repr(settings)
    assert token not in str(settings)


def test_api_docs_off_by_default() -> None:
    assert build().enable_api_docs is False
