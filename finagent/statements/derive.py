"""Derived items of the validated statement set (blueprint 8.9; configs/derivations.yaml).

A derived value is a CALCULATION computed only when every input has a usable value; otherwise a
GAP with reason INPUT_GAP (missing is never zero). Its status and channels are those of its
weakest input (VERIFIED > SINGLE_CHANNEL tag > SINGLE_CHANNEL document) and its inputs are
listed. With unless_presented a target either channel reads is never overwritten.

Two modes: provisional (per filing, before the identity check: a pending document-only value
may be used, and the result is pending too) and final (accepted inputs only).

Optional terms (#147): a term no channel presents is left out and listed (OMITTED:<id>) when
every channel that could show it is visible. Under an evidence view that hides one of them the
term is in absence_unknown: it is left out only when it is NOT_REPORTED, and any other absent
term makes the result an INPUT_GAP, never a partial sum.

Nil terms (#152, Vineet): in the final assembly, an optional term of a rule with
unread_year_nil that no channel read in its fiscal year but some channel read in another year of
the same company is nil for that year (finagent.statements.build.nil_unread_year). It is listed
among the inputs and in the formula, flagged NIL_UNREAD_YEAR:<id> on the result, and adds 0; the
result's status and channels come from the terms that were read.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from finagent.config import Derivations, DerivationSpec, Term
from finagent.evidence.store import FilingRecord
from finagent.ixbrl.tagstore import TagFact
from finagent.records import Observation, RowInstance
from finagent.statements.face_block import DOCUMENT_ONLY, VERIFIED, face_block
from finagent.statements.verify_set import NOT_REPORTED, PENDING, base_observation, channel_rank

INPUT_GAP = "INPUT_GAP"
DERIVED = "DERIVED"
FINANCE_LEASES_INCLUDED = "FINANCE_LEASES_INCLUDED"
OMITTED = "OMITTED"  # flag prefix: OMITTED:<id> for an optional term not presented
# #152: flag (and reason) of a component nil in a year no channel read it, read in another year;
# on a derived result the flag is NIL_UNREAD_YEAR:<id>.
NIL_UNREAD_YEAR = "NIL_UNREAD_YEAR"

Pool = Mapping[str, Observation]


# #196 (Vineet, option b): the wording of a nil component, on the statement line, in the
# forecast basis and in the workbook note.
NIL_WORDING = "treated as nil; the amount may be disclosed inside a combined line or note"


def nil_text(year: int) -> str:
    """"not read in FY2024; treated as nil; the amount may be disclosed inside a combined line
    or note" (#196)."""
    return f"not read in FY{year}; {NIL_WORDING}"


def is_nil(observation: Observation | None) -> bool:
    """A component taken as nil for its year (#152), not a reading."""
    return observation is not None and NIL_UNREAD_YEAR in observation.flags


def usable(observation: Observation | None, provisional: bool) -> bool:
    """Has a value that may enter a sum: accepted, or (provisional mode) pending its check."""
    if observation is None or observation.value is None:
        return False
    if observation.accepted:
        return True
    return provisional and PENDING in observation.flags


def choose_basis(pool: Pool, alternatives: Sequence[str],
                 stop_at_disagree: bool = False) -> str | None:
    """The first alternative with an accepted value; failing that, the first pending
    document-only value (awaiting its identity check); None when neither exists.

    With stop_at_disagree, alternatives after one read as DISAGREE are never used (None)."""
    if stop_at_disagree:
        for index, metric in enumerate(alternatives):
            observation = pool.get(metric)
            if observation is not None and observation.status == "DISAGREE":
                alternatives = alternatives[:index]
                break
    for provisional in (False, True):
        for metric in alternatives:
            if usable(pool.get(metric), provisional):
                return metric
    return None


@dataclass(frozen=True)
class Resolved:
    metric: str  # the reported metric behind a basis, else the id itself
    observation: Observation | None


def resolve(metric: str, pool: Pool, bases: Mapping[str, str | None]) -> Resolved:
    if metric in bases:
        chosen = bases[metric]
        return Resolved(chosen or metric, pool.get(chosen) if chosen else None)
    return Resolved(metric, pool.get(metric))


def choose_bases(pool: Pool, derivations: Derivations) -> dict[str, str | None]:
    return {basis: choose_basis(pool, alts, basis in derivations.stop_at_disagree)
            for basis, alts in derivations.bases.items()}


def _weakest(observations: Sequence[Observation]) -> Observation:
    return min(observations, key=channel_rank)


def derive_formula(
    filing: FilingRecord,
    rule: DerivationSpec,
    pool: Pool,
    bases: Mapping[str, str | None],
    provisional: bool,
    finance_lease_concepts: frozenset[str] = frozenset(),
    carried_flags: frozenset[str] = frozenset(),
    absence_unknown: frozenset[str] = frozenset(),
) -> Observation | None:
    """One formula derivation; None when unless_presented and the target is presented, or when
    a when_not_presented metric is presented (a fallback never replaces a presented line).

    carried_flags: rule flags (e.g. EBIT_PROXY) an input passes on to this result.
    absence_unknown: metrics a channel that could show them cannot see under the evidence view
    (module docstring, #147).
    """
    current = pool.get(rule.derivation_id)
    if rule.unless_presented and current is not None and current.presented:
        return None
    if any((o := pool.get(m)) is not None and o.presented for m in rule.when_not_presented):
        return None
    used: list[tuple[Term, Observation]] = []
    missing: list[str] = []
    omitted: list[str] = []
    nil: list[tuple[Term, Observation]] = []
    for term in rule.terms:
        found = resolve(term[1], pool, bases)
        if usable(found.observation, provisional):
            used.append((term, found.observation))
        else:
            missing.append(found.metric)
    for term in rule.optional:
        found = resolve(term[1], pool, bases)
        if rule.unread_year_nil and is_nil(found.observation):
            nil.append((term, found.observation))  # type: ignore[arg-type]
        elif found.observation is None or not found.observation.presented:
            if found.metric not in absence_unknown or (
                    found.observation is not None and found.observation.reason == NOT_REPORTED):
                omitted.append(found.metric)
            else:
                missing.append(found.metric)
        elif usable(found.observation, provisional):
            used.append((term, found.observation))
        else:
            missing.append(found.metric)
    inputs = tuple(o.metric for _, o in used + nil) + tuple(missing)
    basis = ";".join(f"{b}={c}" for b, c in bases.items()
                     if any(t[1] == b for t in rule.terms + rule.optional)) or None
    flags = tuple(f"{NIL_UNREAD_YEAR}:{o.metric}" for _, o in nil) \
        + tuple(f"{OMITTED}:{m}" for m in omitted)
    if rule.flag:
        flags = (rule.flag,) + flags
        basis = f"{rule.flag};{basis}" if basis else rule.flag
    flags += tuple(sorted({f for _, o in used for f in o.flags if f in carried_flags}
                          - set(flags)))
    if missing:
        # Inputs the filer never reports make the derived item not reported either.
        never = all((o := pool.get(m)) is not None and o.reason == NOT_REPORTED for m in missing)
        return base_observation(
            filing, rule.derivation_id, reason=NOT_REPORTED if never else INPUT_GAP,
            classification="EVIDENCE_GAP", inputs=inputs, basis=basis, flags=flags,
            detail=f"inputs without an accepted value: {missing}")
    value = sum((Decimal(sign) * o.value for (sign, _), o in used), Decimal(0))
    weakest = _weakest([o for _, o in used])
    pending = any(not o.accepted for _, o in used)
    if rule.flag_finance_leases and any(
            c in finance_lease_concepts for _, o in used for c in o.concepts):
        flags += (FINANCE_LEASES_INCLUDED,)
    if pending:
        flags += (PENDING,)
    instant = all(o.fiscal_period_start is None for _, o in used)
    return base_observation(
        filing, rule.derivation_id, period_type="instant" if instant else "duration",
        value=value, classification="CALCULATION", status=weakest.status, reason=DERIVED,
        accepted=not pending, channels=weakest.channels, inputs=inputs, basis=basis,
        flags=flags, detail=" ".join(f"{'+' if s > 0 else '-'}{o.metric}"
                                     for (s, _), o in used + nil))


def derive_face_block(
    filing: FilingRecord,
    rule: DerivationSpec,
    pool: Pool,
    rows: Sequence[RowInstance],
    facts: Sequence[TagFact],
) -> Observation | None:
    """Face-block sum (e.g. total liabilities with no total line); None when presented."""
    current = pool.get(rule.derivation_id)
    if rule.unless_presented and current is not None and current.presented:
        return None
    anchor = pool.get(rule.after_item or "")
    anchor_row = anchor.row_id if usable(anchor, provisional=True) else None
    statement_rows = [r for r in rows if r.statement == rule.statement]
    block = face_block(statement_rows, anchor_row, filing.end, rule.stop_patterns, facts,
                       rule.include_anchor)
    common = dict(period_type="instant", statement=rule.statement,
                  inputs=tuple(c.row_id for c in block.components),
                  detail=_describe(block))
    total = block.total
    if block.gap_reason is not None or total is None:
        return base_observation(filing, rule.derivation_id, reason=block.gap_reason or INPUT_GAP,
                                **common)
    concepts = tuple(c.concept for c in block.components if c.concept)
    common["components"] = tuple((c.row_id, c.label, c.value) for c in block.components)
    if block.status == VERIFIED:
        return base_observation(
            filing, rule.derivation_id, value=total, classification="CALCULATION",
            status="VERIFIED", reason=DERIVED, accepted=True, channels=("document", "tag"),
            concepts=concepts, **common)
    assert block.status == DOCUMENT_ONLY
    return base_observation(
        filing, rule.derivation_id, value=total, classification="CALCULATION",
        status="SINGLE_CHANNEL", reason=DERIVED, channels=("document",), flags=(PENDING,),
        concepts=concepts, **common)


def _describe(block) -> str:
    parts = [f"{c.label} [{c.status}] {c.value}" for c in block.components]
    return (f"components: {parts}; skipped: {list(block.skipped)}; stop row: {block.stop_row!r}"
            + (f"; gap: {block.gap_reason}" if block.gap_reason else ""))


def derive_all(
    filing: FilingRecord,
    derivations: Derivations,
    pool: Mapping[str, Observation],
    provisional: bool,
    rows: Sequence[RowInstance] = (),
    facts: Sequence[TagFact] = (),
    finance_lease_concepts: frozenset[str] = frozenset(),
    face_blocks: Mapping[str, Observation] | None = None,
    absence_unknown: frozenset[str] = frozenset(),
) -> tuple[dict[str, Observation], dict[str, str | None]]:
    """Apply every rule in file order; returns the pool with derived values and the bases.

    face_blocks, when given, supplies face-block results already computed for the filing (final
    mode reuses the per-filing result and its identity check). absence_unknown: metrics whose
    absence proves nothing under the evidence view (optional terms, module docstring).
    """
    result = dict(pool)
    # A basis is chosen the first time a rule uses it, so an alternative that is itself derived
    # (ebit_proxy) exists by then; once chosen it holds for every later rule and identity.
    bases: dict[str, str | None] = {}
    carried = frozenset(r.flag for r in derivations.rules if r.flag)
    for rule in derivations.rules:
        for _, metric in rule.terms + rule.optional:
            if metric in derivations.bases and metric not in bases:
                bases[metric] = choose_basis(result, derivations.bases[metric],
                                             metric in derivations.stop_at_disagree)
        if rule.kind == "face_block_sum":
            if face_blocks is not None:
                current = result.get(rule.derivation_id)
                presented = current is not None and current.presented \
                    and current.classification != "CALCULATION"
                derived = None if presented else face_blocks.get(rule.derivation_id)
            else:
                derived = derive_face_block(filing, rule, result, rows, facts)
        else:
            derived = derive_formula(filing, rule, result, bases, provisional,
                                     finance_lease_concepts, carried, absence_unknown)
        if derived is not None:
            result[rule.derivation_id] = derived
    for basis, alternatives in derivations.bases.items():
        if basis not in bases:
            bases[basis] = choose_basis(result, alternatives,
                                        basis in derivations.stop_at_disagree)
    return result, bases


def finalise_pending(observation: Observation, accepted: bool, reason: str) -> Observation:
    """Settle a pending document-only value after the identity check."""
    flags = tuple(f for f in observation.flags if f != PENDING)
    if accepted:
        return replace(observation, accepted=True, flags=flags)
    return replace(observation, accepted=False, reason=reason, flags=flags)
