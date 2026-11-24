"""Text helpers shared by the goal compiler (blueprint 8.15): normalisation and whole-word
phrase matching, longest phrase first."""

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache

ISSUER_TOKEN = "<issuer>"
YEAR_TOKEN = "<year>"
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                         "–": "-", "—": "-"})

Match = tuple[str, int, int]  # (phrase, start, end)


def normalise(text: str) -> str:
    """Lower case, straight quotes and dashes, single spaces, no trailing ? . or !"""
    text = " ".join(text.translate(_QUOTES).lower().split())
    return text.rstrip("?.! ").strip()


@lru_cache(maxsize=4096)
def phrase_pattern(phrase: str) -> re.Pattern[str]:
    """A whole-word pattern for a lower-case phrase; spaces match any run of whitespace."""
    body = r"\s+".join(re.escape(part) for part in phrase.split())
    return re.compile(r"(?<![\w&])" + body + r"(?![\w&])")


def find_phrases(text: str, phrases: Iterable[str]) -> list[Match]:
    """Non-overlapping whole-word matches in `text`, longest phrase first, in text order."""
    taken: list[tuple[int, int]] = []
    found: list[Match] = []
    for phrase in sorted(set(phrases), key=lambda p: (-len(p), p)):
        for m in phrase_pattern(phrase).finditer(text):
            if any(m.start() < end and start < m.end() for start, end in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append((phrase, m.start(), m.end()))
    return sorted(found, key=lambda f: f[1])


def remove_spans(text: str, spans: Sequence[tuple[int, int]], fill: str = " ") -> str:
    """`text` with each (start, end) span replaced by `fill`, single-spaced."""
    out, last = [], 0
    for start, end in sorted(spans):
        out.append(text[last:start])
        out.append(fill)
        last = end
    out.append(text[last:])
    return " ".join("".join(out).split())
