"""The validated statement set for one company (blueprint 8.9; Phase 4a).

build_statement_set(store, ticker, as_of, classifier) reads up to five fiscal years, as the
evidence store returns them, and for each filing:

1. the document channel (classifier rows, the same read_filing_rows path as E1) and the tag
   channel for every canonical item, for the filing's own period and for the prior-year
   columns it shows; combined in product mode (finagent.statements.verify_set);
2. the A.2 model-input facts (finagent.statements.model_inputs), from the year's own filing;
3. derivations on the filing's values (provisional) and identities I1-I7, I9-I12;
4. acceptance of document-only values by those identities.

I8 compares each filing's prior-year column with the filing before it. Then, per fiscal year:
face items use the latest filing within as_of that shows the year with an accepted value
(flagged RESTATED when it differs from the year's own filing, whose value is kept alongside;
approved answer 5); model inputs come from the year's own filing; an item the filer never
reports in any channel of any filing is GAP NOT_REPORTED (answer 6); depreciation takes D&A
where depreciation alone is not tagged (answer 7); and the formula derivations are recomputed on
accepted values only.

NOT_REPORTED is concluded only when every channel that could show the item is visible (#147;
canonical items: both channels; A.2 inputs: the tag channel). Under an evidence view that hides
one of them (finagent.statements.replay), an item no visible channel reads in any filing is GAP
TAG_HIDDEN or DOCUMENT_HIDDEN, and derivations leave such an item out only when NOT_REPORTED
(finagent.statements.derive). A statement located with no rows for the period counts as not
located (STATEMENT_NOT_LOCATED, #151).

#152 (Vineet): an optional component of a rule with unread_year_nil (funded debt's current
portion, short-term borrowings, commercial paper) that no channel reads in a fiscal year (GAP
NEITHER) but some channel reads in another year of the same company is nil for that year: value
0, classification MODEL_ASSUMPTION (status stays GAP: nothing read it), reason and flag
NIL_UNREAD_YEAR, the years it was read in the basis. Never under a view that hides a channel
that could show it (#147), and never for a component no year reads (it stays NOT_REPORTED).

Refuses held-out tickers (9.2) and any filing filed after as_of. No ticker branches anywhere.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from finagent.config import (
    Derivations,
    IdentityLibrary,
    LineItemSpec,
    ModelInputs,
    check_not_held_out,
    load_concept_map,
    load_derivations,
    load_e1_equivalences,
    load_identities,
    load_label_rules,
    load_model_inputs,
    load_universe,
)
from finagent.document.channel import DocumentReading, read_document_channel
from finagent.evidence.integrity import read_snapshot, read_snapshot_json
from finagent.evidence.store import EvidenceStore, FilingRecord
from finagent.ixbrl.tagstore import TagFact
from finagent.records import (DocumentEdit, IdentityResult, Observation, Outcome, Prediction,
                              RowInstance)
from finagent.statements.derive import NIL_UNREAD_YEAR, derive_all, usable
from finagent.statements.identities import (
    Context,
    apply_acceptance,
    evaluate_filing,
    prior_year_results,
)
from finagent.statements.model_inputs import read_model_input
from finagent.statements.replay import (FULL, EvidenceView, untagged_nci, view_companyfacts,
                                        view_facts, view_rows)
from finagent.statements.verify_set import (
    FROM_LATER_FILING,
    NOT_REPORTED,
    RESTATED,
    observe_comparative,
    observe_item,
    same_amount,
)
from finagent.tags.channel import read_items
from finagent.verify.edits import apply_edits

SUBSTITUTE = "SUBSTITUTE"  # flag prefix: SUBSTITUTE:<item>
DOCUMENT_HIDDEN = "DOCUMENT_HIDDEN"  # location gap reason under the E8 no-document view
TAG_HIDDEN = "TAG_HIDDEN"  # an item never read while the view hides the tag channel (#147)
_PRESENTED_ELSEWHERE = ("NEITHER",)  # gap reasons that become NOT_REPORTED company-wide


@dataclass(frozen=True)
class StatementConfigs:
    concept_map: dict[str, LineItemSpec]
    model_inputs: ModelInputs
    derivations: Derivations
    identities: IdentityLibrary
    label_rules: list
    nci_concepts: tuple[tuple[tuple[str, ...], str], ...]  # (concepts, period type) pairs

    @classmethod
    def load(cls) -> "StatementConfigs":
        items = load_concept_map()
        inputs = load_model_inputs(concept_map=items)
        derivations = load_derivations(concept_map=items, model_inputs=inputs)
        identities = load_identities(concept_map=items, model_inputs=inputs,
                                     derivations=derivations)
        nci = {(tuple(e.nci_concepts), e.period_type)
               for e in load_e1_equivalences(concept_map=items)}
        for alternatives in identities.operands.values():
            for alt in alternatives:
                if alt.when_no_nci is not None:
                    nci.add((alt.when_no_nci.concepts, alt.when_no_nci.period_type))
        for spec in identities.identities:
            nci |= {(c.concepts, c.period_type) for _, c in spec.items_when_no_nci}
        return cls(items, inputs, derivations, identities,
                   load_label_rules(concept_map=items), tuple(sorted(nci)))


@dataclass
class FilingInputs:
    """What one filing's channels read for its own period, kept for the Correlation Agent
    (finagent.verify.workspace): rows, predictions and tags as read; documents after any edit."""

    filing: FilingRecord
    rows: tuple[RowInstance, ...]
    predictions: tuple[Prediction, ...]
    facts: tuple[TagFact, ...]
    documents: dict[str, DocumentReading]
    tags: dict[str, Outcome]
    model_inputs: dict[str, Observation]  # A.2 facts that are not canonical items
    nci: dict[tuple[str, ...], str]
    located: tuple[str, ...]
    scale: int
    # Metrics whose absence proves nothing under the evidence view (absence_unknown, #147).
    absence_unknown: frozenset[str] = frozenset()


@dataclass
class FilingAnalysis:
    """One filing read as filed: values, prior-year columns, identities."""

    filing: FilingRecord
    located: tuple[str, ...]
    gap_reason: str | None
    observations: dict[str, Observation]  # reported metrics, own period, after acceptance
    comparatives: dict[int, dict[str, Observation]]  # fiscal year -> item -> reading
    identities: list[IdentityResult]
    bases: dict[str, str | None]
    scale: int
    face_blocks: dict[str, Observation] = field(default_factory=dict)
    derived: dict[str, Observation] = field(default_factory=dict)  # provisional derivations
    inputs: FilingInputs | None = None


@dataclass
class StatementSet:
    issuer: str
    cik: str
    as_of: date
    filings: tuple[FilingRecord, ...]
    observations: dict[tuple[int, str], Observation]
    identities: list[IdentityResult]
    bases: dict[int, dict[str, str | None]]
    model_version: str
    notes: list[str] = field(default_factory=list)
    gap_reasons: dict[int, str | None] = field(default_factory=dict)
    # Per fiscal year: the NCI status of each NCI concept list (BC13), for the final check.
    nci: dict[int, dict[tuple[str, ...], str]] = field(default_factory=dict)

    def years(self) -> list[int]:
        return [f.fiscal_year for f in self.filings]

    def get(self, fiscal_year: int, metric: str) -> Observation | None:
        return self.observations.get((fiscal_year, metric))


def nci_statuses(facts: Sequence[TagFact], filing: FilingRecord,
                 pairs: Sequence[tuple[tuple[str, ...], str]]) -> dict[tuple[str, ...], str]:
    """REPORTED / ZERO / NIL / ABSENT per NCI concept list (BC13 rule, reused)."""
    from finagent.evaluation.e1 import nci_status

    return {concepts: nci_status(facts, concepts, filing, period) for concepts, period in pairs}


def _filing_scale(observations: Mapping[str, Observation]) -> int:
    """The filing's displayed scale, from the channel's own readings (an edited reading's scale
    moves with its rescale and is not the document's unit)."""
    scales = [o.scale for o in observations.values() if o.scale and not o.correction]
    return max(scales) if scales else 0


def located_statements(rows_by_statement: Mapping[str, Sequence[RowInstance]],
                       view: EvidenceView = FULL) -> tuple[str, ...]:
    """The statements located with at least one row for the period (#151: a table located
    without rows, e.g. a quarterly table, is no reading); none when the view hides the
    document."""
    if not view.document:
        return ()
    return tuple(s for s, rows in rows_by_statement.items() if rows)


def absence_unknown(view: EvidenceView, configs: StatementConfigs) -> frozenset[str]:
    """The metrics a channel that could show them cannot see under the view (#147): canonical
    items when either channel is hidden (both can show them); A.2 inputs that are not items only
    when the tag channel is hidden (they are read from tags alone). Empty with FULL."""
    found: set[str] = set()
    if view.single_channel:
        found |= set(configs.concept_map)
    if view.untagged:
        found |= {s.input_id for s in configs.model_inputs.inputs.values() if not s.is_item}
    return frozenset(found)


def never_read_reason(metric: str, view: EvidenceView = FULL,
                      unknown: frozenset[str] = frozenset()) -> str:
    """The reason for an item no visible channel reads in any filing (#147): NOT_REPORTED only
    when every channel that could show it is visible; otherwise the hidden channel."""
    if metric not in unknown:
        return NOT_REPORTED
    return TAG_HIDDEN if view.untagged else DOCUMENT_HIDDEN


def _comparative_record(filing: FilingRecord, prior: FilingRecord) -> FilingRecord:
    """The later filing read for an earlier fiscal year's column (same accession and date)."""
    return replace(filing, fiscal_year=prior.fiscal_year, start=prior.start, end=prior.end,
                   start_classification=prior.start_classification)


def read_channels(
    filing: FilingRecord, rows: Sequence[RowInstance], predictions: Sequence[Prediction],
    facts: list[TagFact], companyfacts: dict[str, Any], configs: StatementConfigs, as_of: date,
    edits: Sequence[DocumentEdit] = (), view: EvidenceView = FULL,
) -> tuple[dict[str, DocumentReading], dict[str, Outcome]]:
    """Both readings of every canonical item for the record's period: the document channel
    (with any in-memory edit for this period applied) and the tag channel (BC14 answer keys,
    not applied when the view hides the inline tags)."""
    from finagent.dataset.labels import concept_owner, label_rows
    from finagent.evaluation.e1 import rule_answer_keys

    specs = list(configs.concept_map.values())
    documents = read_document_channel(rows, predictions, filing, specs)
    documents = apply_edits(documents, edits, filing, rows, configs.concept_map)
    tags = read_items(filing, specs, companyfacts, facts, as_of)
    if rows and view.inline_tags:
        labels = [label for _, label in label_rows(rows, facts, concept_owner(configs.concept_map),
                                                   configs.label_rules)]
        tags = rule_answer_keys(tags, labels)
    return documents, tags


def observe_channels(
    filing: FilingRecord, documents: Mapping[str, DocumentReading], tags: Mapping[str, Outcome],
    rows: Sequence[RowInstance], located: Sequence[str], configs: StatementConfigs,
) -> dict[str, Observation]:
    """Every canonical item's two readings combined (8.7, product mode)."""
    by_id = {r.row_id: r for r in rows}
    return {spec.item_id: observe_item(filing, spec, documents[spec.item_id],
                                       tags.get(spec.item_id), by_id, located)
            for spec in configs.concept_map.values()}


def read_canonical(
    filing: FilingRecord, rows: Sequence[RowInstance], predictions: Sequence[Prediction],
    facts: list[TagFact], companyfacts: dict[str, Any], located: Sequence[str],
    configs: StatementConfigs, as_of: date, edits: Sequence[DocumentEdit] = (),
    view: EvidenceView = FULL,
) -> dict[str, Observation]:
    """Every canonical item for the record's period, in product mode (with BC14 answer keys)."""
    documents, tags = read_channels(filing, rows, predictions, facts, companyfacts, configs,
                                    as_of, edits, view)
    return observe_channels(filing, documents, tags, rows, located, configs)


def analyse_filing(
    store: EvidenceStore,
    filing: FilingRecord,
    earlier: Sequence[FilingRecord],
    classifier,
    companyfacts: dict[str, Any],
    configs: StatementConfigs,
    as_of: date,
    edits: Sequence[DocumentEdit] = (),
    view: EvidenceView = FULL,
) -> FilingAnalysis:
    """Read one filing as filed. earlier: the set's filings before it (for prior-year columns).
    edits: in-memory document-reading edits (finagent.verify.edits) for this filing's period.
    view: the evidence the build may read (finagent.statements.replay; FULL by default)."""
    from finagent.document import gap_reason as location_gap
    from finagent.filing_rows import read_filing_rows
    from finagent.ixbrl.tagstore import parse_document

    if filing.filed > as_of:
        raise ValueError(f"{filing.accession} was filed {filing.filed}, after {as_of}")
    document = read_snapshot(store.evidence_dir, filing.primary_document, store.manifest)
    located_rows = read_filing_rows(document, filing.accession, store.fiscal_periods(filing),
                                    tagged=view.inline_tags)
    rows = view_rows(view, [r for statement_rows in located_rows.rows.values()
                            for r in statement_rows])
    predictions = classifier.predict(rows) if rows else []
    located = located_statements(located_rows.rows, view)
    gap = None if rows else (location_gap(parse_document(located_rows.clean), located_rows.rows)
                             if view.document else DOCUMENT_HIDDEN)
    facts = view_facts(view, located_rows.facts)

    documents, tags = read_channels(filing, rows, predictions, facts, companyfacts, configs,
                                    as_of, edits, view)
    pool = observe_channels(filing, documents, tags, rows, located, configs)
    model_inputs = {spec.input_id: read_model_input(filing, spec, companyfacts, facts, rows)
                    for spec in configs.model_inputs.inputs.values() if not spec.is_item}
    pool.update(model_inputs)
    comparatives = {}
    for prior in earlier[-2:]:
        record = _comparative_record(filing, prior)
        readings = read_canonical(record, rows, predictions, facts, companyfacts, located,
                                  configs, as_of, edits, view)  # a propagated correction (#107)
        comparatives[prior.fiscal_year] = {k: observe_comparative(v) for k, v in readings.items()}

    lease_concepts = configs.model_inputs.finance_lease_concepts
    unknown = absence_unknown(view, configs)
    derived, bases = derive_all(filing, configs.derivations, pool, provisional=True, rows=rows,
                                facts=facts, finance_lease_concepts=lease_concepts,
                                absence_unknown=unknown)
    scale = _filing_scale(pool)
    nci = nci_statuses(facts, filing, configs.nci_concepts) if view.inline_tags \
        else untagged_nci(documents, rows, configs.nci_concepts)
    ctx = Context(filing, derived, bases, nci, scale, rows, predictions, facts)
    identities = evaluate_filing(configs.identities, ctx)
    settled = apply_acceptance(derived, identities)
    # Reported metrics keep their own reading; a derivation filling an unpresented item (e.g. GP)
    # is kept apart, so the final assembly re-derives it from accepted inputs.
    reported = {k: settled[k] if settled[k].classification != "CALCULATION" else pool[k]
                for k in pool}
    computed = {k: v for k, v in settled.items() if v.classification == "CALCULATION"}
    face_blocks = {r.derivation_id: computed[r.derivation_id]
                   for r in configs.derivations.rules
                   if r.kind == "face_block_sum" and r.derivation_id in computed}
    inputs = FilingInputs(filing, tuple(rows), tuple(predictions), tuple(facts), documents, tags,
                          model_inputs, nci, located, scale, unknown)
    return FilingAnalysis(filing, located, gap, reported, comparatives, identities, bases, scale,
                          face_blocks, computed, inputs)


def select_face_value(original: Observation, later: Sequence[Observation]) -> Observation:
    """Answer 5: the latest filing's accepted reading of the year, flagged; else the original."""
    accepted = [o for o in later if usable(o, provisional=False)]
    if not accepted:
        return original
    latest = accepted[-1]
    if usable(original, provisional=False):
        if same_amount(original, latest):
            return original
        flag = RESTATED
    else:
        flag = FROM_LATER_FILING
    return replace(latest, flags=latest.flags + (flag,), original_value=original.value,
                   original_accession=original.accession,
                   detail=f"{latest.detail}; own filing {original.accession}: "
                          f"{original.status} {original.reason} {original.value}")


def never_reported(analyses: Sequence[FilingAnalysis], metric: str) -> bool:
    """No channel of any filing (own period or prior-year columns) read the metric."""
    for analysis in analyses:
        if (o := analysis.observations.get(metric)) is not None and o.presented:
            return False
        if any((o := c.get(metric)) is not None and o.presented
               for c in analysis.comparatives.values()):
            return False
    return True


def read_years(analyses: Sequence[FilingAnalysis], metric: str) -> frozenset[int]:
    """The fiscal years for which some channel read the metric (own period or a later filing's
    prior-year column), accepted or not."""
    found: set[int] = set()
    for analysis in analyses:
        if (o := analysis.observations.get(metric)) is not None and o.presented:
            found.add(analysis.filing.fiscal_year)
        found.update(year for year, readings in analysis.comparatives.items()
                     if (o := readings.get(metric)) is not None and o.presented)
    return frozenset(found)


def nil_components(derivations: Derivations) -> tuple[str, ...]:
    """The optional terms of the rules with unread_year_nil (#152), in rule order."""
    found = [metric for rule in derivations.rules if rule.unread_year_nil
             for _, metric in rule.optional if metric not in derivations.bases]
    return tuple(dict.fromkeys(found))


def nil_unread_year(observation: Observation, year: int, read_in: Sequence[int]) -> Observation:
    """#152: a component no channel read in fiscal year `year`, read in other years (read_in),
    taken as nil for that year."""
    years = ", ".join(f"FY{y}" for y in read_in)
    return replace(observation, value=Decimal(0), classification="MODEL_ASSUMPTION",
                   reason=NIL_UNREAD_YEAR, accepted=True,
                   flags=observation.flags + (NIL_UNREAD_YEAR,),
                   basis=f"nil: no channel read it in FY{year}; read in {years} (#152)",
                   detail=f"treated as nil for FY{year} (#152): no channel read it this year "
                          f"({observation.reason}); read in {years}")


def substitute(observation: Observation, stand_in: Observation | None, basis: str) -> Observation:
    """Answer 7: an untagged fact takes the stand-in item's accepted value, flagged."""
    if observation.presented or stand_in is None or not usable(stand_in, provisional=False):
        return observation
    return replace(stand_in, metric=observation.metric, basis=basis,
                   flags=stand_in.flags + (f"{SUBSTITUTE}:{stand_in.metric}",),
                   inputs=(stand_in.metric,))


def assemble_year(
    index: int, analyses: Sequence[FilingAnalysis], configs: StatementConfigs,
    reported_never: set[str], view: EvidenceView = FULL,
    read: Mapping[str, frozenset[int]] | None = None,
) -> tuple[dict[str, Observation], dict[str, str | None]]:
    """The final values for the fiscal year of analyses[index]. reported_never: the items no
    channel reads in any filing (NOT_REPORTED, or the hidden channel's reason under a view that
    hides one). read: for each #152 component, the fiscal years some channel read it."""
    own = analyses[index]
    year = own.filing.fiscal_year
    values: dict[str, Observation] = {}
    for item in configs.concept_map:
        later = [a.comparatives[year][item] for a in analyses[index + 1:]
                 if year in a.comparatives and item in a.comparatives[year]]
        values[item] = select_face_value(own.observations[item], later)
    for spec in configs.model_inputs.inputs.values():
        if not spec.is_item:
            values[spec.input_id] = own.observations[spec.input_id]
    unknown = absence_unknown(view, configs)
    for metric in reported_never:
        current = values.get(metric)
        if current is not None and current.status == "GAP" \
                and current.reason in _PRESENTED_ELSEWHERE:
            values[metric] = replace(current, reason=never_read_reason(metric, view, unknown))
    for metric, years in (read or {}).items():
        current = values.get(metric)
        elsewhere = sorted(years - {year})
        if current is not None and current.status == "GAP" and elsewhere \
                and current.reason in _PRESENTED_ELSEWHERE and metric not in unknown:
            values[metric] = nil_unread_year(current, year, elsewhere)
    for spec in configs.model_inputs.inputs.values():
        if spec.substitute_item and spec.input_id in values:
            values[spec.input_id] = substitute(values[spec.input_id],
                                               values.get(spec.substitute_item),
                                               spec.substitute_basis or spec.substitute_item)
    final, bases = derive_all(own.filing, configs.derivations, values, provisional=False,
                              finance_lease_concepts=configs.model_inputs.finance_lease_concepts,
                              face_blocks=own.face_blocks, absence_unknown=unknown)
    return final, bases


def build_statement_set(
    store: EvidenceStore,
    ticker: str,
    as_of: date,
    classifier,
    universe: Mapping[str, str] | None = None,
    configs: StatementConfigs | None = None,
    edits: Sequence[DocumentEdit] = (),
    view: EvidenceView = FULL,
) -> StatementSet:
    """The validated statement set for one train or validation company, as of `as_of`.

    edits: in-memory document-reading edits (Correlation Agent corrections; finagent.verify)
    applied to each filing's own-period readings before verification. Evidence is never
    changed. view: the evidence the build may read (E8 replays; FULL by default)."""
    return analyse_company(store, ticker, as_of, classifier, universe, configs, edits, view)[0]


def analyse_company(
    store: EvidenceStore,
    ticker: str,
    as_of: date,
    classifier,
    universe: Mapping[str, str] | None = None,
    configs: StatementConfigs | None = None,
    edits: Sequence[DocumentEdit] = (),
    view: EvidenceView = FULL,
) -> tuple[StatementSet, list[FilingAnalysis]]:
    """The statement set and the per-filing analyses it was built from (with each filing's
    channel inputs, for the Correlation Agent)."""
    universe = universe if universe is not None else load_universe()
    check_not_held_out([ticker], universe, include_held_out=False)
    configs = configs or StatementConfigs.load()
    filings = store.load_filings(ticker, as_of)
    late = [f.accession for f in filings if f.filed > as_of]
    if late:
        raise ValueError(f"{ticker}: filings filed after {as_of}: {late}")
    companyfacts = view_companyfacts(view, read_snapshot_json(
        store.evidence_dir, f"companyfacts_{filings[0].cik}.json", store.manifest))
    analyses = [analyse_filing(store, f, filings[:i], classifier, companyfacts, configs, as_of,
                               edits, view)
                for i, f in enumerate(filings)]

    identities: list[IdentityResult] = []
    prior_spec = next((s for s in configs.identities.identities if s.kind == "prior_year"), None)
    for i, analysis in enumerate(analyses):
        identities.extend(analysis.identities)
        if prior_spec is not None and i > 0:
            earlier = analyses[i - 1]
            identities.extend(prior_year_results(
                prior_spec, analysis.filing,
                analysis.comparatives.get(earlier.filing.fiscal_year, {}),
                earlier.observations, configs.concept_map, analysis.scale))

    reported = set(configs.concept_map) | {s.input_id for s in configs.model_inputs.inputs.values()}
    never = {m for m in reported if never_reported(analyses, m)}
    read = {m: read_years(analyses, m) for m in nil_components(configs.derivations)}
    observations: dict[tuple[int, str], Observation] = {}
    bases: dict[int, dict[str, str | None]] = {}
    for i, analysis in enumerate(analyses):
        values, year_bases = assemble_year(i, analyses, configs, never, view, read)
        year = analysis.filing.fiscal_year
        bases[year] = year_bases
        observations.update({(year, metric): o for metric, o in values.items()})

    split = universe.get(ticker.strip().upper())
    notes = []
    if split == "train":
        notes.append("train company: document-channel readings are in-sample for M1")
    statement_set = StatementSet(
        issuer=filings[0].issuer, cik=filings[0].cik, as_of=as_of, filings=tuple(filings),
        observations=observations, identities=identities, bases=bases,
        model_version=getattr(classifier, "version", type(classifier).__name__), notes=notes,
        gap_reasons={a.filing.fiscal_year: a.gap_reason for a in analyses},
        nci={a.filing.fiscal_year: dict(a.inputs.nci) for a in analyses if a.inputs})
    return statement_set, analyses
