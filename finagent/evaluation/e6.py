"""E6: point-in-time forecast backtest (blueprint 10.5; BC7; BC34; docs/phase9_plan.md section 8).

Question: is the shipped Base forecast, one year ahead, closer to the outcome than the naive
last value?

Every pinned 10-K of fiscal year t whose FY t+1 10-K is also pinned gives an origin at its
filing date d(t). For each origin:
- **Inputs, all as of d(t):** the shipped statement set (M1 v3 document channel, Correlation
  Agent; FinAgentTools.statement_set) plus the BC7 history for the earlier years the drivers
  read (finagent.evaluation.e6_history). The shipped build_forecast runs unchanged, Base
  scenario.
- **Qualifying:** at least `min_readable_years` fiscal years at or before t have an accepted
  `readable_metric` as of d(t).
- **Model:** the first projected year's line. **Naive:** the accepted FY t value (last value).
- **Actual:** FY t+1 from the statement set as of d(t+1), FY t+1's own filing date. No later
  filing is visible then, so the value is the one first reported. VERIFIED only.
- **Leakage guards:** every input the forecast and the naive read must be filed by d(t)
  (check_inputs raises PointInTimeError), and every actual must come from FY t+1's own filing
  with no RESTATED / FROM_LATER_FILING flag (check_first_reported raises RestatedActualError).
  A leak stops the run; it is never an exclusion.

A point that cannot be scored keeps every reason (missing is never zero, never dropped). Errors
are floats (model calculations, rule 5): absolute error in USD and APE = AE / |actual|; APE is
undefined when the actual is 0 (counted as ACTUAL_ZERO).

**Coverage** (BC35 (4)): every row reports scored origins / qualifying origins and companies
likewise, with one primary reason per excluded qualifying point (PRIMARY_ORDER), so the primary
counts add up to qualifying - scored. Results hold only where the product issued a VERIFIED
forecast; `selection_check` compares naive APE where it did and where it gave GAP (no claim).
"""

import json
import random
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from statistics import mean, median
from typing import Any

from finagent.config import CONFIG_DIR, ConfigError, _load_yaml
from finagent.evaluation.e6_history import BC7_HISTORY, read_history, with_history
from finagent.evidence.integrity import read_snapshot_json
from finagent.evidence.store import FilingRecord
from finagent.model.forecast import build_forecast
from finagent.model.history import Lookup, PointInTimeError, lookup, lookup_basis
from finagent.records import Forecast, InputRef
from finagent.statements.build import StatementSet
from finagent.statements.verify_set import FROM_LATER_FILING, RESTATED

E6_PATH = CONFIG_DIR / "e6.yaml"

# Exclusion reasons (a point may carry several).
ORIGIN_ERROR = "ORIGIN_ERROR"  # the statement set or forecast as of d(t) could not be built
FEWER_YEARS = "FEWER_THAN_MIN_READABLE_YEARS"
ACTUAL_ERROR = "ACTUAL_ERROR"  # the statement set as of d(t+1) could not be built
LABEL_MISMATCH = "FISCAL_YEAR_LABEL_MISMATCH"  # FY t+1's label as of d(t+1) is not t + 1
MODEL_GAP = "MODEL_GAP"
MODEL_INVALID = "MODEL_INVALID"
LINE_MISSING = "MODEL_LINE_MISSING"
NAIVE_MISSING = "NAIVE_MISSING"
ACTUAL_MISSING = "ACTUAL_MISSING"
ACTUAL_NOT_VERIFIED = "ACTUAL_NOT_VERIFIED"
BASIS_CHANGED = "BASIS_CHANGED"  # the basis chosen for FY t differs from FY t+1's
ACTUAL_ZERO = "ACTUAL_ZERO"  # APE undefined (the point still counts for MAE)
LATER_FILING_FLAGS = (RESTATED, FROM_LATER_FILING)

# The primary reason of an excluded qualifying point is the first of these it carries: the
# product first (did it give a forecast), then the naive, then the actual, then the basis. The
# primary counts of a row add up to qualifying - scored (BC35 (4)).
PRIMARY_ORDER = (ORIGIN_ERROR, MODEL_GAP, MODEL_INVALID, LINE_MISSING, NAIVE_MISSING,
                 ACTUAL_ERROR, LABEL_MISMATCH, ACTUAL_MISSING, ACTUAL_NOT_VERIFIED,
                 BASIS_CHANGED)


class RestatedActualError(ValueError):
    """An actual that is not FY t+1's first-reported value (restatement lookahead)."""


# ---------------------------------------------------------------- configuration

@dataclass(frozen=True)
class MetricSpec:
    name: str
    metric: str | None = None  # a statement-set metric
    basis: str | None = None  # or a basis (the metric chosen for the year)
    line: str | None = None  # the forecast's projection line
    groups: tuple[tuple[str, str], ...] = ()  # chosen metric -> group name (reported apart)
    difference: tuple[str, str] | None = None  # or a - b of two other metrics


@dataclass(frozen=True)
class E6Config:
    scenario: str
    horizon: int
    min_readable_years: int
    readable_metric: str
    actual_status: str
    metrics: tuple[MetricSpec, ...]
    draws: int
    seed: int
    level: float
    min_companies: int

    def metric(self, name: str) -> MetricSpec:
        return next(m for m in self.metrics if m.name == name)


def _metric_spec(name: str, raw: Mapping[str, Any]) -> MetricSpec:
    difference = raw.get("difference")
    spec = MetricSpec(name, raw.get("metric"), raw.get("basis"), raw.get("line"),
                      tuple((k, v) for k, v in (raw.get("groups") or {}).items()),
                      tuple(difference) if difference else None)
    sources = [spec.metric is not None, spec.basis is not None, spec.difference is not None]
    if sum(sources) != 1:
        raise ConfigError(f"e6 metric {name}: needs exactly one of metric, basis, difference")
    if spec.difference is None and not spec.line:
        raise ConfigError(f"e6 metric {name}: needs a projection line")
    if spec.difference is not None and len(spec.difference) != 2:
        raise ConfigError(f"e6 metric {name}: difference needs two metrics")
    return spec


def load_e6_config(path: Path | str = E6_PATH) -> E6Config:
    data = _load_yaml(path)
    metrics = tuple(_metric_spec(n, r) for n, r in (data.get("metrics") or {}).items())
    names = {m.name for m in metrics}
    for m in metrics:
        if m.difference and not set(m.difference) <= names - {m.name}:
            raise ConfigError(f"e6 metric {m.name}: difference terms must be other metrics")
    boot = data.get("bootstrap") or {}
    if data.get("horizon") != 1:
        raise ConfigError("e6 horizon: only one year ahead is designed (section 8)")
    return E6Config(str(data["scenario"]), int(data["horizon"]), int(data["min_readable_years"]),
                    str(data.get("readable_metric", "revenue")), str(data["actual_status"]),
                    metrics, int(boot["draws"]), int(boot["seed"]), float(boot["level"]),
                    int(boot["min_companies"]))


# ---------------------------------------------------------------- origins and values

@dataclass(frozen=True)
class Origin:
    issuer: str
    fiscal_year: int  # t, as labelled at the run's as_of
    filed: date  # d(t)
    accession: str
    target_year: int
    target_filed: date  # d(t+1)
    target_accession: str


def list_origins(filings: Sequence[FilingRecord]) -> list[Origin]:
    """Each pinned 10-K whose next fiscal year's 10-K is pinned (filings oldest first)."""
    return [Origin(f.issuer, f.fiscal_year, f.filed, f.accession, n.fiscal_year, n.filed,
                   n.accession)
            for f, n in zip(filings, filings[1:]) if n.fiscal_year == f.fiscal_year + 1]


@dataclass(frozen=True)
class Value:
    """One metric for one year from a statement set: the accepted value, or why there is none."""

    value: Decimal | None
    reason: str | None  # None when there is a value
    metric: str  # the metric read (a basis's choice for the year)
    refs: tuple[InputRef, ...] = ()  # one, or two for a difference

    @property
    def found(self) -> bool:
        return self.value is not None

    @property
    def status(self) -> str | None:
        if not self.refs:
            return None
        statuses = {r.status for r in self.refs}
        return statuses.pop() if len(statuses) == 1 else "+".join(sorted(statuses))


def _from_lookup(found: Lookup) -> Value:
    if not found.found:
        return Value(None, found.reason, found.metric)
    return Value(found.ref.value, None, found.metric, (found.ref,))


def read_value(s: StatementSet, year: int, spec: MetricSpec, config: E6Config,
               as_of: date) -> Value:
    """The metric's accepted value for the year, as of as_of (a difference needs both)."""
    if spec.difference is not None:
        a, b = (read_value(s, year, config.metric(n), config, as_of) for n in spec.difference)
        if not (a.found and b.found):
            missing = next(v for v in (a, b) if not v.found)
            return Value(None, f"{missing.metric}: {missing.reason}", spec.name, a.refs + b.refs)
        return Value(a.value - b.value, None, spec.name, a.refs + b.refs)
    if spec.basis is not None:
        return _from_lookup(lookup_basis(s, year, spec.basis, as_of))
    return _from_lookup(lookup(s, year, spec.metric, as_of))


def model_value(forecast: Forecast, year: int, spec: MetricSpec,
                config: E6Config) -> float | None:
    """The projected line for the year; None when the year or the line is absent."""
    projected = next((y for y in forecast.years if y.fiscal_year == year), None)
    if projected is None:
        return None
    if spec.difference is not None:
        a, b = (model_value(forecast, year, config.metric(n), config) for n in spec.difference)
        return None if a is None or b is None else a - b
    return projected.lines.get(spec.line)


def forecast_inputs(forecast: Forecast) -> list[InputRef]:
    """Every statement value the forecast read: driver inputs and opening balances."""
    return [r for a in forecast.assumptions for r in a.inputs] + \
        [r for o in forecast.opening for r in o.inputs]


def check_inputs(refs: Iterable[InputRef], as_of: date) -> date | None:
    """Raise PointInTimeError if any input was filed after as_of; else the latest filing date."""
    refs = list(refs)
    late = [f"FY{r.fiscal_year} {r.metric} {r.accession} filed {r.filed}" for r in refs
            if r.filed > as_of]
    if late:
        raise PointInTimeError(f"inputs filed after {as_of}: {late[:5]}")
    return max((r.filed for r in refs), default=None)


def check_first_reported(value: Value, accession: str, filed: date) -> None:
    """Raise RestatedActualError unless every ref is FY t+1's own filing, not restated."""
    for r in value.refs:
        if r.accession != accession or r.filed != filed or \
                any(f in LATER_FILING_FLAGS for f in r.flags):
            raise RestatedActualError(
                f"FY{r.fiscal_year} {r.metric}: {r.accession} filed {r.filed} flags {r.flags}; "
                f"expected the first-reported value of {accession} filed {filed}")


# ---------------------------------------------------------------- points

@dataclass(frozen=True)
class Point:
    issuer: str
    origin_year: int
    origin_filed: date
    target_year: int
    target_filed: date
    metric: str
    group: str | None  # e.g. reported / ebit_proxy for operating income
    model: float | None
    naive: Decimal | None
    actual: Decimal | None
    actual_status: str | None
    basis: str | None  # the metric the actual was read from
    excluded: tuple[str, ...] = ()
    detail: str = ""
    # False when the origin does not qualify (FEWER_THAN_MIN_READABLE_YEARS) or its statement
    # set as of d(t) could not be built, so the test could not be made (ORIGIN_ERROR). A
    # qualifying origin whose forecast cannot be built stays qualifying (ORIGIN_ERROR).
    qualifying: bool = True

    @property
    def scored(self) -> bool:
        return not self.excluded

    @property
    def ae_model(self) -> float | None:
        return None if not self.scored else abs(self.model - float(self.actual))

    @property
    def ae_naive(self) -> float | None:
        return None if not self.scored else abs(float(self.naive) - float(self.actual))

    @property
    def ape_defined(self) -> bool:
        return self.scored and self.actual != 0

    @property
    def ape_model(self) -> float | None:
        return self.ae_model / abs(float(self.actual)) if self.ape_defined else None

    @property
    def ape_naive(self) -> float | None:
        return self.ae_naive / abs(float(self.actual)) if self.ape_defined else None

    def record(self) -> dict[str, Any]:
        return {"issuer": self.issuer, "origin_year": self.origin_year,
                "origin_filed": self.origin_filed.isoformat(), "target_year": self.target_year,
                "target_filed": self.target_filed.isoformat(), "metric": self.metric,
                "group": self.group, "model": self.model,
                "naive": None if self.naive is None else str(self.naive),
                "actual": None if self.actual is None else str(self.actual),
                "actual_status": self.actual_status, "basis": self.basis,
                "excluded": list(self.excluded), "detail": self.detail,
                "qualifying": self.qualifying}


@dataclass
class OriginResult:
    origin: Origin
    label: int | None = None  # FY t's label as of d(t)
    readable_years: tuple[int, ...] = ()
    eligible: bool = False
    history_years: tuple[int, ...] = ()
    history_missing: dict[int, str] = field(default_factory=dict)
    history_inputs: int = 0  # forecast inputs read from the BC7 history
    drivers: dict[str, str] = field(default_factory=dict)  # driver -> status:basis (diagnostic)
    forecast_status: str | None = None
    forecast_reasons: tuple[str, ...] = ()
    max_input_filed: date | None = None
    error: str | None = None
    actual_reason: str | None = None  # ACTUAL_ERROR or LABEL_MISMATCH for every metric
    actual_error: str | None = None
    points: list[Point] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        o = self.origin
        return {"issuer": o.issuer, "fiscal_year": o.fiscal_year, "filed": o.filed.isoformat(),
                "accession": o.accession, "target_year": o.target_year,
                "target_filed": o.target_filed.isoformat(),
                "target_accession": o.target_accession, "label": self.label,
                "readable_years": list(self.readable_years), "eligible": self.eligible,
                "history_years": list(self.history_years),
                "history_missing": {str(k): v for k, v in self.history_missing.items()},
                "history_inputs": self.history_inputs, "drivers": self.drivers,
                "forecast_status": self.forecast_status,
                "forecast_reasons": list(self.forecast_reasons),
                "max_input_filed": self.max_input_filed.isoformat()
                if self.max_input_filed else None,
                "error": self.error, "actual_reason": self.actual_reason,
                "actual_error": self.actual_error}


@dataclass(frozen=True)
class Harness:
    """What score_origin calls (the shipped system in the run; synthetic sets in tests).

    statement_set(as_of) -> the issuer's statement set as of the date;
    history(set) -> (the set plus BC7 years, history years, missing years with reasons);
    forecast(set, as_of) -> the Base forecast of the set as of the date."""

    statement_set: Callable[[date], StatementSet]
    history: Callable[[StatementSet], tuple[StatementSet, tuple[int, ...], dict[int, str]]]
    forecast: Callable[[StatementSet, date], Forecast]


def _own_year(s: StatementSet, accession: str) -> int | None:
    found = [f.fiscal_year for f in s.filings if f.accession == accession]
    return found[0] if found and found[0] == max(s.years()) else None


def _point(result: OriginResult, spec: MetricSpec, target: int | None, reasons: list[str],
           model: float | None = None, naive: Value | None = None, actual: Value | None = None,
           group: str | None = None, detail: str = "", qualifying: bool = True) -> Point:
    o = result.origin
    return Point(o.issuer, result.label or o.fiscal_year, o.filed,
                 target or o.target_year, o.target_filed, spec.name, group, model,
                 naive.value if naive else None, actual.value if actual else None,
                 actual.status if actual else None, actual.metric if actual else None,
                 tuple(dict.fromkeys(reasons)), detail, qualifying)


def score_origin(origin: Origin, harness: Harness, config: E6Config) -> OriginResult:
    """One origin: forecast and naive as of d(t), actual as of d(t+1), every metric."""
    result = OriginResult(origin)
    try:
        s = harness.statement_set(origin.filed)
        label = _own_year(s, origin.accession)
        if label is None:
            raise ValueError(f"{origin.accession} is not the latest filing as of {origin.filed}")
        result.label = label
        s, result.history_years, result.history_missing = harness.history(s)
    except PointInTimeError:
        raise
    except Exception as exc:  # reported, never hidden
        result.error = f"{type(exc).__name__}: {exc}"
        result.points = [_point(result, m, None, [ORIGIN_ERROR], detail=result.error,
                                qualifying=False) for m in config.metrics]
        return result
    years = range(label - config.min_readable_years + 1, label + 1)
    result.readable_years = tuple(y for y in years
                                  if lookup(s, y, config.readable_metric, origin.filed).found)
    result.eligible = len(result.readable_years) == config.min_readable_years
    if not result.eligible:
        result.points = [_point(result, m, label + 1, [FEWER_YEARS], qualifying=False)
                         for m in config.metrics]
        return result
    try:
        forecast = harness.forecast(s, origin.filed)
    except PointInTimeError:
        raise
    except Exception as exc:  # reported, never hidden
        result.error = f"{type(exc).__name__}: {exc}"
        result.points = [_point(result, m, label + 1, [ORIGIN_ERROR], detail=result.error)
                         for m in config.metrics]
        return result
    result.forecast_status, result.forecast_reasons = forecast.status, forecast.reasons
    result.drivers = {a.driver_id: f"{a.status}:{a.basis}" for a in forecast.assumptions}
    naive = {m.name: read_value(s, label, m, config, origin.filed) for m in config.metrics}
    refs = forecast_inputs(forecast) + [r for v in naive.values() for r in v.refs]
    result.max_input_filed = check_inputs(refs, origin.filed)
    result.history_inputs = sum(1 for r in forecast_inputs(forecast) if BC7_HISTORY in r.flags)
    target, actuals = label + 1, {}
    try:
        later = harness.statement_set(origin.target_filed)
        target_label = _own_year(later, origin.target_accession)
        if target_label is None:
            raise ValueError(f"{origin.target_accession} is not the latest filing as of "
                             f"{origin.target_filed}")
        if target_label == label + 1:
            for m in config.metrics:
                actuals[m.name] = read_value(later, target_label, m, config,
                                             origin.target_filed)
                check_first_reported(actuals[m.name], origin.target_accession,
                                     origin.target_filed)
    except RestatedActualError:
        raise
    except Exception as exc:  # reported, never hidden
        result.actual_reason, result.actual_error = ACTUAL_ERROR, f"{type(exc).__name__}: {exc}"
    else:
        if target_label != label + 1:
            result.actual_reason = LABEL_MISMATCH
            result.actual_error = f"FY{origin.target_year} labelled FY{target_label} " \
                                  f"as of {origin.target_filed}, not FY{label + 1}"
    for m in config.metrics:
        result.points.append(_metric_point(result, m, forecast, naive[m.name],
                                           actuals.get(m.name), target, config))
    return result


def _metric_point(result: OriginResult, spec: MetricSpec, forecast: Forecast, naive: Value,
                  actual: Value | None, target: int, config: E6Config) -> Point:
    reasons: list[str] = []
    details: list[str] = []
    if result.actual_reason is not None:
        reasons.append(result.actual_reason)
        details.append(result.actual_error or "")
    model = None
    if forecast.status != "VERIFIED":
        reasons.append(MODEL_GAP if forecast.status == "GAP" else MODEL_INVALID)
        details.append("; ".join(forecast.reasons[:3]))
    else:
        model = model_value(forecast, target, spec, config)
        if model is None:
            reasons.append(LINE_MISSING)
    if not naive.found:
        reasons.append(NAIVE_MISSING)
        details.append(f"naive {naive.metric}: {naive.reason}")
    group = None
    if actual is not None:
        if not actual.found:
            reasons.append(ACTUAL_MISSING)
            details.append(f"actual {actual.metric}: {actual.reason}")
        elif any(r.status != config.actual_status for r in actual.refs):
            reasons.append(ACTUAL_NOT_VERIFIED)
            details.append(f"actual {actual.metric}: {actual.status}")
        groups = dict(spec.groups)
        if groups and actual.found:
            group = groups.get(actual.metric, actual.metric)
        if spec.basis is not None and naive.found and actual.found \
                and naive.metric != actual.metric:
            reasons.append(BASIS_CHANGED)
            details.append(f"FY{target - 1} {naive.metric}; FY{target} {actual.metric}")
    return _point(result, spec, target, reasons, model, naive, actual, group, "; ".join(details))


# ---------------------------------------------------------------- summary

def company_bootstrap(by_company: Mapping[str, Sequence[float]], draws: int, seed: int,
                      level: float) -> tuple[float, float] | None:
    """Percentile interval of the mean over points, resampling companies (a drawn company's
    points move together; the E1 convention)."""
    issuers = sorted(i for i, values in by_company.items() if values)
    if not issuers:
        return None
    rng = random.Random(seed)
    estimates = sorted(mean([v for i in rng.choices(issuers, k=len(issuers))
                             for v in by_company[i]]) for _ in range(draws))
    low = estimates[int((1 - level) / 2 * draws)]
    high = estimates[min(draws - 1, int((1 + level) / 2 * draws))]
    return low, high


def primary_reason(point: Point) -> str | None:
    """The first of the point's exclusion reasons in PRIMARY_ORDER (None when scored)."""
    if point.scored:
        return None
    return next((r for r in PRIMARY_ORDER if r in point.excluded), point.excluded[0])


def row_coverage(points: Sequence[Point]) -> dict[str, Any]:
    """One row's coverage (BC35 (4)): scored / qualifying origins, companies likewise, one
    primary reason per excluded qualifying point (adding up to qualifying - scored), and the
    reasons of the points that do not qualify."""
    qualifying = [p for p in points if p.qualifying]
    scored = [p for p in qualifying if p.scored]
    rank = {r: i for i, r in enumerate(PRIMARY_ORDER)}
    primary = Counter(primary_reason(p) for p in qualifying if not p.scored)
    return {
        "qualifying": len(qualifying), "scored": len(scored),
        "coverage": len(scored) / len(qualifying) if qualifying else None,
        "companies_qualifying": len({p.issuer for p in qualifying}),
        "companies_scored": len({p.issuer for p in scored}),
        "primary_exclusions": dict(sorted(primary.items(),
                                          key=lambda kv: (rank.get(kv[0], len(rank)), kv[0]))),
        "not_qualifying": dict(sorted(Counter(r for p in points if not p.qualifying
                                              for r in p.excluded).items())),
    }


def naive_ape(point: Point) -> float | None:
    """|naive - actual| / |actual|, whatever the model did; None without both values or at 0."""
    if point.naive is None or point.actual is None or point.actual == 0:
        return None
    return abs(float(point.naive) - float(point.actual)) / abs(float(point.actual))


def _naive_ape_summary(by_company: Mapping[str, Sequence[float]]) -> dict[str, Any]:
    values = [v for vs in by_company.values() for v in vs]
    return {"points": len(values), "companies": len(by_company),
            "mean_naive_ape": mean(values) if values else None,
            "median_naive_ape": median(values) if values else None}


def selection_check(points: Sequence[Point]) -> dict[str, Any]:
    """Descriptive, no claim (docs/e6_prereg.md): naive APE on the scored points vs on the
    qualifying points excluded for MODEL_GAP alone (so with a naive value, a VERIFIED
    first-reported actual and the same basis, as a scored point has). It shows whether the
    origins the product declines look harder for naive than those it forecasts."""
    scored: dict[str, list[float]] = {}
    gap: dict[str, list[float]] = {}
    for p in points:
        ape = naive_ape(p)
        if ape is None:
            continue
        if p.scored:
            scored.setdefault(p.issuer, []).append(ape)
        elif p.qualifying and p.excluded == (MODEL_GAP,):
            gap.setdefault(p.issuer, []).append(ape)
    gap_points = sum(1 for p in points if p.qualifying and MODEL_GAP in p.excluded)
    return {"scored": _naive_ape_summary(scored),
            "gap_excluded": _naive_ape_summary(gap) | {"gap_points": gap_points}}


def summarise(points: Sequence[Point], config: E6Config) -> dict[str, Any]:
    """MAE, MAPE, MAE ratio, wins and the decision rule for the scored points of one row, with
    the row's coverage and selection check beside them."""
    scored = [p for p in points if p.scored]
    ape = [p for p in scored if p.ape_defined]
    by_company: dict[str, list[float]] = {}
    for p in ape:
        by_company.setdefault(p.issuer, []).append(p.ape_naive - p.ape_model)
    companies = sorted({p.issuer for p in scored})
    mae_model = mean(p.ae_model for p in scored) if scored else None
    mae_naive = mean(p.ae_naive for p in scored) if scored else None
    enough = len(by_company) >= config.min_companies
    ci = company_bootstrap(by_company, config.draws, config.seed, config.level) \
        if enough else None
    company_means = {i: mean(v) for i, v in by_company.items()}
    return {
        "points": len(points), "scored": len(scored), "ape_points": len(ape),
        "companies": companies, "ape_companies": sorted(by_company),
        "mae_model": mae_model, "mae_naive": mae_naive,
        "mae_ratio": mae_model / mae_naive if scored and mae_naive else None,
        "mape_model": mean(p.ape_model for p in ape) if ape else None,
        "mape_naive": mean(p.ape_naive for p in ape) if ape else None,
        "mean_ape_difference": mean(d for v in by_company.values() for d in v) if ape else None,
        "ci95": list(ci) if ci else None,
        "beats_naive": bool(ci and ci[0] > 0),
        "claim": "model beats naive" if ci and ci[0] > 0 else "no accuracy claim",
        "too_few_companies": not enough,
        "wins": sum(p.ape_model < p.ape_naive for p in ape),
        "losses": sum(p.ape_model > p.ape_naive for p in ape),
        "ties": sum(p.ape_model == p.ape_naive for p in ape),
        "company_wins": sum(v > 0 for v in company_means.values()),
        "company_losses": sum(v < 0 for v in company_means.values()),
        "company_ties": sum(v == 0 for v in company_means.values()),
        "actual_zero": sum(1 for p in scored if not p.ape_defined),
        "exclusions": dict(sorted(Counter(r for p in points for r in p.excluded).items())),
        "excluded_points": sum(1 for p in points if not p.scored),
    } | row_coverage(points) | {"selection_check": selection_check(points)}


def rows(points: Sequence[Point], config: E6Config) -> dict[str, list[Point]]:
    """Each metric pooled, then each group of a grouped metric (e.g. operating_income:reported)."""
    found: dict[str, list[Point]] = {}
    for m in config.metrics:
        mine = [p for p in points if p.metric == m.name]
        found[m.name] = mine
        for _, group in m.groups:
            found[f"{m.name}:{group}"] = [p for p in mine if p.group == group]
    return found


def _add_ungrouped(found: dict[str, dict[str, Any]], points: Sequence[Point],
                   config: E6Config) -> dict[str, dict[str, Any]]:
    """On a grouped metric's pooled row: the qualifying points with no group (no actual, so no
    basis to group by). They count in the pooled row only."""
    for m in config.metrics:
        if m.groups:
            found[m.name]["ungrouped_qualifying"] = sum(
                1 for p in points if p.metric == m.name and p.qualifying and p.group is None)
    return found


def metrics(points: Sequence[Point], config: E6Config) -> dict[str, dict[str, Any]]:
    found = {name: summarise(mine, config) for name, mine in rows(points, config).items()}
    return _add_ungrouped(found, points, config)


def coverage(results: Sequence[OriginResult], config: E6Config) -> dict[str, Any]:
    """The train sanity check (and coverage.json): origins, and per row the coverage (scored /
    qualifying origins and companies, primary reasons) and every exclusion reason. No error,
    no accuracy."""
    points = [p for r in results for p in r.points]
    per_metric = {}
    for name, mine in rows(points, config).items():
        per_metric[name] = {
            "points": len(mine),
            "actual_zero": sum(1 for p in mine if p.scored and not p.ape_defined),
            "exclusions": dict(sorted(Counter(r for p in mine for r in p.excluded).items())),
        } | row_coverage(mine)
    _add_ungrouped(per_metric, points, config)
    return {
        "companies": sorted({r.origin.issuer for r in results}),
        "origins": len(results),
        "eligible_origins": sum(r.eligible for r in results),
        "origins_with_error": sum(r.error is not None for r in results),
        "forecast_status": dict(sorted(Counter(r.forecast_status or "not built"
                                               for r in results).items())),
        "origins_using_history": sum(bool(r.history_years) for r in results),
        "history_inputs": sum(r.history_inputs for r in results),
        "metrics": per_metric,
    }


# ---------------------------------------------------------------- the shipped system

def shipped_harness(tools, ticker: str, companyfacts: dict[str, Any],
                    config: E6Config) -> Harness:
    """The shipped statement set (tools.statement_set), the BC7 history and build_forecast
    under the configured scenario, unchanged."""
    scenario = tools.scenario(config.scenario)

    def history(s: StatementSet) -> tuple[StatementSet, tuple[int, ...], dict[int, str]]:
        found = read_history(s, tools.store, companyfacts, tools.configs, tools.forecast_config)
        return with_history(s, found), found.years, found.missing

    return Harness(lambda as_of: tools.statement_set(ticker, as_of), history,
                   lambda s, as_of: build_forecast(s, as_of, tools.forecast_config, scenario))


def run_company(tools, ticker: str, as_of: date, config: E6Config) -> list[OriginResult]:
    """Every origin of one company (pinned filings visible as of the run's as_of). The tools'
    caches are cleared afterwards so a long run does not hold every company's sets."""
    filings = tools.filings(ticker, as_of)  # refuses held-out issuers
    if not filings:
        return []
    companyfacts = read_snapshot_json(tools.store.evidence_dir,
                                      f"companyfacts_{filings[0].cik}.json", tools.store.manifest)
    harness = shipped_harness(tools, ticker, companyfacts, config)
    try:
        return [score_origin(o, harness, config) for o in list_origins(filings)]
    finally:
        tools.reconciliations.clear()
        tools.forecasts.clear()


# ---------------------------------------------------------------- run guards and report

OWN_OUTPUTS = ("eval/experiments.csv",)  # the record command's own file (#300)


def dirty_paths(porcelain: str, own: Sequence[str] = OWN_OUTPUTS) -> list[str]:
    """Changed paths from `git status --porcelain`, the record command's own file excepted."""
    paths = [line[3:].strip() for line in porcelain.splitlines() if line.strip()]
    return [p for p in paths if p not in own]


def earlier_validation_runs(runs: Path) -> list[str]:
    """Run directories holding an E6 validation run (E6 on validation runs once)."""
    found = []
    for header in sorted(Path(runs).glob("*/header.json")):
        try:
            data = json.loads(header.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if data.get("eval_id") == "E6" and data.get("split") == "validation":
            found.append(header.parent.name)
    return found


def validation_refusal(earlier: Sequence[str], prereg_tracked: bool, prereg_changed: bool,
                       dirty: Sequence[str]) -> str | None:
    """Why a validation run may not start (docs/e6_prereg.md); None when it may."""
    if earlier:
        return f"E6 on validation runs once; already run: {', '.join(earlier)}"
    if not prereg_tracked or prereg_changed:
        return "docs/e6_prereg.md must be committed, unchanged, before the validation run"
    if dirty:
        return f"the working tree must be clean at the pre-registered commit: {list(dirty)[:5]}"
    return None


def _money(x: float | None) -> str:
    return "—" if x is None else f"{x / 1e6:,.0f}"


def _rate(x: float | None) -> str:
    return "—" if x is None else f"{x:.3f}"


def coverage_text(m: Mapping[str, Any]) -> str:
    """"scored / qualifying (%)" for one row."""
    share = "—" if m["coverage"] is None else f"{m['coverage']:.0%}"
    return f"{m['scored']} / {m['qualifying']} ({share})"


def _companies_text(m: Mapping[str, Any]) -> str:
    return f"{m['companies_scored']} / {m['companies_qualifying']}"


def _counts(found: Mapping[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in found.items()) or "none"


def render_results(header: Mapping[str, Any], found: Mapping[str, Mapping[str, Any]],
                   cover: Mapping[str, Any]) -> str:
    """The validation report: every row, whatever it shows, with its coverage (scored /
    qualifying origins) in the same row as every result (docs/e6_prereg.md; BC35 (4))."""
    lines = [
        f"# E6 results: {header['run_id']} (validation, pre-registered)", "",
        f"Run {header['date']} at commit {header['git_commit']}"
        f"{' (dirty)' if header.get('git_dirty') else ''}; as_of {header['as_of']}; "
        f"model {header['model_version']}; scenario {header['scenario']}. Pre-registration: "
        f"docs/e6_prereg.md (sha256 {str(header.get('prereg_sha256'))[:12]}). Written by "
        "eval/e6_backtest.py; nothing here was chosen after the run.", "",
        "**Every result here applies only where the shipped product issued a VERIFIED forecast "
        "at the origin.** Where it gave GAP or INVALID, the origin is excluded and counted. "
        "Coverage = scored origins / qualifying origins, per row, in the same row as each "
        "result.", "",
        f"Origins: {cover['origins']} ({cover['eligible_origins']} qualifying; "
        f"{cover['origins_with_error']} with an error). Forecast status: "
        f"{cover['forecast_status']}.", "",
        "MAE in USD millions. APE difference = naive APE − model APE (positive: the model is "
        "closer). Claim: the model beats naive only if the 95% paired company-bootstrap "
        "interval lies entirely above 0.", "",
        "| row | coverage: scored / qualifying (%) | companies: scored / qualifying | MAE model "
        "| MAE naive | MAE ratio | MAPE model | MAPE naive | APE difference [95% CI] | "
        "wins / losses / ties | company wins / losses | claim |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, m in found.items():
        ci = m["ci95"]
        interval = f" [{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else (
            " (too few companies)" if m["too_few_companies"] and m["scored"] else "")
        diff = "—" if m["mean_ape_difference"] is None else f"{m['mean_ape_difference']:+.3f}"
        lines.append(
            f"| {name} | {coverage_text(m)} | {_companies_text(m)} | "
            f"{_money(m['mae_model'])} | {_money(m['mae_naive'])} | {_rate(m['mae_ratio'])} | "
            f"{_rate(m['mape_model'])} | {_rate(m['mape_naive'])} | {diff}{interval} | "
            f"{m['wins']} / {m['losses']} / {m['ties']} | "
            f"{m['company_wins']} / {m['company_losses']} | {m['claim']} |")
    for name, m in found.items():
        if m.get("ungrouped_qualifying"):
            lines += ["", f"{name}: {m['ungrouped_qualifying']} qualifying origin(s) have no "
                      "actual, so no basis to group by; they are in the pooled row only."]
    lines += ["", "## Exclusions", "",
              "**Primary reason**, one per excluded qualifying point, the first it carries in "
              f"this fixed order: {', '.join(PRIMARY_ORDER)}. It adds up to qualifying − "
              "scored.", "",
              "| row | coverage: scored / qualifying (%) | qualifying − scored | primary reasons "
              "| not qualifying |", "|---|---|---|---|---|"]
    for name, m in found.items():
        lines.append(f"| {name} | {coverage_text(m)} | {m['qualifying'] - m['scored']} | "
                     f"{_counts(m['primary_exclusions'])} | {_counts(m['not_qualifying'])} |")
    lines += ["", "**All reasons** (a point may carry several).", "",
              "| row | coverage: scored / qualifying (%) | excluded points | reasons | "
              "actual 0 (no APE) |", "|---|---|---|---|---|"]
    for name, m in found.items():
        lines.append(f"| {name} | {coverage_text(m)} | {m['excluded_points']} | "
                     f"{_counts(m['exclusions'])} | {m['actual_zero']} |")
    lines += ["", "## Selection check (descriptive; no claim)", "",
              "Naive APE where the product forecast (the scored points) and where it gave GAP "
              "(points excluded for MODEL_GAP alone: a naive value, a VERIFIED first-reported "
              "actual and the same basis). It shows whether the origins the product declines "
              "look harder for naive than those it forecasts. It decides nothing.", "",
              "| row | coverage: scored / qualifying (%) | scored: points (companies) | scored: "
              "mean / median naive APE | GAP-excluded: points (companies), of GAP points | "
              "GAP-excluded: mean / median naive APE |", "|---|---|---|---|---|---|"]
    for name, m in found.items():
        s, g = m["selection_check"]["scored"], m["selection_check"]["gap_excluded"]
        lines.append(
            f"| {name} | {coverage_text(m)} | {s['points']} ({s['companies']}) | "
            f"{_rate(s['mean_naive_ape'])} / {_rate(s['median_naive_ape'])} | "
            f"{g['points']} ({g['companies']}), of {g['gap_points']} | "
            f"{_rate(g['mean_naive_ape'])} / {_rate(g['median_naive_ape'])} |")
    return "\n".join(lines) + "\n"
