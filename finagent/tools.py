"""Planner tools: one method per action of the 8.15 registry (Phase 6.3).

Every method takes `as_of` and never reads a filing filed after it: filings come from the
evidence store as of that date, and every statement set is checked with check_point_in_time
(finagent.model.history). Each returns an Outcome whose observations are AnswerObservations
for the observation gate (finagent.planner.gate), which checks them again independently.

- reported_verified: the post-agent statement set's value (#120 d) when both channels agree
  (VERIFIED). A value only the tag channel read gives MISSING_PRIMARY (DOC_MISSING); a value only
  the document read gives MISSING_PRIMARY (TAG_MISSING). A restated year returns the statement
  set's value (latest filing, flagged RESTATED) with the original alongside (#117).
- reported_tag_only: the tag-only value, flagged SINGLE_CHANNEL (8.15).
- reported_document_only (BC25): the document-only value only when 8.7 accepted it (every
  identity it enters passes), flagged SINGLE_CHANNEL; else GAP with the 8.7 reason.
- reconcile: the Correlation Agent and the final check (finagent.verify.reconcile): the I9, I10
  (and I11 when computable) residuals of the fiscal year, as CALCULATIONs (flagged
  SINGLE_CHANNEL_INPUTS under a view that hides a whole channel, #144).
- project / liquidity: the integrated forecast under the scenario at the target year
  (CALCULATION_FROM_MODEL_ASSUMPTION); free cash flow = CFO - capex (#134).
- credit_ratios: every ratio of finagent.model.ratios from the fiscal year's inputs
  (finagent.model.ratio_inputs), each recomputed a second way in Decimal from the statement-set
  values; not meaningful = value None flagged NOT_MEANINGFUL with its reason (#120 b); a missing
  input = a gap for that ratio (#120 c).
- debt_service_scoped (8.12, BC27): the scoped scheduled debt-service coverage of the target
  year on the cash flow available for debt service, and its components
  (CALCULATION_FROM_MODEL_ASSUMPTION; finagent.credit.debt_service), each recomputed a second
  way; a negative coverage carries its label in the basis (#169); the comprehensive forward
  DSCR is always a gap (COMPREHENSIVE_DSCR_WITHHELD with its reasons).
- risk_signals (8.13, Appendix C.2; 7.3): every signal of configs/signals.yaml for the fiscal
  year (finagent.risk.signals); each FIRED signal's measures, recomputed a second way from the
  statement-set Decimals, as observations "<signal>.<condition>" with the card's quotes
  (finagent.risk.insights; BM25 over Items 7 and 8 of that 10-K) or flagged EVIDENCE_GAP; a
  required "signals_fired" count; the other signals as gaps (NOT_FIRED, GAP, NOT_APPLIED with
  their reasons), never with numbers. R10 is a model output of the Moderate scenario (D3):
  its fired measure carries no quote and is flagged MODEL_DERIVED ("model-derived; no filing
  evidence expected", #180), never EVIDENCE_GAP.
- explain_movement (8.13, Appendix D; 7.3): the value, its change from the previous fiscal
  year (CALCULATION) and 1-2 quoted Item 7 sentences from the fiscal year's 10-K for the frozen
  explain query; no sentence above the threshold gives GAP EVIDENCE_GAP (D7: the answer still
  shows the figure of the fact prerequisite). #186: a year one channel read is used when 8.7
  accepted it, flagged SINGLE_CHANNEL (fact and change), and the change is then not validated
  and its basis says so. #185: a sentence whose stated change contradicts the computed change
  is not quoted (AMOUNT_MISMATCH, finagent.retrieval.quotes).

Caches are plain dicts with explicit keys: one reconciliation per (issuer, as_of, view) and one
forecast per (issuer, as_of, view, scenario). The view (finagent.statements.replay) is FULL
except in E8 replays; a view that hides a whole channel flags model outputs, ratios and
statement residuals SINGLE_CHANNEL_INPUTS (#119, #144).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from finagent.config import (AgentConfig, ForecastConfig, IntentVocabulary, ScenarioSpec,
                             SignalConfig, SignalSpec, check_not_held_out, load_agent_config,
                             load_forecast_config, load_intent_vocabulary, load_scenarios,
                             load_signal_config, load_universe)
from finagent.credit.debt_service import (NOT_APPLIED, basis_text, debt_service_table,
                                          withheld_reason)
from finagent.evidence.store import EvidenceStore, FilingRecord
from finagent.model.forecast import build_forecast
from finagent.model.history import check_point_in_time
from finagent.model.ratio_inputs import (HIST_KEYS, HistoricalInputs, equity_terms,
                                         historical_inputs)
from finagent.model.ratios import BY_ID, RatioSpec, RatioValue, compute_all
from finagent.records import (AnswerObservation, ConditionResult, Forecast, InsightCard,
                              Observation, Outcome, ProjectedYear, SignalResult, Target)
from finagent.retrieval.index import Retriever
from finagent.retrieval.quotes import ChangeCheck, quotes_for, threshold_note
from finagent.retrieval.templates import QueryTemplates, load_query_templates
from finagent.risk.insights import EVIDENCE_GAP, MODEL_DERIVED, cards
from finagent.risk.signals import (FALLBACK, FIRED, Sources, condition, evaluate_set, history,
                                   ratio_specs, read_input)
from finagent.statements.build import StatementConfigs, StatementSet
from finagent.statements.replay import FULL, SINGLE_CHANNEL_INPUTS, EvidenceView
from finagent.verify.reconcile import Reconciliation, reconcile_company

ROOT = Path(__file__).resolve().parent.parent
VERIFIED, GAP, INVALID = "VERIFIED", "GAP", "INVALID"
MISSING_PRIMARY, CONFLICT = "MISSING_PRIMARY", "CONFLICT"
DOC_MISSING, TAG_MISSING = "DOC_MISSING", "TAG_MISSING"
NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
SINGLE_CHANNEL = "SINGLE_CHANNEL"
NOT_MEANINGFUL = "NOT_MEANINGFUL"
RATIO_UNITS = {"ratio": "x", "percent": "percent", "days": "days", "dollars": "USD"}
DERIVED_FORECAST = {"free_cash_flow": (("cfo", 1), ("capex", -1))}  # #134, as the workbook
RECOMPUTE_TOLERANCE = 1e-9  # relative: float vs Decimal recomputation of a ratio
# Input flags an answer passes on (the value rests on a corrected, restated or proxy input).
CARRIED_FLAGS = ("CORRECTED", "RESTATED", "FROM_LATER_FILING", "EBIT_PROXY",
                 "EBIT_PROXY_NET_INTEREST", "FINANCE_LEASES_INCLUDED")


class ToolError(Exception):
    """A tool was called with arguments it cannot accept (e.g. no as_of date)."""


def check_as_of(as_of: Any) -> date:
    """as_of must be a calendar date (not a datetime, not a string)."""
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ToolError(f"as_of must be a date, got {as_of!r}")
    return as_of


def resolve_target(origin: int, target: Target) -> int:
    """The projected fiscal year of a target: None = origin + 1; "+n" = origin + n; a year."""
    if target is None:
        return origin + 1
    if isinstance(target, str):
        return origin + int(target.lstrip("+"))
    return int(target)


def carried(flags: Sequence[str]) -> tuple[str, ...]:
    return tuple(f for f in flags if f in CARRIED_FLAGS)


def fact_basis(o: Observation) -> str:
    """One line: how the statement set established the value."""
    parts = [o.reason]
    if o.row_label:
        parts.append(f"row {o.row_label!r}")
    if o.concepts:
        parts.append("tags " + ", ".join(o.concepts))
    if o.classification == "CALCULATION" and o.inputs:
        parts.append("from " + ", ".join(o.inputs))
    if o.basis:
        parts.append(o.basis)
    return "; ".join(parts)


def fact_answer(o: Observation, extra_flags: tuple[str, ...] = ()) -> AnswerObservation:
    """A statement-set observation as an answer (its own provenance; #117 original kept)."""
    flags = tuple(dict.fromkeys(o.flags + extra_flags))
    return AnswerObservation(
        o.issuer, o.cik, o.fiscal_year, o.metric, o.unit, o.value, o.classification,
        o.accession, o.filing_url, o.filed, o.fiscal_period_end, fact_basis(o),
        o.status == VERIFIED, tuple(o.channels), flags, detail=o.detail,
        original_value=o.original_value, original_accession=o.original_accession)


def fact_outcome(o: Observation | None) -> Outcome:
    """reported_verified's outcome for one statement-set observation (module docstring)."""
    if o is None:
        return Outcome(GAP, "NO_OBSERVATION")
    if o.status == VERIFIED:
        return Outcome(VERIFIED, o.reason, (fact_answer(o),))
    if o.status == SINGLE_CHANNEL:
        if tuple(o.channels) == ("tag",):
            return Outcome(MISSING_PRIMARY, DOC_MISSING, detail=f"{o.reason}: tag channel only")
        if tuple(o.channels) == ("document",):
            return Outcome(MISSING_PRIMARY, TAG_MISSING, detail=f"{o.reason}: document only")
        return Outcome(GAP, o.reason)
    if o.status in ("CONFLICT", "DISAGREE"):
        return Outcome(CONFLICT, o.reason, detail=o.detail)
    if o.status == INVALID:
        return Outcome(INVALID, o.reason, detail=o.detail)
    return Outcome(GAP, o.reason, detail=o.detail)


def single_channel_outcome(o: Observation | None, channel: str) -> Outcome:
    """The value one channel alone read, when 8.7 accepted it; flagged SINGLE_CHANNEL."""
    if o is None:
        return Outcome(GAP, "NO_OBSERVATION")
    if o.status != SINGLE_CHANNEL or tuple(o.channels) != (channel,):
        return Outcome(GAP, f"NOT_{channel.upper()}_ONLY", detail=f"{o.status} {o.reason}")
    if not o.accepted or o.value is None:
        return Outcome(GAP, o.reason, detail=f"{channel}-only value not accepted (8.7)")
    return Outcome(VERIFIED, o.reason, (fact_answer(o, (SINGLE_CHANNEL,)),))


def explain_fact(o: Observation | None) -> Outcome:
    """#186: an explained year's fact: VERIFIED when both channels agree; else the value one
    channel read, when 8.7 accepted it, flagged SINGLE_CHANNEL (as reported_tag_only or
    reported_document_only would give it); else the fact's own outcome."""
    found = fact_outcome(o)
    if found.status == MISSING_PRIMARY:
        single = single_channel_outcome(o, "tag" if found.reason == DOC_MISSING else "document")
        if single.status == VERIFIED:
            return single
    return found


@dataclass(frozen=True)
class ExplainInputs:
    """An explain search ready to run (FinAgentTools.explain_inputs)."""

    year: int
    now: Observation
    before: Observation
    first: Outcome  # the explained year's fact outcome
    channels: tuple[str, ...]  # channels that read both years
    single: bool  # either year's fact is SINGLE_CHANNEL (#186)
    query: str
    anchors: tuple[str, ...]
    expected: ChangeCheck


def explain_query(templates: QueryTemplates, metric: str, now: Observation,
                  before: Observation) -> tuple[str, tuple[str, ...], ChangeCheck]:
    """The explain search for a metric's (non-zero) change between two accepted values: the
    frozen query and anchors for its direction, and the computed change a quoted sentence must
    not contradict (#185)."""
    assert now.value is not None and before.value is not None
    change = now.value - before.value
    query, anchors = templates.explain(metric, "increased" if change > 0 else "decreased")
    scale = next((o.scale for o in (now, before) if o.scale is not None), None)
    return query, tuple(anchors), ChangeCheck(change, before.value,
                                              Decimal(10) ** scale if scale is not None else None)


def inputs_text(year: int, now: Observation, before: Observation) -> str:
    """How the two years of an explained change were read (#186: never "verified" unless both
    channels agree in both years)."""
    def read(o: Observation) -> str:
        if o.status == VERIFIED:
            return "VERIFIED"
        return f"SINGLE_CHANNEL, {' and '.join(o.channels)} only"
    if now.status == VERIFIED and before.status == VERIFIED:
        return "both VERIFIED"
    return (f"FY{year} {read(now)}, FY{year - 1} {read(before)}; single-channel inputs, not "
            "verified by both channels")


# ---------------------------------------------------------------- ratios, second way

def decimal_inputs(s: StatementSet, inputs: HistoricalInputs) -> dict[str, Decimal]:
    """The same inputs as Decimals, read from the statement-set observations themselves."""
    found: dict[str, Decimal] = {}
    for key, o in inputs.sources.items():
        if o.value is not None:
            found[key] = o.value
    terms = equity_terms(s, inputs.fiscal_year)
    if terms:
        found["equity"] = -sum((v for _, v in terms), Decimal(0))
    years = s.years()
    i = years.index(inputs.fiscal_year)
    if i > 0:
        prior = s.get(years[i - 1], HIST_KEYS["revenue"])
        if prior is not None and "revenue_prior" in inputs.values and prior.value is not None:
            found["revenue_prior"] = prior.value
    return found


def decimal_opening(s: StatementSet, inputs: HistoricalInputs) -> dict[str, Decimal]:
    years = s.years()
    i = years.index(inputs.fiscal_year)
    found: dict[str, Decimal] = {}
    if i == 0:
        return found
    for key in inputs.opening:
        o = s.get(years[i - 1], HIST_KEYS[key])
        if o is not None and o.value is not None:
            found[key] = o.value
    return found


def _decimal_sum(terms, values: Mapping[str, Decimal]) -> Decimal | None:
    total = Decimal(0)
    for sign, key, _ in terms:
        if key in values:
            total += sign * values[key]
    return total


def decimal_ratio(spec: RatioSpec, values: Mapping[str, Decimal],
                  opening: Mapping[str, Decimal], days: float | None,
                  parts: Mapping[str, float]) -> float | None:
    """The ratio recomputed in Decimal (difference kinds from the component ratios)."""
    if spec.kind == "balance_change":
        used = [(sign, key) for sign, key, _ in spec.numerator if key in values]
        if not used or any(key not in opening for _, key in used):
            return None
        return float(sum((sign * (values[key] - opening[key]) for sign, key in used),
                         Decimal(0)))
    if spec.kind == "difference":
        return sum(sign * parts[k] for sign, k, _ in spec.numerator if k in parts)
    den = _decimal_sum(spec.denominator, values)
    if den is None or den <= 0:
        return None
    if spec.kind == "average_days":
        key = spec.numerator[0][1]
        if days is None or key not in values or key not in opening:
            return None
        return float((opening[key] + values[key]) / 2 * Decimal(str(days)) / den)
    num = _decimal_sum(spec.numerator, values)
    if num is None:
        return None
    return float(num / den - 1) if spec.kind == "growth" else float(num / den)


def same_ratio(a: float, b: float | None) -> bool:
    return b is not None and abs(a - b) <= RECOMPUTE_TOLERANCE * max(1.0, abs(b))


# ---------------------------------------------------------------- signals, second way

def decimal_year(s: StatementSet, year: int, config: SignalConfig
                 ) -> tuple[dict[str, Decimal], dict[str, Decimal], HistoricalInputs]:
    """(values, opening balances, inputs) of one year as Decimals from the observations."""
    inputs = historical_inputs(s, year)
    values = decimal_inputs(s, inputs)
    for key, spec in config.inputs.items():
        found = read_input(s, year, spec)  # the metric, or its fallback sum (#179)
        if found.value is not None:
            values[key] = found.value
    return values, decimal_opening(s, inputs), inputs


def decimal_sources(s: StatementSet, config: SignalConfig, forecast: Any = None,
                    origin_year: int | None = None) -> Sources:
    """The signal sources recomputed in Decimal from the statement-set values (the second way
    behind every fired measure)."""
    years, specs = set(s.years()), ratio_specs(config)
    memo: dict[int, tuple[dict[str, Decimal], dict[str, Decimal], HistoricalInputs]] = {}

    def year_of(y: int) -> tuple[dict[str, Decimal], dict[str, Decimal], HistoricalInputs]:
        if y not in memo:
            memo[y] = decimal_year(s, y, config)
        return memo[y]

    def ratio(y: int, rid: str) -> RatioValue | None:
        if y not in years:
            return None
        values, opening, inputs = year_of(y)
        value = decimal_ratio(specs[rid], values, opening, inputs.days, {})
        return RatioValue(rid, value, "OK") if value is not None else \
            RatioValue(rid, None, "GAP", "not recomputable in Decimal")

    def value(y: int, key: str) -> object:
        return year_of(y)[0].get(key) if y in years else None

    def absent(y: int, key: str) -> bool:
        return y in years and key in year_of(y)[2].absent

    return Sources(ratio, value, absent, specs, forecast, origin_year)


def recompute_problem(result: SignalResult, spec: SignalSpec, second: Sources) -> str | None:
    """A fired signal's measures recomputed the second way; the first that differs, or None."""
    specs = {c.condition_id: c for c in spec.conditions + ((spec.while_,) if spec.while_
                                                            else ())}
    for c in result.conditions:
        if c.value is None:
            continue
        again = condition(specs[c.condition_id], spec, result.fiscal_year, second)
        if not same_ratio(c.value, again.value):
            return f"{c.condition_id}: {c.value} vs {again.value}"
    return None


def condition_keys(c: ConditionResult, spec: SignalSpec, specs: Mapping[str, RatioSpec]
                   ) -> tuple[str, ...]:
    """The input keys a condition's measure reads."""
    of = next(x.of for x in spec.conditions + ((spec.while_,) if spec.while_ else ())
              if x.condition_id == c.condition_id)
    if of[0] in specs:
        r = specs[of[0]]
        return tuple(k for _, k, _ in r.numerator + r.denominator)
    return of


def signal_summary(results: Sequence[SignalResult]) -> str:
    groups: dict[str, list[str]] = {}
    for r in results:
        groups.setdefault(r.state, []).append(r.signal_id)
    return "; ".join(f"{state.lower().replace('_', ' ')}: {', '.join(ids)}"
                     for state, ids in groups.items())


# ---------------------------------------------------------------- the tools

@dataclass
class FinAgentTools:
    """The planner's tools over one evidence store, M1 classifier and evidence view."""

    store: EvidenceStore
    classifier: Any
    universe: Mapping[str, str] = field(default_factory=load_universe)
    configs: StatementConfigs = field(default_factory=StatementConfigs.load)
    agent: AgentConfig = field(default_factory=load_agent_config)
    forecast_config: ForecastConfig = field(default_factory=load_forecast_config)
    scenarios: tuple[ScenarioSpec, ...] = field(default_factory=lambda: tuple(load_scenarios()))
    vocabulary: IntentVocabulary = field(default_factory=load_intent_vocabulary)
    view: EvidenceView = FULL
    reconciliations: dict[tuple[str, date, EvidenceView], Reconciliation] = \
        field(default_factory=dict)
    forecasts: dict[tuple[str, date, EvidenceView, str], Forecast] = field(default_factory=dict)
    signal_config: SignalConfig = field(default_factory=load_signal_config)
    templates: QueryTemplates = field(default_factory=load_query_templates)
    retriever: Retriever | None = None  # built on first use over the same store

    @classmethod
    def default(cls, view: EvidenceView = FULL, dataset_version: str = "v3") -> "FinAgentTools":
        """The pinned evidence store and the M1 document model of `dataset_version`."""
        from finagent.training.m1 import load_model

        classifier = load_model(ROOT / "models", dataset_version,
                                ROOT / "datasets" / f"rows_{dataset_version}.meta.json")
        return cls(EvidenceStore(ROOT / "evidence"), classifier, view=view)

    # ------------------------------------------------------------ evidence
    def filings(self, issuer: str, as_of: date) -> tuple[FilingRecord, ...]:
        """The issuer's 10-K filings filed on or before as_of (held-out issuers refused)."""
        check_as_of(as_of)
        check_not_held_out([issuer], self.universe, include_held_out=False)
        return tuple(f for f in self.store.load_filings(issuer, as_of) if f.filed <= as_of)

    def filing(self, issuer: str, fiscal_year: int, as_of: date) -> FilingRecord | None:
        return next((f for f in self.filings(issuer, as_of) if f.fiscal_year == fiscal_year),
                    None)

    def resolve_year(self, issuer: str, period: int | str, as_of: date) -> int | None:
        """The fiscal year of a goal period ("latest" = the latest 10-K filed by as_of)."""
        years = [f.fiscal_year for f in self.filings(issuer, as_of)]
        if not years:
            return None
        if period == "latest":
            return max(years)
        return int(period) if int(period) in years else None

    def reconciliation(self, issuer: str, as_of: date) -> Reconciliation:
        """The post-agent reconciliation (cached per issuer, as_of, view)."""
        check_as_of(as_of)
        key = (issuer, as_of, self.view)
        if key not in self.reconciliations:
            self.reconciliations[key] = reconcile_company(
                self.store, issuer, as_of, self.classifier, self.universe, self.configs,
                self.agent, self.view)
        rec = self.reconciliations[key]
        check_point_in_time(rec.statement_set, as_of)
        return rec

    def statement_set(self, issuer: str, as_of: date) -> StatementSet:
        return self.reconciliation(issuer, as_of).statement_set

    def scenario(self, name: str) -> ScenarioSpec:
        found = [s for s in self.scenarios if s.name == name]
        if not found:
            raise ToolError(f"unknown scenario {name!r}")
        return found[0]

    def forecast(self, issuer: str, scenario: str, as_of: date) -> Forecast:
        """The integrated forecast under one scenario (cached per issuer, as_of, view,
        scenario)."""
        key = (issuer, check_as_of(as_of), self.view, scenario)
        if key not in self.forecasts:
            self.forecasts[key] = build_forecast(self.statement_set(issuer, as_of), as_of,
                                                 self.forecast_config, self.scenario(scenario))
        return self.forecasts[key]

    def _no_year(self, issuer: str, period: int | str, as_of: date) -> Outcome:
        years = sorted(f.fiscal_year for f in self.filings(issuer, as_of))
        return Outcome(GAP, "PERIOD_NOT_AVAILABLE",
                       detail=f"FY{period} is not among the 10-Ks filed by {as_of}: {years}")

    # ------------------------------------------------------------ fact
    def _fact(self, issuer: str, period: int | str, metric: str,
              as_of: date) -> tuple[int | None, Observation | None]:
        year = self.resolve_year(issuer, period, as_of)
        if year is None:
            return None, None
        return year, self.statement_set(issuer, as_of).get(year, metric)

    def reported_verified(self, issuer: str, period: int | str, metric: str, *,
                          as_of: date) -> Outcome:
        year, o = self._fact(issuer, period, metric, check_as_of(as_of))
        return self._no_year(issuer, period, as_of) if year is None else fact_outcome(o)

    def reported_tag_only(self, issuer: str, period: int | str, metric: str, *,
                          as_of: date) -> Outcome:
        year, o = self._fact(issuer, period, metric, check_as_of(as_of))
        if year is None:
            return self._no_year(issuer, period, as_of)
        return single_channel_outcome(o, "tag")

    def reported_document_only(self, issuer: str, period: int | str, metric: str, *,
                               as_of: date) -> Outcome:
        year, o = self._fact(issuer, period, metric, check_as_of(as_of))
        if year is None:
            return self._no_year(issuer, period, as_of)
        return single_channel_outcome(o, "document")

    # ------------------------------------------------------------ statements
    def reconcile(self, issuer: str, period: int | str, *, as_of: date) -> Outcome:
        year = self.resolve_year(issuer, period, check_as_of(as_of))
        if year is None:
            return self._no_year(issuer, period, as_of)
        rec = self.reconciliation(issuer, as_of)
        check = rec.checks.get(year)
        if check is None:
            return Outcome(GAP, "NO_FINAL_CHECK")
        if check.status != VERIFIED:
            return Outcome(check.status, "FINAL_CHECK_" + check.status, detail=check.reason)
        f = self.filing(issuer, year, as_of)
        assert f is not None
        corrected = sum(1 for (y, _), o in rec.statement_set.observations.items()
                        if y == year and "CORRECTED" in o.flags)
        flags = (("CORRECTED_VALUES",) if corrected else ()) + (
            (SINGLE_CHANNEL_INPUTS,) if self.view.single_channel else ())
        found = tuple(
            AnswerObservation(issuer, f.cik, year, r.identity_id, "USD", r.residual,
                              "CALCULATION", f.accession, f.filing_url, f.filed, f.end,
                              f"final check (BC22): {check.reason}", True,
                              flags=flags + (("REVIEW",) if r.status == "REVIEW" else ()),
                              detail=f"tolerance {r.tolerance}")
            for r in check.identities if r.status != "NOT_COMPUTABLE" and r.residual is not None)
        return Outcome(VERIFIED, check.reason, found,
                       detail=f"{len(rec.edits)} agent edit(s); {corrected} corrected value(s)")

    # ------------------------------------------------------------ forecast, liquidity
    def _forecast_target(self, issuer: str, scenario: str, target: Target,
                         as_of: date) -> tuple[Forecast, ProjectedYear] | Outcome:
        """The verified forecast and its target year, or the GAP that stops the answer."""
        f = self.forecast(issuer, scenario, as_of)
        if f.status != VERIFIED or f.origin_year is None:
            return Outcome(f.status if f.status != VERIFIED else GAP, "FORECAST_" + f.status,
                           detail="; ".join(f.reasons))
        year = resolve_target(f.origin_year, target)
        projected = {y.fiscal_year: y for y in f.years}
        if year not in projected:
            return Outcome(GAP, "TARGET_NOT_PROJECTED",
                           detail=f"FY{year}: projected years are {sorted(projected)}")
        return f, projected[year]

    def _model_flags(self, f: Forecast, year: ProjectedYear) -> tuple[str, ...]:
        return tuple(dict.fromkeys(f.flags + year.flags + (
            (SINGLE_CHANNEL_INPUTS,) if self.view.single_channel else ())))

    def _projection(self, issuer: str, scenario: str, target: Target, metrics: Sequence[str],
                    as_of: date) -> Outcome:
        found_target = self._forecast_target(issuer, scenario, target, as_of)
        if isinstance(found_target, Outcome):
            return found_target
        f, projected_year = found_target
        assert f.origin_year is not None
        year = projected_year.fiscal_year
        origin = self.filing(issuer, f.origin_year, as_of)
        assert origin is not None
        lines = projected_year.lines
        flags = self._model_flags(f, projected_year)
        found, gaps = [], []
        for metric in metrics:
            if metric in DERIVED_FORECAST:
                parts = DERIVED_FORECAST[metric]
                if not all(line in lines for line, _ in parts):
                    gaps.append((metric, "NOT_MODELLED"))
                    continue
                value = sum(sign * lines[line] for line, sign in parts)
                basis = " ".join(f"{'+' if s > 0 else '-'} {line}" for line, s in parts)[2:]
            elif metric in lines:
                value, basis = lines[metric], f"projected line {metric}"
            else:
                gaps.append((metric, "NOT_MODELLED"))
                continue
            found.append(AnswerObservation(
                issuer, origin.cik, year, metric, "USD", value,
                "CALCULATION_FROM_MODEL_ASSUMPTION", origin.accession, origin.filing_url,
                origin.filed, None,
                f"{scenario} scenario from FY{f.origin_year} (checks F1-F5 pass): {basis}",
                True, flags=flags, scenario=scenario))
        return Outcome(VERIFIED, "FORECAST_VERIFIED", tuple(found), gaps=tuple(gaps))

    def project(self, issuer: str, period: int | str, scenario: str, target: Target, *,
                as_of: date) -> Outcome:
        check_as_of(as_of)
        return self._projection(issuer, scenario, target, list(self.vocabulary.metrics[
            "forecast"]), as_of)

    def liquidity(self, issuer: str, period: int | str, scenario: str, target: Target, *,
                  as_of: date) -> Outcome:
        check_as_of(as_of)
        return self._projection(issuer, scenario, target, list(self.vocabulary.metrics[
            "liquidity"]), as_of)

    # ------------------------------------------------------------ credit
    def credit_ratios(self, issuer: str, period: int | str, *, as_of: date) -> Outcome:
        year = self.resolve_year(issuer, period, check_as_of(as_of))
        if year is None:
            return self._no_year(issuer, period, as_of)
        s = self.statement_set(issuer, as_of)
        f = self.filing(issuer, year, as_of)
        assert f is not None
        inputs = historical_inputs(s, year)
        ratios = compute_all(inputs.values, inputs.absent, inputs.days, inputs.opening)
        values, opening = decimal_inputs(s, inputs), decimal_opening(s, inputs)
        found, gaps = [], []
        for ratio_id in self.vocabulary.metrics["credit"]:
            r, spec = ratios[ratio_id], BY_ID[ratio_id]
            result = self._ratio_answer(s, f, spec, r, inputs, values, opening, ratios)
            if isinstance(result, AnswerObservation):
                found.append(result)
            else:
                gaps.append((ratio_id, result))
        return Outcome(VERIFIED if found else GAP, "RATIOS_COMPUTED" if found else "NO_RATIO",
                       tuple(found), gaps=tuple(gaps))

    def _ratio_answer(self, s: StatementSet, f: FilingRecord, spec: RatioSpec, r: RatioValue,
                      inputs: HistoricalInputs, values: Mapping[str, Decimal],
                      opening: Mapping[str, Decimal],
                      ratios: Mapping[str, RatioValue]) -> AnswerObservation | str:
        """One ratio as an answer, or the reason it has none."""
        if r.status in ("GAP", "NOT_APPLIED"):
            return f"{'INPUT_GAP' if r.status == 'GAP' else 'NOT_APPLIED'}: {r.detail}"
        used = {k for _, k, _ in spec.numerator + spec.denominator}
        sources = [inputs.sources[k] for k in used if k in inputs.sources]
        flags = tuple(dict.fromkeys(fl for o in sources for fl in carried(o.flags)))
        if self.view.single_channel:
            flags += (SINGLE_CHANNEL_INPUTS,)
        accessions = tuple(sorted({o.accession for o in sources} - {f.accession}))
        unit = RATIO_UNITS[spec.fmt]
        basis = f"{spec.label}: " + " / ".join(
            "+".join(f"{'-' if sg < 0 else ''}{k}" for sg, k, _ in part)
            for part in (spec.numerator, spec.denominator) if part)
        if r.status == "NM":
            return AnswerObservation(s.issuer, f.cik, f.fiscal_year, spec.ratio_id, unit, None,
                                     "CALCULATION", f.accession, f.filing_url, f.filed, f.end,
                                     basis, True, flags=flags + (NOT_MEANINGFUL,),
                                     detail=r.detail, sources=accessions)
        assert r.value is not None
        parts = {k: v.value for k, v in ratios.items() if v.status == "OK" and v.value is not None}
        second = decimal_ratio(spec, values, opening, inputs.days, parts)
        if not same_ratio(r.value, second):
            return f"RECOMPUTATION_MISMATCH: {r.value} vs {second}"
        metrics = sorted({o.metric for o in sources})
        return AnswerObservation(s.issuer, f.cik, f.fiscal_year, spec.ratio_id, unit, r.value,
                                 "CALCULATION", f.accession, f.filing_url, f.filed, f.end,
                                 basis + f" (inputs {', '.join(metrics)}; recomputed in Decimal)",
                                 True, flags=flags, sources=accessions)

    # ------------------------------------------------------------ debt service
    def debt_service_scoped(self, issuer: str, period: int | str, scenario: str,
                            target: Target, *, as_of: date) -> Outcome:
        """The scoped debt-service coverage of the target year and its components (8.12;
        finagent.credit.debt_service); the comprehensive DSCR is a gap, always withheld."""
        check_as_of(as_of)
        found_target = self._forecast_target(issuer, scenario, target, as_of)
        if isinstance(found_target, Outcome):
            return found_target
        f, projected_year = found_target
        assert f.origin_year is not None
        ds = {d.fiscal_year: d for d in debt_service_table(f)}[projected_year.fiscal_year]
        if ds.status == INVALID:
            return Outcome(INVALID, "RECOMPUTATION_MISMATCH", detail=ds.detail)
        if ds.status == NOT_APPLIED:
            return Outcome(GAP, NOT_APPLIED, detail=ds.detail)
        origin = self.filing(issuer, f.origin_year, as_of)
        assert origin is not None
        flags = self._model_flags(f, projected_year)
        basis = (f"{scenario} scenario from FY{f.origin_year} (checks F1-F5 pass; recomputed "
                 f"from the cash movement): "
                 + basis_text(bool(f.switches.get("refinance_maturities")), ds.negative))
        found, gaps = [], [("comprehensive_dscr", withheld_reason())]
        for metric, unit, value in (
                ("scoped_dscr", "x", ds.scoped_dscr),
                ("cash_available_for_debt_service", "USD", ds.cash_available),
                ("cfo_less_capex", "USD", ds.cfo_less_capex),
                ("debt_service", "USD", ds.debt_service),
                ("scheduled_principal", "USD", ds.scheduled_principal),
                ("funded_interest", "USD", ds.funded_interest),
                ("finance_lease_payments", "USD", ds.finance_lease_payments)):
            not_meaningful = metric == "scoped_dscr" and value is None
            if value is None and not not_meaningful:
                gaps.append((metric, f"{NOT_APPLIED}: the filer has no such obligation"))
                continue
            found.append(AnswerObservation(
                issuer, origin.cik, projected_year.fiscal_year, metric, unit, value,
                "CALCULATION_FROM_MODEL_ASSUMPTION", origin.accession, origin.filing_url,
                origin.filed, None, basis, True,
                flags=flags + ((NOT_MEANINGFUL,) if not_meaningful else ()),
                scenario=scenario, detail=ds.detail if not_meaningful else ""))
        return Outcome(VERIFIED, "SCOPED_DEBT_SERVICE", tuple(found), gaps=tuple(gaps))

    # ------------------------------------------------------------ risk signals, explain
    def search(self) -> Retriever:
        """The point-in-time retriever over the same store and universe (held-out refused)."""
        if self.retriever is None:
            self.retriever = Retriever(self.store, self.universe)
        return self.retriever

    def signal_results(self, issuer: str, year: int, as_of: date) -> tuple[SignalResult, ...]:
        """Every signal of one fiscal year (R10 on the scenario forecast from the latest 10-K)."""
        s = self.statement_set(issuer, check_as_of(as_of))
        names = {k: q.name for k, q in self.templates.signals.items()}
        return evaluate_set(s, year, self.signal_config, names,
                            lambda scenario: self.forecast(issuer, scenario, as_of),
                            history(s, self.signal_config))

    def insight_cards(self, issuer: str, year: int, as_of: date
                      ) -> tuple[tuple[SignalResult, ...], tuple[InsightCard, ...]]:
        """The fiscal year's signals and a card for each fired one (evidence from its 10-K)."""
        f = self.filing(issuer, year, check_as_of(as_of))
        if f is None:
            raise ToolError(f"{issuer} FY{year}: no 10-K filed by {as_of}")
        results = self.signal_results(issuer, year, as_of)
        return results, cards(results, f, self.search(), self.templates,
                              self.signal_config.evidence, as_of)

    def risk_signals(self, issuer: str, period: int | str, *, as_of: date) -> Outcome:
        year = self.resolve_year(issuer, period, check_as_of(as_of))
        if year is None:
            return self._no_year(issuer, period, as_of)
        s = self.statement_set(issuer, as_of)
        f = self.filing(issuer, year, as_of)
        assert f is not None
        results, made = self.insight_cards(issuer, year, as_of)
        by_signal = {c.signal_id: c for c in made}
        second = decimal_sources(s, self.signal_config,
                                 lambda scenario: self.forecast(issuer, scenario, as_of),
                                 max(s.years()))
        found, gaps = [], []
        for r in results:
            spec = self.signal_config.signals[r.signal_id]
            if r.state != FIRED:
                why = " ".join(x for x in (r.reason, r.detail) if x)
                gaps.append((r.signal_id, f"{r.state}: {why}" if why else r.state))
                continue
            problem = recompute_problem(r, spec, second)
            if problem:
                gaps.append((r.signal_id, f"RECOMPUTATION_MISMATCH: {problem}"))
                continue
            found.extend(self._signal_answers(s, f, r, spec, by_signal[r.signal_id]))
        fired = sorted({o.metric.split(".")[0] for o in found}, key=lambda k: list(
            self.signal_config.signals).index(k))
        basis = (f"{len(fired)} of {len(results)} signals fired (Appendix C.2 thresholds, "
                 f"configs/signals.yaml v{self.signal_config.version}): "
                 + signal_summary(results))
        count = AnswerObservation(issuer, f.cik, year, "signals_fired", "count",
                                  Decimal(len(fired)), "CALCULATION", f.accession, f.filing_url,
                                  f.filed, f.end, basis, True,
                                  flags=(SINGLE_CHANNEL_INPUTS,) if self.view.single_channel
                                  else ())
        return Outcome(VERIFIED, "SIGNALS_EVALUATED", (count, *found), gaps=tuple(gaps))

    def _signal_answers(self, s: StatementSet, f: FilingRecord, r: SignalResult,
                        spec: SignalSpec, card: InsightCard) -> list[AnswerObservation]:
        """One observation per measure of a fired signal; the card's quotes (or its
        EVIDENCE_GAP flag) go on the measures that fired, not on the context measures."""
        model = r.data == "scenario"
        specs = ratio_specs(self.signal_config)
        found = []
        for c in card.metrics:
            assert c.value is not None
            fired = c.state == FIRED
            years = [y for y, _ in c.values] or [r.fiscal_year, r.fiscal_year - 1]
            keys = condition_keys(c, spec, specs)
            flags = () if model else tuple(dict.fromkeys(
                fl for y in years if y in s.years()
                for k, o in historical_inputs(s, y).sources.items() if k in keys
                for fl in carried(o.flags)))
            flags += tuple(dict.fromkeys(  # an input built from its fallback (#179)
                f"{FALLBACK}:{self.signal_config.inputs[k].metric}" for k in keys
                if k in self.signal_config.inputs and not model
                for y in years if y in s.years()
                and read_input(s, y, self.signal_config.inputs[k]).note))
            flags += ((SINGLE_CHANNEL_INPUTS,) if self.view.single_channel else ()) + (
                (EVIDENCE_GAP,) if fired and card.status == EVIDENCE_GAP else ()) + (
                (MODEL_DERIVED,) if fired and model else ())  # #180: no quote expected
            basis = (f"{r.signal_id} {r.name} (severity {r.severity}): {c.label}, test "
                     f"{c.op} {c.threshold:g} {c.unit}: {c.state}"
                     + (f"; {c.detail}" if c.detail else "")
                     + ("" if model else "; recomputed in Decimal"))
            found.append(AnswerObservation(
                s.issuer, f.cik, r.fiscal_year, f"{r.signal_id}.{c.condition_id}", c.unit,
                c.value, "CALCULATION_FROM_MODEL_ASSUMPTION" if model else "CALCULATION",
                f.accession, f.filing_url, f.filed, f.end, basis, True, flags=flags,
                scenario=r.scenario, detail=card.text, quotes=card.evidence if fired else ()))
        return found

    def explain_inputs(self, issuer: str, period: int | str, metric: str, *,
                       as_of: date) -> "ExplainInputs | Outcome":
        """What explain_movement searches with, or the GAP outcome that stops it before any
        search (both years' facts accepted, a common channel when one is single-channel, a
        non-zero change). Shared with the 7.4 variant comparison so both search alike."""
        year = self.resolve_year(issuer, period, check_as_of(as_of))
        if year is None:
            return self._no_year(issuer, period, as_of)
        s = self.statement_set(issuer, as_of)
        now, before = s.get(year, metric), s.get(year - 1, metric)
        first, prior = explain_fact(now), explain_fact(before)
        if first.status != VERIFIED:
            return Outcome(GAP, "FACT_NOT_VERIFIED", detail=f"FY{year} {metric}: "
                                                            f"{first.status} {first.reason}")
        if prior.status != VERIFIED:
            return Outcome(GAP, "PRIOR_YEAR_NOT_VERIFIED",
                           detail=f"FY{year - 1} {metric}: {prior.status} {prior.reason}; "
                                  "no accepted value to compare")
        assert now is not None and before is not None
        assert now.value is not None and before.value is not None
        channels = tuple(c for c in ("document", "tag")
                         if c in now.channels and c in before.channels)
        single = SINGLE_CHANNEL in first.observations[0].flags + prior.observations[0].flags
        if single and not channels:
            return Outcome(GAP, "NO_COMMON_CHANNEL",
                           detail=f"FY{year} {metric} read by {', '.join(now.channels)} only, "
                                  f"FY{year - 1} by {', '.join(before.channels)} only")
        if now.value == before.value:
            return Outcome(GAP, "NO_MOVEMENT", detail=f"FY{year} {metric} equals FY{year - 1}")
        query, anchors, expected = explain_query(self.templates, metric, now, before)
        return ExplainInputs(year, now, before, first, channels, single, query, anchors,
                             expected)

    def explain_movement(self, issuer: str, period: int | str, metric: str, *,
                         as_of: date) -> Outcome:
        """The metric's value, its change from the previous year and 1-2 quoted Item 7
        sentences (module docstring). #186: a fact one channel read is used when 8.7 accepted
        it; the change is then flagged SINGLE_CHANNEL and never presented as verified. #185:
        a sentence stating a change that contradicts the computed one is not quoted."""
        found = self.explain_inputs(issuer, period, metric, as_of=as_of)
        if isinstance(found, Outcome):
            return found
        year, now, before, query, anchors = (found.year, found.now, found.before, found.query,
                                             found.anchors)
        first, channels, single, expected = (found.first, found.channels, found.single,
                                             found.expected)
        change = now.value - before.value
        spec = self.signal_config.evidence
        result = quotes_for(self.search(), issuer, year, query, anchors, spec.explain_sections,
                            spec, as_of=as_of, expected=expected)
        if not result.quotes:
            best = f"best score {result.candidates[0].quote.score:.2f}" if result.candidates \
                else "no candidate sentence"
            note = threshold_note(result, spec)
            return Outcome(GAP, EVIDENCE_GAP,
                           detail=f"no Item 7 sentence above the threshold "
                                  f"({spec.min_score:g}) for {query!r} ({best})"
                                  + (f"; {note}" if note else ""))
        f = self.filing(issuer, year, as_of)
        assert f is not None
        flags = tuple(dict.fromkeys(carried(now.flags) + carried(before.flags)
                                    + ((SINGLE_CHANNEL,) if single else ())))
        moved = AnswerObservation(
            issuer, f.cik, year, f"{metric}_change", "USD", change, "CALCULATION",
            f.accession, f.filing_url, f.filed, f.end,
            f"FY{year} minus FY{year - 1} {metric} ({inputs_text(year, now, before)}); "
            f"quotes: Item 7 of this 10-K for {query!r}", not single,
            channels=channels if single else (), flags=flags,
            sources=tuple(sorted({now.accession, before.accession} - {f.accession})),
            quotes=result.quotes)
        return Outcome(VERIFIED, "EXPLAINED", (first.observations[0], moved))


