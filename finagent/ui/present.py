"""Plain-language answers for the app (Phase 9 UI rework; a presentation layer only).

present() turns an Investigation's structured records (the requested goals, their observations,
gaps and gate decisions, the comparisons, the clarification) into:
- a headline of one or two plain sentences;
- one short finding per value, or per fired risk signal, with status badges and the evidence
  under it (a quote with its 10-K section, "the filing does not explain this movement", or the
  low-confidence sentence, labelled as not evidence);
- for a risk answer, the signals checked and not flagged, by name;
- the technical detail (basis, accession, URL, thresholds, scores, chunk ids, gate decisions) for
  a "How this was computed" expander.

It never parses the rendered answer text and computes no value: every number it shows is an
observation's value or recorded input, a configured threshold, a fiscal year, a filing date or a
count of records, formatted. audit() checks that independently of present(). Values, statuses, the
gate and the raw answer (finagent.planner.render) do not change.

One recorded input is read from a string: the growth of each input of a growth_gap measure (e.g.
"inventory +48.9%, cogs +21.1%") is carried only in the observation's basis (#352). It is read
with a pattern keyed on the condition's own inputs (configs/signals.yaml); when it is not found,
the condition's configured sentence is used instead.

Wording lives in configs/ui.yaml `present`. Names come from the concept map and model inputs
(finagent.workbook.build.labels), the ratios (finagent.model.ratios), the signal names
(configs/query_templates.yaml) and the company names (configs/universe.yaml).

CLI (the no-new-numbers audit over the example and E5 questions, docs/issues_log.md #352):
    python -m finagent.ui.present audit --run-id ui-present-0001
"""

import argparse
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from finagent.config import ConditionSpec, SignalSpec, load_signal_config
from finagent.planner.render import comparison_line, observation_line

ROOT = Path(__file__).resolve().parents[2]
UI_PATH = ROOT / "configs" / "ui.yaml"
UNIVERSE_PATH = ROOT / "configs" / "universe.yaml"
MODEL = "CALCULATION_FROM_MODEL_ASSUMPTION"
WITHHELD = "_WITHHELD"
SIGNALS_FIRED = "signals_fired"
LIQUIDITY = ("liquidity_shortfall", "liquidity_headroom")
FIRED_FLAGS = ("EVIDENCE_GAP", "MODEL_DERIVED")  # a fired measure without a quote (tools.py)
CARRIED = ("CORRECTED", "RESTATED", "SINGLE_CHANNEL")  # input flags shown as badges
GROWTH = re.compile(r"([+-]\d+(?:\.\d+)?)%")
NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
URL = re.compile(r"https?://\S+")
QUOTED = re.compile(r"“([^”]*)”")


# ---------------------------------------------------------------- records

@dataclass(frozen=True)
class Badge:
    key: str
    label: str
    color: str
    tip: str


@dataclass(frozen=True)
class Evidence:
    kind: str  # quote | gap | low | model | source
    text: str
    source: str = ""  # "10-K FY2026, MD&A"
    url: str = ""


@dataclass(frozen=True)
class Finding:
    title: str
    text: str
    badges: tuple[Badge, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    more: tuple[str, ...] = ()  # details: a fired signal's measures that did not fire, a rule
    group: str = ""


@dataclass(frozen=True)
class Clarify:
    question: str
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlainAnswer:
    kind: str
    headline: str
    findings: tuple[Finding, ...] = ()
    checked: tuple[str, ...] = ()  # risk: signals checked and not flagged
    unavailable: tuple[Finding, ...] = ()  # requested goals without a value, in plain words
    notes: tuple[str, ...] = ()
    clarify: Clarify | None = None
    technical: tuple[str, ...] = ()  # "How this was computed"


# ---------------------------------------------------------------- labels

def company_names(path: Path = UNIVERSE_PATH) -> dict[str, str]:
    """Ticker -> the company's first name (configs/universe.yaml names)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {t: names[0] for t, names in (raw.get("names") or {}).items() if names}


@dataclass(frozen=True)
class Labels:
    companies: Mapping[str, str]
    metrics: Mapping[str, str]
    signals: Mapping[str, str]  # signal id -> name (configs/query_templates.yaml)
    specs: Mapping[str, SignalSpec]
    text: Mapping[str, Any]  # configs/ui.yaml present

    @classmethod
    def load(cls, path: Path = UI_PATH) -> "Labels":
        from finagent.model.ratios import BY_ID
        from finagent.retrieval.templates import load_query_templates
        from finagent.workbook.build import labels

        text = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["present"]
        metrics = {**labels(), **{k: r.label for k, r in BY_ID.items()},
                   **text["metric_labels"], **text["identity_labels"]}
        return cls(company_names(), metrics,
                   {k: v.name for k, v in load_query_templates().signals.items()},
                   load_signal_config().signals, text)

    def company(self, ticker: str) -> str:
        return self.companies.get(ticker, ticker)

    def metric(self, metric: str) -> str:
        return self.metrics.get(metric) or metric.replace("_", " ").capitalize()

    def kind(self, kind: str) -> str:
        return self.text["kinds"].get(kind, kind.capitalize())

    def badge(self, key: str) -> Badge:
        b = self.text["badges"].get(key)
        if b is None:
            return Badge(key, key.replace("_", " ").capitalize(), "gray", "")
        return Badge(key, b["label"], b["color"], b["tip"])

    def section(self, section: str) -> str:
        names = self.text["sections"]
        best = max((p for p in names if section.startswith(p)), key=len, default=None)
        return names[best] if best else section.replace("_", " ")

    def reason(self, code: str) -> str:
        return self.text["reasons"].get(code, code.replace("_", " ").lower())

    def condition(self, metric: str) -> tuple[SignalSpec, ConditionSpec] | None:
        """The signal and condition of a risk measure "<signal>.<condition>"."""
        sid, _, cid = metric.partition(".")
        spec = self.specs.get(sid)
        if spec is None or not cid:
            return None
        found = [c for c in spec.conditions + ((spec.while_,) if spec.while_ else ())
                 if c.condition_id == cid]
        return (spec, found[0]) if found else None


# ---------------------------------------------------------------- formats

def usd(value: Decimal | float | int) -> str:
    """USD with thousands separators: bn from a billion, m from a million, else whole dollars."""
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    sign, size = ("-" if amount < 0 else ""), abs(amount)
    if size >= 10 ** 9:
        return f"USD {sign}{size / 10 ** 9:,.1f}bn"
    if size >= 10 ** 6:
        return f"USD {sign}{size / 10 ** 6:,.1f}m"
    return f"USD {sign}{size:,.0f}"


def number(value: Decimal | float | int, unit: str, signed: bool = False) -> str:
    """A value in its unit: USD, x (2 decimals), percent (a fraction), pp, %, days, count."""
    if unit == "USD":
        return usd(value)
    v = float(value)
    sign = "+" if signed and v > 0 else ""
    if unit == "x":
        return f"{v:.2f}x"
    if unit == "percent":
        return f"{sign}{v * 100:.1f}%"
    if unit == "pp":
        return f"{sign}{v:.1f} pp"
    if unit == "%":
        return f"{sign}{v:.1f}%"
    if unit == "days":
        return f"{sign}{v:.1f} days"
    if unit == "count":
        return f"{int(v)}"
    return f"{value} {unit}".strip()


def limit(threshold: float, unit: str) -> str:
    """A configured threshold as written in configs/signals.yaml."""
    if unit == "USD":
        return usd(threshold)
    return {"x": f"{threshold:g}x", "pp": f"{threshold:g} pp", "days": f"{threshold:g} days",
            "%": f"{threshold:g}%"}.get(unit, f"{threshold:g} {unit}")


def day(value: date | str | None) -> str:
    """29 Jul 2026."""
    if value is None or value == "":
        return ""
    d = date.fromisoformat(value) if isinstance(value, str) else value
    return f"{d.day} {d:%b %Y}"


def lower_first(text: str) -> str:
    """"Total assets" -> "total assets"; "EBIT margin" and "DSO (days)" unchanged."""
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def joined(items: Sequence[str]) -> str:
    """a; a and b; a, b and c."""
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def first_two(phrases: Sequence[str]) -> str:
    """At most two values in a headline; the rest are findings below."""
    return joined(phrases[:2]) + (", with more below" if len(phrases) > 2 else "") \
        if phrases else ""


def period_text(period: Any) -> str:
    return f"FY{period}" if isinstance(period, int) else "the latest year"


# ---------------------------------------------------------------- badges and evidence

def badges(o: Any, labels: Labels) -> tuple[Badge, ...]:
    """The status badges of one observation."""
    keys: list[str] = []
    if o.classification == "REPORTED_FACT":
        single = "SINGLE_CHANNEL" in o.flags
        keys.append("SINGLE_CHANNEL" if single else "VERIFIED")
    elif o.classification == MODEL:
        keys.append("MODEL")
    else:
        keys.append("CALCULATION")
    keys += [f for f in CARRIED if f in o.flags and f not in keys]
    if o.original_value is not None and "RESTATED" not in keys:
        keys.append("RESTATED")
    if o.value is None or "NOT_MEANINGFUL" in o.flags:
        keys.append("NOT_MEANINGFUL")
    if o.quotes:
        keys.append("QUOTED")
    keys += [f for f in ("EVIDENCE_GAP", "MODEL_DERIVED", "REVIEW") if f in o.flags]
    return tuple(labels.badge(k) for k in dict.fromkeys(keys))


def source(o: Any, labels: Labels, section: str = "") -> str:
    where = f", {labels.section(section)}" if section else ""
    return f"10-K FY{o.fiscal_year}{where}"


def evidence(o: Any, labels: Labels) -> tuple[Evidence, ...]:
    """A value's quotes, or its evidence gap and low-confidence sentence, or model-derived."""
    found = [Evidence("quote", q.text, source(o, labels, q.section),
                      o.filing_url if q.accession == o.accession else "") for q in o.quotes]
    if not o.quotes and "EVIDENCE_GAP" in o.flags:
        found.append(Evidence("gap", labels.text["evidence"]["gap"]))
        low = o.low_confidence
        if low is not None:
            found.append(Evidence("low", low.quote.text, source(o, labels, low.quote.section),
                                  o.filing_url if low.quote.accession == o.accession else ""))
    if not o.quotes and "MODEL_DERIVED" in o.flags:
        found.append(Evidence("model", labels.text["evidence"]["model"]))
    return tuple(found)


def filing_source(o: Any, origin: Any = None) -> Evidence:
    """Where a value comes from (linked to the filing). A model output's fiscal year is the
    projected year, so it cites its origin 10-K's fiscal year (the statements prerequisite)."""
    if o.classification == MODEL:
        where = f"FY{origin.fiscal_year} " if origin is not None else ""
        return Evidence("source", "", f"model from the {where}10-K filed {day(o.filed)}",
                        o.filing_url)
    return Evidence("source", "", f"10-K FY{o.fiscal_year}, filed {day(o.filed)}", o.filing_url)


def verb(name: str, present_tense: bool = False) -> str:
    """was / were (is / are) by the last word of a plain name ("Total assets" -> were)."""
    last = name.split("(")[0].split()[-1].lower() if name.split() else ""
    plural = last.endswith("s") and not last.endswith(("ss", "us", "is"))
    return ("are" if plural else "is") if present_tense else ("were" if plural else "was")


def status_finding(g: Any, labels: Labels) -> Finding:
    """A requested goal without a value, in plain words."""
    goal = g.goal
    title = labels.metric(goal.metric) if goal.metric else labels.kind(goal.kind)
    if goal.kind in ("forecast", "liquidity", "debt") and goal.scenario:
        title += f" ({goal.scenario} scenario)"
    who = f"{labels.company(goal.issuer)}, {period_text(goal.period)}"
    if g.status == "SKIPPED" and goal.conditional:
        text = labels.text["conditional_skipped"]
    else:
        text = f"Not available: {labels.reason(g.reason)}."
    key = {"MISSING_PRIMARY": "SINGLE_CHANNEL"}.get(g.status, g.status)
    return Finding(title, f"{who}. {text}", (labels.badge(key),))


# ---------------------------------------------------------------- parts of an answer

@dataclass
class Part:
    """What one group of requested goals (same issuer, kind and scenario) contributes."""

    context: str = ""  # "Microsoft, FY2026 (10-K filed 29 Jul 2026)"
    clause: str = ""  # "5 of 11 risk signals fired"
    findings: list[Finding] = field(default_factory=list)
    unavailable: list[Finding] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def context(o: Any, labels: Labels) -> str:
    return f"{labels.company(o.issuer)}, FY{o.fiscal_year} (10-K filed {day(o.filed)})"


def model_context(o: Any, origin: Any, labels: Labels) -> str:
    base = f"{labels.company(o.issuer)}, {o.scenario} scenario"
    if origin is not None:
        return f"{base}, from the FY{origin.fiscal_year} 10-K (filed {day(origin.filed)})"
    return f"{base}, from the latest 10-K (filed {day(o.filed)})"


def origin_of(issuer: str, goals: Sequence[Any]) -> Any:
    """The statements prerequisite of a model answer: the origin 10-K's fiscal year."""
    return next((o for g in goals if not g.requested and g.goal.kind == "statements"
                 and g.goal.issuer == issuer for o in g.observations), None)


def flag_notes(observations: Iterable[Any], labels: Labels) -> list[str]:
    notes = labels.text["flags"]
    return list(dict.fromkeys(notes[f] for o in observations for f in o.flags if f in notes))


def value_part(goals: Sequence[Any], labels: Labels, present_tense: bool = False) -> Part:
    """Reported facts ("was") and credit ratios ("is")."""
    part = Part()
    phrases = []
    for g in goals:
        if g.status != "VERIFIED" or not g.observations:
            part.unavailable.append(status_finding(g, labels))
            continue
        for o in g.observations:
            name = labels.metric(o.metric)
            if o.value is None:
                shown, text = "not meaningful", "Not meaningful here."
            else:
                shown = number(o.value, o.unit)
                text = f"{shown} in FY{o.fiscal_year}"
                if o.original_value is not None:
                    text += f" (first reported as {number(o.original_value, o.unit)})"
                text += "."
            part.context = part.context or context(o, labels)
            phrases.append(f"{lower_first(name)} {verb(name, present_tense)} {shown}")
            part.findings.append(Finding(name, text, badges(o, labels),
                                         evidence(o, labels) + (filing_source(o),)))
    part.clause = first_two(phrases)
    return part


def statements_part(goals: Sequence[Any], labels: Labels) -> Part:
    part = Part()
    for g in goals:
        if g.status != "VERIFIED" or not g.observations:
            part.unavailable.append(status_finding(g, labels))
            continue
        review = [o for o in g.observations if "REVIEW" in o.flags]
        part.context = context(g.observations[0], labels)
        part.clause = labels.text["statements"]["tie_out"]
        for o in g.observations:
            if "REVIEW" in o.flags:
                text = labels.text["statements"]["review"].format(value=usd(abs(o.value)))
            elif o.value == 0:
                text = labels.text["statements"]["holds"]
            else:
                text = labels.text["statements"]["differs"].format(value=usd(abs(o.value)))
            part.findings.append(Finding(labels.metric(o.metric), text, badges(o, labels),
                                         (filing_source(o),)))
        if review:
            part.notes.append(labels.text["statements"]["review_note"].format(
                items=joined([lower_first(labels.metric(o.metric)) for o in review])))
        part.notes += flag_notes(g.observations, labels)
    return part


def model_part(goals: Sequence[Any], every: Sequence[Any], labels: Labels) -> Part:
    """Forecast, liquidity and debt goals of one issuer and scenario."""
    part = Part()
    observations = [o for g in goals if g.status == "VERIFIED" for o in g.observations]
    phrases, by_metric = [], {o.metric: o for o in observations}
    origin = origin_of(goals[0].goal.issuer, every)
    for g in goals:
        if g.status != "VERIFIED" or not g.observations:
            part.unavailable.append(status_finding(g, labels))
            continue
        group = ""
        if g.goal.kind == "debt":
            group = labels.text["debt_group"]["conditional" if g.goal.conditional else "plain"]
        for o in g.observations:
            text = f"{number(o.value, o.unit)} in FY{o.fiscal_year} ({o.scenario} scenario)."
            part.findings.append(Finding(labels.metric(o.metric), text, badges(o, labels),
                                         (filing_source(o, origin),), group=group))
        for metric, reason in g.gaps:
            code, _, why = reason.partition(":")
            if code.endswith(WITHHELD):
                part.findings.append(Finding(labels.metric(metric), labels.text["withheld"]
                                             .format(why=why.strip()),
                                             (labels.badge("WITHHELD"),), group=group))
            elif g.goal.kind == "debt":
                part.notes.append(f"{labels.metric(metric)}: {labels.reason(code)}.")
    if observations:
        part.context = model_context(observations[0], origin, labels)
    if any(m in by_metric for m in LIQUIDITY):
        phrases.append(liquidity_phrase(by_metric))
    debt = {id(o) for g in goals if g.goal.kind == "debt" for o in g.observations}
    phrases += [f"{lower_first(labels.metric(o.metric))} is projected at "
                f"{number(o.value, o.unit)} in FY{o.fiscal_year}" for o in observations
                if o.metric not in LIQUIDITY and (id(o) not in debt or o.metric == "scoped_dscr")]
    part.clause = first_two(phrases)
    part.notes += flag_notes(observations, labels)
    return part


def liquidity_phrase(by_metric: Mapping[str, Any]) -> str:
    """Falls short by the recorded shortfall, or has the recorded headroom."""
    short, head = by_metric.get("liquidity_shortfall"), by_metric.get("liquidity_headroom")
    if short is not None and short.value is not None and float(short.value) > 0:
        return f"liquidity falls short by {usd(short.value)} in FY{short.fiscal_year}"
    if head is not None and head.value is not None and float(head.value) < 0:
        return f"liquidity falls short by {usd(abs(head.value))} in FY{head.fiscal_year}"
    o = head if head is not None else short
    if head is not None:
        return f"liquidity headroom is {usd(head.value)} in FY{o.fiscal_year}"
    return f"liquidity does not fall short in FY{o.fiscal_year}"


def explain_part(goals: Sequence[Any], every: Sequence[Any], labels: Labels) -> Part:
    part = Part()
    for g in goals:
        metric = labels.metric(g.goal.metric)
        fact = next((o for o in g.observations if o.metric == g.goal.metric), None) or next(
            (o for s in every if not s.requested and s.goal.kind == "fact"
             and s.goal.issuer == g.goal.issuer and s.goal.metric == g.goal.metric
             and s.status == "VERIFIED" for o in s.observations), None)
        change = next((o for o in g.observations if o.metric.endswith("_change")), None)
        if fact is not None:
            part.context = context(fact, labels)
            part.findings.append(Finding(metric, f"{number(fact.value, fact.unit)} in "
                                         f"FY{fact.fiscal_year}.", badges(fact, labels),
                                         (filing_source(fact),)))
        if g.status == "VERIFIED" and change is not None:
            up = float(change.value) > 0
            moved = f"{'up' if up else 'down'} {usd(abs(change.value))} from " \
                    f"FY{change.fiscal_year - 1}"
            part.clause = f"{lower_first(metric)} was {number(fact.value, fact.unit)}, " \
                          f"{moved}" if fact is not None else f"{lower_first(metric)} {moved}"
            part.findings.append(Finding(labels.text["explain_title"].format(
                metric=lower_first(metric)), f"{upper_first(moved)}.", badges(change, labels),
                evidence(change, labels)))
        else:
            if fact is not None:
                part.clause = f"{lower_first(metric)} was {number(fact.value, fact.unit)}, " \
                              f"but {labels.reason(g.reason)}"
            if fact is not None and g.reason == "EVIDENCE_GAP":
                part.findings.append(Finding(labels.text["explain_title"].format(
                    metric=lower_first(metric)), labels.text["evidence"]["gap"],
                    (labels.badge("EVIDENCE_GAP"),)))
            else:
                part.unavailable.append(status_finding(g, labels))
    return part


# ---------------------------------------------------------------- risk

def fired(o: Any) -> bool:
    """A fired measure carries the card's quotes, or its EVIDENCE_GAP or MODEL_DERIVED flag
    (finagent.tools FinAgentTools._signal_answers); a context measure carries neither."""
    return bool(o.quotes) or any(f in o.flags for f in FIRED_FLAGS)


def grew(name: str, pct: float) -> str:
    if pct > 0:
        return f"{name} grew {pct:.1f}%"
    return f"{name} fell {abs(pct):.1f}%" if pct < 0 else f"{name} was flat"


def moved(value: float, unit: str) -> str:
    if value == 0:
        return "did not change"
    return f"{'rose' if value > 0 else 'fell'} by {number(abs(value), unit)}"


def growth_parts(basis: str, keys: Sequence[str]) -> list[float] | None:
    """The recorded growth of each input of a growth measure ("inventory +48.9%"; #352)."""
    found = []
    for key in keys:
        m = re.search(rf"(?<![\w]){re.escape(key)} {GROWTH.pattern}", basis)
        if m is None:
            return None
        found.append(float(m.group(1)))
    return found


def rule_text(c: ConditionSpec) -> str:
    """"above 10 pp"; a growth measure's pp is a percent growth ("above 0%")."""
    unit = "%" if c.measure == "growth" else c.unit
    return f"{'above' if c.op == '>' else 'below'} {limit(c.threshold, unit)}"


def flag_clause(c: ConditionSpec) -> str:
    return f"flag {rule_text(c)}"


def measure_text(o: Any, c: ConditionSpec, labels: Labels) -> str:
    """One measure in plain words, with its value and test."""
    v, names = float(o.value), labels.text["inputs"]
    sentence = labels.text["conditions"].get(c.condition_id, {}).get("text", c.label + " {value}")
    if c.measure == "growth_gap":
        parts = growth_parts(o.basis, c.of)
        if parts is not None:
            a, b = (names.get(k, k) for k in c.of)
            return f"{grew(a, parts[0])} while {grew(b, parts[1])} " \
                   f"(gap {number(v, 'pp', signed=True)}; {flag_clause(c)})"
        return f"{sentence.format(value=number(v, c.unit))} ({flag_clause(c)})"
    if c.measure == "growth":
        return f"{grew(names.get(c.of[0], c.of[0]), v)} ({flag_clause(c)})"
    shown = sentence.format(value=number(v, c.unit), moved=moved(v, c.unit))
    return f"{shown} ({flag_clause(c)})"


def composite_text(o: Any, spec: SignalSpec, c: ConditionSpec, fired_ids: set[str],
                   labels: Labels) -> tuple[str, str]:
    """(finding text, rule) of a composite signal: its own measure and the signals with it."""
    names = labels.text["inputs"]
    with_ = [labels.signals.get(s, s) for s in spec.any_of + spec.all_of if s in fired_ids]
    own = grew(names.get(c.of[0], c.of[0]), float(o.value)) if c.measure == "growth" else \
        measure_text(o, c, labels)
    text = f"{own} while {joined(with_)} also fired" if with_ else own
    rule = labels.text["composite_rule"].format(
        measure=lower_first(c.label), limit=rule_text(c),
        any=joined([labels.signals.get(s, s) for s in spec.any_of]) or "-",
        all=joined([labels.signals.get(s, s) for s in spec.all_of]) or "-")
    return text, rule


def signal_finding(sid: str, observations: Sequence[Any], fired_ids: set[str],
                   labels: Labels) -> Finding:
    spec = labels.specs[sid]
    hits = [o for o in observations if fired(o)]
    rest = [o for o in observations if not fired(o)]
    more: list[str] = []
    if spec.composite:
        c = labels.condition(hits[0].metric)[1]  # type: ignore[index]
        text, rule = composite_text(hits[0], spec, c, fired_ids, labels)
        more.append(rule)
    else:
        text = "; ".join(measure_text(o, labels.condition(o.metric)[1], labels)  # type: ignore
                         for o in hits)
    more += [upper_first(measure_text(o, labels.condition(o.metric)[1], labels))  # type: ignore
             + f": {labels.reason('NOT_FIRED')}." for o in rest]
    shown: list[Evidence] = []
    for o in hits:
        shown += [e for e in evidence(o, labels) if e not in shown]
    marks = tuple(dict.fromkeys(b for o in hits for b in badges(o, labels)))
    return Finding(labels.signals.get(sid, sid), upper_first(text) + ".", marks,
                   tuple(shown) + (filing_source(hits[0]),), tuple(more))


def risk_part(goals: Sequence[Any], labels: Labels) -> Part:
    part = Part()
    order = list(labels.specs)
    for g in goals:
        if g.status != "VERIFIED" or not g.observations:
            part.unavailable.append(status_finding(g, labels))
            continue
        count = next((o for o in g.observations if o.metric == SIGNALS_FIRED), None)
        measures = [o for o in g.observations if o.metric != SIGNALS_FIRED]
        by_signal: dict[str, list[Any]] = {}
        for o in measures:
            by_signal.setdefault(o.metric.split(".")[0], []).append(o)
        fired_ids = set(by_signal)
        for sid in sorted(by_signal, key=lambda s: order.index(s) if s in order else len(order)):
            part.findings.append(signal_finding(sid, by_signal[sid], fired_ids, labels))
        for sid, reason in g.gaps:
            state, _, rest = reason.partition(":")
            name = labels.signals.get(sid, sid)
            if state == "NOT_FIRED":
                part.checked.append(name)
            else:
                code = rest.split()[0] if state in ("GAP", "NOT_APPLIED") and rest.split() \
                    else state
                part.unavailable.append(Finding(name, f"Not checked: {labels.reason(code)}.",
                                                (labels.badge("GAP"),)))
        anchor = count or (measures[0] if measures else None)
        if anchor is not None:
            part.context = context(anchor, labels)
        n = int(count.value) if count is not None else len(fired_ids)
        total = n + len(g.gaps)
        part.clause = f"{n} of {total} risk signals fired" if n else \
            f"no risk signal fired ({total} checked)"
    return part


# ---------------------------------------------------------------- the answer

def part_of(kind: str, goals: Sequence[Any], every: Sequence[Any], labels: Labels) -> Part:
    if kind == "fact":
        return value_part(goals, labels)
    if kind == "credit":
        return value_part(goals, labels, present_tense=True)
    if kind == "statements":
        return statements_part(goals, labels)
    if kind in ("forecast", "liquidity", "debt"):
        return model_part(goals, every, labels)
    if kind == "explain":
        return explain_part(goals, every, labels)
    if kind == "risk":
        return risk_part(goals, labels)
    return Part(unavailable=[status_finding(g, labels) for g in goals])


def groups(requested: Sequence[Any]) -> list[tuple[str, list[Any]]]:
    """Requested goals grouped by issuer, kind family and scenario, in order."""
    found: dict[tuple[str, str, str], list[Any]] = {}
    for g in requested:
        family = "model" if g.goal.kind in ("forecast", "liquidity", "debt") else g.goal.kind
        found.setdefault((g.goal.issuer, family, g.goal.scenario), []).append(g)
    return [(gs[0].goal.kind, gs) for gs in found.values()]


def sentences(parts: Sequence[Part]) -> list[str]:
    """One sentence per part; a part with the same context as the one before drops it."""
    found, last = [], None
    for p in parts:
        if not p.clause:
            continue
        if p.context and p.context != last:
            found.append(f"{p.context}: {p.clause}.")
        else:
            found.append(f"{upper_first(p.clause)}.")
        last = p.context or last
    return found


def comparison_sentence(c: Any, labels: Labels) -> str:
    left, right = c.left, c.right
    name = lower_first(labels.metric(c.metric))
    if float(c.value) == 0:
        how = "the same as"
    else:
        size = usd(abs(c.value)) if c.unit == "USD" else number(abs(float(c.value)), c.unit)
        how = f"{size} {'higher' if float(c.value) > 0 else 'lower'} than"
    return (f"{labels.company(left.issuer)}'s FY{left.fiscal_year} {name} "
            f"({number(left.value, left.unit)}) was {how} {labels.company(right.issuer)}'s "
            f"FY{right.fiscal_year} {name} ({number(right.value, right.unit)})")


def technical_lines(inv: Any) -> tuple[str, ...]:
    """The raw record behind the plain view: every goal, observation, quote, gap and gate
    decision as recorded (the raw answer text itself stays in the trace and its download)."""
    lines = [f"Question: {inv.question}",
             f"as_of {inv.as_of}; status {inv.status} ({inv.stop_reason}); compiler "
             f"{inv.compilation.compiler}"]
    for g in inv.goals:
        goal = g.goal
        what = " ".join(str(x) for x in (goal.kind, goal.metric, goal.issuer, goal.period,
                                         goal.scenario, goal.target or "") if x not in ("", None))
        lines.append(f"Goal ({'requested' if g.requested else 'prerequisite'}) {what}: "
                     f"{g.status} {g.reason}" + (f" ({g.detail})" if g.detail else ""))
        for o in g.observations:
            lines.append("  " + observation_line(o, inv.scenario_defaulted))
            lines += [f"    Quote: {q.section}, chunk {q.chunk_id}, score {q.score:.2f}, "
                      f"sha256 {q.chunk_sha256[:12]}" for q in o.quotes]
            if o.low_confidence is not None:
                lines.append(f"    {o.low_confidence.display()}")
        lines += [f"  Gap: {metric}: {reason}" for metric, reason in g.gaps]
        lines += [f"  Gate: {d.metric} {'accepted' if d.accepted else 'rejected'}"
                  + (f" {d.code}" if d.code else "") + (f": {d.detail}" if d.detail else "")
                  for d in g.decisions]
    lines += [comparison_line(c) for c in inv.comparisons]
    return tuple(lines)


def clarification(c: Any, labels: Labels, technical: tuple[str, ...]) -> PlainAnswer:
    conf = labels.text["clarify"]
    entry = conf["reasons"].get(c.reason, conf["other"])
    raw = f"Clarification {c.reason} ({c.category}): {c.message}" + \
        (f" Clause: {c.clause}" if c.clause else "")
    return PlainAnswer("clarification", conf["intro"],
                       clarify=Clarify(entry["ask"], tuple(entry.get("try", ()))),
                       technical=technical + (raw,))


def present(inv: Any, labels: Labels) -> PlainAnswer:
    """The plain-language view of an Investigation (module docstring)."""
    technical = technical_lines(inv)
    if inv.compilation.clarification is not None:
        return clarification(inv.compilation.clarification, labels, technical)
    requested = [g for g in inv.goals if g.requested]
    parts = [part_of(kind, gs, inv.goals, labels) for kind, gs in groups(requested)]
    notes: list[str] = []
    headline = sentences(parts)
    if inv.comparisons:
        headline = [comparison_sentence(c, labels) + "." for c in inv.comparisons]
        if len({o.fiscal_year for c in inv.comparisons for o in (c.left, c.right)}) > 1:
            notes.append(labels.text["notes"]["fiscal_years"])
    if not headline:
        headline = [labels.text["notes"]["no_value"]]
    if inv.scenario_defaulted:
        notes.append(labels.text["notes"]["base_default"])
    for d in getattr(inv.compilation, "dropped", ()):
        notes.append(labels.text["notes"]["dropped"].format(text=f"“{d.text}”"))
    kinds = list(dict.fromkeys(g.goal.kind for g in requested))
    kind = "comparison" if inv.comparisons else (kinds[0] if len(kinds) == 1 else
                                                 "+".join(kinds) or "none")
    return PlainAnswer(
        kind, " ".join(headline[:2]),
        findings=tuple(f for p in parts for f in p.findings),
        checked=tuple(c for p in parts for c in p.checked),
        unavailable=tuple(f for p in parts for f in p.unavailable),
        notes=tuple(dict.fromkeys(headline[2:] + [n for p in parts for n in p.notes] + notes)),
        technical=technical)


def plain_text(p: PlainAnswer, labels_shown: bool = True) -> str:
    """The plain view as text (the download and the report)."""
    lines = [p.headline]
    group = ""
    for f in p.findings:
        if f.group and f.group != group:
            lines.append(f"{f.group}:")
        group = f.group
        tags = f"  [{', '.join(b.label for b in f.badges)}]" if labels_shown and f.badges else ""
        lines.append(f"- {f.title}: {f.text}{tags}")
        for e in f.evidence:
            if e.kind == "quote":
                lines.append(f'    > "{e.text}"  (Source: {e.source})')
            elif e.kind == "low":
                lines.append(f'    Possibly related (low confidence, not evidence): "{e.text}"'
                             f"  ({e.source})")
            elif e.kind == "source":
                lines.append(f"    Source: {e.source}")
            else:
                lines.append(f"    {e.text}")
        lines += [f"    · {m}" for m in f.more]
    if p.checked:
        lines.append(f"Checked, not flagged: {', '.join(p.checked)}.")
    for f in p.unavailable:
        lines.append(f"- {f.title}: {f.text}")
    lines += p.notes
    if p.clarify is not None:
        lines.append(p.clarify.question)
        lines += [f'  Try: "{s}"' for s in p.clarify.suggestions]
    return "\n".join(lines)


# ---------------------------------------------------------------- no-new-numbers audit

def display_forms(value: float) -> set[str]:
    """Every way the plain view may write a recorded number (unsigned)."""
    a = abs(value)
    forms = {f"{a / 10 ** 9:,.1f}", f"{a / 10 ** 6:,.1f}", f"{a:,.0f}", f"{a:.2f}", f"{a:.1f}",
             f"{a * 100:.1f}", f"{a:g}"}
    return forms | ({str(int(a))} if a == int(a) else set())


def allowed_numbers(inv: Any, labels: Labels) -> set[str]:
    """The recorded numbers of an investigation, built from its records (not from present()):
    observation values and original values, the growth inputs recorded in a basis, comparison
    values, the risk measures' configured thresholds and signal count, fiscal years (and the
    year before a change), goal periods and targets, filing and period-end dates."""
    values: list[float] = [float(c.value) for c in inv.comparisons]
    whole: set[int] = set()
    for g in inv.goals:
        whole |= {x for x in (g.goal.period, g.goal.target) if isinstance(x, int)}
        if g.goal.kind == "risk":
            values.append(len(labels.specs))
        for o in g.observations:
            values += [float(v) for v in (o.value, o.original_value) if v is not None]
            values += [float(x) for x in GROWTH.findall(o.basis)]
            whole |= {o.fiscal_year} | ({o.fiscal_year - 1} if o.metric.endswith("_change")
                                        else set())
            for d in (o.filed, o.fiscal_period_end):
                if d:
                    d = date.fromisoformat(d) if isinstance(d, str) else d
                    whole |= {d.day, d.year}
            found = labels.condition(o.metric)
            if found is not None:
                spec, _ = found
                values += [c.threshold for c in spec.conditions
                           + ((spec.while_,) if spec.while_ else ())]
    forms = {str(n) for n in whole}
    for v in values:
        forms |= display_forms(v)
    return forms


def shown_texts(p: PlainAnswer, question: str) -> list[tuple[str, str]]:
    """(where, text) of every line the plain view shows, quotes excluded (checked verbatim);
    a dropped clause shown in “...” is excluded when it is the user's own words."""
    def own(text: str) -> str:
        return QUOTED.sub(lambda m: "" if m.group(1).lower() in question.lower() else m.group(0),
                          text)

    found = [("headline", p.headline)]
    for f in p.findings + p.unavailable:
        found += [(f"finding {f.title}", f.title), (f"finding {f.title}", f.text)]
        found += [(f"details {f.title}", m) for m in f.more]
        found += [(f"evidence {f.title}", e.source) for e in f.evidence]
        found += [(f"evidence {f.title}", e.text) for e in f.evidence
                  if e.kind not in ("quote", "low")]
        found += [(f"badge {f.title}", b.label) for b in f.badges]
    found += [("checked", c) for c in p.checked]
    found += [("note", own(n)) for n in p.notes]
    if p.clarify is not None:
        found += [("clarify", p.clarify.question)] + [("clarify", s)
                                                      for s in p.clarify.suggestions]
    return found


def audit(inv: Any, p: PlainAnswer, labels: Labels) -> tuple[int, list[str]]:
    """(numbers shown, problems): a number not equal, after formatting, to a recorded one, or
    a quoted sentence not verbatim the recorded quote or low-confidence sentence."""
    allowed = allowed_numbers(inv, labels)
    recorded = {q.text for g in inv.goals for o in g.observations for q in o.quotes} | \
        {o.low_confidence.quote.text for g in inv.goals for o in g.observations
         if o.low_confidence is not None}
    shown, problems = 0, []
    for where, text in shown_texts(p, inv.question):
        for token in NUMBER.findall(URL.sub(" ", text).replace("10-K", " ")):
            shown += 1
            if token not in allowed:
                problems.append(f"{where}: {token!r} is not a recorded number ({text!r})")
    for f in p.findings + p.unavailable:
        for e in f.evidence:
            if e.kind in ("quote", "low") and e.text not in recorded:
                problems.append(f"evidence {f.title}: quote not verbatim ({e.text[:60]!r})")
    return shown, problems


# ---------------------------------------------------------------- the audit run

def e5_sample(rows: Sequence[Mapping[str, Any]], grid: Mapping[str, str], n: int
              ) -> list[Mapping[str, Any]]:
    """n canonical E5 questions, round-robin by group in id order (first of each group, then
    the second, ...), fixed before any is run."""
    by_group: dict[str, list[Mapping[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["author"] == "canonical":
            by_group.setdefault(grid[r["cell"]], []).append(r)
    picked: list[Mapping[str, Any]] = []
    rank = 0
    while len(picked) < n and any(rank < len(v) for v in by_group.values()):
        picked += [v[rank] for v in by_group.values() if rank < len(v)][:n - len(picked)]
        rank += 1
    return picked


def audit_questions(ui: Any, universe: Mapping[str, str]) -> list[dict[str, Any]]:
    """The 6 example questions for every validation company, then 20 E5 canonical questions."""
    import csv

    e5 = yaml.safe_load((ROOT / "configs" / "e5.yaml").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (ROOT / e5["files"]["gold"]).read_text(
        encoding="utf-8").splitlines() if line.strip()]
    with (ROOT / e5["files"]["grid"]).open(encoding="utf-8") as handle:
        grid = {r["cell"]: r["group"] for r in csv.DictReader(handle)}
    found = [{"id": f"{t}-{e.kind}", "question": e.question, "selected": t,
              "as_of": ui.as_of_default} for t in sorted(universe)
             if universe[t] == "validation" for e in ui.examples]
    found += [{"id": r["id"], "question": r["question"], "selected": r["selected_issuer"] or None,
               "as_of": date.fromisoformat(str(e5["as_of"]))}
              for r in e5_sample(rows, grid, 20)]
    return found


def run_audit(run_id: str) -> int:
    from finagent.config import load_universe
    from finagent.planner.compile import CompilerResources, load_trained_parser
    from finagent.planner.investigate import investigate
    from finagent.tools import FinAgentTools
    from finagent.ui.data import load_ui_config

    out = ROOT / "runs" / run_id
    if out.exists():
        raise SystemExit(f"{out} exists; pick a new run id")
    ui, universe, labels = load_ui_config(), load_universe(), Labels.load()
    resources = CompilerResources.load()
    tools, parser = FinAgentTools.default(), load_trained_parser(resources)
    report, answers, total, bad = [], [], 0, 0
    for q in audit_questions(ui, universe):  # as the app asks (investigate compiles)
        inv = investigate(q["question"], q["as_of"], tools=tools, parser=parser,
                          resources=resources, selected_issuer=q["selected"])
        plain = present(inv, labels)
        shown, problems = audit(inv, plain, labels)
        total, bad = total + shown, bad + len(problems)
        report.append(f"{q['id']:<16} {plain.kind:<14} numbers {shown:>3}  problems "
                      f"{len(problems)}  | {q['question']}")
        report += [f"    {x}" for x in problems]
        answers.append(f"{'=' * 100}\n{q['id']} | {q['selected'] or '-'} | {q['question']}\n"
                       f"--- raw answer ---\n{inv.answer}\n--- plain ---\n{plain_text(plain)}")
    out.mkdir(parents=True)
    summary = f"{len(answers)} answers, {total} numbers shown, {bad} problems"
    (out / "audit.txt").write_text("\n".join([summary, ""] + report) + "\n", encoding="utf-8")
    (out / "answers.txt").write_text("\n".join(answers) + "\n", encoding="utf-8")
    print(summary)
    return 0 if bad == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("audit", help="the no-new-numbers audit (example and E5 questions)")
    a.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    return run_audit(args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
