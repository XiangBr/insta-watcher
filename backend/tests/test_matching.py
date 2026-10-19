import pytest

from app.matching import KeywordMatcher, normalize, normalize_keywords
from app.models import MatchMode

matcher = KeywordMatcher()
W = MatchMode.WHOLE_WORD
C = MatchMode.CONTAINS


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Preço", "preco"),
        ("PREÇO!!!", "preco"),
        ("quero   o LINK 🔥🔥", "quero o link"),
        ("ação, coração; MÃE", "acao coracao mae"),
        ("👀👀", ""),
        ("Ｌｉｎｋ", "link"),  # noqa: RUF001 (full-width letters on purpose)
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize("text", ["preço", "preco", "PRECO", "Preço?", "qual o PREÇO 🙏"])
def test_accents_and_case_do_not_matter(text: str) -> None:
    assert matcher.match(text, ["preço"], W).matched
    assert matcher.match(text, ["preco"], W).matched


def test_whole_word_does_not_match_inside_other_words() -> None:
    assert not matcher.match("linkedin", ["link"], W).matched
    assert not matcher.match("precoce", ["preco"], W).matched
    assert matcher.match("manda o link!", ["link"], W).matched


def test_contains_matches_inside_other_words() -> None:
    assert matcher.match("linkedin", ["link"], C).matched
    assert matcher.match("precoce", ["preco"], C).matched


def test_emoji_around_or_glued_to_the_word() -> None:
    assert matcher.match("🔥link🔥", ["link"], W).matched
    assert matcher.match("link😍", ["link"], W).matched
    assert not matcher.match("🔥🔥", ["link"], W).matched


def test_any_keyword_matches_and_reports_which() -> None:
    result = matcher.match("me passa o valor", ["preço", "valor", "link"], W)
    assert result.matched
    assert result.keyword == "valor"


def test_multi_word_keyword() -> None:
    assert matcher.match("Quero o E-BOOK grátis!", ["e-book gratis"], W).matched
    assert not matcher.match("e-book pago, grátis não", ["e-book gratis"], W).matched


def test_empty_inputs_never_match() -> None:
    assert not matcher.match("", ["link"], W).matched
    assert not matcher.match("link", [], W).matched
    assert not matcher.match("link", ["🔥"], W).matched


def test_normalize_keywords_dedupes_and_drops_empty() -> None:
    assert normalize_keywords(["Preço", "preco", " ", "🔥", "Link"]) == ["preco", "link"]
