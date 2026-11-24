"""The goal compiler (blueprint 8.15 steps 1-5; Phase 6).

compile_question(question, parser, resources) turns a question into typed goals or a
CLARIFICATION:

1. advice patterns (configs/intents.yaml) -> ADVICE, before anything else;
2. issuer resolution (finagent.planner.issuers): names, tickers, CIKs; a second issuer only for
   an explicit comparison;
3. quarterly or part-year periods -> UNSUPPORTED_PERIOD;
4. clause splitting and conditions (finagent.planner.clauses); years per clause, two or more ->
   MULTIPLE_PERIODS (one fiscal period per goal);
5. each clause through a parser: the trained one (intent classifier + slot filler, falling back
   to the regex grammar below the confidence threshold, then CLARIFICATION) or the grammar alone
   (M0, the E5 baseline); there is no LLM fallback in Phase 6 (blueprint 15, Phase 8);
6. assembly: one goal per issuer and metric; forecast, liquidity and debt goals get Base when no
   scenario is named; a conditional debt goal needs a liquidity goal in the question and takes
   its scenario and target unless it names its own.

Nothing here reads a filing: the compiler needs only configs and the pinned SEC ticker map.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from finagent.config import IntentVocabulary, PlannerConfig
from finagent.planner import grammar
from finagent.planner.clauses import (detect_condition, distinct_years, find_years, mask_years,
                                      split_clauses)
from finagent.planner.intents import IntentClassifier
from finagent.planner.issuers import IssuerIndex, IssuerMention, find_mentions, mask_issuers, \
    resolve
from finagent.planner.slots import fill_slots
from finagent.planner.text import normalise
from finagent.records import Clarification, ClauseParse, Compilation, Goal

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "models"


@dataclass(frozen=True)
class CompilerResources:
    """Configs and the issuer index the compiler reads (no filing data)."""

    index: IssuerIndex
    vocabulary: IntentVocabulary
    planner: PlannerConfig

    @classmethod
    def load(cls, evidence_dir: Path | str | None = None) -> "CompilerResources":
        from finagent.config import load_intent_vocabulary, load_planner_config

        vocabulary = load_intent_vocabulary()
        return cls(IssuerIndex.load(evidence_dir, vocabulary), vocabulary, load_planner_config())

    def clarify(self, code: str, clause: str = "") -> Clarification:
        category, message = self.vocabulary.clarification(code)
        return Clarification(code, category, message, clause)


@dataclass(frozen=True)
class ClauseText:
    """One clause: as split, and its body (condition removed, years masked) with its years."""

    text: str
    body: str
    years: tuple[int, ...]
    conditional: bool
    supported: bool


class Parser(Protocol):
    name: str

    def parse(self, clause: ClauseText, resources: CompilerResources) -> ClauseParse | str: ...


class GrammarParser:
    """M0: the prototype's regex grammar alone (finagent.planner.grammar)."""

    name = "m0-grammar"

    def parse(self, clause: ClauseText, resources: CompilerResources) -> ClauseParse | str:
        found = grammar.parse_clause(clause.body, clause.years, clause.conditional,
                                     resources.vocabulary, resources.planner)
        return found if found is not None else "OUT_OF_GRAMMAR"


@dataclass
class TrainedParser:
    """M1: the intent classifier and slot filler; below the threshold, the grammar; then
    LOW_CONFIDENCE."""

    classifier: IntentClassifier
    threshold: float
    version: str = ""

    @property
    def name(self) -> str:
        return f"m1-{self.classifier.version}" + (f"-{self.version}" if self.version else "")

    def parse(self, clause: ClauseText, resources: CompilerResources) -> ClauseParse | str:
        (label, confidence), = self.classifier.predict([clause.body])
        if confidence < self.threshold:
            found = grammar.parse_clause(clause.body, clause.years, clause.conditional,
                                         resources.vocabulary, resources.planner)
            return found if found is not None else "LOW_CONFIDENCE"
        if label == "advice":
            return "ADVICE"
        if label == "unsupported":
            return "OUT_OF_SCOPE"
        slots = fill_slots(clause.body, label, clause.years, resources.vocabulary,
                           resources.planner)
        if isinstance(slots, str):
            return slots
        return ClauseParse(clause.body, label, slots.metrics, slots.period,  # type: ignore[arg-type]
                           slots.scenario, slots.target, clause.conditional, "classifier",
                           confidence)


def load_trained_parser(resources: CompilerResources, models_dir: Path = MODELS_DIR,
                        version: str | None = None) -> TrainedParser:
    """models/intent_{version}.joblib (gitignored), with the configured confidence threshold."""
    import joblib

    version = version or resources.planner.model_version
    classifier = joblib.load(Path(models_dir) / f"intent_{version}.joblib")
    return TrainedParser(classifier, resources.planner.confidence_threshold, version)


def clause_texts(masked_question: str, vocabulary: IntentVocabulary) -> list[ClauseText]:
    """The clauses of an issuer-masked question (front end shared by M0, M1 and training)."""
    found = []
    for text in split_clauses(normalise(masked_question), vocabulary):
        condition = detect_condition(text, vocabulary)
        years = find_years(condition.body)
        found.append(ClauseText(text, mask_years(condition.body, years),
                                tuple(distinct_years(years)), condition.conditional,
                                condition.supported))
    return found


def question_clauses(question: str, resources: CompilerResources
                     ) -> tuple[list[ClauseText], list[IssuerMention]]:
    """Clause bodies of any question, issuer rules not applied (for training data)."""
    mentions = find_mentions(question, resources.index)
    return clause_texts(mask_issuers(question, mentions), resources.vocabulary), mentions


def matches_any(text: str, patterns: Sequence[str]) -> bool:
    import re

    return any(re.search(p, text) for p in patterns)


def assemble(parses: Sequence[ClauseParse], issuers: Sequence[str],
             planner: PlannerConfig) -> tuple[Goal, ...] | str:
    """Goals for every issuer, metric and clause; or a clarification code (module docstring)."""
    liquidity = next((p for p in parses if p.kind == "liquidity"), None)
    goals: list[Goal] = []
    for p in parses:
        spec = planner.kinds[p.kind]
        if p.conditional and not spec.conditional:
            return "UNSUPPORTED_CONDITION"
        if p.conditional and liquidity is None:
            return "CONDITIONAL_WITHOUT_LIQUIDITY"
        scenario, target = p.scenario, p.target
        if spec.scenario:
            if p.conditional and liquidity is not None:
                scenario = scenario or liquidity.scenario
                target = target if target is not None else liquidity.target
            scenario = scenario or planner.default_scenario
        for issuer in issuers:
            for metric in p.metrics or ("",):
                goals.append(Goal(issuer, p.period, p.kind, metric, scenario, target,
                                  p.conditional))
    return tuple(dict.fromkeys(goals))


def compile_question(question: str, parser: Parser, resources: CompilerResources,
                     selected_issuer: str | None = None) -> Compilation:
    """Typed goals or a CLARIFICATION for one question (module docstring)."""
    v = resources.vocabulary
    text = normalise(question)

    def stop(code: str, clause: str = "", **fields) -> Compilation:
        return Compilation(question, parser.name, clarification=resources.clarify(code, clause),
                           **fields)

    if matches_any(text, v.advice_patterns):
        return stop("ADVICE")
    resolution = resolve(question, resources.index, v, resources.planner.max_issuers,
                         selected_issuer)
    fields = {"issuers": resolution.issuers, "comparison": resolution.comparison,
              "held_out": resolution.held_out}
    if resolution.clarification:
        return stop(resolution.clarification, **fields)
    if matches_any(text, v.unsupported_period_patterns):
        return stop("UNSUPPORTED_PERIOD", **fields)
    parses: list[ClauseParse] = []
    for clause in clause_texts(mask_issuers(question, resolution.mentions), v):
        if clause.conditional and not clause.supported:
            return stop("UNSUPPORTED_CONDITION", clause.text, clauses=tuple(parses), **fields)
        if len(clause.years) > 1:
            return stop("MULTIPLE_PERIODS", clause.text, clauses=tuple(parses), **fields)
        parsed = parser.parse(clause, resources)
        if isinstance(parsed, str):
            return stop(parsed, clause.text, clauses=tuple(parses), **fields)
        parses.append(parsed)
    if not parses:
        return stop("OUT_OF_SCOPE", **fields)
    goals = assemble(parses, resolution.issuers, resources.planner)
    if isinstance(goals, str):
        return stop(goals, clauses=tuple(parses), **fields)
    return Compilation(question, parser.name, goals, None, clauses=tuple(parses), **fields)
