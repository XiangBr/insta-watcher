from typing import Any

import pytest
from pydantic import ValidationError

from app.campaigns import CampaignInput, validate_link_url

BASE: dict[str, Any] = {
    "name": "ebook",
    "keywords": ["ebook"],
    "post_id": "18446074984116754",
    "dm_message": "Oi {username}! Aqui está:",
}


def build(**overrides: Any) -> CampaignInput:
    return CampaignInput(**{**BASE, **overrides})


def test_valid_campaign() -> None:
    campaign = build(link_url="https://exemplo.com/ebook", link_button_label="Baixar")
    assert campaign.link_url == "https://exemplo.com/ebook"


@pytest.mark.parametrize(
    "url",
    [
        "http://exemplo.com",
        "javascript:alert(1)",
        "data:text/html,x",
        "https://user:pass@exemplo.com",
        "https://localhost",
        "https://exem plo.com",
        "ftp://exemplo.com",
    ],
)
def test_unsafe_links_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        validate_link_url(url)


@pytest.mark.parametrize(
    "overrides",
    [
        {"post_id": None},  # neither post nor any-post
        {"match_any_post": True},  # both
        {"keywords": ["🔥", "  "]},  # nothing matchable
        {"keywords": []},
        {"link_button_label": "Abrir"},  # button without link
        {"link_url": "https://x.com", "link_button_label": "Um título longo demais"},
        {"dm_message": "x" * 1001},
        {"dm_message": "x" * 990, "link_url": "https://exemplo.com/abc"},
        {"post_id": "abc"},
    ],
)
def test_invalid_campaigns_are_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        build(**overrides)


def test_any_post_campaign() -> None:
    assert build(post_id=None, match_any_post=True).match_any_post
