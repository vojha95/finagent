"""FinAgent demo (Phase 9 cut-down; BC33). Local and offline:

    streamlit run finagent/ui/app.py

Three pages: Ask (finagent.planner.investigate on the selected company), Company (the latest
pinned runs) and Results (the experiments). Serves the validation companies only; a question
naming any other company is refused before any tool runs. Layout only: every value comes from
finagent.ui.data, which reads the library and the pinned runs and computes no number.
"""

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from finagent.ui import data as d

st.set_page_config(page_title="FinAgent", layout="wide")

# Two validated categorical slots (light / dark surface): blue, orange.
SERIES = {"light": ("#2a78d6", "#eb6834"), "dark": ("#3987e5", "#d95926")}
STATUS_BOX = {"COMPLETE": st.success, "PARTIAL": st.warning, "EVIDENCE_GAP": st.warning,
              "CLARIFICATION": st.info}


# ---------------------------------------------------------------- cached resources

@st.cache_resource
def ui_config() -> d.UiConfig:
    return d.load_ui_config()


@st.cache_resource
def universe() -> dict[str, str]:
    from finagent.config import load_universe

    return dict(load_universe())


@st.cache_resource(show_spinner="Loading the intent compiler...")
def planner():
    from finagent.planner.compile import CompilerResources, load_trained_parser

    resources = CompilerResources.load()
    return resources, load_trained_parser(resources)


@st.cache_resource(show_spinner="Loading the evidence store and the document model (M1 v3)...")
def tools():
    from finagent.tools import FinAgentTools

    return FinAgentTools.default()


@st.cache_data
def statement_tables(xlsx: str, manifest: str, scenario: str) -> list[d.StatementTable]:
    from pathlib import Path

    return d.statement_tables(Path(xlsx), d.read_manifest(Path(manifest)), scenario)


def series_colors() -> tuple[str, str]:
    theme = getattr(st.context, "theme", None)
    kind = theme.get("type") if theme is not None else None
    return SERIES["dark" if kind == "dark" else "light"]


def table(rows, columns=None, height=None) -> None:
    frame = pd.DataFrame(rows, columns=columns) if columns else pd.DataFrame(rows)
    kwargs = {"height": height} if height else {}
    st.dataframe(frame, hide_index=True, width="stretch", **kwargs)


# ---------------------------------------------------------------- sidebar

cfg = ui_config()
companies = d.served_companies(universe(), cfg.split)
names = d.company_names()
st.sidebar.title("FinAgent")
page = st.sidebar.radio("Page", ["Ask", "Company", "Results"], key="page")
ticker = st.sidebar.selectbox("Company", companies, key="company",
                              format_func=lambda t: f"{t} - {names.get(t, t)}")
as_of = st.sidebar.date_input("As of", value=cfg.as_of_default, min_value=date(2015, 1, 1),
                              max_value=cfg.snapshot, key="as_of")
st.sidebar.caption(
    f"Serves the {len(companies)} {cfg.split} companies only; held-out and every other company "
    f"are refused. Evidence pinned on {cfg.snapshot} (manifest "
    f"{cfg.pinned_files['evidence/manifest.json'][:12]}...). Runs locally and offline; no "
    "external AI API.")


# ---------------------------------------------------------------- page 1: Ask

def use_example(question: str) -> None:
    st.session_state["question"] = question
    st.session_state["pending"] = True


def ask(question: str) -> dict:
    from finagent.planner.compile import compile_question
    from finagent.planner.investigate import investigate

    d.check_as_of(as_of, cfg)
    resources, parser = planner()
    compiled = compile_question(question, parser, resources, ticker)
    refused = d.refusal(compiled, universe(), cfg.split, ticker)
    if refused:
        return {"refusal": refused}
    with st.spinner("Investigating (the first question about a company builds its statements)"):
        inv = investigate(question, as_of, tools=tools(), parser=parser, resources=resources,
                          selected_issuer=ticker)
    return {"inv": inv}


def show_answer(inv) -> None:
    box = STATUS_BOX.get(inv.status, st.warning)
    box(f"Status: {inv.status} ({inv.stop_reason}); {inv.steps_used} tool steps")
    clar = inv.compilation.clarification
    if clar is not None:
        st.info(f"Clarification ({clar.category}): {clar.message}")
    if inv.answer:
        st.code(inv.answer, language=None, wrap_lines=True)
    values = d.value_rows(inv)
    if values:
        st.subheader("Values")
        table(values)
    gate = d.gate_rows(inv)
    if gate:
        st.subheader("Observation gate")
        table(gate)
    with st.expander("Trace: goals, tool calls, sources"):
        st.markdown("**Goals**")
        table(d.goal_rows(inv))
        st.markdown("**Tool calls**")
        table(d.trace_rows(inv))
        st.markdown("**Sources (by accession)**")
        table(d.source_rows(inv))
        st.download_button("Download the trace (JSON)", d.trace_export(inv),
                           file_name=f"trace-{inv.run_id}.json", mime="application/json",
                           help="Exported on request; the demo never saves traces.")


def page_ask() -> None:
    st.header("Ask")
    st.caption(f"Runs finagent.planner.investigate(question, as_of) for {ticker} as of {as_of}. "
               "Every value passes the observation gate; quotes and evidence gaps appear in "
               "the answer.")
    cols = st.columns(len(cfg.examples))
    for col, ex in zip(cols, cfg.examples):
        col.button(ex.label, key=f"example-{ex.kind}", on_click=use_example,
                   args=(ex.question,), help=ex.question, width="stretch")
    question = st.text_input("Question", key="question",
                             placeholder="e.g. What were total assets in the latest reported year?")
    clicked = st.button("Ask", type="primary", key="ask")
    if (clicked or st.session_state.pop("pending", False)) and question.strip():
        key = (question.strip(), as_of.isoformat(), ticker)
        st.session_state["result"] = (key, ask(question.strip()))
    stored = st.session_state.get("result")
    if not stored:
        return
    (q, when, who), result = stored
    st.caption(f"Question: {q} | company {who} | as of {when}")
    if "refusal" in result:
        st.error(result["refusal"])
    else:
        show_answer(result["inv"])


# ---------------------------------------------------------------- page 2: Company

def show_card(card: d.CardView) -> None:
    with st.container(border=True):
        scenario = f" [{card.scenario}]" if card.scenario else ""
        st.markdown(f"**{card.title}**{scenario} | severity {card.severity} | "
                    f"status **{card.status}**")
        table(card.metrics, ["Condition", "State", "Value", "Test", "By year"])
        if card.quotes:
            for q in card.quotes:
                st.markdown(f"> {q['text']}")
                st.caption(f"Quote: {q['section']}, chunk {q['chunk_id']}, 10-K {q['accession']}")
        elif card.status == "EVIDENCE_GAP":
            st.warning("EVIDENCE_GAP: the filing does not explain the movement (no sentence "
                       "above the relevance threshold).")
            if card.low:
                with st.container(border=True):
                    st.markdown(f"**{card.low['label']}** (not evidence; never counted as a "
                                "quote)")
                    st.markdown(f"_{card.low['quote']['text']}_")
                    st.caption(d.low_confidence_text(card.low))
        elif card.detail:
            st.caption(f"Evidence: {card.detail}")
        st.caption(card.filing)


def page_company() -> None:
    st.header(f"{ticker} - {names.get(ticker, ticker)}")
    runs = d.latest_runs(cfg)
    missing = [k for k, v in runs.items() if v is None]
    if missing:
        st.error(f"Pinned runs not found: {', '.join(missing)}. Build them first (README).")
        return
    built = d.run_as_of(runs["workbooks"])
    if built != as_of:
        st.info(f"This page shows the pinned runs, built as of {built}. Set as of to {built} to "
                f"see them. For as of {as_of}, ask on the Ask page: the library answers at "
                "any date and never uses a filing filed after it.")
        return
    st.caption("Pinned runs: " + ", ".join(p.name for p in runs.values()))
    scenario = st.radio("Scenario", d.SCENARIOS, horizontal=True, key="scenario")
    wb = runs["workbooks"]
    manifest = wb / f"{ticker}.manifest.json"
    st.caption(d.read_manifest(manifest)["banner"])

    st.subheader("Statements ($ millions; A = reported, E = forecast)")
    tables = statement_tables(str(wb / f"{ticker}.xlsx"), str(manifest), scenario)
    for tab, t in zip(st.tabs([t.sheet for t in tables]), tables):
        with tab:
            table(t.rows, list(t.columns), height=min(38 * (len(t.rows) + 1), 900))
            if t.notes:
                with st.expander(f"{len(t.notes)} GAP / NIL notes"):
                    st.markdown("\n".join(f"- {n}" for n in t.notes))

    st.subheader(f"Model checks F1-F5 ({scenario})")
    head, rows = d.check_table(runs["forecast"] / "checks.csv", ticker, scenario)
    table(rows, list(head))

    st.subheader(f"Credit and liquidity ({scenario})")
    head, rows, notes = d.credit_table(runs["credit"], ticker, scenario)
    table(rows, list(head))
    st.caption(f"Scoped DSCR: cash available for debt service over scheduled debt service "
               f"(the comprehensive DSCR is withheld by design, 8.12). Default headroom basis: "
               f"{d.default_headroom_basis(runs['credit'])}.")
    for note in notes:
        st.caption(note)

    st.download_button(f"Download the {ticker} workbook ({wb.name})",
                       (wb / f"{ticker}.xlsx").read_bytes(), file_name=f"{ticker}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    year, cards = d.insight_cards(runs["insights"] / "cards.json", ticker)
    st.subheader(f"Insight cards, FY{year}" if year else "Insight cards")
    if not cards:
        st.caption(f"No risk signal fired in FY{year}." if year else "No insight cards.")
    for card in cards:
        show_card(card)


# ---------------------------------------------------------------- page 3: Results

def interval_chart(frame: pd.DataFrame, y: str, value: str, color: str, domain: list[str],
                   title: str, offset: str | None = None) -> alt.LayerChart:
    blue, orange = series_colors()
    enc = {"y": alt.Y(f"{y}:N", sort=None, title=None),
           "color": alt.Color(f"{color}:N", scale=alt.Scale(domain=domain, range=[blue, orange]),
                              legend=alt.Legend(orient="top", title=None)),
           "tooltip": list(frame.columns)}
    if offset:
        enc["yOffset"] = alt.YOffset(f"{offset}:N", sort=domain)
    base = alt.Chart(frame).encode(**enc)
    rules = base.mark_rule(strokeWidth=2).encode(
        x=alt.X("Low:Q", scale=alt.Scale(zero=False), title=title), x2="High:Q")
    points = base.mark_point(filled=True, size=90, opacity=1).encode(x=f"{value}:Q")
    return (rules + points).properties(height=40 * len(frame) if not offset else 260)


def page_results() -> None:
    st.header("Results")
    st.caption("Every experiment's headline, read from eval/experiments.csv and the runs' "
               "metrics files. Post-hoc runs are labelled.")
    experiments = d.read_experiments()
    table(d.headline_rows(cfg, experiments=experiments), height=1100)

    left, right = st.columns(2)
    with left:
        st.markdown("**E1: field accuracy by document model (95% company bootstrap)**")
        e1 = pd.DataFrame(d.e1_chart_rows(cfg, experiments))
        e1["Model, split"] = e1["Model"] + " (" + e1["Split"] + ")"
        st.altair_chart(interval_chart(e1, "Model, split", "Field accuracy", "Split",
                                       ["validation", "held-out"], "Field accuracy"),
                        width="stretch")
    with right:
        st.markdown("**E5: planner, M0 grammar vs M1 intent compiler (Wilson 95%)**")
        e5 = pd.DataFrame(d.e5_chart_rows(cfg, experiments))
        domain = [r["compiler"] for r in cfg.charts["e5"]["runs"]]
        st.altair_chart(interval_chart(e5, "Measure", "Rate", "Compiler", domain, "Rate",
                                       offset="Compiler"), width="stretch")

    st.subheader("E7 light: local latency (e7-0001)")
    timings = d.e7_questions()
    if not timings:
        st.caption(d.NOT_RUN)
    else:
        table([{k: t[k] for k in ("pass", "order", "id", "group", "seconds", "status",
                                  "question")} for t in timings], height=420)
        st.caption("Definitions and the full table: docs/e7_light.md.")

    st.subheader("Limitations")
    st.markdown(cfg.limitations.read_text(encoding="utf-8"))


{"Ask": page_ask, "Company": page_company, "Results": page_results}[page]()
