"""Clause splitting, conditions and fiscal years (blueprint 8.15 steps 2 and 5; Phase 6).

Works on the normalised question with issuers masked (finagent.planner.text). A clause ends at
";", at the end of a sentence, at ", if" and at "and" or ", then" before an action word (the
prototype rule, with the action words in configs/intents.yaml). A clause is conditional when a
marker ("if", "in case", ...) introduces a liquidity shortfall ("if there is a shortfall, check
debt service"), or when it opens with a back reference ("if so, ..."); the condition phrase is
removed from the clause body. A marker that introduces anything else makes the condition
unsupported; "if" in the middle of a clause with no shortfall after it ("check if the statements
reconcile") is not a condition. A clause with no request content (no company, period, metric,
scenario or request word; has_request_content) is dropped by the compiler and recorded (#232).

Fiscal years: FY2025, FY25, FY 2025, fiscal (year) 2025, a bare 19xx/20xx that is not an amount
($2025, 2,025, 2025 million, 2025%, 2025.5, 2025,000), and ranges (2023-2025, FY23 to FY25: two
years). A bare year may end at a comma or a full stop ("at the end of 2023, please", #223).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from finagent.config import IntentVocabulary
from finagent.planner.text import ISSUER_TOKEN, YEAR_TOKEN, find_phrases, phrase_pattern

_SENTENCE = re.compile(r"(?<!\bvs)[.?!]+\s+(?=[a-z<])")
_YEAR_DIGITS = r"((?:19|20)\d{2}|'?\d{2})"
_FY = r"(?:fy\s?'?|fiscal\s+(?:year\s+)?'?)"
# Not an amount: no digit, decimal or thousands part ("2025.5", "2025,000") and no unit after it;
# a comma or full stop that ends the year is fine (#223).
_AMOUNT_AFTER = (r"(?![\d%]|[,.]\d|\s*(?:%|percent|million|billion|thousand|mn|bn|mm|usd|"
                 r"dollars)\b)")
_RANGE = re.compile(_FY + r"?((?:19|20)\d{2}|\d{2})\s*(?:-|to|through|until|and)\s*" + _FY +
                    r"?((?:19|20)\d{2}|\d{2})\b")
_FY_YEAR = re.compile(r"\b" + _FY + _YEAR_DIGITS + r"\b")
_BARE_YEAR = re.compile(r"(?<![\d$,.])(?<!\$\s)((?:19|20)\d{2})\b" + _AMOUNT_AFTER)


@dataclass(frozen=True)
class Condition:
    """A clause's condition: conditional or not, supported (a liquidity shortfall) or not, and
    the clause body with the condition phrase removed."""

    conditional: bool
    supported: bool
    body: str


@dataclass(frozen=True)
class YearMatch:
    year: int
    start: int
    end: int


def split_clauses(text: str, vocabulary: IntentVocabulary) -> list[str]:
    """Clauses of a normalised, issuer-masked question; leading filler words dropped."""
    words = "|".join(re.escape(w) for w in sorted(vocabulary.action_words, key=len,
                                                   reverse=True))
    before_action = re.compile(r",?\s+and\s+(?=(?:" + words + r")\b)|,\s*then\s+|,\s+(?=if\b)")
    clauses = []
    for sentence in _SENTENCE.split(text):
        for part in re.split(r"\s*;\s*", sentence):
            clauses += before_action.split(part)
    return [c for c in (strip_lead(c, vocabulary) for c in clauses) if c]


def strip_lead(clause: str, vocabulary: IntentVocabulary) -> str:
    """The clause without leading filler words ("and", "then", "please") and punctuation."""
    clause = clause.strip(" ,;:.")
    lead = re.compile(r"^(?:" + "|".join(re.escape(w) for w in vocabulary.lead_words) +
                      r")\b[\s,]*")
    while (stripped := lead.sub("", clause)) != clause:
        clause = stripped
    return clause.strip(" ,;:.")


def detect_condition(clause: str, vocabulary: IntentVocabulary) -> Condition:
    """The clause's condition (module docstring)."""
    for back in sorted(vocabulary.back_references, key=len, reverse=True):
        m = phrase_pattern(back).match(clause)
        if m:
            return Condition(True, True, clause[m.end():].strip(" ,"))
    markers = find_phrases(clause, vocabulary.condition_markers)
    if not markers:
        return Condition(False, True, clause)
    _, start, end = markers[0]
    subjects = [s for s in find_phrases(clause, vocabulary.condition_subjects) if s[1] >= end]
    at_start = clause[:start].strip(" ,") == ""
    if not subjects:
        return Condition(True, False, clause) if at_start else Condition(False, True, clause)
    stop = subjects[0][2]
    rest = re.match(r"\s*(?:,|then\b)?\s*", clause[stop:])
    body = (clause[:start] + " " + clause[stop + (rest.end() if rest else 0):]).strip(" ,")
    return Condition(True, True, " ".join(body.split()))


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def has_request_content(text: str, years: Sequence[int], vocabulary: IntentVocabulary) -> bool:
    """True when an issuer-masked clause names a company, a fiscal year or period phrase, a
    metric of any goal kind, a scenario, or a request word (#232)."""
    if ISSUER_TOKEN in text or years:
        return True
    phrases = {p for table in vocabulary.metrics.values() for ps in table.values() for p in ps}
    phrases |= set(vocabulary.latest_phrases) | set(vocabulary.relative_phrases)
    phrases |= set(vocabulary.multiple_phrases) | set(vocabulary.ambiguous_scenarios)
    phrases |= {p for ps in vocabulary.targets.values() for p in ps}
    phrases |= {p for ps in vocabulary.scenarios.values() for p in ps}
    phrases |= set(vocabulary.request_words)
    return bool(find_phrases(text, phrases))


def numbers_in(text: str) -> tuple[str, ...]:
    """The figures written in a clause ("2025", "1,234.5")."""
    return tuple(_NUMBER.findall(text))


def _year(digits: str) -> int:
    digits = digits.lstrip("'")
    return int(digits) if len(digits) == 4 else 2000 + int(digits)


def find_years(text: str) -> list[YearMatch]:
    """Fiscal years named in a normalised clause, in text order (module docstring)."""
    found: list[YearMatch] = []

    def add(year: int, start: int, end: int) -> None:
        if not any(start < m.end and m.start < end for m in found):
            found.append(YearMatch(year, start, end))

    for m in _RANGE.finditer(text):
        first, second = m.group(1), m.group(2)
        if len(first) == 2 and not re.match(r"\s*" + _FY, text[m.start():]):
            continue  # "12-15" is not a range of years unless written with FY
        add(_year(first), m.start(), m.end())
        found.append(YearMatch(_year(second), m.start(), m.end()))
    for m in _FY_YEAR.finditer(text):
        add(_year(m.group(1)), m.start(), m.end())
    for m in _BARE_YEAR.finditer(text):
        add(int(m.group(1)), m.start(), m.end())
    return sorted(found, key=lambda m: (m.start, m.year))


def mask_years(text: str, years: Sequence[YearMatch]) -> str:
    """The clause with each year (or year range) replaced by the year token."""
    spans = sorted({(m.start, m.end) for m in years})
    out, last = [], 0
    for start, end in spans:
        out += [text[last:start], YEAR_TOKEN]
        last = end
    out.append(text[last:])
    return " ".join("".join(out).split())


def distinct_years(years: Sequence[YearMatch]) -> list[int]:
    return list(dict.fromkeys(m.year for m in years))
