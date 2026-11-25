"""Insight cards (blueprint 8.13, 13; Phase 7.3).

A card is made for each FIRED signal: its numbers (the conditions with a value), its severity,
and evidence from the same 10-K (the fiscal year's): the signal's frozen query
(configs/query_templates.yaml; R11 unites R1-R4) searched with BM25 over the text of Item 7
and Item 8 with its notes, quoted by finagent.retrieval.quotes. With 1-2 quotes the card is
VERIFIED; with none it is EVIDENCE_GAP ("the filing does not explain the movement"). The card
text is a template filled with the numbers; no model writes free text on a card. Evidence v2
(D-EV; configs/signals.yaml evidence_version) searches with hybrid_rrf_rerank and quotes only
from passages at or above the reranker threshold; the gap detail then names that threshold.

#180 (Vineet): a signal on model data (data: scenario, R10) is a model output that no filing
sentence can explain. Its card searches nothing and quotes nothing; it is VERIFIED with the
label "model-derived; no filing evidence expected" (configs/signals.yaml), never EVIDENCE_GAP.
"""

from collections.abc import Sequence
from datetime import date

from finagent.config import EvidenceSpec
from finagent.evidence.store import FilingRecord
from finagent.records import ConditionResult, InsightCard, SignalResult
from finagent.retrieval.index import Retriever
from finagent.retrieval.quotes import QuoteResult, quotes_for, threshold_note
from finagent.retrieval.templates import QueryTemplates

VERIFIED, EVIDENCE_GAP, FIRED = "VERIFIED", "EVIDENCE_GAP", "FIRED"
MODEL_DERIVED = "MODEL_DERIVED"  # flag on a model-derived signal's answer (#180)
GAP_TEXT = "EVIDENCE_GAP: the filing does not explain the movement (no sentence above the " \
           "relevance threshold)."


def number(value: float, unit: str, signed: bool = False) -> str:
    """A condition value as shown on a card."""
    if unit == "USD":
        return f"USD {value / 10 ** 6:,.1f}m"
    sign = "+" if signed else ""
    if unit == "days":
        return f"{value:{sign}.1f} days"
    if unit == "pp":
        return f"{value:{sign}.1f} pp"
    return f"{value:{sign}.2f}x"


def signed(c: ConditionResult) -> bool:
    return c.unit in ("days", "pp") or c.condition_id.endswith("_change")


def condition_text(c: ConditionResult) -> str:
    """e.g. "DSO up +6.2 days (test > +5.0 days; FY2024 41.0, FY2025 47.2)"."""
    assert c.value is not None
    shown = number(c.value, c.unit, signed(c))
    test = f"test {c.op} {number(c.threshold, c.unit, signed(c))}"
    years = ", ".join(f"FY{y} {v:,.4g}" for y, v in c.values) if c.unit != "USD" else \
        ", ".join(f"FY{y} {number(v, c.unit)}" for y, v in c.values)
    extra = "; ".join(x for x in (years, c.detail) if x)
    return f"{c.label} {shown} ({test}{'; ' + extra if extra else ''})"


def shown_conditions(result: SignalResult) -> tuple[ConditionResult, ...]:
    """The conditions with a value (fired first), the numbers a card carries."""
    with_value = [c for c in result.conditions if c.value is not None]
    return tuple(sorted(with_value, key=lambda c: c.state != FIRED))


def model_derived(result: SignalResult) -> bool:
    """A signal on model data (the scenario forecast): no filing evidence is expected (#180)."""
    return result.data == "scenario"


def model_card_made(card: InsightCard) -> bool:
    """A model-derived card (#180): VERIFIED with no quote (a quoted card always has one)."""
    return card.status == VERIFIED and not card.evidence


def card_text(result: SignalResult, status: str, label: str = "") -> str:
    parts = [condition_text(c) for c in shown_conditions(result) if c.state == FIRED]
    if result.components:
        fired = [s for s, state in result.components if state == FIRED]
        parts.append("components fired: " + ", ".join(fired))
    scenario = f" [{result.scenario}]" if result.scenario else ""
    head = f"{result.signal_id} {result.name}{scenario} (FY{result.fiscal_year}, " \
           f"severity {result.severity}): "
    if label:
        tail = f" Evidence: {label}."
    else:
        tail = " Evidence: quoted below." if status == VERIFIED else " " + GAP_TEXT
    return head + "; ".join(parts) + "." + tail


def evidence(retriever: Retriever, result: SignalResult, templates: QueryTemplates,
             spec: EvidenceSpec, as_of: date) -> tuple[QuoteResult, str]:
    """The signal's quotes from the fiscal year's 10-K, and the query sent."""
    q = templates.signals[result.signal_id]
    return quotes_for(retriever, result.issuer, result.fiscal_year, q.query, q.anchors,
                      spec.sections, spec, as_of=as_of), q.query


def model_card(result: SignalResult, filing: FilingRecord, label: str) -> InsightCard:
    """#180: the card of a model-derived signal: VERIFIED, labelled, no query, no quote."""
    return InsightCard(result.signal_id, result.name, result.issuer, result.fiscal_year,
                       shown_conditions(result), result.severity, (), VERIFIED,  # type: ignore
                       filing.accession, filing.filing_url, filing.filed, filing.end,
                       card_text(result, VERIFIED, label), result.scenario, "", label)


def make_card(result: SignalResult, filing: FilingRecord, found: QuoteResult,
              query: str, spec: EvidenceSpec | None = None) -> InsightCard:
    status = VERIFIED if found.quotes else EVIDENCE_GAP
    note = threshold_note(found, spec) if spec is not None else ""
    detail = "" if found.quotes else (
        f"{len(found.candidates)} candidate sentence(s), best score "
        f"{found.candidates[0].quote.score:.2f}" if found.candidates
        else "no candidate sentence") + (f"; skipped {found.skipped}" if found.skipped else "") \
        + (f"; {note}" if note else "")
    return InsightCard(result.signal_id, result.name, result.issuer, result.fiscal_year,
                       shown_conditions(result), result.severity, found.quotes,
                       status, filing.accession, filing.filing_url,  # type: ignore[arg-type]
                       filing.filed, filing.end, card_text(result, status), result.scenario,
                       query, detail)


def cards(results: Sequence[SignalResult], filing: FilingRecord, retriever: Retriever,
          templates: QueryTemplates, spec: EvidenceSpec,
          as_of: date) -> tuple[InsightCard, ...]:
    """A card for each fired signal of one fiscal year (results of one 10-K)."""
    made = []
    for result in results:
        if result.state != FIRED:
            continue
        if result.fiscal_year != filing.fiscal_year or result.issuer != filing.issuer:
            raise ValueError(f"{result.signal_id}: result is not for {filing.accession}")
        if model_derived(result):
            made.append(model_card(result, filing, spec.model_derived_label))
            continue
        found, query = evidence(retriever, result, templates, spec, as_of)
        made.append(make_card(result, filing, found, query, spec))
    return tuple(made)
