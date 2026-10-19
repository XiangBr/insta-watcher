"""Keyword matching. Case, accents, emoji and punctuation never matter.

The dispatcher depends only on the TriggerMatcher protocol, so an intent classifier can be
plugged in later as another implementation (see docs/architecture.md).
"""

import unicodedata
from dataclasses import dataclass
from typing import Protocol

from app.models import MatchMode


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    keyword: str | None = None


class TriggerMatcher(Protocol):
    def match(self, text: str, keywords: list[str], mode: MatchMode) -> MatchResult: ...


def normalize(text: str) -> str:
    """Lowercase, strip diacritics, turn everything that is not a letter or digit into spaces.

    `Preço!!` -> `preco`, `QUERO  o LINK 🔥` -> `quero o link`.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    kept: list[str] = []
    for ch in decomposed:
        category = unicodedata.category(ch)
        if category.startswith("M"):  # combining marks: the accents after NFKD
            continue
        kept.append(ch if category[0] in ("L", "N") else " ")
    return " ".join("".join(kept).split())


def normalize_keywords(keywords: list[str]) -> list[str]:
    """Normalized, de-duplicated, non-empty keywords. Used to validate campaigns too."""
    seen: dict[str, None] = {}
    for keyword in keywords:
        normalized = normalize(keyword)
        if normalized:
            seen.setdefault(normalized, None)
    return list(seen)


class KeywordMatcher:
    """Any keyword matching is enough (OR)."""

    def match(self, text: str, keywords: list[str], mode: MatchMode) -> MatchResult:
        haystack = normalize(text)
        if not haystack:
            return MatchResult(matched=False)
        padded = f" {haystack} "
        for original in keywords:
            needle = normalize(original)
            if not needle:
                continue
            found = f" {needle} " in padded if mode == MatchMode.WHOLE_WORD else needle in haystack
            if found:
                return MatchResult(matched=True, keyword=original)
        return MatchResult(matched=False)
