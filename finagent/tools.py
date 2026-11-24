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
  (and I11 when computable) residuals of the fiscal year, as CALCULATIONs.
- project / liquidity: the integrated forecast under the scenario at the target year
  (CALCULATION_FROM_MODEL_ASSUMPTION); free cash flow = CFO - capex (#134).
- credit_ratios: every ratio of finagent.model.ratios from the fiscal year's inputs
  (finagent.model.ratio_inputs), each recomputed a second way in Decimal from the statement-set
  values; not meaningful = value None flagged NOT_MEANINGFUL with its reason (#120 b); a missing
  input = a gap for that ratio (#120 c).
- debt_service_scoped, explain_movement, risk_signals: Phase 7 placeholders, GAP
  NOT_YET_AVAILABLE, never a value.

Caches are plain dicts with explicit keys: one reconciliation per (issuer, as_of, view) and one
forecast per (issuer, as_of, view, scenario). The view (finagent.statements.replay) is FULL
except in E8 replays; a view that hides a whole channel flags model outputs
SINGLE_CHANNEL_INPUTS (#119).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from finagent.config import (AgentConfig, ForecastConfig, IntentVocabulary, ScenarioSpec,
                             check_not_held_out, load_agent_config, load_forecast_config,
                             load_intent_vocabulary, load_scenarios, load_universe)
from finagent.evidence.store import EvidenceStore, FilingRecord
from finagent.model.forecast import build_forecast
from finagent.model.history import check_point_in_time
from finagent.model.ratio_inputs import (HIST_KEYS, HistoricalInputs, equity_terms,
                                         historical_inputs)
from finagent.model.ratios import BY_ID, RatioSpec, RatioValue, compute_all
from finagent.records import AnswerObservation, Forecast, Observation, Outcome, Target
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
RATIO_UNITS = {"ratio": "x", "percent": "percent", "days": "days"}
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
        flags = ("CORRECTED_VALUES",) if corrected else ()
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
    def _projection(self, issuer: str, scenario: str, target: Target, metrics: Sequence[str],
                    as_of: date) -> Outcome:
        f = self.forecast(issuer, scenario, as_of)
        if f.status != VERIFIED or f.origin_year is None:
            return Outcome(f.status if f.status != VERIFIED else GAP, "FORECAST_" + f.status,
                           detail="; ".join(f.reasons))
        year = resolve_target(f.origin_year, target)
        projected = {y.fiscal_year: y for y in f.years}
        if year not in projected:
            return Outcome(GAP, "TARGET_NOT_PROJECTED",
                           detail=f"FY{year}: projected years are {sorted(projected)}")
        origin = self.filing(issuer, f.origin_year, as_of)
        assert origin is not None
        lines = projected[year].lines
        flags = tuple(dict.fromkeys(f.flags + projected[year].flags + (
            (SINGLE_CHANNEL_INPUTS,) if self.view.single_channel else ())))
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

    # ------------------------------------------------------------ Phase 7 placeholders
    def debt_service_scoped(self, issuer: str, period: int | str, scenario: str,
                            target: Target, *, as_of: date) -> Outcome:
        check_as_of(as_of)
        return Outcome(GAP, NOT_YET_AVAILABLE,
                       detail="scoped debt service (8.12) is built in Phase 7")

    def explain_movement(self, issuer: str, period: int | str, metric: str, *,
                         as_of: date) -> Outcome:
        check_as_of(as_of)
        return Outcome(GAP, NOT_YET_AVAILABLE,
                       detail="explanations with retrieval (8.13) are built in Phase 7")

    def risk_signals(self, issuer: str, period: int | str, *, as_of: date) -> Outcome:
        check_as_of(as_of)
        return Outcome(GAP, NOT_YET_AVAILABLE,
                       detail="risk signals and insight cards (8.13) are built in Phase 7")


