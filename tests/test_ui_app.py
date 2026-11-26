"""Smoke tests of the Streamlit app with streamlit.testing (Phase 9; BC33, BC36): every page
renders without an exception, the Company page renders for a company of each split, and a
question runs end to end through investigate and the plain view. Questions are asked about
validation and train companies only; held-out companies are only rendered from the pinned
serving run (D2b: never evaluated).

They need the git-ignored models and the serving run, so they skip where those are absent."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from finagent.config import load_universe  # noqa: E402
from finagent.ui import data as d  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "finagent" / "ui" / "app.py")
CFG = d.load_ui_config()
UNIVERSE = load_universe()

pytestmark = pytest.mark.skipif(
    not (ROOT / "models" / "m1_v3.joblib").exists() or d.serving_run(CFG) is None,
    reason="needs the pinned models and the serving run (git-ignored)")


def first(split):
    return d.served_companies(UNIVERSE, [split])[0]


@pytest.fixture(scope="module")
def app():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def _goto(at, page, ticker=None):
    if ticker is not None:
        at.sidebar.selectbox(key="company").set_value(ticker)
    at.sidebar.radio(key="page").set_value(page).run()
    assert not at.exception, at.exception
    return at


def test_header_and_sidebar(app):
    assert any(m.value == f"## {CFG.product}" for m in app.markdown)
    picker = app.sidebar.selectbox(key="company")
    assert list(picker.options) and len(picker.options) == len(
        d.served_companies(UNIVERSE, list(CFG.splits)))  # every served company (D2b)
    shorts = {v["short"] for v in CFG.splits.values()}
    assert all(any(o.endswith(f"· {s}") for o in picker.options) for s in shorts)


def test_ask_page_renders(app):
    _goto(app, "Ask", first("validation"))
    assert app.header[0].value == "Ask"
    keys = {b.key for b in app.button}
    assert {f"example-{e.kind}" for e in CFG.examples} | {"ask"} <= keys


@pytest.mark.parametrize("split", list(CFG.splits))
def test_company_page_renders_for_each_split(app, split):
    _goto(app, "Company", first(split))
    names = d.company_names()
    assert app.header[0].value == f"{names.get(first(split), first(split))} ({first(split)})"
    assert any(c.value == CFG.splits[split]["label"] for c in app.caption)  # the split label
    shown = [t.label for t in app.tabs]  # document order: the statement sheet tabs nest inside
    top = ["Statements", "Forecast & checks", "Credit & liquidity", "Risk cards", "Download"]
    assert [t for t in shown if t in top] == top
    assert len(shown) > len(top)  # the statement sheets
    assert len(app.metric) == len(CFG.serving["cards"])
    assert any(m.value.startswith("Recalculated in LibreOffice")
               for m in list(app.success) + list(app.warning))


def test_company_page_each_scenario(app):
    _goto(app, "Company", first("validation"))
    for scenario in ("Moderate", "Severe", "Base"):
        app.radio(key="scenario").set_value(scenario).run()
        assert not app.exception, app.exception
        assert any(m.value == f"**Model checks F1-F5 ({scenario})**" for m in app.markdown)


def test_company_page_refuses_another_as_of(app):
    _goto(app, "Company", first("validation"))
    app.sidebar.date_input(key="as_of").set_value(CFG.snapshot.replace(year=2024)).run()
    assert not app.exception
    assert any("pinned runs" in i.value for i in app.info)
    app.sidebar.date_input(key="as_of").set_value(CFG.snapshot).run()


def test_results_page_renders(app):
    _goto(app, "Results")
    assert app.header[0].value == "Results"
    assert [s.value for s in app.subheader][-1] == "Limitations"
    shown = " ".join(c.value for c in app.caption)
    assert all(title in shown for title in CFG.cards)  # the headline cards first
    assert any("e6-0001" in str(df.value.to_dict()) for df in app.dataframe)
    assert any("about 20 hours" in str(df.value.to_dict()) for df in app.dataframe)  # D10


def test_one_question_runs(app):
    _goto(app, "Ask", first("validation"))
    app.button(key="example-fact").click().run(timeout=300)
    assert not app.exception, app.exception
    assert any(m.value.startswith("#### ") and "total assets were USD" in m.value
               for m in app.markdown)
    labels = [e.label for e in app.expander]
    assert "How this was computed" in labels and "Raw answer and trace" in labels
    assert any("total assets" in c.value and "basis:" in c.value for c in app.code)
    ticker = first("validation")  # BC37: the trace says which path built the statement set
    assert any(c.value.startswith(f"Statement sets: {ticker}: ") for c in app.caption)


def test_a_train_company_is_served(app):
    _goto(app, "Ask", first("train"))
    app.button(key="example-fact").click().run(timeout=300)
    assert not app.exception, app.exception
    assert not app.error
    assert any(m.value.startswith("#### ") for m in app.markdown)


def test_clarification_suggestion_and_outside_universe(app):
    _goto(app, "Ask", first("validation"))
    app.button(key="example-clarification").click().run(timeout=120)
    assert any("Which fiscal year" in i.value for i in app.info)
    app.button(key="suggest-0").click().run(timeout=300)
    assert not app.exception, app.exception
    assert any(m.value.startswith("#### ") for m in app.markdown)
    app.text_input(key="question").set_value("What was Tesla's revenue in FY2021?")
    app.button(key="ask").click().run(timeout=120)
    assert any("not one FinAgent covers" in i.value for i in app.info)
