"""Planner test helpers: synthetic filings, answer observations and stub tools (no evidence is
read). The stub tools answer every registry action from canned outcomes and record each call."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from finagent.evidence.store import FilingRecord
from finagent.records import AnswerObservation, Outcome
from finagent.tools import resolve_target

AS_OF = date(2026, 11, 24)
YEARS = (2021, 2022, 2023, 2024, 2025)


def filings(issuer: str = "MSFT", cik: str = "0000789019") -> tuple[FilingRecord, ...]:
    return tuple(FilingRecord(issuer, cik, "10-K", y, date(y, 1, 1), "CALCULATION",
                              date(y, 12, 31), date(y + 1, 2, 1),
                              f"{cik}-{y % 100 + 1:02d}-000001", f"{issuer.lower()}-{y}.htm",
                              f"https://example/{issuer}/{y}.htm") for y in YEARS)


def answer(f: FilingRecord, metric: str, value: Any, *, unit: str = "USD",
           classification: str = "REPORTED_FACT", fiscal_year: int | None = None,
           period_end: date | None = None, projected: bool = False, scenario: str = "",
           **fields) -> AnswerObservation:
    return AnswerObservation(
        f.issuer, f.cik, fiscal_year or f.fiscal_year, metric, unit, value, classification,
        f.accession, f.filing_url, f.filed, None if projected else (period_end or f.end),
        fields.pop("basis", "test basis"), fields.pop("validated", True),
        fields.pop("channels", ("document", "tag")), scenario=scenario, **fields)


def by_year(fs: tuple[FilingRecord, ...], year: int) -> FilingRecord:
    return next(f for f in fs if f.fiscal_year == year)


@dataclass
class KeywordClassifier:
    """Intent stub for TrainedParser: the label of the first keyword found in the clause."""

    keywords: dict[str, str]
    default: str = "fact"
    confidence: float = 0.99
    version: str = "stub"

    def predict(self, bodies):
        out = []
        for body in bodies:
            label = next((v for k, v in self.keywords.items() if k in body.lower()),
                         self.default)
            out.append((label, self.confidence))
        return out


@dataclass
class StubTools:
    """Registry actions answered from canned outcomes (overrides: action -> fn(**args))."""

    fs: dict[str, tuple[FilingRecord, ...]] = field(
        default_factory=lambda: {"MSFT": filings(), "AMZN": filings("AMZN", "0001018724")})
    shortfall: float = 0.0
    overrides: dict[str, Callable[..., Outcome]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def filings(self, issuer: str, as_of: date) -> tuple[FilingRecord, ...]:
        return tuple(f for f in self.fs.get(issuer, ()) if f.filed <= as_of)

    def filing(self, issuer: str, year: int, as_of: date) -> FilingRecord | None:
        return next((f for f in self.filings(issuer, as_of) if f.fiscal_year == year), None)

    def resolve_year(self, issuer: str, period: Any, as_of: date) -> int | None:
        years = [f.fiscal_year for f in self.filings(issuer, as_of)]
        if not years:
            return None
        return max(years) if period == "latest" else (int(period) if int(period) in years
                                                      else None)

    def _run(self, name: str, **args) -> Outcome:
        self.calls.append((name, args))
        if name in self.overrides:
            return self.overrides[name](**args)
        return getattr(self, "_" + name)(**args)

    def __getattr__(self, name: str):
        if name in ("reported_verified", "reported_tag_only", "reported_document_only",
                    "reconcile", "project", "credit_ratios", "liquidity",
                    "debt_service_scoped", "explain_movement", "risk_signals"):
            return lambda **args: self._run(name, **args)
        raise AttributeError(name)

    # ------------------------------------------------------------ default outcomes
    def _f(self, issuer: str, period: Any, as_of: date) -> FilingRecord:
        return by_year(self.filings(issuer, as_of), int(period))

    def _reported_verified(self, issuer, period, metric, as_of):
        f = self._f(issuer, period, as_of)
        return Outcome("VERIFIED", "AGREE", (answer(f, metric, Decimal("1000000000")),))

    def _reported_tag_only(self, issuer, period, metric, as_of):
        f = self._f(issuer, period, as_of)
        return Outcome("VERIFIED", "TAG_ONLY", (answer(f, metric, Decimal("5"),
                                                       validated=False, channels=("tag",),
                                                       flags=("TAG_ONLY", "SINGLE_CHANNEL")),))

    def _reported_document_only(self, issuer, period, metric, as_of):
        return Outcome("GAP", "NO_IDENTITY_CHECK")

    def _reconcile(self, issuer, period, as_of):
        f = self._f(issuer, period, as_of)
        return Outcome("VERIFIED", "I9 and I10 exactly zero", tuple(
            answer(f, i, Decimal(0), classification="CALCULATION") for i in ("I9", "I10")))

    def _projected(self, issuer, scenario, target, as_of, values: dict[str, float]):
        fs = self.filings(issuer, as_of)
        origin = max(fs, key=lambda f: f.fiscal_year)
        year = resolve_target(origin.fiscal_year, target)
        return Outcome("VERIFIED", "FORECAST_VERIFIED", tuple(
            answer(origin, m, v, classification="CALCULATION_FROM_MODEL_ASSUMPTION",
                   fiscal_year=year, projected=True, scenario=scenario)
            for m, v in values.items()))

    def _project(self, issuer, period, scenario, target, as_of):
        return self._projected(issuer, scenario, target, as_of,
                               {"revenue": 1100.0, "free_cash_flow": 90.0, "cash": 150.0})

    def _liquidity(self, issuer, period, scenario, target, as_of):
        return self._projected(issuer, scenario, target, as_of,
                               {"liquidity_headroom": -self.shortfall or 50.0,
                                "liquidity_shortfall": self.shortfall})

    def _credit_ratios(self, issuer, period, as_of):
        f = self._f(issuer, period, as_of)
        return Outcome("VERIFIED", "RATIOS_COMPUTED", (
            answer(f, "current_ratio", 1.5, unit="x", classification="CALCULATION"),
            answer(f, "ebit_to_gross_interest", None, unit="x", classification="CALCULATION",
                   flags=("NOT_MEANINGFUL",), detail="non-positive denominator")),
            gaps=(("quick_ratio", "INPUT_GAP: inputs without a value: ['cash']"),))

    def _debt_service_scoped(self, issuer, period, scenario, target, as_of):
        return Outcome("GAP", "NOT_YET_AVAILABLE")

    def _explain_movement(self, issuer, period, metric, as_of):
        return Outcome("GAP", "NOT_YET_AVAILABLE")

    def _risk_signals(self, issuer, period, as_of):
        return Outcome("GAP", "NOT_YET_AVAILABLE")


def missing_primary(reason: str = "DOC_MISSING") -> Callable[..., Outcome]:
    return lambda **args: Outcome("MISSING_PRIMARY", reason)


def with_year(o: AnswerObservation, year: int) -> AnswerObservation:
    return replace(o, fiscal_year=year)
