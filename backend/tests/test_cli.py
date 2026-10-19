from collections.abc import Iterator

import pytest

from app.cli import clean_pasted_token, describe_token, main
from app.config import get_settings
from tests.conftest import strong_secrets

TOKEN = "IGAAexampleOnlyNotReal_0123-abcDEF"


def test_bracketed_paste_markers_and_whitespace_are_removed() -> None:
    assert clean_pasted_token(f"\x1b[200~{TOKEN}\x1b[201~\r\n") == TOKEN
    assert clean_pasted_token(f"  {TOKEN[:10]}\n{TOKEN[10:]}  ") == TOKEN


def test_description_never_contains_the_secret_part() -> None:
    shape = describe_token(TOKEN)
    assert TOKEN[4:] not in shape
    assert "starts_with='IGAA'" in shape
    assert f"length={len(TOKEN)}" in shape


def test_facebook_token_is_flagged() -> None:
    assert "Facebook token" in describe_token("EAAB" + "x" * 40)


def test_unexpected_characters_are_flagged() -> None:
    assert "unexpected characters" in describe_token(TOKEN + "é")


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> Iterator[None]:
    for name, value in strong_secrets().items():
        monkeypatch.setenv(name.upper(), value)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_campaign_lifecycle_through_the_cli(
    cli_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["campaign", "create", "--name", "ebook", "--keyword", "ebook", "--keyword", "pdf"]
    args += ["--post-id", "18446074984116754", "--message", "Oi {username}!\nSegue:"]
    args += ["--link", "https://exemplo.com/e", "--button", "Baixar"]
    assert main(args) == 0
    assert "campaign 1 created (active)" in capsys.readouterr().out

    assert main(["campaign", "list"]) == 0
    listing = capsys.readouterr().out
    assert "[1] ACTIVE ebook | post 18446074984116754 | ebook, pdf" in listing

    assert main(["campaign", "pause", "1"]) == 0
    assert main(["campaign", "list"]) == 0
    assert "[1] paused" in capsys.readouterr().out


def test_invalid_campaign_is_explained(cli_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["campaign", "create", "--name", "x", "--keyword", "x", "--any-post"]
    args += ["--message", "oi", "--link", "http://inseguro.com"]
    assert main(args) == 1
    assert "https" in capsys.readouterr().out


def test_pause_needs_an_account(cli_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["pause"]) == 1
    assert "set-token" in capsys.readouterr().out
