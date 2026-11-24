"""Identities I1-I12 for one filing (blueprint Appendix B; configs/identities.yaml).

Each identity is evaluated on the filing's own values, document-only values included while
they await this check (they are what the check tests). Results are PASS, FAIL or
NOT_COMPUTABLE (missing operands listed); optional operands the filer does not present are left
out and listed, never stored as 0. Tolerance: exactly zero for I9-I11; otherwise the larger of
rounding at the reported scale (half a displayed unit per operand) and 0.1% of the largest
operand. Phase 4a reports failures only (the Correlation Agent is Phase 5).

apply_acceptance then settles each pending document-only value (blueprint 8.7): accepted only if
it enters at least one computable identity and every computable identity it enters passes;
otherwise IDENTITY_FAILED (some identity failed) or NO_IDENTITY_CHECK (none could test it).
I8 (a cross-filing restatement check) is not used for acceptance.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from finagent.config import IdentityLibrary, IdentitySpec, OperandAlternative, OptionalTerm, Term
from finagent.evidence.store import FilingRecord
from finagent.ixbrl.tagstore import TagFact
from finagent.records import IdentityResult, Observation, Prediction, RowInstance
from finagent.statements.derive import Pool, finalise_pending, resolve, usable
from finagent.statements.face_block import VERIFIED, facts_by_cell, read_cell
from finagent.statements.model_inputs import MATCH
from finagent.statements.verify_set import IDENTITY_FAILED, NO_IDENTITY_CHECK, PENDING

PASS = "PASS"
FAIL = "FAIL"
NOT_COMPUTABLE = "NOT_COMPUTABLE"

DEFAULT_SHARE = Decimal("0.001")  # 0.1% of the largest operand
NOT_FOR_ACCEPTANCE = ("I8",)

NciStatus = Mapping[tuple[str, ...], str]  # nci concepts -> REPORTED / ZERO / NIL / ABSENT


@dataclass
class Context:
    """What one filing's identities read."""

    filing: FilingRecord
    pool: Pool
    bases: Mapping[str, str | None]
    nci: NciStatus = field(default_factory=dict)
    scale: int = 0  # the filing's displayed power of ten (e.g. 6 for millions)
    rows: Sequence[RowInstance] = ()
    predictions: Sequence[Prediction] = ()
    facts: Sequence[TagFact] = ()


@dataclass
class Operands:
    """Signed operand values for one evaluation, with what was missing, omitted or pending."""

    values: list[tuple[str, Decimal]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    provisional: list[str] = field(default_factory=list)
    inputs: set[str] = field(default_factory=set)

    def add(self, sign: int, metric: str, observation: Observation, ctx: Context) -> None:
        self.values.append((f"{'+' if sign > 0 else '-'}{metric}", sign * observation.value))
        if not observation.accepted:
            self.provisional.append(metric)
        self.inputs |= expand(metric, ctx.pool)

    @property
    def residual(self) -> Decimal:
        return sum((v for _, v in self.values), Decimal(0))


def expand(metric: str, pool: Pool, seen: frozenset[str] = frozenset()) -> set[str]:
    """The reported metrics a value rests on: a formula-derived value expands to its inputs."""
    observation = pool.get(metric)
    if observation is None or metric in seen or observation.classification != "CALCULATION" \
            or not all(i in pool for i in observation.inputs) or not observation.inputs:
        return {metric}
    found: set[str] = set()
    for item in observation.inputs:
        found |= expand(item, pool, seen | {metric})
    return found


def presented(metric: str, ctx: Context) -> bool:
    observation = resolve(metric, ctx.pool, ctx.bases).observation
    return observation is not None and observation.presented


def nci_absent(alternative: OperandAlternative, ctx: Context) -> bool:
    condition = alternative.when_no_nci
    if condition is None:
        return True
    return ctx.nci.get(condition.concepts) not in (None, "REPORTED")


def add_term(term: Term, ops: Operands, ctx: Context, library: IdentityLibrary) -> None:
    """Resolve one required term (an operand, basis or metric) into ops."""
    sign, metric = term
    if metric in library.operands:
        alternatives = [a for a in library.operands[metric] if nci_absent(a, ctx)]
        chosen = next((a for a in alternatives
                       if all(presented(m, ctx) for _, m in a.terms)), None)
        if chosen is None:
            ops.missing.append(metric)
            return
        for inner_sign, inner in chosen.terms:
            add_term((sign * inner_sign, inner), ops, ctx, library)
        return
    found = resolve(metric, ctx.pool, ctx.bases)
    if usable(found.observation, provisional=True):
        ops.add(sign, found.metric, found.observation, ctx)
    else:
        ops.missing.append(found.metric)


def add_optional(optional: OptionalTerm, ops: Operands, ctx: Context,
                 library: IdentityLibrary) -> None:
    if any(ctx.bases.get(basis) != metric for basis, metric in optional.when_basis):
        return
    if not presented(optional.term[1], ctx):
        ops.omitted.append(optional.term[1])
        return
    observation = ctx.pool.get(optional.term[1])
    if optional.on_face and (observation is None or observation.display_check != MATCH):
        ops.omitted.append(f"{optional.term[1]} (not a face row)")
        return
    add_term(optional.term, ops, ctx, library)


def tolerance(spec: IdentitySpec, ops: Operands, scale: int) -> Decimal:
    if spec.tolerance == "exact":
        return Decimal(0)
    rounding = Decimal(5).scaleb(scale - 1) * len(ops.values)
    largest = max((abs(v) for _, v in ops.values), default=Decimal(0))
    return max(rounding, largest * DEFAULT_SHARE)


def _result(ctx: Context, spec: IdentitySpec, ops: Operands, status: str,
            residual: Decimal | None, tol: Decimal | None, subject: str | None = None,
            detail: str = "") -> IdentityResult:
    return IdentityResult(
        issuer=ctx.filing.issuer, fiscal_year=ctx.filing.fiscal_year,
        identity_id=spec.identity_id, residual=residual, tolerance=tol, status=status,
        accession=ctx.filing.accession, subject=subject, operands=tuple(ops.values),
        omitted=tuple(ops.omitted), missing=tuple(ops.missing),
        provisional=tuple(sorted(set(ops.provisional))), inputs=tuple(sorted(ops.inputs)),
        detail=detail)


def _sum_variant(spec: IdentitySpec, terms: Sequence[Term], ctx: Context,
                 library: IdentityLibrary) -> Operands:
    ops = Operands()
    for term in terms:
        add_term(term, ops, ctx, library)
    for optional in spec.optional:
        add_optional(optional, ops, ctx, library)
    return ops


def evaluate_sum(spec: IdentitySpec, ctx: Context, library: IdentityLibrary) -> IdentityResult:
    """Signed terms summing to zero; with variants the first passing variant is kept."""
    not_presented = [m for m in spec.requires_presented
                     if not presented(m, ctx) or ctx.pool[m].classification == "CALCULATION"]
    if not_presented:
        return _result(ctx, spec, Operands(missing=not_presented), NOT_COMPUTABLE, None, None,
                       detail=f"not presented: {not_presented}")
    if spec.requires_any and not any(presented(m, ctx) for m in spec.requires_any):
        return _result(ctx, spec, Operands(omitted=list(spec.requires_any)), NOT_COMPUTABLE,
                       None, None, detail=f"none of {list(spec.requires_any)} disclosed")
    variants = spec.variants or (spec.terms,)
    attempts = []
    for index, terms in enumerate(variants):
        ops = _sum_variant(spec, terms, ctx, library)
        if ops.missing:
            attempts.append((index, ops, None, None))
            continue
        tol = tolerance(spec, ops, ctx.scale)
        status = PASS if abs(ops.residual) <= tol else FAIL
        attempts.append((index, ops, status, tol))
        if status == PASS:
            break
    computed = [a for a in attempts if a[2] is not None]
    index, ops, status, tol = next((a for a in computed if a[2] == PASS),
                                   computed[0] if computed else attempts[0])
    detail = f"variant {index + 1}" if spec.variants else ""
    if status is None:
        return _result(ctx, spec, ops, NOT_COMPUTABLE, None, None, detail=detail)
    return _result(ctx, spec, ops, status, ops.residual, tol, detail=detail)


def cf_top_reading(ctx: Context, statement: str, items: Sequence[str]):
    """The first row of `statement` the classifier reads as one of `items`, in both channels."""
    by_id = {r.row_id: r for r in ctx.rows if r.statement == statement}
    candidates = sorted((by_id[p.row_id] for p in ctx.predictions
                         if p.row_id in by_id and p.item in items), key=lambda r: r.position)
    if not candidates:
        return None
    return read_cell(candidates[0], ctx.filing.end, facts_by_cell(ctx.facts))


def evaluate_cf_top_row(spec: IdentitySpec, ctx: Context, library: IdentityLibrary
                        ) -> IdentityResult:
    """I6: the cash flow statement's starting net income equals an income statement reading."""
    cell = cf_top_reading(ctx, spec.statement or "CF", spec.items)
    if cell is None or cell.value is None:
        why = "no cash flow net income row" if cell is None else "CF row channels disagree"
        return _result(ctx, spec, Operands(missing=[f"{spec.statement}:{spec.items[0]}"]),
                       NOT_COMPUTABLE, None, None, detail=why)
    attempts = []
    for item in spec.items:
        observation = ctx.pool.get(item)
        if not usable(observation, provisional=True) or observation.row_id == cell.row_id:
            continue
        ops = Operands()
        ops.values.append((f"+{spec.statement}:{cell.label}", cell.value))
        if cell.status != VERIFIED:
            ops.provisional.append(f"{spec.statement}:{cell.label}")
        ops.add(-1, item, observation, ctx)
        tol = tolerance(spec, ops, ctx.scale)
        attempts.append((abs(ops.residual) > tol, abs(ops.residual), ops, tol))
    if not attempts:
        return _result(ctx, spec, Operands(missing=list(spec.items)), NOT_COMPUTABLE, None, None)
    failed, _, ops, tol = min(attempts, key=lambda a: (a[0], a[1]))
    return _result(ctx, spec, ops, FAIL if failed else PASS, ops.residual, tol,
                   detail=f"CF row {cell.row_id} [{cell.status}]")


def evaluate_ordering(spec: IdentitySpec, ctx: Context, library: IdentityLibrary
                      ) -> IdentityResult:
    """I7: sum(lower) <= middle <= upper; residual is the larger excess (<= 0 passes)."""
    lower = Operands()
    for metric in spec.lower:
        if metric not in library.operands and not presented(metric, ctx):
            lower.omitted.append(metric)
        else:
            add_term((1, metric), lower, ctx, library)
    bounds = Operands()
    add_term((1, spec.middle), bounds, ctx, library)
    add_term((1, spec.upper), bounds, ctx, library)
    ops = Operands(lower.values + bounds.values, lower.missing + bounds.missing, lower.omitted,
                   lower.provisional + bounds.provisional, lower.inputs | bounds.inputs)
    if ops.missing or not lower.values:
        return _result(ctx, spec, ops, NOT_COMPUTABLE, None, None)
    middle, upper = (v for _, v in bounds.values)
    residual = max(lower.residual - middle, middle - upper)
    tol = tolerance(spec, ops, ctx.scale)
    return _result(ctx, spec, ops, PASS if residual <= tol else FAIL, residual, tol)


def evaluate_sanity(spec: IdentitySpec, ctx: Context, library: IdentityLibrary
                    ) -> list[IdentityResult]:
    """I12: one result per check; residual is the value (or a - b for less_than)."""
    results = []
    for check in spec.checks:
        ops = Operands()
        for metric in check.terms:
            add_term((1, metric), ops, ctx, library)
        subject = f"{check.rule}({','.join(check.terms)})"
        if ops.missing:
            results.append(_result(ctx, spec, ops, NOT_COMPUTABLE, None, None, subject))
            continue
        values = [v for _, v in ops.values]
        if check.rule == "positive":
            residual, ok = values[0], values[0] > 0
        elif check.rule == "non_negative":
            residual, ok = values[0], values[0] >= 0
        else:
            residual, ok = values[0] - values[1], values[0] < values[1]
        results.append(_result(ctx, spec, ops, PASS if ok else FAIL, residual, Decimal(0),
                               subject))
    return results


def evaluate_filing(library: IdentityLibrary, ctx: Context) -> list[IdentityResult]:
    """Every within-filing identity (I8 is evaluated across filings by prior_year_results)."""
    results: list[IdentityResult] = []
    for spec in library.identities:
        if spec.kind == "sum":
            results.append(evaluate_sum(spec, ctx, library))
        elif spec.kind == "cf_top_row":
            results.append(evaluate_cf_top_row(spec, ctx, library))
        elif spec.kind == "ordering":
            results.append(evaluate_ordering(spec, ctx, library))
        elif spec.kind == "sanity":
            results.extend(evaluate_sanity(spec, ctx, library))
    return results


def prior_year_results(
    spec: IdentitySpec,
    filing: FilingRecord,
    comparatives: Mapping[str, Observation],
    earlier: Mapping[str, Observation],
    items: Iterable[str],
    scale: int = 0,
) -> list[IdentityResult]:
    """I8 per item: filing t's prior-year column vs filing t-1's own column, in each channel.

    FAIL if any channel compared differs beyond tolerance; PASS if at least one channel was
    compared and none differs; NOT_COMPUTABLE when no channel has both readings. Items neither
    filing presents are skipped. The residual shown is the tag channel's when compared.
    """
    results = []
    for item in items:
        later, before = comparatives.get(item), earlier.get(item)
        if later is None or before is None or not (later.presented or before.presented):
            continue
        ops = Operands()
        residuals = {}
        for channel, attr in (("tag", "tag_value"), ("document", "document_value")):
            a, b = getattr(later, attr), getattr(before, attr)
            if a is not None and b is not None:
                residuals[channel] = a - b
                ops.values += [(f"+{channel}:t", a), (f"-{channel}:t-1", -b)]
        result_spec = spec
        if not residuals:
            ops.missing.append(item)
            results.append(IdentityResult(
                filing.issuer, filing.fiscal_year, spec.identity_id, None, None, NOT_COMPUTABLE,
                filing.accession, item, missing=(item,)))
            continue
        tol = tolerance(result_spec, ops, scale)
        failed = [c for c, r in residuals.items() if abs(r) > tol]
        shown = residuals.get("tag", residuals.get("document"))
        detail = "; ".join(f"{c} residual {r}" for c, r in residuals.items())
        results.append(IdentityResult(
            filing.issuer, filing.fiscal_year, spec.identity_id, shown, tol,
            FAIL if failed else PASS, filing.accession, item, tuple(ops.values),
            inputs=(item,), detail=detail))
    return results


def apply_acceptance(
    pool: Mapping[str, Observation], results: Sequence[IdentityResult]
) -> dict[str, Observation]:
    """Settle every pending document-only value against the identities it enters."""
    settled = dict(pool)
    for metric, observation in pool.items():
        if PENDING not in observation.flags or observation.classification == "CALCULATION" \
                and observation.inputs and all(i in pool for i in observation.inputs):
            continue
        entering = [r for r in results if r.identity_id not in NOT_FOR_ACCEPTANCE
                    and r.status in (PASS, FAIL) and metric in r.inputs]
        if not entering:
            settled[metric] = finalise_pending(observation, False, NO_IDENTITY_CHECK)
        elif any(r.status == FAIL for r in entering):
            settled[metric] = finalise_pending(observation, False, IDENTITY_FAILED)
        else:
            settled[metric] = finalise_pending(observation, True, observation.reason)
    return settled
