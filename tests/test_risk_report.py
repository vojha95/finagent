"""The insights run's tables and report (finagent.risk.report; 7.3)."""

from datetime import date
from types import SimpleNamespace

import pytest

from finagent.records import ConditionResult, InsightCard, Quote, SignalResult
from finagent.risk.report import (EXPLAIN_QUESTIONS, CompanyInsights, card_rows,
                                  company_insights, counts, explain_runs, grid, render_report,
                                  risk_compilation, signal_rows, write_outputs)

AS_OF = date(2026, 11, 24)
HEADER = {"run_id": "insights-test", "git_commit": "abc", "git_dirty": False,
          "evidence_manifest_sha256": "0" * 64, "as_of": "2026-11-24",
          "signals_sha256": "1" * 64, "templates_sha256": "2" * 64, "min_score": 0.3}
IDS = ["R1", "R2"]


def company() -> CompanyInsights:
    cond = ConditionResult("receivables_growth_gap", "receivables growth minus revenue growth",
                           "FIRED", 22.2, "pp", ">", 10.0)
    r1 = SignalResult("R1", "Receivables outpacing sales", "SYNT", 2025, "FIRED", "medium",
                      "history", (cond,))
    r2 = SignalResult("R2", "Inventory build", "SYNT", 2025, "GAP", "medium", "history",
                      reason="NO_COGS")
    q = Quote("a:ITEM_7:0001", "a", "Receivables rose on longer terms.", "ITEM_7", 0.42, "s")
    card = InsightCard("R1", r1.name, "SYNT", 2025, (cond,), "medium", (q,), "VERIFIED", "a",
                       "https://example/a.htm", date(2026, 2, 1), date(2025, 12, 31),
                       "R1 Receivables outpacing sales (FY2025, severity medium): receivables "
                       "growth minus revenue growth +22.2 pp. Evidence: quoted below.")
    early = SignalResult("R1", r1.name, "SYNT", 2024, "NOT_FIRED", "medium", "history")
    return CompanyInsights("SYNT", {2024: (early, early), 2025: (r1, r2)}, {2024: (),
                                                                         2025: (card,)},
                           "answer text", "VERIFIED SIGNALS_EVALUATED",
                           [{"metric": "R1.receivables_growth_gap", "accepted": True,
                             "code": ""}])


def test_rows_and_counts():
    c = company()
    assert signal_rows([c])[2][:5] == ["SYNT", "FY2025", "R1", "FIRED", ""]
    assert signal_rows([c])[3][4] == "NO_COGS"
    assert card_rows([c])[0][3:5] == ["VERIFIED", "medium"]
    assert card_rows([c])[0][6].startswith("0.420 a:ITEM_7:0001: Receivables")
    text = counts([c])
    assert "FIRED 1, NOT_FIRED 2, GAP 1" in text[0] and "1 quoted" in text[1]
    assert grid(c, IDS)[-1] == "| FY2025 | **F** | gap |"
    assert c.latest == 2025


def test_report_and_outputs(tmp_path):
    c = company()
    run = SimpleNamespace(status="COMPLETE", answer="explained", compilation=SimpleNamespace(
        compiler="classifier", issuers=("AMZN",)))
    text = render_report([c, CompanyInsights("BAD", error="ValueError: x")], HEADER,
                         [("Why?", run)], IDS, "STATEMENTS 1 observations; 0 differences\n"
                                               "CHECK DETAIL TEXT F5\nUNCHANGED\nother line")
    assert "### SYNT FY2025 (latest 10-K)" in text and "Receivables rose on longer terms." in text
    assert "CHECK DETAIL TEXT F5" in text
    assert "R2 GAP (NO_COGS)" in text and "## BAD: ERROR" in text
    assert "## Explain answers (dev companies only" in text and "UNCHANGED" in text
    assert "other line" not in text
    write_outputs(tmp_path, [c], HEADER, [("Why?", run)])
    assert (tmp_path / "signals.csv").read_text().count("\n") == 1 + 4
    assert (tmp_path / "cards.csv").read_text().count("\n") >= 2
    assert "=== Why? [COMPLETE" in (tmp_path / "answers.txt").read_text()


def test_company_insights_reads_every_year_then_asks_the_planner(monkeypatch):
    import finagent.planner.investigate as inv

    c = company()
    tools = SimpleNamespace(
        filings=lambda t, a: [SimpleNamespace(fiscal_year=2024), SimpleNamespace(
            fiscal_year=2025)],
        insight_cards=lambda t, y, a: (c.signals[y], c.cards[y]))
    decision = SimpleNamespace(metric="signals_fired", accepted=True, code="", detail="")
    goal = SimpleNamespace(status="VERIFIED", reason="SIGNALS_EVALUATED", decisions=())
    asked = []

    def fake(question, as_of, tools=None, resources=None, compilation=None):
        asked.append(compilation.goals[0])
        return SimpleNamespace(requested=(goal,), answer="the answer")

    monkeypatch.setattr(inv, "investigate", fake)
    monkeypatch.setattr("finagent.risk.report.asdict", lambda d: vars(d))
    goal.decisions = (decision,)
    got = company_insights(tools, "SYNT", AS_OF, None)
    assert sorted(got.signals) == [2024, 2025] and got.cards[2025] == c.cards[2025]
    assert got.goal_status == "VERIFIED SIGNALS_EVALUATED" and got.answer == "the answer"
    assert asked[0].kind == "risk" and got.gate[0]["metric"] == "signals_fired"


def test_risk_compilation_and_dev_only_explain():
    comp = risk_compilation("SYNT")
    assert comp.goals[0].kind == "risk" and comp.goals[0].period == "latest"
    assert all(("Amazon" in q) or ("Microsoft" in q) for q in EXPLAIN_QUESTIONS)

    class Run(SimpleNamespace):
        pass

    import finagent.planner.investigate as inv

    def fake(question, as_of, tools=None, resources=None):
        issuers = ("CVX",) if "Chevron" in question else ("AMZN",)
        return Run(compilation=SimpleNamespace(issuers=issuers), status="COMPLETE", answer="")

    original = inv.investigate
    inv.investigate = fake
    try:
        assert len(explain_runs(None, AS_OF, None, ["Why did Amazon's revenue rise?"])) == 1
        with pytest.raises(ValueError):
            explain_runs(None, AS_OF, None, ["Why did Chevron's revenue rise?"])
    finally:
        inv.investigate = original
