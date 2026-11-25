"""Smoke tests of the Streamlit demo with streamlit.testing (Phase 9 cut-down; BC33): each page
renders without an exception, and one question runs end to end through investigate.

They need the git-ignored models and pinned runs, so they skip where those are absent."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from finagent.ui import data as d  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "finagent" / "ui" / "app.py")

pytestmark = pytest.mark.skipif(
    not (ROOT / "models" / "m1_v3.joblib").exists()
    or any(v is None for v in d.latest_runs(d.load_ui_config()).values()),
    reason="needs the pinned models and runs (git-ignored)")


@pytest.fixture(scope="module")
def app():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def _goto(at, page):
    at.sidebar.radio(key="page").set_value(page).run()
    assert not at.exception, at.exception
    return at


def test_ask_page_renders(app):
    _goto(app, "Ask")
    assert app.header[0].value == "Ask"
    labels = [b.label for b in app.button]
    assert len([b for b in labels if b != "Ask"]) == 6 and "Ask" in labels


def test_company_page_renders_each_scenario(app):
    _goto(app, "Company")
    subheaders = [s.value for s in app.subheader]
    assert any(s.startswith("Statements") for s in subheaders)
    assert any(s.startswith("Model checks F1-F5") for s in subheaders)
    assert any(s.startswith("Credit and liquidity") for s in subheaders)
    assert any(s.startswith("Insight cards") for s in subheaders)
    for scenario in ("Moderate", "Severe"):
        app.radio(key="scenario").set_value(scenario).run()
        assert not app.exception
        assert f"Model checks F1-F5 ({scenario})" in [s.value for s in app.subheader]


def test_company_page_refuses_another_as_of(app):
    _goto(app, "Company")
    cfg = d.load_ui_config()
    app.sidebar.date_input(key="as_of").set_value(cfg.snapshot.replace(year=2024)).run()
    assert not app.exception
    assert any("pinned runs" in i.value for i in app.info)
    app.sidebar.date_input(key="as_of").set_value(cfg.snapshot).run()


def test_results_page_renders(app):
    _goto(app, "Results")
    assert [s.value for s in app.subheader][-1] == "Limitations"
    assert len(app.dataframe) >= 1


def test_one_question_runs(app):
    _goto(app, "Ask")
    app.button(key="example-fact").click().run(timeout=300)
    assert not app.exception, app.exception
    assert any(s.value.startswith("Status: COMPLETE") for s in app.success)
    assert app.code and "total assets" in app.code[0].value


def test_clarification_and_refusal(app):
    _goto(app, "Ask")
    app.button(key="example-clarification").click().run(timeout=60)
    assert any(i.value.startswith("Status: CLARIFICATION") for i in app.info)
    app.text_input(key="question").set_value("Compare Amazon and Apple revenue in FY2024")
    app.button(key="ask").click().run(timeout=60)
    assert any("Nothing was run" in e.value for e in app.error)
