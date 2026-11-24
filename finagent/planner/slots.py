"""Slot filler of the trained goal compiler (blueprint 8.15 step 3; Phase 6).

Given a clause body (normalised, issuers and years masked) and its goal kind, reads the metric(s),
period, scenario and target from configs/intents.yaml phrases, longest phrase first, using the
metric table of that kind (so "cash" is balance-sheet cash in a fact clause and closing cash in a
forecast clause). The kind's slots come from configs/planner.yaml. A slot the clause cannot fill
unambiguously is a clarification code, never a guess:

- MISSING_METRIC: the kind needs a metric and none is named;
- MIXED_REQUEST: a fact, credit, forecast or explain clause also names a reported item or ratio
  that is not a measure of its own kind ("revenue and current ratio");
- MULTIPLE_PERIODS: two fiscal years, or "last three years", "next three years";
- RELATIVE_PERIOD: "prior year", "two years ago" (name the fiscal year);
- TARGET_NOT_APPLICABLE / SCENARIO_NOT_APPLICABLE: a projected year or a named scenario on a kind
  that takes none ("stress" or "recession" on such a kind is an ordinary word and ignored);
- AMBIGUOUS_SCENARIO: on a scenario kind, "stress", "downside" without Moderate or Severe, or two
  different scenarios.

For a kind with a target (forecast, liquidity, debt) the period is the origin ("latest") and a
fiscal year in the clause is the target; "next year" is the default first projected year (None).
An unnamed scenario is left "" here and filled by the compiler (Base, or the liquidity goal's
scenario for a conditional debt goal).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from finagent.config import IntentVocabulary, PlannerConfig
from finagent.planner.text import find_phrases, remove_spans
from finagent.records import Period, Target

MIXED_CHECK_KINDS = ("fact", "credit", "forecast", "explain")
MIXED_SOURCE_KINDS = ("fact", "credit")


@dataclass(frozen=True)
class Slots:
    metrics: tuple[str, ...] = ()
    period: Period = "latest"
    scenario: str = ""
    target: Target = None


def phrase_table(vocabulary: IntentVocabulary, kind: str) -> dict[str, str]:
    """phrase -> metric id for one goal kind ({} for a kind with no metrics)."""
    return {p: metric for metric, phrases in vocabulary.metrics.get(kind, {}).items()
            for p in phrases}


def read_metrics(body: str, kind: str, vocabulary: IntentVocabulary
                 ) -> tuple[tuple[str, ...], str]:
    """(metric ids in clause order, the clause with the matched phrases removed)."""
    table = phrase_table(vocabulary, kind)
    matches = find_phrases(body, table)
    metrics = tuple(dict.fromkeys(table[p] for p, _, _ in matches))
    return metrics, remove_spans(body, [(s, e) for _, s, e in matches])


def foreign_metrics(rest: str, kind: str, vocabulary: IntentVocabulary) -> list[str]:
    """Reported-item or ratio phrases of another kind left in the clause (MIXED_REQUEST)."""
    own = set(phrase_table(vocabulary, kind))
    other = {p for k in MIXED_SOURCE_KINDS if k != kind
             for p in phrase_table(vocabulary, k)} - own
    return [p for p, _, _ in find_phrases(rest, other)]


def read_scenario(body: str, vocabulary: IntentVocabulary) -> tuple[str, bool]:
    """(scenario name or "", ambiguous). Named phrases win over ambiguous ones they contain
    ("severe stress" is Severe); two different named scenarios are ambiguous."""
    named = {p: name for name, phrases in vocabulary.scenarios.items() for p in phrases}
    phrases = list(named) + list(vocabulary.ambiguous_scenarios)
    found = find_phrases(body, phrases)
    names = {named[p] for p, _, _ in found if p in named}
    if len(names) > 1:
        return "", True
    if names:
        return names.pop(), False
    return "", bool(found)


def read_target(body: str, vocabulary: IntentVocabulary) -> tuple[str | None, bool]:
    """(offset "+1"/"+2"/"+3" or None, conflicting offsets)."""
    by_phrase = {p: k for k, phrases in vocabulary.targets.items() for p in phrases}
    offsets = {by_phrase[p] for p, _, _ in find_phrases(body, by_phrase)}
    if len(offsets) > 1:
        return None, True
    return (offsets.pop() if offsets else None), False


def fill_slots(body: str, kind: str, years: Sequence[int], vocabulary: IntentVocabulary,
               planner: PlannerConfig) -> Slots | str:
    """The clause's slots, or a clarification code (module docstring)."""
    spec = planner.kinds[kind]
    metrics: tuple[str, ...] = ()
    if spec.metrics != "none":
        metrics, rest = read_metrics(body, kind, vocabulary)
        if spec.metrics == "required" and not metrics:
            return "MISSING_METRIC"
        if kind in MIXED_CHECK_KINDS and foreign_metrics(rest, kind, vocabulary):
            return "MIXED_REQUEST"
    if find_phrases(body, vocabulary.multiple_phrases) or len(set(years)) > 1:
        return "MULTIPLE_PERIODS"
    if find_phrases(body, vocabulary.relative_phrases):
        return "RELATIVE_PERIOD"
    offset, conflict = read_target(body, vocabulary)
    scenario, ambiguous = read_scenario(body, vocabulary)
    if not spec.scenario:  # a named scenario is refused; "stress", "recession" are just words
        if scenario:
            return "SCENARIO_NOT_APPLICABLE"
    elif ambiguous:
        return "AMBIGUOUS_SCENARIO"
    year = years[0] if years else None
    if not spec.target:
        if offset is not None or conflict:
            return "TARGET_NOT_APPLICABLE"
        return Slots(metrics, year if year is not None else "latest", "", None)
    if conflict or (offset is not None and year is not None):
        return "MULTIPLE_PERIODS"
    target: Target = year if year is not None else (None if offset in (None, "+1") else offset)
    return Slots(metrics, "latest", scenario, target)
