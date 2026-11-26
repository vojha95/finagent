"""E6 harness (finagent.evaluation.e6): origins, values, leakage guards, exclusions, metrics."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import json
import pytest
from statements_support import FILING, obs

from finagent.config import ConfigError
from finagent.evaluation import e6
from finagent.evaluation.e6 import (Harness, Origin, Point, RestatedActualError, check_inputs,
                                    check_first_reported, company_bootstrap, coverage,
                                    coverage_text, dirty_paths, earlier_validation_runs,
                                    list_origins, load_e6_config, metrics, model_value,
                                    naive_ape, primary_reason, read_value, render_results,
                                    row_coverage, score_origin, selection_check, summarise,
                                    validation_refusal)
from finagent.model.history import PointInTimeError
from finagent.records import Forecast, InputRef, ModelAssumption, ProjectedYear
from finagent.statements.build import StatementSet
from finagent.statements.verify_set import RESTATED

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_e6_config()
D2024, D2025, D2026 = date(2025, 2, 1), date(2026, 2, 1), date(2027, 2, 1)
BASES = {"ebit_basis": "operating_income", "capex_basis": "capital_expenditure"}


def filing(year: int):
    return replace(FILING, fiscal_year=year, start=date(year, 1, 1), end=date(year, 12, 31),
                   filed=date(year + 1, 2, 1), accession=f"A-{year}")


def value(year: int, metric: str, amount, status="VERIFIED", flags=(), accession=None):
    o = obs(metric, amount, status=status, flags=flags)
    return replace(o, fiscal_year=year, accession=accession or f"A-{year}",
                   filed=date(year + 1, 2, 1))


def make(as_of: date, years, amounts: dict, bases=None, statuses=None, flags=None) -> StatementSet:
    """amounts: {(year, metric): amount}; statuses/flags override per key."""
    observations = {k: value(k[0], k[1], v, (statuses or {}).get(k, "VERIFIED"),
                             (flags or {}).get(k, ())) for k, v in amounts.items()}
    return StatementSet("SYNT", FILING.cik, as_of, tuple(filing(y) for y in years),
                        observations, [], {y: dict(bases or BASES) for y in years}, "test")


ORIGIN_VALUES = {(2022, "revenue"): 80, (2023, "revenue"): 90, (2024, "revenue"): 100,
                 (2024, "operating_income"): 20, (2024, "operating_cash_flow"): 30,
                 (2024, "capital_expenditure"): 10}
TARGET_VALUES = {(2025, "revenue"): 120, (2025, "operating_income"): 25,
                 (2025, "operating_cash_flow"): 35, (2025, "capital_expenditure"): 12}
ORIGIN = Origin("SYNT", 2024, D2024, "A-2024", 2025, D2025, "A-2025")
LINES = {"revenue": 110.0, "ebit": 22.0, "cfo": 31.0, "capex": 11.0}


def forecast(s: StatementSet, as_of: date, status="VERIFIED", filed=D2024) -> Forecast:
    ref = InputRef("revenue", 2024, Decimal(100), "VERIFIED", "AGREE", "A-2024", filed)
    years = (ProjectedYear(2025, dict(LINES)),) if status == "VERIFIED" else ()
    return Forecast("SYNT", FILING.cik, as_of, 2024, status,
                    () if status == "VERIFIED" else ("revenue_growth: gap",),
                    (ModelAssumption("revenue_growth", 0.1, "CAGR_4Y", "SET", (ref,)),), (),
                    years, (), {})


def harness(origin_set=None, target_set=None, fc=forecast, calls=None) -> Harness:
    sets = {D2024: origin_set or make(D2024, (2022, 2023, 2024), ORIGIN_VALUES),
            D2025: target_set or make(D2025, (2022, 2023, 2024, 2025),
                                      ORIGIN_VALUES | TARGET_VALUES),
            # a later set restates FY2025: the harness must never read it for the actual
            D2026: make(D2026, (2023, 2024, 2025, 2026),
                        ORIGIN_VALUES | {k: v * 2 for k, v in TARGET_VALUES.items()},
                        flags={k: (RESTATED,) for k in TARGET_VALUES})}

    def statement_set(as_of):
        if calls is not None:
            calls.append(as_of)
        if isinstance(sets[as_of], Exception):
            raise sets[as_of]
        return sets[as_of]

    return Harness(statement_set, lambda s: (s, (), {}), fc)


def points(result):
    return {p.metric: p for p in result.points}


# ---------------------------------------------------------------- configuration

def test_config_loads_the_designed_metrics_and_rule():
    assert [m.name for m in CONFIG.metrics] == ["revenue", "operating_income", "cfo", "capex",
                                                "fcf"]
    assert (CONFIG.draws, CONFIG.seed, CONFIG.level) == (2000, 0, 0.95)
    assert CONFIG.scenario == "Base" and CONFIG.horizon == 1
    assert CONFIG.min_readable_years == 3 and CONFIG.actual_status == "VERIFIED"
    assert dict(CONFIG.metric("operating_income").groups) == {
        "operating_income": "reported", "ebit_proxy": "ebit_proxy"}
    assert CONFIG.metric("fcf").difference == ("cfo", "capex")


@pytest.mark.parametrize("body, message", [
    ("metrics: {x: {metric: revenue, basis: b, line: l}}", "exactly one"),
    ("metrics: {x: {metric: revenue}}", "projection line"),
    ("metrics: {x: {difference: [x, y]}}", "other metrics"),
])
def test_config_refuses_bad_metrics(tmp_path, body, message):
    path = tmp_path / "e6.yaml"
    path.write_text("scenario: Base\nhorizon: 1\nmin_readable_years: 3\nactual_status: VERIFIED\n"
                    f"{body}\nbootstrap: {{draws: 10, seed: 0, level: 0.95, min_companies: 3}}\n")
    with pytest.raises(ConfigError, match=message):
        load_e6_config(path)


def test_config_refuses_another_horizon(tmp_path):
    path = tmp_path / "e6.yaml"
    path.write_text((ROOT / "configs" / "e6.yaml").read_text().replace("horizon: 1",
                                                                         "horizon: 2"))
    with pytest.raises(ConfigError, match="one year ahead"):
        load_e6_config(path)


# ---------------------------------------------------------------- origins and values

def test_list_origins_pairs_consecutive_pinned_years_only():
    found = list_origins([filing(2021), filing(2022), filing(2024), filing(2025)])
    assert [(o.fiscal_year, o.target_year) for o in found] == [(2021, 2022), (2024, 2025)]
    assert found[0].filed == date(2022, 2, 1) and found[0].target_filed == date(2023, 2, 1)


def test_read_value_metric_basis_difference_and_missing():
    s = make(D2024, (2022, 2023, 2024), ORIGIN_VALUES)
    assert read_value(s, 2024, CONFIG.metric("revenue"), CONFIG, D2024).value == Decimal(100)
    oi = read_value(s, 2024, CONFIG.metric("operating_income"), CONFIG, D2024)
    assert (oi.value, oi.metric) == (Decimal(20), "operating_income")
    fcf = read_value(s, 2024, CONFIG.metric("fcf"), CONFIG, D2024)
    assert fcf.value == Decimal(20) and len(fcf.refs) == 2 and fcf.status == "VERIFIED"
    missing = read_value(s, 2023, CONFIG.metric("cfo"), CONFIG, D2024)
    assert missing.value is None and missing.reason == "NEITHER"
    assert read_value(s, 2023, CONFIG.metric("fcf"), CONFIG, D2024).reason.startswith(
        "operating_cash_flow")


def test_model_value_line_difference_and_absent_year():
    f = forecast(None, D2024)
    assert model_value(f, 2025, CONFIG.metric("operating_income"), CONFIG) == 22.0
    assert model_value(f, 2025, CONFIG.metric("fcf"), CONFIG) == 20.0
    assert model_value(f, 2026, CONFIG.metric("revenue"), CONFIG) is None


# ---------------------------------------------------------------- leakage guards

def test_check_inputs_raises_on_an_input_filed_after_the_origin():
    ref = InputRef("revenue", 2024, Decimal(1), "VERIFIED", "AGREE", "A-2024", D2024)
    assert check_inputs([ref], D2024) == D2024
    with pytest.raises(PointInTimeError, match="after 2025-02-01"):
        check_inputs([ref, replace(ref, filed=D2025)], D2024)


def test_leakage_any_input_read_at_an_origin_after_d_t_fails_the_origin():
    """Point in time: an input filed after d(t) stops the run (never an exclusion)."""
    late = lambda s, as_of: forecast(s, as_of, filed=D2025)  # noqa: E731
    with pytest.raises(PointInTimeError):
        score_origin(ORIGIN, harness(fc=late), CONFIG)
    result = score_origin(ORIGIN, harness(), CONFIG)
    assert result.max_input_filed is not None and result.max_input_filed <= ORIGIN.filed


def test_leakage_the_actual_is_first_reported_not_restated():
    """The actual comes from the set as of FY t+1's own filing date, never a later one that
    restates it; a restated or later-filing actual raises."""
    calls: list[date] = []
    result = score_origin(ORIGIN, harness(calls=calls), CONFIG)
    assert calls == [D2024, D2025]  # never the later set (D2026) that restates FY2025
    assert points(result)["revenue"].actual == Decimal(120)  # not the restated 240
    restated = make(D2025, (2022, 2023, 2024, 2025), ORIGIN_VALUES | TARGET_VALUES,
                    flags={(2025, "revenue"): (RESTATED,)})
    with pytest.raises(RestatedActualError, match="first-reported"):
        score_origin(ORIGIN, harness(target_set=restated), CONFIG)


def test_check_first_reported_needs_the_target_filing_itself():
    good = read_value(make(D2025, (2024, 2025), TARGET_VALUES), 2025, CONFIG.metric("revenue"),
                      CONFIG, D2025)
    check_first_reported(good, "A-2025", D2025)
    with pytest.raises(RestatedActualError):
        check_first_reported(good, "A-OTHER", D2025)
    with pytest.raises(RestatedActualError):
        check_first_reported(good, "A-2025", D2026)


# ---------------------------------------------------------------- scoring and exclusions

def test_score_origin_scores_every_metric_with_naive_and_actual():
    result = score_origin(ORIGIN, harness(), CONFIG)
    found = points(result)
    assert result.eligible and result.readable_years == (2022, 2023, 2024)
    assert all(p.scored for p in found.values())
    rev = found["revenue"]
    assert (rev.model, rev.naive, rev.actual) == (110.0, Decimal(100), Decimal(120))
    assert rev.ae_model == 10.0 and rev.ae_naive == 20.0
    assert rev.ape_model == pytest.approx(10 / 120) and rev.ape_naive == pytest.approx(20 / 120)
    assert found["operating_income"].group == "reported"
    assert found["fcf"].actual == Decimal(23) and found["fcf"].model == 20.0


def test_too_few_readable_years_excludes_the_origin():
    short = make(D2024, (2023, 2024), {k: v for k, v in ORIGIN_VALUES.items() if k[0] != 2022})
    result = score_origin(ORIGIN, harness(origin_set=short), CONFIG)
    assert not result.eligible and result.forecast_status is None
    assert all(p.excluded == (e6.FEWER_YEARS,) and not p.qualifying for p in result.points)


def test_qualifying_origin_keeps_its_points_in_the_denominator_when_the_model_fails():
    def broken(s, as_of):
        raise RuntimeError("forecast could not be built")

    result = score_origin(ORIGIN, harness(fc=broken), CONFIG)
    assert result.eligible and result.error.startswith("RuntimeError")
    assert all(p.excluded == (e6.ORIGIN_ERROR,) and p.qualifying for p in result.points)
    gap = score_origin(ORIGIN, harness(fc=lambda s, a: forecast(s, a, "GAP")), CONFIG)
    assert all(p.qualifying and e6.MODEL_GAP in p.excluded for p in gap.points)
    assert all(p.qualifying for p in score_origin(ORIGIN, harness(), CONFIG).points)
    assert points(gap)["revenue"].record()["qualifying"] is True


def test_model_gap_and_missing_or_unverified_actuals_are_excluded_and_counted():
    gap = lambda s, as_of: forecast(s, as_of, status="GAP")  # noqa: E731
    result = score_origin(ORIGIN, harness(fc=gap), CONFIG)
    assert all(e6.MODEL_GAP in p.excluded for p in result.points)
    target = make(D2025, (2022, 2023, 2024, 2025),
                  ORIGIN_VALUES | {k: v for k, v in TARGET_VALUES.items()
                                   if k[1] != "operating_cash_flow"},
                  statuses={(2025, "revenue"): "SINGLE_CHANNEL"})
    found = points(score_origin(ORIGIN, harness(target_set=target), CONFIG))
    assert found["revenue"].excluded == (e6.ACTUAL_NOT_VERIFIED,)
    assert found["cfo"].excluded == (e6.ACTUAL_MISSING,)
    assert found["fcf"].excluded == (e6.ACTUAL_MISSING,)  # missing is never zero
    assert found["capex"].scored


def test_naive_missing_and_basis_change_are_excluded():
    origin = make(D2024, (2022, 2023, 2024),
                  {k: v for k, v in ORIGIN_VALUES.items() if k[1] != "capital_expenditure"})
    target = make(D2025, (2022, 2023, 2024, 2025),
                  ORIGIN_VALUES | TARGET_VALUES | {(2025, "ebit_proxy"): 26},
                  bases={"ebit_basis": "ebit_proxy", "capex_basis": "capital_expenditure"})
    found = points(score_origin(ORIGIN, harness(origin_set=origin, target_set=target), CONFIG))
    assert found["capex"].excluded == (e6.NAIVE_MISSING,)
    assert e6.BASIS_CHANGED in found["operating_income"].excluded
    assert found["operating_income"].group == "ebit_proxy"


def test_errors_and_label_mismatch_are_reported_not_hidden():
    result = score_origin(ORIGIN, harness(origin_set=RuntimeError("no filings")), CONFIG)
    assert result.error.startswith("RuntimeError") and \
        all(p.excluded == (e6.ORIGIN_ERROR,) for p in result.points)
    assert not any(p.qualifying for p in result.points)  # the test could not be made
    result = score_origin(ORIGIN, harness(target_set=RuntimeError("not pinned")), CONFIG)
    assert result.actual_reason == e6.ACTUAL_ERROR and result.forecast_status == "VERIFIED"
    assert all(p.excluded == (e6.ACTUAL_ERROR,) for p in result.points)
    relabelled = replace(make(D2025, (2022, 2023, 2024, 2025), ORIGIN_VALUES | TARGET_VALUES))
    relabelled = replace(relabelled, filings=tuple(
        replace(f, fiscal_year=2026) if f.accession == "A-2025" else f
        for f in relabelled.filings))
    result = score_origin(ORIGIN, harness(target_set=relabelled), CONFIG)
    assert result.actual_reason == e6.LABEL_MISMATCH
    assert all(e6.LABEL_MISMATCH in p.excluded for p in result.points)


# ---------------------------------------------------------------- metrics

def pt(issuer, model, naive, actual, metric="revenue", excluded=()):
    return Point(issuer, 2024, D2024, 2025, D2025, metric, None, model,
                 Decimal(str(naive)), Decimal(str(actual)), "VERIFIED", metric, excluded)


def test_company_bootstrap_is_seeded_and_resamples_companies():
    by = {"A": [0.1, 0.1], "B": [0.2], "C": [0.3]}
    assert company_bootstrap(by, 500, 0, 0.95) == company_bootstrap(by, 500, 0, 0.95)
    low, high = company_bootstrap(by, 500, 0, 0.95)
    assert 0.1 <= low <= high <= 0.3
    assert company_bootstrap({"A": [0.5]}, 50, 0, 0.95) == (0.5, 0.5)
    assert company_bootstrap({}, 50, 0, 0.95) is None


def test_summarise_mae_mape_ratio_wins_and_rule():
    found = summarise([pt("A", 105, 90, 100), pt("B", 210, 180, 200), pt("C", 95, 80, 100),
                       pt("D", 1, 2, 0), pt("E", 1, 1, 1, excluded=(e6.ACTUAL_MISSING,))],
                      CONFIG)
    assert found["scored"] == 4 and found["ape_points"] == 3 and found["actual_zero"] == 1
    assert found["mae_model"] == pytest.approx((5 + 10 + 5 + 1) / 4)
    assert found["mae_naive"] == pytest.approx((10 + 20 + 20 + 2) / 4)
    assert found["mae_ratio"] == pytest.approx(21 / 52)
    assert found["mape_model"] == pytest.approx((0.05 + 0.05 + 0.05) / 3)
    assert (found["wins"], found["losses"], found["ties"]) == (3, 0, 0)
    assert found["beats_naive"] and found["ci95"][0] > 0
    assert found["claim"] == "model beats naive"
    assert found["exclusions"] == {e6.ACTUAL_MISSING: 1}


def test_summarise_makes_no_claim_when_the_interval_reaches_zero_or_few_companies():
    mixed = summarise([pt("A", 110, 100, 100), pt("B", 100, 110, 100), pt("C", 100, 100, 100)],
                      CONFIG)
    assert not mixed["beats_naive"] and mixed["claim"] == "no accuracy claim"
    few = summarise([pt("A", 105, 90, 100), pt("B", 105, 90, 100)], CONFIG)
    assert few["too_few_companies"] and few["ci95"] is None and not few["beats_naive"]


def test_metrics_pool_and_group_operating_income():
    a = replace(pt("A", 1, 1, 1, "operating_income"), group="reported")
    b = replace(pt("B", 1, 1, 1, "operating_income"), group="ebit_proxy")
    found = metrics([a, b], CONFIG)
    assert found["operating_income"]["points"] == 2
    assert found["operating_income:reported"]["points"] == 1
    assert found["operating_income:ebit_proxy"]["points"] == 1


def test_coverage_counts_origins_points_and_exclusions_without_errors():
    short = make(D2024, (2023, 2024), {k: v for k, v in ORIGIN_VALUES.items() if k[0] != 2022})
    results = [score_origin(ORIGIN, harness(), CONFIG),
               score_origin(ORIGIN, harness(fc=lambda s, a: forecast(s, a, "GAP")), CONFIG),
               score_origin(ORIGIN, harness(origin_set=short), CONFIG)]
    found = coverage(results, CONFIG)
    assert found["origins"] == 3 and found["eligible_origins"] == 2
    assert found["forecast_status"] == {"GAP": 1, "VERIFIED": 1, "not built": 1}
    assert found["metrics"]["revenue"] == {
        "points": 3, "actual_zero": 0,
        "exclusions": {e6.FEWER_YEARS: 1, e6.MODEL_GAP: 1},
        "qualifying": 2, "scored": 1, "coverage": 0.5, "companies_qualifying": 1,
        "companies_scored": 1, "primary_exclusions": {e6.MODEL_GAP: 1},
        "not_qualifying": {e6.FEWER_YEARS: 1}}
    assert found["metrics"]["operating_income"]["ungrouped_qualifying"] == 0
    assert found["metrics"]["operating_income:reported"]["qualifying"] == 2
    dumped = json.dumps(found)
    assert "mae_model" not in dumped and "naive_ape" not in dumped  # train: coverage only


def test_primary_reason_follows_the_fixed_order_and_sums_to_the_excluded():
    both = pt("A", 1, 1, 1, excluded=(e6.ACTUAL_NOT_VERIFIED, e6.MODEL_GAP))
    assert primary_reason(both) == e6.MODEL_GAP
    assert primary_reason(pt("A", 1, 1, 1)) is None
    assert primary_reason(pt("A", 1, 1, 1, excluded=(e6.BASIS_CHANGED, e6.NAIVE_MISSING))) \
        == e6.NAIVE_MISSING
    assert e6.PRIMARY_ORDER[:2] == (e6.ORIGIN_ERROR, e6.MODEL_GAP)


def test_row_coverage_is_scored_over_qualifying_origins_and_companies():
    rows_ = [pt("A", 1, 1, 1), pt("A", 1, 1, 1, excluded=(e6.MODEL_GAP,)),
             pt("B", 1, 1, 1, excluded=(e6.ACTUAL_NOT_VERIFIED, e6.MODEL_GAP)),
             pt("C", 1, 1, 1, excluded=(e6.ACTUAL_NOT_VERIFIED,)),
             replace(pt("D", 1, 1, 1, excluded=(e6.FEWER_YEARS,)), qualifying=False)]
    found = row_coverage(rows_)
    assert (found["scored"], found["qualifying"]) == (1, 4)
    assert found["coverage"] == pytest.approx(0.25)
    assert (found["companies_scored"], found["companies_qualifying"]) == (1, 3)
    assert found["primary_exclusions"] == {e6.MODEL_GAP: 2, e6.ACTUAL_NOT_VERIFIED: 1}
    assert list(found["primary_exclusions"]) == [e6.MODEL_GAP, e6.ACTUAL_NOT_VERIFIED]
    assert sum(found["primary_exclusions"].values()) == found["qualifying"] - found["scored"]
    assert found["not_qualifying"] == {e6.FEWER_YEARS: 1}
    assert row_coverage([])["coverage"] is None
    assert coverage_text(found) == "1 / 4 (25%)" and coverage_text(row_coverage([])) == \
        "0 / 0 (—)"


def test_group_rows_use_their_own_qualifying_origins_and_pooled_counts_ungrouped():
    a = replace(pt("A", 1, 1, 1, "operating_income"), group="reported")
    b = replace(pt("B", 1, 1, 1, "operating_income", excluded=(e6.MODEL_GAP,)),
                group="ebit_proxy")
    c = replace(pt("C", 1, 1, 1, "operating_income", excluded=(e6.ACTUAL_ERROR, e6.MODEL_GAP)),
                model=None, actual=None, basis=None)
    found = metrics([a, b, c], CONFIG)
    assert coverage_text(found["operating_income"]) == "1 / 3 (33%)"
    assert coverage_text(found["operating_income:reported"]) == "1 / 1 (100%)"
    assert coverage_text(found["operating_income:ebit_proxy"]) == "0 / 1 (0%)"
    assert found["operating_income"]["ungrouped_qualifying"] == 1
    assert "ungrouped_qualifying" not in found["revenue"]


def test_selection_check_compares_naive_ape_where_scored_and_where_gap_alone():
    scored = [pt("A", 105, 90, 100), pt("B", 210, 180, 200), pt("C", 1, 2, 0)]  # C: actual 0
    gap_only = pt("D", 1, 50, 100, excluded=(e6.MODEL_GAP,))
    gap_unverified = pt("E", 1, 50, 100, excluded=(e6.MODEL_GAP, e6.ACTUAL_NOT_VERIFIED))
    gap_no_actual = replace(pt("F", 1, 50, 100, excluded=(e6.ACTUAL_ERROR, e6.MODEL_GAP)),
                            actual=None)
    other = pt("G", 1, 50, 100, excluded=(e6.ACTUAL_NOT_VERIFIED,))  # not a GAP point
    found = selection_check(scored + [gap_only, gap_unverified, gap_no_actual, other])
    assert found["scored"] == {"points": 2, "companies": 2, "mean_naive_ape": pytest.approx(0.1),
                               "median_naive_ape": pytest.approx(0.1)}
    assert found["gap_excluded"] == {"points": 1, "companies": 1,
                                     "mean_naive_ape": pytest.approx(0.5),
                                     "median_naive_ape": pytest.approx(0.5), "gap_points": 3}
    assert naive_ape(gap_no_actual) is None and naive_ape(pt("C", 1, 2, 0)) is None
    assert selection_check([])["scored"]["mean_naive_ape"] is None


# ---------------------------------------------------------------- run guards and report

def test_dirty_paths_ignore_the_record_file():
    assert dirty_paths(" M eval/experiments.csv\n?? docs/x.md\n") == ["docs/x.md"]


def test_validation_runs_once_after_the_prereg_is_committed(tmp_path):
    (tmp_path / "e6-0001").mkdir()
    (tmp_path / "e6-0001" / "header.json").write_text(json.dumps(
        {"eval_id": "E6", "split": "validation"}))
    (tmp_path / "e6-train-0001").mkdir()
    (tmp_path / "e6-train-0001" / "header.json").write_text(json.dumps(
        {"eval_id": "E6", "split": "train"}))
    assert earlier_validation_runs(tmp_path) == ["e6-0001"]
    assert "once" in validation_refusal(["e6-0001"], True, False, [])
    assert "committed" in validation_refusal([], False, False, [])
    assert "committed" in validation_refusal([], True, True, [])
    assert "clean" in validation_refusal([], True, False, ["finagent/x.py"])
    assert validation_refusal([], True, False, []) is None


def test_render_results_shows_every_row_and_claim():
    header = {"run_id": "e6-0001", "date": "2026-12-01", "git_commit": "abc1234",
              "git_dirty": False, "as_of": "2026-11-24", "model_version": "m1",
              "scenario": "Base", "prereg_sha256": "f" * 64}
    results = [score_origin(ORIGIN, harness(), CONFIG),
               score_origin(ORIGIN, harness(fc=lambda s, a: forecast(s, a, "GAP")), CONFIG)]
    found = metrics([p for r in results for p in r.points], CONFIG)
    text = render_results(header, found, coverage(results, CONFIG))
    for row in ("revenue", "operating_income:reported", "operating_income:ebit_proxy", "fcf"):
        assert f"| {row} |" in text
    assert "no accuracy claim" in text and "(too few companies)" in text
    assert "only where the shipped product issued a VERIFIED forecast" in text
    # coverage sits in the same row as every result, in each table
    rows_ = [line for line in text.splitlines() if line.startswith("| revenue |")]
    assert len(rows_) == 4 and all("| 1 / 2 (50%) |" in line for line in rows_)
    assert "| operating_income:ebit_proxy | 0 / 0 (—) |" in text
    assert f"{e6.MODEL_GAP} 1" in text and "adds up to qualifying − scored" in text
    assert "## Selection check (descriptive; no claim)" in text
    assert "| 1 (1), of 1 |" in text  # the GAP point has a naive value and a VERIFIED actual


# ---------------------------------------------------------------- the shipped system

@pytest.mark.skipif(not (ROOT / "models" / "m1_v3.joblib").exists(),
                    reason="needs the pinned M1 v3 model (git-ignored)")
def test_shipped_harness_on_one_train_origin_reads_nothing_after_d_t():
    """Leakage on real evidence (a train company, its first origin): every forecast and naive
    input is filed by d(t), the BC7 history is used, and the actuals are FY t+1's own."""
    from finagent.tools import FinAgentTools

    tools = FinAgentTools.default(dataset_version="v3")
    filings = tools.filings("AAPL", date(2026, 11, 24))
    origin = list_origins(filings)[0]
    from finagent.evidence.integrity import read_snapshot_json
    facts = read_snapshot_json(tools.store.evidence_dir, f"companyfacts_{filings[0].cik}.json",
                               tools.store.manifest)
    result = score_origin(origin, e6.shipped_harness(tools, "AAPL", facts, CONFIG), CONFIG)
    assert result.max_input_filed is not None and result.max_input_filed <= origin.filed
    assert result.history_years and result.history_inputs > 0
    assert result.eligible and result.error is None and result.actual_error is None
    assert all(p.actual_status in (None, "VERIFIED") or p.excluded for p in result.points)
