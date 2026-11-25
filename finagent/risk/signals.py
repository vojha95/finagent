"""Risk signals (blueprint 8.13, Appendix C.2; Phase 7.3).

Each signal of configs/signals.yaml is evaluated for one fiscal year of the validated statement
set, from the same accepted inputs as the credit ratios (finagent.model.ratio_inputs), so a
signal's numbers match the Ratios sheet. A condition reads its ratios and inputs through a
Sources object (a float path here; finagent.tools builds a second, Decimal path from the
statement-set values and recomputes every fired measure with it).

Three-valued logic, never "missing = not fired":
- a condition is FIRED or NOT_FIRED when its measure has a value; GAP (INPUT_GAP, NO_YEAR,
  NOT_MEANINGFUL, ...) when it cannot be established; NOT_APPLIED when the filer has no such
  line (e.g. no receivables);
- fires "any": FIRED when one condition fires; NOT_FIRED only when every condition is NOT_FIRED
  or NOT_APPLIED (NOT_APPLIED when all are); otherwise GAP;
- fires "all": NOT_FIRED when one condition is NOT_FIRED; FIRED when all fire; NOT_APPLIED when
  one is NOT_APPLIED; otherwise GAP;
- requires: an input the signal needs at all (cost of sales for R2 and R9) that the filer does
  not report makes the signal GAP with the configured reason (D4: NO_COGS);
- a composite (R11) combines its components' results the same way, with its "while" condition.

R10 (data: scenario) runs only for the forecast origin (the latest 10-K), on the configured
scenario's projected years (D3); for another year it is NOT_APPLIED.
"""

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from finagent.config import ConditionSpec, SignalConfig, SignalSpec
from finagent.model.ratio_inputs import HIST_KEYS, accepted_value, hist_metric, historical_inputs
from finagent.model.ratios import BY_ID, RatioSpec, RatioValue, compute, compute_all
from finagent.records import ConditionResult, Forecast, SignalResult
from finagent.statements.build import StatementSet
from finagent.statements.verify_set import NOT_REPORTED

FIRED, NOT_FIRED, GAP, NOT_APPLIED = "FIRED", "NOT_FIRED", "GAP", "NOT_APPLIED"
VERIFIED = "VERIFIED"

RatioOf = Callable[[int, str], RatioValue | None]
InputOf = Callable[[int, str], object]  # a float or Decimal value, or None


@dataclass(frozen=True)
class YearData:
    """One fiscal year's signal inputs and ratios (float path)."""

    fiscal_year: int
    values: dict[str, float]
    absent: frozenset[str]  # optional inputs the filer does not report (left out of sums)
    not_reported: frozenset[str]  # inputs with no observation or NOT_REPORTED
    ratios: dict[str, RatioValue]


@dataclass(frozen=True)
class Sources:
    """Where a condition reads its numbers: ratios and inputs by (fiscal year, key)."""

    ratio: RatioOf
    input: InputOf
    absent: Callable[[int, str], bool]
    specs: Mapping[str, RatioSpec]
    forecast: Callable[[str], Forecast | None] | None = None
    origin_year: int | None = None


def ratio_specs(config: SignalConfig) -> dict[str, RatioSpec]:
    """finagent.model.ratios plus the signal ratios of the config (kind "ratio")."""
    found = dict(BY_ID)
    for rid, (label, num, den) in config.ratios.items():
        found[rid] = RatioSpec(rid, label, "ratio", tuple((1, k, False) for k in num),
                               tuple((1, k, False) for k in den))
    return found


def year_data(s: StatementSet, year: int, config: SignalConfig) -> YearData:
    """The ratio inputs of the year, the config's extra inputs, and every ratio."""
    inputs = historical_inputs(s, year)
    values, absent = dict(inputs.values), set(inputs.absent)
    not_reported = set()
    for key in HIST_KEYS:
        metric = hist_metric(s, year, key)
        o = s.get(year, metric) if metric else None
        if o is None or o.reason == NOT_REPORTED:
            not_reported.add(key)
    for key, metric in config.inputs.items():
        o = s.get(year, metric)
        value = accepted_value(o)
        if value is not None:
            values[key] = value
        elif o is None or o.reason == NOT_REPORTED:
            not_reported.add(key)
    ratios = compute_all(values, absent, inputs.days, inputs.opening)
    specs = ratio_specs(config)
    for rid in config.ratios:
        ratios[rid] = compute(specs[rid], values, absent)
    return YearData(year, values, frozenset(absent), frozenset(not_reported), ratios)


def history(s: StatementSet, config: SignalConfig) -> dict[int, YearData]:
    return {y: year_data(s, y, config) for y in s.years()}


def float_sources(data: Mapping[int, YearData], config: SignalConfig,
                  forecast: Callable[[str], Forecast | None] | None = None,
                  origin_year: int | None = None) -> Sources:
    def ratio(year: int, rid: str) -> RatioValue | None:
        return data[year].ratios.get(rid) if year in data else None

    def value(year: int, key: str) -> object:
        return data[year].values.get(key) if year in data else None

    def absent(year: int, key: str) -> bool:
        return year in data and key in data[year].absent

    return Sources(ratio, value, absent, ratio_specs(config), forecast, origin_year)


# ---------------------------------------------------------------- conditions

def passes(value: float, op: str, threshold: float) -> bool:
    return value > threshold if op == ">" else value < threshold


def blocked(c: ConditionSpec, state: str, reason: str, detail: str,
            value: float | None = None) -> ConditionResult:
    return ConditionResult(c.condition_id, c.label, state, value, c.unit, c.op, c.threshold,
                           reason, detail)  # type: ignore[arg-type]


def tested(c: ConditionSpec, value: float, values: Sequence[tuple[int, float]] = (),
           detail: str = "") -> ConditionResult:
    state = FIRED if passes(value, c.op, c.threshold) else NOT_FIRED
    return ConditionResult(c.condition_id, c.label, state, value, c.unit, c.op,  # type: ignore
                           c.threshold, "", detail, tuple(values))


def ratio_block(c: ConditionSpec, year: int, r: RatioValue | None) -> ConditionResult:
    """Why a ratio has no value in a year, as the condition's result."""
    if r is None:
        return blocked(c, GAP, "NO_YEAR", f"FY{year} is not in the statement set")
    if r.status == "NOT_APPLIED":
        return blocked(c, NOT_APPLIED, "NOT_APPLIED", f"FY{year} {r.ratio_id}: {r.detail}")
    reason = "NOT_MEANINGFUL" if r.status == "NM" else "INPUT_GAP"
    return blocked(c, GAP, reason, f"FY{year} {r.ratio_id}: {r.detail}")


def scale(spec: RatioSpec, unit: str) -> float:
    """Percent ratios are fractions; a pp condition shows them in percentage points."""
    return 100.0 if spec.fmt == "percent" and unit == "pp" else 1.0


def ratio_series(c: ConditionSpec, years: Sequence[int],
                 src: Sources) -> list[tuple[int, float]] | ConditionResult:
    """(year, ratio) for each year, or the first year's reason it has none."""
    spec = src.specs[c.of[0]]
    found = []
    for year in years:
        r = src.ratio(year, c.of[0])
        if r is None or r.status != "OK" or r.value is None:
            return ratio_block(c, year, r)
        found.append((year, r.value * scale(spec, c.unit)))
    return found


def growth(c: ConditionSpec, key: str, year: int, src: Sources) -> float | ConditionResult:
    """Growth of one input from the previous year, in percentage points."""
    if src.absent(year, key):
        return blocked(c, NOT_APPLIED, "NOT_APPLIED", f"{key} is not reported")
    now, before = src.input(year, key), src.input(year - 1, key)
    if now is None or before is None:
        missing = [f"FY{y} {key}" for y, v in ((year, now), (year - 1, before)) if v is None]
        return blocked(c, GAP, "INPUT_GAP", f"inputs without a value: {missing}")
    if before <= 0:  # type: ignore[operator]
        return blocked(c, GAP, "NOT_MEANINGFUL", f"FY{year - 1} {key} is not positive")
    return float((now / before - 1) * 100)  # type: ignore[operator]


def level_years(c: ConditionSpec, year: int, src: Sources) -> ConditionResult:
    """The test must hold in each of c.years years; one year failing it settles NOT_FIRED."""
    found, block = [], None
    for y in range(year, year - c.years, -1):
        r = src.ratio(y, c.of[0])
        if r is None or r.status != "OK" or r.value is None:
            block = block or ratio_block(c, y, r)
            continue
        found.append((y, r.value * scale(src.specs[c.of[0]], c.unit)))
    current = found[0][1] if found and found[0][0] == year else None
    if any(not passes(v, c.op, c.threshold) for _, v in found):
        return ConditionResult(c.condition_id, c.label, NOT_FIRED, current, c.unit, c.op,
                               c.threshold, "", "", tuple(sorted(found)))
    if block is not None:
        return block
    return tested(c, found[0][1], sorted(found))


def scenario_max(c: ConditionSpec, spec: SignalSpec, year: int,
                 src: Sources) -> ConditionResult:
    if src.forecast is None or year != src.origin_year:
        return blocked(c, NOT_APPLIED, "NOT_APPLIED",
                       f"projections run from the latest 10-K (FY{src.origin_year}) only")
    f = src.forecast(spec.scenario)
    if f is None or f.status != VERIFIED:
        status = f.status if f is not None else "NOT_BUILT"
        return blocked(c, GAP, f"FORECAST_{status}", "; ".join(f.reasons) if f else "")
    lines = [(y.fiscal_year, y.lines.get(c.of[0])) for y in f.years]
    if not lines or any(v is None for _, v in lines):
        return blocked(c, GAP, "NOT_MODELLED", f"{c.of[0]} is not projected in every year")
    peak = max(lines, key=lambda pair: (pair[1], -pair[0]))
    return tested(c, float(peak[1]), lines,  # type: ignore[arg-type]
                  f"{spec.scenario} scenario from FY{year}; largest in FY{peak[0]}")


def condition(c: ConditionSpec, spec: SignalSpec, year: int, src: Sources) -> ConditionResult:
    """One condition of a signal for one fiscal year (configs/signals.yaml measures)."""
    if c.measure == "scenario_max":
        return scenario_max(c, spec, year, src)
    if c.measure == "level_years":
        return level_years(c, year, src)
    if c.measure in ("growth", "growth_gap"):
        parts = [growth(c, key, year, src) for key in c.of]
        stop = next((p for p in parts if isinstance(p, ConditionResult)), None)
        if stop is not None:
            return stop
        value = parts[0] - (parts[1] if len(parts) > 1 else 0.0)  # type: ignore[operator]
        detail = ", ".join(f"{k} {p:+.1f}%" for k, p in zip(c.of, parts))  # type: ignore
        return tested(c, value, (), detail)
    years = {"level": [year], "change": [year - 1, year],
             "vs_median": list(range(year - c.years, year + 1))}[c.measure]
    series = ratio_series(c, years, src)
    if isinstance(series, ConditionResult):
        return series
    current = series[-1][1]
    if c.measure == "level":
        return tested(c, current, series)
    if c.measure == "change":
        return tested(c, current - series[0][1], series)
    median = statistics.median(v for _, v in series[:-1])
    if median <= 0:
        return blocked(c, GAP, "NOT_MEANINGFUL", f"{c.years}-year median is not positive")
    return tested(c, current / median, series, f"{c.years}-year median {median:.4g}")


# ---------------------------------------------------------------- signals

def any_state(states: Sequence[str]) -> str:
    if FIRED in states:
        return FIRED
    if all(s == NOT_APPLIED for s in states):
        return NOT_APPLIED
    if all(s in (NOT_FIRED, NOT_APPLIED) for s in states):
        return NOT_FIRED
    return GAP


def all_state(states: Sequence[str]) -> str:
    if NOT_FIRED in states:
        return NOT_FIRED
    if NOT_APPLIED in states:
        return NOT_APPLIED
    return FIRED if all(s == FIRED for s in states) else GAP


def why(results: Sequence[ConditionResult], state: str) -> tuple[str, str]:
    """The reason and detail of a GAP or NOT_APPLIED signal: its first such condition."""
    first = next((r for r in results if r.state == state), None)
    return (first.reason, first.detail) if first else ("", "")


@dataclass
class SignalContext:
    """What one fiscal year's evaluation needs beside the sources."""

    issuer: str
    names: Mapping[str, str]  # signal id -> name (configs/query_templates.yaml)
    not_reported: Callable[[int, str], bool] = lambda year, key: False
    done: dict[str, SignalResult] = field(default_factory=dict)


def requirement(spec: SignalSpec, year: int, src: Sources,
                ctx: SignalContext) -> tuple[str, str] | None:
    """(reason, detail) when an input the signal needs at all is not available (D4)."""
    for key, reason in spec.requires.items():
        if src.input(year, key) is not None:
            continue
        if ctx.not_reported(year, key):
            return reason, f"the filer reports no {key} line in FY{year}"
        return "INPUT_GAP", f"FY{year} {key} has no accepted value"
    return None


def composite(spec: SignalSpec, year: int, src: Sources, ctx: SignalContext,
              base: Mapping[str, object]) -> SignalResult:
    """(any_of) and (all_of) and while; a GAP or NOT_APPLIED names the part that blocks it."""
    any_part = [ctx.done[s] for s in spec.any_of]
    all_part = [ctx.done[s] for s in spec.all_of]
    while_ = condition(spec.while_, spec, year, src)  # type: ignore[arg-type]
    groups = [(any_state([p.state for p in any_part]), any_part),
              (all_state([p.state for p in all_part]), all_part)]
    state = all_state([g for g, _ in groups] + [while_.state])
    reason, detail = "", ""
    if state in (GAP, NOT_APPLIED):
        stuck = [p for g, parts in groups if g == state for p in parts if p.state == state]
        reason, detail = (stuck[0].reason, f"{stuck[0].signal_id}: {stuck[0].detail}") \
            if stuck else (while_.reason, while_.detail)
    return SignalResult(**base, state=state, conditions=(while_,),  # type: ignore[arg-type]
                        reason=reason, detail=detail,
                        components=tuple((p.signal_id, p.state) for p in any_part + all_part))


def signal(spec: SignalSpec, year: int, src: Sources, ctx: SignalContext) -> SignalResult:
    """One signal for one fiscal year (module docstring)."""
    name = ctx.names.get(spec.signal_id, spec.signal_id)
    base = dict(signal_id=spec.signal_id, name=name, issuer=ctx.issuer, fiscal_year=year,
                severity=spec.severity, data=spec.data, scenario=spec.scenario)
    if spec.composite:
        return composite(spec, year, src, ctx, base)
    missing = requirement(spec, year, src, ctx)
    if missing is not None:
        return SignalResult(**base, state=GAP, reason=missing[0], detail=missing[1])
    results = tuple(condition(c, spec, year, src) for c in spec.conditions)
    combine = any_state if spec.fires == "any" else all_state
    state = combine([r.state for r in results])
    reason, detail = why(results, state) if state in (GAP, NOT_APPLIED) else ("", "")
    return SignalResult(**base, state=state, conditions=results, reason=reason, detail=detail)


def evaluate(year: int, config: SignalConfig, src: Sources,
             ctx: SignalContext) -> tuple[SignalResult, ...]:
    """Every signal for one fiscal year, composites after their components."""
    ctx.done = {}
    order = [s for s in config.signals.values() if not s.composite] + \
        [s for s in config.signals.values() if s.composite]
    for spec in order:
        ctx.done[spec.signal_id] = signal(spec, year, src, ctx)
    return tuple(ctx.done[s] for s in config.signals)


def evaluate_set(s: StatementSet, year: int, config: SignalConfig, names: Mapping[str, str],
                 forecast: Callable[[str], Forecast | None] | None = None,
                 data: Mapping[int, YearData] | None = None) -> tuple[SignalResult, ...]:
    """Every signal of one fiscal year of a statement set (float path)."""
    data = data if data is not None else history(s, config)
    src = float_sources(data, config, forecast, max(s.years()) if s.years() else None)
    ctx = SignalContext(s.issuer, names,
                        lambda y, key: y in data and key in data[y].not_reported)
    return evaluate(year, config, src, ctx)
