"""FinAgent app (Phase 9; BC33, BC36). Local and offline:

    streamlit run finagent/ui/app.py

Three pages: Ask (finagent.planner.investigate on the selected company, shown in plain words by
finagent.ui.present, with the raw answer and trace behind it), Company (the pinned serving run)
and Results (the experiments). Serves every company in configs/universe.yaml, each labelled by
its split (D2b); a question naming a company outside it is refused before any tool runs. Layout
only: every value comes from finagent.ui.data and finagent.ui.present, which read the library and
the pinned runs and compute no reported or model number.
"""

from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from finagent.ui import data as d
from finagent.ui import present as p

st.set_page_config(page_title="FinAgent", layout="wide")

# Two validated categorical slots (light / dark surface): blue, orange.
SERIES = {"light": ("#2a78d6", "#eb6834"), "dark": ("#3987e5", "#d95926")}
STATUS = {"COMPLETE": ("Answered", "green"), "PARTIAL": ("Partly answered", "orange"),
          "EVIDENCE_GAP": ("Evidence gap", "orange"), "CLARIFICATION": ("Needs a detail", "blue")}
CSS = """<style>
table.fa-statement {width: 100%; border-collapse: collapse; font-size: 0.88rem;}
table.fa-statement th, table.fa-statement td {padding: 3px 10px;
  border-bottom: 1px solid rgba(128, 128, 128, 0.18);}
table.fa-statement th {font-weight: 600; text-align: right;}
table.fa-statement th.line, table.fa-statement td.line {text-align: left;}
table.fa-statement td.num {text-align: right; font-variant-numeric: tabular-nums;
  white-space: nowrap;}
table.fa-statement .e {background: rgba(42, 120, 214, 0.06);}
table.fa-statement td.na {color: #8a8f98; cursor: help; text-decoration: underline dotted;}
table.fa-statement td.section {font-weight: 600; padding-top: 10px;}
</style>"""


# ---------------------------------------------------------------- cached resources

@st.cache_resource
def ui_config() -> d.UiConfig:
    return d.load_ui_config()


@st.cache_resource
def universe() -> dict[str, str]:
    from finagent.config import load_universe

    return dict(load_universe())


@st.cache_resource
def labels() -> p.Labels:
    return p.Labels.load()


@st.cache_resource(show_spinner="Loading the models once for this app: the intent compiler, "
                                "the document model (M1 v3), the evidence search's embedder and "
                                "reranker; checking the pinned statement sets...")
def session():
    """Planner, tools, search models and the serving run's snapshot, once per process (BC37).
    D2b, BC36: the app serves every split."""
    from finagent.ui.snapshot import load_session

    return load_session(d.RUNS / str(ui_config().serving["run"]), include_held_out=True)


@st.cache_resource(show_spinner="Loading the pinned statements...")
def pinned_json(path: str) -> dict:
    return d.read_json(Path(path))


@st.cache_data
def statement_tables(xlsx: str, manifest: str, scenario: str) -> list[d.StatementTable]:
    return d.statement_tables(Path(xlsx), d.read_manifest(Path(manifest)), scenario)


def series_colors() -> tuple[str, str]:
    theme = getattr(st.context, "theme", None)
    kind = theme.get("type") if theme is not None else None
    return SERIES["dark" if kind == "dark" else "light"]


def table(rows, columns=None, height=None) -> None:
    frame = pd.DataFrame(rows, columns=columns) if columns else pd.DataFrame(rows)
    kwargs = {"height": height} if height else {}
    st.dataframe(frame, hide_index=True, width="stretch", **kwargs)


def md(text: str) -> str:
    """Free text (a filing quote, a reason) for st.markdown: "$" would start LaTeX."""
    return text.replace("$", "\\$")


def badge(b: p.Badge) -> None:
    st.badge(b.label, color=b.color, help=b.tip or None)


def badges(items) -> None:
    if items:
        with st.container(horizontal=True, gap="small"):
            for b in items:
                badge(b)


# ---------------------------------------------------------------- header and sidebar

cfg = ui_config()
companies = d.served_companies(universe(), list(cfg.splits))
names = d.company_names()
st.markdown(CSS, unsafe_allow_html=True)

with st.sidebar:
    st.markdown(f"### {cfg.product}")
    page = st.radio("Page", ["Ask", "Company", "Results"], key="page")
    ticker = st.selectbox(
        "Company", companies, key="company",
        format_func=lambda t: f"{names.get(t, t)} ({t}) · "
                              f"{d.split_label(t, universe(), cfg)['short']}")
    as_of = st.date_input("As of", value=cfg.as_of_default, min_value=date(2015, 1, 1),
                          max_value=cfg.snapshot, key="as_of",
                          help="FinAgent never uses a filing filed after this date.")
    st.caption(d.split_label(ticker, universe(), cfg)["label"])
    st.divider()
    st.caption(f"{len(companies)} companies. Evidence pinned on {p.day(cfg.snapshot)}. Runs "
               "locally and offline; no external AI service.")

st.markdown(f"## {cfg.product}")
st.caption(cfg.tagline)
session()  # BC37: load the shared models at startup, not on the first question


def company_line() -> None:
    """The selected company with its split label (every page)."""
    split = d.split_label(ticker, universe(), cfg)
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.markdown(f"**{names.get(ticker, ticker)}** ({ticker})")
        st.badge(split["short"], color="gray", help=split["tip"])


# ---------------------------------------------------------------- page 1: Ask

def use_question(question: str) -> None:
    st.session_state["question"] = question
    st.session_state["pending"] = True


def ask(question: str) -> dict:
    from finagent.planner.compile import compile_question
    from finagent.planner.investigate import investigate

    from finagent.ui.snapshot import statement_paths

    d.check_as_of(as_of, cfg)
    s = session()
    compiled = compile_question(question, s.parser, s.resources, ticker)
    refused = d.refusal(compiled, universe(), list(cfg.splits), ticker)
    if refused:
        return {"refusal": refused}
    with st.spinner("Investigating (at another as of date, the first question about a company "
                    "builds its statements live)"):
        inv = investigate(question, as_of, tools=s.tools, parser=s.parser,
                          resources=s.resources, selected_issuer=ticker, include_held_out=True)
    return {"inv": inv, "paths": statement_paths(s.tools, inv)}


def show_evidence(e: p.Evidence) -> None:
    link = f"[{e.source}]({e.url})" if e.url else e.source
    if e.kind == "quote":
        st.markdown(f"> {md(e.text)}")
        st.caption(f"Source: {link}")
    elif e.kind == "gap":
        st.caption(f":orange[{e.text}]")
    elif e.kind == "low":
        with st.container(border=True):
            st.caption("**Possibly related (low confidence, not evidence)**")
            st.caption(md(e.text))
            st.caption(f"Source: {link}")
    elif e.kind == "source":
        st.caption(f"Source: {link}")
    else:
        st.caption(md(e.text))


def show_finding(f: p.Finding) -> None:
    with st.container(border=True):
        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            st.markdown(f"**{md(f.title)}**")
            for b in f.badges:
                badge(b)
        st.markdown(md(f.text))
        for e in f.evidence:
            show_evidence(e)
        if f.more:
            with st.expander("Details"):
                st.markdown("\n".join(f"- {md(m)}" for m in f.more))


def show_plain(plain: p.PlainAnswer) -> None:
    if plain.clarify is not None:
        st.info(md(f"{plain.headline} {plain.clarify.question}"))
        if plain.clarify.suggestions:
            st.caption("Try one of these:")
            with st.container(horizontal=True, gap="small"):
                for i, s in enumerate(plain.clarify.suggestions):
                    st.button(s, key=f"suggest-{i}", on_click=use_question, args=(s,))
        return
    st.markdown(f"#### {md(plain.headline)}")
    group = None
    for f in plain.findings:
        if f.group and f.group != group:
            st.markdown(f"**{f.group}**")
        group = f.group
        show_finding(f)
    if plain.checked:
        with st.expander(f"Checked, not flagged ({len(plain.checked)})"):
            st.markdown("\n".join(f"- {c}" for c in plain.checked))
    if plain.unavailable:
        st.markdown("**Not available**")
        for f in plain.unavailable:
            show_finding(f)
    for note in plain.notes:
        st.caption(md(note))


def show_answer(inv, paths: list[str]) -> None:
    plain = p.present(inv, labels())
    text, color = STATUS.get(inv.status, (inv.status, "gray"))
    st.badge(text, color=color, help=f"{inv.status} ({inv.stop_reason}); "
                                     f"{inv.steps_used} tool steps")
    show_plain(plain)
    with st.expander("How this was computed"):
        st.caption("Basis, filings, thresholds, scores, chunk ids and gate decisions, as "
                   "recorded.")
        st.code("\n".join(plain.technical), language=None, wrap_lines=True)
        values = d.value_rows(inv)
        if values:
            st.markdown("**Values**")
            table(values)
        gate = d.gate_rows(inv)
        if gate:
            st.markdown("**Observation gate**")
            table(gate)
    with st.expander("Raw answer and trace"):
        if paths:  # BC37: which path built each statement set the answer used
            st.caption("Statement sets: " + "; ".join(paths))
        st.markdown("**Raw answer**")
        st.code(inv.answer or "(none)", language=None, wrap_lines=True)
        st.markdown("**Goals**")
        table(d.goal_rows(inv))
        st.markdown("**Tool calls**")
        table(d.trace_rows(inv))
        st.markdown("**Sources (by accession)**")
        table(d.source_rows(inv))
        with st.container(horizontal=True, gap="small"):
            st.download_button("Download the trace and raw answer (JSON)",
                               d.trace_export(inv, paths),
                               file_name=f"trace-{inv.run_id}.json", mime="application/json",
                               help="Exported on request; the app never saves traces.")
            st.download_button("Download this answer (text)", p.plain_text(plain),
                               file_name=f"answer-{inv.run_id}.txt", mime="text/plain")


def page_ask() -> None:
    st.header("Ask")
    company_line()
    st.caption(f"Questions are about the company selected in the sidebar, as of "
               f"{p.day(as_of)}. Every value passes the observation gate.")
    st.caption("Examples")
    with st.container(horizontal=True, gap="small"):
        for ex in cfg.examples:
            st.button(ex.label, key=f"example-{ex.kind}", on_click=use_question,
                      args=(ex.question,), help=ex.question)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        question = st.text_input("Question", key="question",
                                 placeholder="e.g. What were total assets in the latest "
                                             "reported year?")
        clicked = st.button("Ask", type="primary", key="ask")
    if (clicked or st.session_state.pop("pending", False)) and question.strip():
        key = (question.strip(), as_of.isoformat(), ticker)
        st.session_state["result"] = (key, ask(question.strip()))
    stored = st.session_state.get("result")
    if not stored:
        return
    (q, when, who), result = stored
    st.divider()
    st.caption(f"{q} · {names.get(who, who)} ({who}) · as of {p.day(date.fromisoformat(when))}")
    if "refusal" in result:
        st.error(result["refusal"])
    else:
        show_answer(result["inv"], result.get("paths", []))


# ---------------------------------------------------------------- page 2: Company

def show_card(card: d.CardView) -> None:
    with st.container(border=True):
        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            st.markdown(f"**{card.name or card.title}**")
            if card.scenario:
                st.badge(f"{card.scenario} scenario", color="violet")
            badge(labels().badge("QUOTED" if card.quotes else
                                 "EVIDENCE_GAP" if card.status == "EVIDENCE_GAP"
                                 else "MODEL_DERIVED"))
            st.badge(f"severity {card.severity}", color="gray")
        fired = [m for m in card.metrics if m[1] == "FIRED"]
        table(fired or card.metrics, ["Measure", "State", "Value", "Flag when", "By year"])
        for q in card.quotes:
            st.markdown(f"> {md(q['text'])}")
            where = labels().section(q.get("section", ""))
            st.caption(f"Source: [10-K, {where}]({card.url})" if card.url else
                       f"Source: 10-K, {where}")
        if not card.quotes and card.status == "EVIDENCE_GAP":
            st.caption(f":orange[{labels().text['evidence']['gap']}]")
            if card.low:
                with st.container(border=True):
                    st.caption("**Possibly related (low confidence, not evidence)**")
                    st.caption(md(card.low["quote"]["text"]))
        elif not card.quotes and card.detail:
            st.caption(md(card.detail))
        with st.expander("Details"):
            other = [m for m in card.metrics if m[1] != "FIRED"]
            if other:
                table(other, ["Measure", "State", "Value", "Flag when", "By year"])
            st.caption(md(card.text))
            if card.low:
                st.caption(d.low_confidence_text(card.low))
            st.caption(card.filing)


def page_company() -> None:
    st.header(f"{names.get(ticker, ticker)} ({ticker})")
    split = d.split_label(ticker, universe(), cfg)
    st.badge(split["short"], color="gray", help=split["tip"])
    st.caption(split["label"])
    run = d.serving_run(cfg)
    if run is None:
        st.error(f"The pinned serving run {cfg.serving['run']} was not found. Build it first "
                 "(python -m finagent.ui.serving build).")
        return
    built = d.run_as_of(run)
    if built != as_of:
        st.info(f"This page shows the pinned runs, built as of {p.day(built)}. Set as of to "
                f"{p.day(built)} to see them. For as of {p.day(as_of)}, ask on the Ask page: "
                "the library answers at any date and never uses a filing filed after it.")
        return
    year, metric_cards = d.company_cards(pinned_json(str(run / "statements" / "statements.json")),
                                         ticker, cfg.serving["cards"], labels().metrics)
    if year is not None:
        st.caption(f"Latest reported year, FY{year} (USD)")
        with st.container(horizontal=True, gap="small"):
            for c in metric_cards:
                st.metric(c.label, c.value, border=True, help=c.help)
    scenario = st.radio("Scenario", d.SCENARIOS, horizontal=True, key="scenario",
                        help="Base, Moderate and Severe: the model's three scenarios.")
    wb = run / "workbooks"
    manifest = wb / f"{ticker}.manifest.json"
    tabs = st.tabs(["Statements", "Forecast & checks", "Credit & liquidity", "Risk cards",
                    "Download"])
    with tabs[0]:
        st.caption("USD millions. A = reported, E = model forecast for the selected scenario. "
                   "n/a = not reported or not readable (hover for the reason); nil = treated "
                   "as nil by rule #152.")
        tables = statement_tables(str(wb / f"{ticker}.xlsx"), str(manifest), scenario)
        for tab, t in zip(st.tabs([t.sheet for t in tables]), tables):
            with tab:
                st.markdown(d.statement_html(t), unsafe_allow_html=True)
                if t.notes:
                    with st.expander(f"Not available or nil: {len(t.notes)} cells"):
                        st.markdown("\n".join(f"- {n}" for n in t.notes))
    with tabs[1]:
        st.caption(d.read_manifest(manifest)["banner"])
        st.markdown("**Scenarios**")
        table(d.scenario_rows(pinned_json(str(run / "forecast" / "scenarios.json")), ticker))
        st.markdown(f"**Assumptions ({scenario})**")
        table(d.driver_rows(run / "forecast" / "drivers.csv", ticker, scenario))
        st.markdown(f"**Model checks F1-F5 ({scenario})**")
        st.caption("Each projected year must pass all five: the balance sheet balances, cash "
                   "ties to an independent recomputation, and the other ties in Appendix B.")
        head, rows = d.check_table(run / "forecast" / "checks.csv", ticker, scenario)
        table(rows, list(head))
    with tabs[2]:
        st.markdown(f"**Debt service and liquidity ({scenario})**")
        head, rows, notes = d.credit_table(run / "credit", ticker, scenario)
        table(rows, list(head))
        st.caption("Scoped DSCR: cash available for debt service over scheduled debt service "
                   "(the comprehensive DSCR is withheld by design, 8.12). Default headroom "
                   f"basis: {d.default_headroom_basis(run / 'credit')}.")
        for note in notes:
            st.caption(note)
    with tabs[3]:
        year, cards = d.insight_cards(run / "insights" / "cards.json", ticker)
        st.markdown(f"**Risk signals, FY{year}**" if year else "**Risk signals**")
        if not cards:
            st.caption(f"No risk signal fired in FY{year}." if year else "No risk cards.")
        for card in cards:
            show_card(card)
        if year:
            states = d.signal_states(run / "insights" / "signals.csv", ticker, year)
            quiet = [s for s in states if s["state"] != "FIRED"]
            if quiet:
                with st.expander(f"Checked, not flagged or not checked ({len(quiet)})"):
                    st.markdown("\n".join(
                        f"- {labels().signals.get(s['signal'], s['signal'])}: "
                        f"{labels().reason(s['reason'] or s['state'])}" for s in quiet))
    with tabs[4]:
        st.markdown(f"**{ticker} workbook** ({cfg.serving['run']})")
        st.caption("Inputs as read from the filings; the projected years are live formulas.")
        ok, text = d.recalc_text(wb / "recalc.json", ticker)
        (st.success if ok else st.warning)(text)
        st.download_button(f"Download {ticker}.xlsx", (wb / f"{ticker}.xlsx").read_bytes(),
                           file_name=f"{ticker}.xlsx", key="download-workbook",
                           mime="application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet")


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
    st.caption("Read from eval/experiments.csv and the runs' metrics files. Post-hoc runs are "
               "labelled. Intervals are 95%.")
    experiments = d.read_experiments()
    cards = d.headline_cards(cfg, experiments=experiments)
    cols = st.columns(len(cards))
    for col, c in zip(cols, cards):
        with col, st.container(border=True, height="stretch"):
            st.caption(c["Title"])
            st.markdown(f"**{c['Value']}**")
            st.caption(c["Explain"])
            st.caption(f"{c['Measure']} ({c['Run']})")

    st.subheader("All results")
    table(d.headline_rows(cfg, experiments=experiments), height=720)

    left, right = st.columns(2)
    with left:
        st.markdown("**E1: share of figures read correctly, by document model**")
        st.caption("Dot = accuracy; line = 95% company-bootstrap interval.")
        e1 = pd.DataFrame(d.e1_chart_rows(cfg, experiments))
        e1["Model, split"] = e1["Model"] + " (" + e1["Split"] + ")"
        st.altair_chart(interval_chart(e1, "Model, split", "Field accuracy", "Split",
                                       ["validation", "held-out"], "Field accuracy"),
                        width="stretch")
    with right:
        st.markdown("**E5: planner, M0 grammar vs M1 intent compiler**")
        st.caption("Dot = rate; line = Wilson 95% interval.")
        e5 = pd.DataFrame(d.e5_chart_rows(cfg, experiments))
        domain = [r["compiler"] for r in cfg.charts["e5"]["runs"]]
        st.altair_chart(interval_chart(e5, "Measure", "Rate", "Compiler", domain, "Rate",
                                       offset="Compiler"), width="stretch")

    st.subheader("E7: speed, beside a manual build (D10)")
    e7 = d.e7_metrics(cfg)
    table(d.manual_vs_app(cfg, d.build_runs(cfg), e7))
    st.caption("Not like for like: the app reads pinned filings on this machine; the manual "
               "estimate includes finding and reading them. Build times were recorded once, "
               "on this machine. Since BC37 the app loads its models at start and reuses the "
               "serving run's statement sets at its as of date, so a company's first answer "
               "no longer builds them.")
    latest = d.latest_e7(cfg, e7)
    timings = d.e7_questions(run_id=latest) if latest else []
    if timings:
        with st.expander(f"E7 per question ({latest})"):
            table([{k: t[k] for k in ("pass", "order", "id", "group", "seconds", "status",
                                      "question")} for t in timings], height=420)
            st.caption("Definitions: finagent/evaluation/e7.py. Full tables: "
                       + ", ".join(f"{r['run']} in {r['doc']}" for r in cfg.e7 if r.get("doc"))
                       + ".")

    st.subheader("Limitations")
    st.markdown(cfg.limitations.read_text(encoding="utf-8"))


{"Ask": page_ask, "Company": page_company, "Results": page_results}[page]()
