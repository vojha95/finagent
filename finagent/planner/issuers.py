"""Issuer resolution for the goal compiler (blueprint 8.15 step 1; Phase 6).

Names come from configs/universe.yaml (names); tickers and CIKs from the pinned SEC
company_tickers.json (read-only, hash-checked), which generalises the prototype's two hand-written
issuer patterns. Mentions found in a question (#231, Vineet's rules):

- a universe name, case-insensitive, with or without a possessive, which may be written with or
  without an apostrophe ("Chevron's", "chevrons", "abbotts"). A one-word name that is also a
  common English word (the bundled general word list, configs/intents.yaml common_words; never a
  per-company list) needs a capital letter or a possessive: "Target", "target's", not "the target
  year";
- a universe ticker written in capitals ("AMZN", "$MSFT"), or in lower case when it is not a
  common English word ("msft"; never "low", "cost" or "t"), and not straight after an apostrophe;
- an SEC CIK ("CIK 0000789019");
- a company outside the universe: an SEC registrant title written with capitals ("Tesla"), or a
  capitalised possessive that is no known name or question word ("Acme's"). Either makes the
  question a CLARIFICATION, so a question about another company is never answered with the
  selected one.

Rules (resolve): no issuer -> the selected issuer, else NO_ISSUER; one issuer other than the
selected one -> ISSUER_MISMATCH; a second issuer only with an explicit comparison cue, else
SECOND_ISSUER; more than max_issuers -> TOO_MANY_ISSUERS. Held-out issuers are resolved and
reported (the planner reads no data for them, 9.2). Nothing here reads a filing.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from finagent.config import HELD_OUT, IntentVocabulary, IssuerNames
from finagent.planner.text import ISSUER_TOKEN, find_phrases, normalise

_POSSESSIVE = r"(?:['’]s)?"
# A name's possessive: 's, a bare apostrophe after a final s ("Starbucks'"), or s alone ("abbotts").
_NAME_POSSESSIVE = r"(?P<possessive>['’]s|(?<=s)['’]|s)?"
_TOKEN = re.compile(r"[A-Z][A-Za-z0-9&.'’-]*")
_CIK = re.compile(r"\bcik\s*(?:no\.?|number|#|:)?\s*0*(\d{1,10})\b", re.IGNORECASE)
_YEARISH = re.compile(r"^(?:fy)?'?\d+$", re.IGNORECASE)
MAX_TITLE_TOKENS = 5


@dataclass(frozen=True)
class IssuerMention:
    """A company named in a question. ticker is None for a company outside the universe."""

    ticker: str | None
    start: int
    end: int
    text: str
    source: str  # name / ticker / cik / registrant / possessive


@dataclass(frozen=True)
class IssuerIndex:
    """Everything issuer resolution needs: the universe (ticker -> split), its names, and the
    SEC registrant titles and CIKs (normalised title -> ticker, CIK -> ticker)."""

    universe: dict[str, str]
    names: IssuerNames
    registrants: dict[str, str]
    ciks: dict[int, str]
    stop_words: frozenset[str]
    title_suffixes: frozenset[str]
    common_words: frozenset[str] = frozenset()  # #231: the bundled general English word list

    @classmethod
    def build(cls, universe: Mapping[str, str], names: IssuerNames,
              sec_tickers: Iterable[Mapping], vocabulary: IntentVocabulary,
              common_words: Iterable[str] = ()) -> "IssuerIndex":
        """From the SEC ticker map entries ({"cik_str", "ticker", "title"})."""
        suffixes = vocabulary.title_suffixes
        registrants: dict[str, str] = {}
        ciks: dict[int, str] = {}
        for entry in sec_tickers:
            ticker = str(entry["ticker"]).upper()
            ciks.setdefault(int(entry["cik_str"]), ticker)
            title = normalise_title(str(entry["title"]), suffixes)
            if title:
                registrants.setdefault(title, ticker)
        return cls(dict(universe), names, registrants, ciks,
                   stop_words(vocabulary), suffixes, frozenset(common_words))

    @classmethod
    def load(cls, evidence_dir: Path | str | None = None,
             vocabulary: IntentVocabulary | None = None) -> "IssuerIndex":
        """From configs, the bundled word list (hash-checked) and the pinned company_tickers.json
        (hash-checked, never written)."""
        from finagent.config import (load_common_words, load_intent_vocabulary,
                                     load_issuer_names, load_universe)
        from finagent.evidence.integrity import read_snapshot_json
        from finagent.evidence.store import EvidenceStore

        store = EvidenceStore(evidence_dir or Path(__file__).resolve().parents[2] / "evidence")
        sec = read_snapshot_json(store.evidence_dir, "company_tickers.json", store.manifest)
        return cls.build(load_universe(), load_issuer_names(), sec.values(),
                         vocabulary or load_intent_vocabulary(), load_common_words())

    def is_common_word(self, text: str) -> bool:
        """A single word of the bundled general English word list (case-insensitive)."""
        return text.lower() in self.common_words


@dataclass(frozen=True)
class IssuerResolution:
    issuers: tuple[str, ...]
    mentions: tuple[IssuerMention, ...]
    comparison: bool
    clarification: str | None = None  # a configs/intents.yaml clarification code
    held_out: tuple[str, ...] = ()


def stop_words(vocabulary: IntentVocabulary) -> frozenset[str]:
    """Words never taken for a company name: question words and every vocabulary phrase word."""
    words = set(vocabulary.question_words) | set(vocabulary.action_words)
    for table in vocabulary.metrics.values():
        for phrases in table.values():
            words.update(w for p in phrases for w in p.split())
    for phrases in list(vocabulary.scenarios.values()) + [vocabulary.ambiguous_scenarios]:
        words.update(w for p in phrases for w in p.split())
    return frozenset(words)


def normalise_title(title: str, suffixes: Iterable[str]) -> str:
    """An SEC title or a question span reduced for matching: lower case, no possessive, no
    punctuation other than '&', no corporate suffix words ("AMAZON COM INC" -> "amazon com")."""
    text = re.sub(r"['’]s\b", "", title.lower())
    text = re.sub(r"/[a-z]{2}/", " ", text)  # state of incorporation, e.g. /DE/
    words = re.sub(r"[^a-z0-9& ]+", " ", text).split()
    drop = set(suffixes)
    return " ".join(w for w in words if w not in drop)


def _name_pattern(name: str) -> re.Pattern[str]:
    """The name in any case, with an optional possessive (group "possessive")."""
    body = r"\s+".join(re.escape(p) for p in name.split())
    return re.compile(r"(?<![\w&$])" + body + _NAME_POSSESSIVE + r"(?![\w&])", re.IGNORECASE)


def _ticker_pattern(ticker: str, any_case: bool = False) -> re.Pattern[str]:
    """The ticker in capitals; with any_case also in lower or mixed case, then never straight
    after an apostrophe ("can't" is not T)."""
    if any_case:
        return re.compile(r"(?<![\w&'’])\$?" + re.escape(ticker) + _POSSESSIVE + r"(?![\w&])",
                          re.IGNORECASE)
    return re.compile(r"(?<![\w&])\$?" + re.escape(ticker) + _POSSESSIVE + r"(?![\w&])")


def name_matches(name: str, index: IssuerIndex, question: str) -> list[re.Match[str]]:
    """Mentions of one universe name (#231): any case, possessive optional; a one-word name that
    is a common English word only with a capital letter or a possessive."""
    common = len(name.split()) == 1 and index.is_common_word(name)
    return [m for m in _name_pattern(name).finditer(question)
            if not common or m.group(0)[:1].isupper() or m.group("possessive")]


def _overlaps(start: int, end: int, taken: Sequence[IssuerMention]) -> bool:
    return any(start < m.end and m.start < end for m in taken)


def _add(found: list[IssuerMention], mention: IssuerMention) -> None:
    if not _overlaps(mention.start, mention.end, found):
        found.append(mention)


def universe_mentions(question: str, index: IssuerIndex) -> list[IssuerMention]:
    """Universe names and tickers (#231 rules, module docstring), longest mention first."""
    candidates: list[IssuerMention] = []
    for ticker, listed in index.names.names.items():
        for name in listed:
            candidates += [IssuerMention(ticker, m.start(), m.end(), m.group(0), "name")
                           for m in name_matches(name, index, question)]
        pattern = _ticker_pattern(ticker, any_case=not index.is_common_word(ticker))
        candidates += [IssuerMention(ticker, m.start(), m.end(), m.group(0), "ticker")
                       for m in pattern.finditer(question)]
    found: list[IssuerMention] = []
    for mention in sorted(candidates, key=lambda m: (-(m.end - m.start), m.start)):
        _add(found, mention)
    return found


def cik_mentions(question: str, index: IssuerIndex) -> list[IssuerMention]:
    """CIKs: a universe ticker, or None for a registrant outside the universe."""
    found = []
    for m in _CIK.finditer(question):
        ticker = index.ciks.get(int(m.group(1)))
        found.append(IssuerMention(ticker if ticker in index.universe else None,
                                   m.start(), m.end(), m.group(0), "cik"))
    return found


def _capitalised_runs(question: str) -> list[list[re.Match[str]]]:
    """Runs of adjacent capitalised tokens (joined by spaces, '&' or 'and')."""
    runs: list[list[re.Match[str]]] = []
    for m in _TOKEN.finditer(question):
        if runs and re.fullmatch(r"\s+(?:&\s+|and\s+)?", question[runs[-1][-1].end():m.start()]):
            runs[-1].append(m)
        else:
            runs.append([m])
    return runs


def registrant_mentions(question: str, index: IssuerIndex,
                        taken: Sequence[IssuerMention]) -> list[IssuerMention]:
    """Capitalised spans equal to an SEC registrant title (suffixes dropped), longest first.
    Spans made only of stop words, years, or one short token are ignored."""
    found: list[IssuerMention] = []
    for run in _capitalised_runs(question):
        for size in range(min(len(run), MAX_TITLE_TOKENS), 0, -1):
            for i in range(len(run) - size + 1):
                start, end = run[i].start(), run[i + size - 1].end()
                if _overlaps(start, end, list(taken) + found):
                    continue
                words = normalise_title(question[start:end], ()).split()
                if not words or all(w in index.stop_words or _YEARISH.match(w) for w in words) \
                        or (len(words) == 1 and len(words[0]) < 3):
                    continue
                ticker = index.registrants.get(normalise_title(question[start:end],
                                                               index.title_suffixes))
                if ticker is not None:
                    _add(found, IssuerMention(ticker if ticker in index.universe else None,
                                              start, end, question[start:end], "registrant"))
    return found


def possessive_mentions(question: str, index: IssuerIndex,
                        taken: Sequence[IssuerMention]) -> list[IssuerMention]:
    """A capitalised word with a possessive ("Acme's") that is no known name or question word."""
    found = []
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9&.-]*)['’]s\b", question):
        word = m.group(1).lower()
        if _overlaps(m.start(), m.end(), taken) or word in index.stop_words \
                or _YEARISH.match(word):
            continue
        found.append(IssuerMention(None, m.start(), m.end(), m.group(0), "possessive"))
    return found


def find_mentions(question: str, index: IssuerIndex) -> list[IssuerMention]:
    """Every company named in the question, in text order."""
    found = universe_mentions(question, index)
    for mention in cik_mentions(question, index):
        _add(found, mention)
    found += registrant_mentions(question, index, found)
    found += possessive_mentions(question, index, found)
    return sorted(found, key=lambda m: m.start)


def mask_issuers(question: str, mentions: Sequence[IssuerMention]) -> str:
    """The question with every mention replaced by the issuer token."""
    out, last = [], 0
    for m in sorted(mentions, key=lambda m: m.start):
        out += [question[last:m.start], ISSUER_TOKEN]
        last = m.end
    out.append(question[last:])
    return "".join(out)


def has_comparison(question: str, vocabulary: IntentVocabulary) -> bool:
    return bool(find_phrases(normalise(question), vocabulary.comparison_cues))


def resolve(question: str, index: IssuerIndex, vocabulary: IntentVocabulary,
            max_issuers: int, selected_issuer: str | None = None) -> IssuerResolution:
    """The question's issuers under the 8.15 rules (module docstring)."""
    selected = selected_issuer.strip().upper() if selected_issuer else None
    if selected is not None and selected not in index.universe:
        raise ValueError(f"selected issuer {selected} is not in the universe")
    mentions = tuple(find_mentions(question, index))

    def done(issuers: Sequence[str], comparison: bool = False,
             clarification: str | None = None) -> IssuerResolution:
        held = tuple(t for t in issuers if index.universe.get(t) == HELD_OUT)
        return IssuerResolution(tuple(issuers), mentions, comparison, clarification, held)

    if any(m.ticker is None for m in mentions):
        return done((), clarification="UNSUPPORTED_ISSUER")
    issuers = list(dict.fromkeys(m.ticker for m in mentions if m.ticker))
    if not issuers:
        return done((selected,)) if selected else done((), clarification="NO_ISSUER")
    if len(issuers) == 1:
        if selected and issuers[0] != selected:
            return done(issuers, clarification="ISSUER_MISMATCH")
        return done(issuers)
    if len(issuers) > max_issuers:
        return done(issuers, clarification="TOO_MANY_ISSUERS")
    if not has_comparison(question, vocabulary):
        return done(issuers, clarification="SECOND_ISSUER")
    if selected and selected not in issuers:
        return done(issuers, clarification="ISSUER_MISMATCH")
    return done(issuers, comparison=True)
