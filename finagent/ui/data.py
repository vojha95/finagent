"""Data layer of the Streamlit app (Phase 9; BC33, BC36). No Streamlit import, so every function
is testable on its own.

The app computes no reported or model number. It shows:
- on the Ask page, what finagent.planner.investigate returns, in plain words
  (finagent.ui.present) with the raw answer, values, gate decisions and trace behind it;
- on the Company page, the pinned serving run (configs/ui.yaml serving; built at as_of = the
  snapshot date): the workbook's input cells and the expected value of each formula cell from the
  workbook manifest (independently re-evaluated and recalculated with 0 mismatches), the forecast
  scenarios and checks, the credit run and the insight cards;
- on the Results page, eval/experiments.csv and the runs' metrics files, by the paths in
  configs/ui.yaml. The one derived figure is the median and maximum of the recorded per-company
  build times of the serving run, labelled as such, shown in E7 beside D10.
Formatting (USD bn / m, percentages, decimals) changes only how a value is displayed; it is the
same everywhere (finagent.ui.present formats).

Served companies (D2b, BC36): every company in configs/universe.yaml, each labelled by its split.
A question naming a company outside the universe is refused before any tool runs.
"""

import csv
import html
import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from finagent.config import SPLITS, ConfigError
from finagent.planner.trace import run_record, step_records
from finagent.records import AnswerObservation, Compilation
from finagent.ui.present import company_names, number, usd  # noqa: F401 (company_names: app)

ROOT = Path(__file__).resolve().parents[2]
UI_PATH = ROOT / "configs" / "ui.yaml"
RUNS = ROOT / "runs"
EXPERIMENTS = ROOT / "eval" / "experiments.csv"
SCENARIOS = ("Base", "Moderate", "Severe")
FIRST_ROW = 6  # statement sheets: rows 1-3 title, 5 year header (finagent.workbook.statements)
HEADER_ROW = 5
ALL = "*"  # finagent.workbook.grid.ALL: an expected value shared by every scenario
NOT_RUN = "not run yet"
NA = "n/a"  # a GAP cell (its reason is the tooltip)
NM_TEXT = "n.m."


# ---------------------------------------------------------------- config

@dataclass(frozen=True)
class Example:
    label: str
    kind: str
    question: str


@dataclass(frozen=True)
class UiConfig:
    splits: dict[str, dict[str, str]]  # split -> short, label, tip (the picker's order)
    as_of_default: date
    snapshot: date
    serving: dict[str, Any]  # run, files, cards
    pinned_files: dict[str, str]
    retrieval_models: tuple[str, ...]
    examples: tuple[Example, ...]
    results: tuple[dict[str, Any], ...]
    charts: dict[str, Any]
    limitations: Path
    product: str = "FinAgent"
    tagline: str = ""
    cards: tuple[str, ...] = ()
    manual_build: dict[str, Any] | None = None


def _date(value: Any, where: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ConfigError(f"ui.yaml: {where} is not a date: {value!r}") from exc


def load_ui_config(path: Path = UI_PATH) -> UiConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    default = _date(raw["as_of"]["default"], "as_of.default")
    snapshot = _date(raw["as_of"]["snapshot"], "as_of.snapshot")
    if default > snapshot:
        raise ConfigError("ui.yaml: the default as_of is after the snapshot date")
    examples = tuple(Example(e["label"], e["kind"], e["question"]) for e in raw["examples"])
    if len({e.kind for e in examples}) != len(examples):
        raise ConfigError("ui.yaml: each example question needs its own kind")
    splits = {str(k): {f: str(v[f]) for f in ("short", "label", "tip")}
              for k, v in raw["splits"].items()}
    if set(splits) - set(SPLITS):
        raise ConfigError(f"ui.yaml: unknown splits {sorted(set(splits) - set(SPLITS))}")
    results = tuple(raw["results"])
    titles = [r["card"] for r in results if r.get("card")]
    cards = tuple(raw.get("cards", ()))
    if sorted(cards) != sorted(titles):
        raise ConfigError("ui.yaml: cards must name each results row with a card once")
    return UiConfig(
        splits=splits, as_of_default=default, snapshot=snapshot,
        serving=dict(raw["serving"]),
        pinned_files={str(k): str(v) for k, v in raw["pinned_files"].items()},
        retrieval_models=tuple(raw["retrieval_models"]), examples=examples,
        results=results, charts=dict(raw["charts"]), limitations=ROOT / raw["limitations"],
        product=str(raw.get("product", "FinAgent")), tagline=str(raw.get("tagline", "")),
        cards=cards, manual_build=raw.get("manual_build"))


# ---------------------------------------------------------------- companies and refusal

def served_companies(universe: Mapping[str, str], splits: Sequence[str]) -> list[str]:
    """The served tickers: by split in the configured order, then by ticker."""
    order = list(splits)
    return sorted((t for t, s in universe.items() if s in order),
                  key=lambda t: (order.index(universe[t]), t))


def split_label(ticker: str, universe: Mapping[str, str], config: UiConfig) -> dict[str, str]:
    """The company's split label (short for the picker, label and tip for every page)."""
    return config.splits.get(universe.get(ticker, ""), {"short": "", "label": "", "tip": ""})


def refusal(compilation: Compilation, universe: Mapping[str, str], splits: Sequence[str],
            selected: str | None = None) -> str | None:
    """Why the app refuses a compiled question (None: it may run). Every named or goal issuer,
    and the selected one, must be a served company (D2b: every split of the universe)."""
    named = set(compilation.issuers) | {g.issuer for g in compilation.goals} | \
        set(compilation.held_out) | ({selected} if selected else set())
    outside = sorted(t for t in named if universe.get(t) not in splits)
    if not outside:
        return None
    return (f"This app serves the companies in configs/universe.yaml only; "
            f"{', '.join(outside)} is outside them. Nothing was run.")


def check_as_of(as_of: date, config: UiConfig) -> None:
    """Rule 6: never after the pinned snapshot (the date picker's maximum)."""
    if as_of > config.snapshot:
        raise ValueError(f"as_of {as_of} is after the evidence snapshot {config.snapshot}")


# ---------------------------------------------------------------- pinned runs

def serving_run(config: UiConfig, runs_dir: Path = RUNS) -> Path | None:
    """The configured serving run, when it holds every configured file."""
    run = Path(runs_dir) / str(config.serving["run"])
    return run if run.is_dir() and all((run / f).exists()
                                       for f in config.serving["files"]) else None


def run_as_of(run_dir: Path) -> date | None:
    """The as_of in a run's header (serving.json, summary.json, credit.json or cards.json)."""
    for name in ("serving.json", "summary.json", "credit.json", "cards.json", "scenarios.json"):
        path = run_dir / name
        if path.exists():
            header = json.loads(path.read_text(encoding="utf-8")).get("header", {})
            if header.get("as_of"):
                return date.fromisoformat(header["as_of"])
    return None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- statements (workbook)

def format_number(value: float, number_format: str) -> str:
    """A value as the workbook's number format shows it (configs/workbook.yaml formats)."""
    if "%" in number_format:
        digits = 2 if "0.00%" in number_format else 1
        text = f"{abs(value) * 100:.{digits}f}%"
        return f"({text})" if value < 0 else text
    if ",," in number_format:  # full dollars stored, $ millions shown
        if round(value / 1e6) == 0:
            return "0" if number_format.endswith(";0") else "–"
        text = f"{abs(value) / 1e6:,.0f}"
        return f"({text})" if value < 0 else text
    if '"x"' in number_format:
        return f"{value:.2f}x"
    if number_format == "0.0":
        return f"{value:.1f}"
    return f"{value:,.4g}"


@dataclass(frozen=True)
class StatementTable:
    sheet: str
    columns: tuple[str, ...]  # "Line", then the year headers (FY2021A ... FY2028E)
    rows: tuple[tuple[str, ...], ...]  # display strings
    notes: tuple[str, ...]  # GAP and NIL notes, "<year> <line>: <reason>"
    tips: tuple[tuple[str, ...], ...] = ()  # per cell: the GAP or NIL reason ("" for none)


def statement_tables(xlsx: Path, manifest: Mapping[str, Any], scenario: str,
                     sheets: Sequence[str] = ("Income Statement", "Balance Sheet", "Cash Flow")
                     ) -> list[StatementTable]:
    """The statement sheets of a pinned workbook for one scenario: input cells as written,
    formula cells as the manifest's expected value for the scenario (or for every scenario),
    GAP cells as "n/a" and NIL inputs (#152) as "nil", each with its reason as the tip."""
    from openpyxl import load_workbook

    wb = load_workbook(xlsx)
    cells = manifest.get("cells", {})
    gaps = {g["cell"]: g["reason"] for g in manifest.get("gaps", [])}
    nils = {n["cell"]: n["basis"] for n in manifest.get("nils", [])}
    verified = scenario in manifest.get("verified_scenarios", [])
    tables = []
    for sheet in sheets:
        ws = wb[sheet]
        header = [ws.cell(HEADER_ROW, c).value for c in range(2, ws.max_column + 1)]
        width = max((i + 1 for i, h in enumerate(header) if h), default=0)
        years = [str(h) for h in header[:width]]
        rows, notes, tips = [], [], []
        for r in range(FIRST_ROW, ws.max_row + 1):
            label = ws.cell(r, 1).value
            if not label:
                continue
            shown, tip = [str(label)], [""]
            for i in range(width):
                cell = ws.cell(r, i + 2)
                key = f"{sheet}!{cell.coordinate}"
                if key in gaps:
                    shown.append(NA)
                    tip.append(gaps[key])
                    notes.append(f"{years[i]} {label}: {gaps[key]}")
                    continue
                if key in nils:
                    shown.append("nil")
                    tip.append(nils[key])
                    notes.append(f"{years[i]} {label}: {nils[key]}")
                    continue
                tip.append("")
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    expected = cells.get(key, {}).get("expected", {})
                    value = expected.get(scenario, expected.get(ALL)) \
                        if (verified or ALL in expected) else None
                    shown.append(value if isinstance(value, str) else "" if value is None
                                 else format_number(float(value), cell.number_format))
                elif isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                    shown.append(format_number(float(cell.value), cell.number_format))
                else:
                    shown.append("" if cell.value is None else str(cell.value))
            rows.append(tuple(shown))
            tips.append(tuple(tip))
        tables.append(StatementTable(sheet, ("Line", *years), tuple(rows), tuple(notes),
                                     tuple(tips)))
    return tables


def statement_html(t: StatementTable) -> str:
    """A statement as an HTML table: lines left, numbers right-aligned, A and E columns marked,
    a section row (no values) in bold, n/a and nil cells with their reason as the tooltip."""
    head = "".join(f'<th class="{"line" if i == 0 else "num " + c[-1:].lower()}">'
                   f"{html.escape(c if i else '')}</th>" for i, c in enumerate(t.columns))
    body = []
    tips = t.tips or tuple(("",) * len(r) for r in t.rows)
    for row, tip in zip(t.rows, tips):
        section = all(v == "" for v in row[1:])
        cells = [f'<td class="line{" section" if section else ""}">{html.escape(row[0])}</td>']
        for col, (value, why) in enumerate(zip(row[1:], tip[1:]), 1):
            kind = t.columns[col][-1:].lower()
            title = f' title="{html.escape(why, quote=True)}"' if why else ""
            mark = " na" if value in (NA, "nil") else ""
            cells.append(f'<td class="num {kind}{mark}"{title}>{html.escape(value)}</td>')
        body.append(f"<tr>{''.join(cells)}</tr>")
    return (f'<table class="fa-statement"><thead><tr>{head}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table>")


def read_manifest(path: Path) -> dict[str, Any]:
    return read_json(path)


def recalc_text(recalc_json: Path, ticker: str) -> tuple[bool, str]:
    """Whether the workbook's LibreOffice recalculation matched the model in every scenario,
    and one plain sentence saying so (with the first mismatches when it did not)."""
    entry = read_json(recalc_json).get("workbooks", {}).get(ticker) if recalc_json.exists() \
        else None
    if entry is None:
        return False, "This workbook was not recalculated."
    if entry.get("status") == "PASS":
        return True, ("Recalculated in LibreOffice: every formula gives the model's value in "
                      "all three scenarios.")
    first = sorted({m for s in entry.get("scenarios", {}).values() for m in s.get("first", [])})
    count = max((s.get("mismatches", 0) for s in entry.get("scenarios", {}).values()), default=0)
    return False, (f"Recalculated in LibreOffice: {count} cell(s) differ from the model "
                   f"({'; '.join(first)}). Use those cells with care.")


# ---------------------------------------------------------------- Company page headline cards

@dataclass(frozen=True)
class MetricCard:
    label: str
    value: str
    status: str
    help: str


def company_cards(statements: Mapping[str, Any], ticker: str, metrics: Sequence[str],
                  labels: Mapping[str, str]) -> tuple[int | None, list[MetricCard]]:
    """The latest reported fiscal year's value of each metric from the pinned statement set
    (statements.json), with its status: a VERIFIED value, or an accepted SINGLE_CHANNEL one
    (named "one source only"); anything else shows "n/a" with its status and reason."""
    from finagent.ui.present import day

    company = next((c for c in statements["companies"] if c["issuer"] == ticker), None)
    if company is None or not company.get("filings"):
        return None, []
    year = max(int(f["fiscal_year"]) for f in company["filings"])
    found = {o["metric"]: o for o in company["observations"]
             if int(o["fiscal_year"]) == year}
    cards = []
    for metric in metrics:
        o = found.get(metric)
        name = labels.get(metric, metric.replace("_", " ").capitalize())
        shown = o is not None and o.get("value") is not None and (
            o.get("status") == "VERIFIED"
            or (o.get("status") == "SINGLE_CHANNEL" and o.get("accepted")))
        if not shown:
            why = "not reported or not readable" if o is None else \
                f"{o.get('status')}: {o.get('reason', '')}"
            cards.append(MetricCard(name, NA, o.get("status", "GAP") if o else "GAP",
                                    f"FY{year}: {why}"))
            continue
        notes = [f.lower() for f in o.get("flags", []) if f in ("CORRECTED", "RESTATED")]
        if o["status"] == "SINGLE_CHANNEL":
            notes.insert(0, "one source only (not cross-checked)")
        cards.append(MetricCard(name, usd(float(o["value"])), o["status"],
                                f"FY{year}, 10-K filed {day(o.get('filed'))}"
                                + (f"; {', '.join(notes)}" if notes else "")))
    return year, cards


# ---------------------------------------------------------------- forecast and credit

def _csv_rows(path: Path, ticker: str, scenario: str | None = None) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [r for r in csv.DictReader(handle)
                if r["ticker"] == ticker and (scenario is None or r.get("scenario") == scenario)]


def check_table(checks_csv: Path, ticker: str, scenario: str
                ) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
    """F1-F5 (Appendix B) status per check and fiscal year, as recorded by the forecast run."""
    rows = _csv_rows(checks_csv, ticker, scenario)
    years = sorted({r["fiscal_year"] for r in rows})
    found: dict[str, dict[str, str]] = {}
    details: dict[str, str] = {}
    for r in rows:
        found.setdefault(r["check"], {})[r["fiscal_year"]] = r["status"]
        details.setdefault(r["check"], r["detail"])
    head = ("Check", "What it tests", *(f"FY{y}" for y in years))
    return head, [(c, details[c], *(found[c].get(y, "") for y in years)) for c in sorted(found)]


def scenario_rows(scenarios_json: Mapping[str, Any], ticker: str) -> list[dict[str, str]]:
    """Each scenario's forecast status, origin 10-K year, reasons and flags (scenarios.json)."""
    company = next((c for c in scenarios_json["companies"] if c["ticker"] == ticker), None)
    if company is None:
        return []
    rows = []
    for name, f in (company.get("scenarios") or {}).items():
        rows.append({"Scenario": name, "Status": f.get("status", ""),
                     "From the 10-K of": f"FY{f['origin_year']}" if f.get("origin_year") else "",
                     "Flags": ", ".join(f.get("flags", [])),
                     "Reasons": "; ".join(f.get("reasons", []))})
    return rows


def driver_value(value: str) -> str:
    """A driver: an amount (USD) or a rate shown as a percent."""
    if value in ("", None):
        return ""
    v = float(value)
    return usd(v) if abs(v) >= 1e4 else f"{v * 100:.1f}%"


def driver_rows(drivers_csv: Path, ticker: str, scenario: str) -> list[dict[str, str]]:
    """The scenario's forecast drivers as recorded by the forecast run (drivers.csv)."""
    return [{"Driver": r["driver"].replace("_", " ").capitalize(),
             "Value": driver_value(r["value"]), "Basis": r["basis"], "Status": r["status"],
             "How": r["detail"]} for r in _csv_rows(drivers_csv, ticker, scenario)]


def _usd(text: str) -> str:
    return usd(float(text)) if text not in ("", None) else ""


def credit_table(credit_dir: Path, ticker: str, scenario: str
                 ) -> tuple[tuple[str, ...], list[tuple[str, ...]], list[str]]:
    """Scoped DSCR and liquidity headroom on both bases (cash; cash plus haircut securities),
    per projected year, as recorded by the credit run; plus its notes."""
    service = {r["fiscal_year"]: r for r in _csv_rows(credit_dir / "debt_service.csv", ticker,
                                                      scenario)}
    liquidity = {r["fiscal_year"]: r for r in _csv_rows(credit_dir / "liquidity.csv", ticker,
                                                        scenario)}
    years = sorted(set(service) | set(liquidity))
    s = [service.get(y, {}) for y in years]
    q = [liquidity.get(y, {}) for y in years]
    rows = [
        ("Cash available for debt service", *(_usd(x.get("cash_available", "")) for x in s)),
        ("Debt service (principal, interest, finance leases)",
         *(_usd(x.get("debt_service", "")) for x in s)),
        ("Scoped DSCR", *(f"{float(x['scoped_dscr']):.2f}x" if x.get("scoped_dscr") else ""
                          for x in s)),
        ("Headroom, cash basis", *(_usd(x.get("cash_headroom", "")) for x in q)),
        ("Result, cash basis", *(x.get("label", "") for x in q)),
        ("Headroom, cash + haircut securities basis",
         *(_usd(x.get("headroom_with_reserve", "")) for x in q)),
        ("Result, cash + haircut securities basis", *(x.get("label_with_reserve", "") for x in q)),
        ("Status (debt service / liquidity)",
         *(f"{a.get('status', '')} / {b.get('status', '')}" for a, b in zip(s, q))),
    ]
    notes = sorted({f"FY{y}: {service[y]['detail']}" for y in years
                    if y in service and service[y].get("detail")})
    return ("Measure", *(f"FY{y}" for y in years)), rows, notes


def default_headroom_basis(credit_dir: Path) -> str:
    header = read_json(credit_dir / "credit.json")["header"]
    return str(header.get("default_headroom_basis", ""))


# ---------------------------------------------------------------- insight cards

UNIT_TEXT = {"pp": " pp", "x": "x", "days": " days"}


def metric_value(value: float | None, unit: str, signed: bool = False) -> str:
    if value is None:
        return ""
    if unit == "USD":
        return usd(value)
    text = f"{value:+.2f}" if signed else f"{value:,.4g}"
    return text + UNIT_TEXT.get(unit, f" {unit}" if unit else "")


@dataclass(frozen=True)
class CardView:
    title: str
    severity: str
    status: str
    scenario: str
    text: str  # the library's rendered card (finagent.risk.insights.card_text)
    metrics: tuple[tuple[str, ...], ...]  # condition, state, value, test, values by year
    quotes: tuple[dict[str, Any], ...]
    low: dict[str, Any] | None  # D-EV2 low-confidence sentence: never evidence
    detail: str
    filing: str
    name: str = ""  # the signal's name, without its id
    url: str = ""


def insight_cards(cards_json: Path, ticker: str) -> tuple[int | None, list[CardView]]:
    """The insight cards of the company's latest fiscal year in the pinned insights run (one
    card per fired signal)."""
    data = read_json(cards_json)
    company = next((c for c in data["companies"] if c["ticker"] == ticker), None)
    if company is None or not company.get("cards"):
        return None, []
    year = max(int(y) for y in company["cards"])
    views = []
    for card in company["cards"][str(year)]:
        metrics = tuple(
            (m["label"], m["state"], metric_value(m.get("value"), m.get("unit", ""),
                                                  m.get("unit") in ("pp",)),
             f"{m.get('op', '')} {metric_value(m.get('threshold'), m.get('unit', ''))}".strip(),
             ", ".join(f"FY{y} {metric_value(v, m.get('unit', '') if m.get('unit') == 'USD' else '')}"
                       for y, v in m.get("values", [])))
            for m in card["metrics"])
        views.append(CardView(
            f"{card['signal_id']} {card['name']}", card["severity"], card["status"],
            card.get("scenario", ""), card["text"], metrics, tuple(card.get("evidence") or ()),
            card.get("low_confidence"), card.get("detail", ""),
            f"10-K {card['accession']}, filed {card['filed']}", card["name"],
            card.get("filing_url", "")))
    return year, views


def signal_states(signals_csv: Path, ticker: str, year: int) -> list[dict[str, str]]:
    """Every signal's state in one fiscal year, as recorded by the insights run (signals.csv):
    FIRED, NOT_FIRED, GAP or NOT_APPLIED, with the reason."""
    with Path(signals_csv).open(newline="", encoding="utf-8") as handle:
        return [{"signal": r["signal"], "state": r["state"], "reason": r["reason"],
                 "detail": r["detail"]} for r in csv.DictReader(handle)
                if r["ticker"] == ticker and r["fiscal_year"] == f"FY{year}"]


def low_confidence_text(low: Mapping[str, Any]) -> str:
    """The D-EV2 sentence with its provenance (as LowConfidenceSentence.display shows it)."""
    q = low["quote"]
    return (f"{q['section']}, chunk {q['chunk_id']}; passage reranker score "
            f"{low['rerank_score']:.3f}, below the threshold {low['threshold']:.3f}; not evidence")


# ---------------------------------------------------------------- Ask page (technical tables)

def verification_label(o: AnswerObservation, accepted: bool | None) -> str:
    """The blueprint 6.1 / 8.7 word for a returned value: the gate's rejection, CORRECTED,
    SINGLE_CHANNEL (one channel; flagged), VERIFIED (validated and accepted)."""
    if accepted is False:
        return "INVALID (gate)"
    if "CORRECTED" in o.flags:
        return "CORRECTED"
    if "SINGLE_CHANNEL" in o.flags or "SINGLE_CHANNEL_INPUTS" in o.flags:
        return "SINGLE_CHANNEL"
    return "VERIFIED" if o.validated else "NOT VALIDATED"


def _decision(goal: Any, metric: str) -> Any:
    return next((d for d in goal.decisions if d.metric == metric), None)


def goal_rows(inv: Any) -> list[dict[str, Any]]:
    rows = []
    for g in inv.goals:
        goal = g.goal
        period = f"FY{goal.period}" if isinstance(goal.period, int) else str(goal.period)
        rows.append({"Goal": goal.kind, "Company": goal.issuer, "Period": period,
                     "Metric": goal.metric or "(default set)", "Scenario": goal.scenario,
                     "Requested": "yes" if g.requested else "prerequisite",
                     "Status": g.status, "Reason": g.reason, "Detail": g.detail})
    return rows


def shown_value(o: AnswerObservation) -> str:
    return NM_TEXT if o.value is None else number(o.value, o.unit)


def value_rows(inv: Any) -> list[dict[str, Any]]:
    """Every value the run returned, with its verification word and the gate's decision."""
    rows = []
    for g in inv.goals:
        for o in g.observations:
            d = _decision(g, o.metric)
            rows.append({"Goal": g.goal.kind, "Company": o.issuer, "FY": o.fiscal_year,
                         "Metric": o.metric, "Scenario": o.scenario,
                         "Value": shown_value(o), "Classification": o.classification,
                         "Verification": verification_label(o, d.accepted if d else None),
                         "Channels": ", ".join(o.channels), "Flags": ", ".join(o.flags),
                         "Gate": ("accepted" if d.accepted else f"rejected {d.code}")
                         if d else "", "10-K": o.accession, "Filed": str(o.filed)})
    return rows


def gate_rows(inv: Any) -> list[dict[str, Any]]:
    return [{"Goal": f"{g.goal.kind} {g.goal.issuer}", "Metric": d.metric,
             "Accepted": "yes" if d.accepted else "no", "Code": d.code, "Detail": d.detail}
            for g in inv.goals for d in g.decisions]


def trace_rows(inv: Any) -> list[dict[str, Any]]:
    return [{"Step": s.step, "Goal": s.goal, "Tool": s.tool,
             "Arguments": json.dumps(s.arguments, default=str, sort_keys=True),
             "Status": s.status, "Decision": s.decision, "Reason": s.reason,
             "Gate": s.gate, "Latency (ms)": s.latency_ms}
            for s in inv.trace]


def source_rows(inv: Any) -> list[dict[str, Any]]:
    """Each 10-K a returned value cites, by accession, with the values that cite it."""
    found: dict[str, dict[str, Any]] = {}
    for g in inv.goals:
        for o in g.observations:
            for accession in (o.accession, *o.sources):
                row = found.setdefault(accession, {
                    "Accession": accession, "Company": o.issuer, "Filed": "", "URL": "",
                    "Cited by": []})
                if accession == o.accession:
                    row["Filed"], row["URL"] = str(o.filed), o.filing_url
                label = f"{o.metric} FY{o.fiscal_year}"
                if label not in row["Cited by"]:
                    row["Cited by"].append(label)
    return [{**r, "Cited by": ", ".join(r["Cited by"])} for r in found.values()]


def trace_export(inv: Any) -> str:
    """The run and its steps as JSON, with the raw answer text (exported for download; never
    persisted by the app)."""
    return json.dumps({"run": run_record(inv), "steps": step_records(inv),
                       "answer": inv.answer}, default=str, indent=1, sort_keys=True)


# ---------------------------------------------------------------- Results page

def resolve(data: Any, path: Sequence[Any]) -> Any:
    """Follow path keys into nested metrics; "key=value" selects the list item with it."""
    for key in path:
        if isinstance(data, list) and isinstance(key, str) and "=" in key:
            name, _, wanted = key.partition("=")
            data = next(item for item in data if str(item.get(name)) == wanted)
        elif isinstance(data, list):
            data = data[int(key)]
        else:
            data = data[key]
    return data


def read_experiments(path: Path = EXPERIMENTS) -> dict[str, dict[str, Any]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {r["run_id"]: {**r, "metrics": json.loads(r["metrics"])} for r in rows}


def _f3(x: float) -> str:
    return f"{x:.3f}"


def e6_text(row: Mapping[str, Any]) -> str:
    """An E6 row: APE difference [95% CI]; coverage; the pre-registered claim."""
    diff = "no scored origin" if row.get("mean_ape_difference") is None else \
        f"APE difference {row['mean_ape_difference']:+.3f} [{row['ci95'][0]:+.3f}, " \
        f"{row['ci95'][1]:+.3f}]"
    claim = "beats naive (claim)" if row.get("beats_naive") else "no accuracy claim"
    return (f"{diff}; coverage {row['scored']}/{row['qualifying']} origins, "
            f"{row['companies_scored']}/{row['companies_qualifying']} companies; {claim}")


def format_result(kind: str, value: Any, ci: Any = None, after: Any = None) -> str:
    if kind == "rate_ci":
        return f"{_f3(value)} [{_f3(ci[0])}, {_f3(ci[1])}]"
    if kind == "diff_ci":
        return f"{value:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}]"
    if kind == "kn":
        tail = f" [{_f3(value['ci95'][0])}, {_f3(value['ci95'][1])}]" if value.get("ci95") else ""
        rate = _f3(value["rate"]) if value.get("rate") is not None else "n/a"
        return f"{value['k']}/{value['n']} = {rate}{tail}"
    if kind == "pair":
        return f"{_f3(value[0])} [{_f3(value[1][0])}, {_f3(value[1][1])}]"
    if kind == "before_after":
        return f"{_f3(value)} -> {_f3(after)}"
    if kind == "p50_p95":
        return f"{value['p50']:.2f} / {value['p95']:.2f}"
    if kind == "number":
        return str(value) if isinstance(value, int) else _f3(float(value))
    if kind == "e6":
        return e6_text(value)
    return str(value)


def _metrics_source(row: Mapping[str, Any], experiments: Mapping[str, Mapping[str, Any]],
                    root: Path) -> Any:
    if row["source"] == "csv":
        found = experiments.get(row["run"])
        return None if found is None else found["metrics"]
    path = root / row["source"]
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def result_value(row: Mapping[str, Any], experiments: Mapping[str, Mapping[str, Any]],
                 root: Path = ROOT) -> str:
    metrics = _metrics_source(row, experiments, root)
    if metrics is None:
        return NOT_RUN
    return format_result(row["kind"], resolve(metrics, row["path"]),
                         resolve(metrics, row["ci"]) if row.get("ci") else None,
                         resolve(metrics, row["after"]) if row.get("after") else None)


def headline_rows(config: UiConfig, root: Path = ROOT,
                  experiments: Mapping[str, Mapping[str, Any]] | None = None
                  ) -> list[dict[str, str]]:
    """The Results table: one row per configured measure, read from its run."""
    experiments = read_experiments(root / "eval" / "experiments.csv") \
        if experiments is None else experiments
    return [{"Experiment": row["experiment"], "Run": row["run"], "Model": row["model"],
             "Measure": row["measure"], "Value": result_value(row, experiments, root),
             "What it means": row.get("explain", ""), "Note": row.get("note", ""),
             "Source": row.get("doc", "")} for row in config.results]


def headline_cards(config: UiConfig, root: Path = ROOT,
                   experiments: Mapping[str, Mapping[str, Any]] | None = None
                   ) -> list[dict[str, str]]:
    """The Results page's headline cards, in the configured order."""
    experiments = read_experiments(root / "eval" / "experiments.csv") \
        if experiments is None else experiments
    by_title = {r["card"]: r for r in config.results if r.get("card")}
    return [{"Title": title, "Value": result_value(by_title[title], experiments, root),
             "Measure": f"{by_title[title]['model']}: {by_title[title]['measure']}",
             "Run": by_title[title]["run"], "Explain": by_title[title].get("explain", ""),
             "Note": by_title[title].get("note", "")} for title in config.cards]


def e1_chart_rows(config: UiConfig, experiments: Mapping[str, Mapping[str, Any]]
                  ) -> list[dict[str, Any]]:
    rows = []
    for item in config.charts["e1"]:
        m = experiments[item["run"]]["metrics"]
        rows.append({"Model": item["model"], "Split": item["split"], "Run": item["run"],
                     "Field accuracy": m["field_accuracy"], "Low": m["ci95"][0],
                     "High": m["ci95"][1], "Fields": m["fields"]})
    return rows


def e5_chart_rows(config: UiConfig, experiments: Mapping[str, Mapping[str, Any]]
                  ) -> list[dict[str, Any]]:
    spec = config.charts["e5"]
    rows = []
    for run in spec["runs"]:
        overall = experiments[run["run"]]["metrics"]["overall"]
        for key, label in spec["measures"].items():
            v = overall[key]
            rows.append({"Compiler": run["compiler"], "Run": run["run"], "Measure": label,
                         "Rate": v["rate"], "Low": v["ci95"][0], "High": v["ci95"][1],
                         "Count": f"{v['k']}/{v['n']}"})
    return rows


def e7_questions(root: Path = ROOT, run_id: str = "e7-0001") -> list[dict[str, Any]]:
    path = root / "runs" / run_id / "questions.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def build_seconds(serving: Mapping[str, Any]) -> list[float]:
    """Each company's recorded build time in the serving run (the sum of its stages)."""
    return [round(sum(c["seconds"].values()), 2) for c in serving.get("companies", [])
            if c.get("seconds") and not c.get("error")]


def manual_vs_app(config: UiConfig, serving: Mapping[str, Any] | None,
                  e7: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """E7 beside D10: the app's recorded latency and build times, and Vineet's manual estimate.
    The build-time median and maximum are the only figures the app derives (from serving.json's
    recorded seconds)."""
    rows = []
    if e7 is not None:
        rows.append({"What": "Answer one question (cold pass, p50 / p95)",
                     "Who": "FinAgent (e7-0001)",
                     "Time": f"{e7['cold']['p50']:.2f} s / {e7['cold']['p95']:.2f} s"})
    seconds = build_seconds(serving or {})
    if seconds:
        rows.append({"What": "Build one company's statements, three-scenario model, credit, "
                             "risk cards and recalculated workbook",
                     "Who": f"FinAgent ({config.serving['run']}, {len(seconds)} companies; "
                            "median / max of the recorded times)",
                     "Time": f"{statistics.median(seconds):.0f} s / {max(seconds):.0f} s"})
    if config.manual_build:
        rows.append({"What": "Build one company's three-statement model from scratch",
                     "Who": f"Analyst, by hand ({config.manual_build['source']})",
                     "Time": f"about {config.manual_build['hours']} hours"})
    return rows


def as_records(rows: Sequence[Any]) -> list[dict[str, Any]]:
    return [asdict(r) if hasattr(r, "__dataclass_fields__") else dict(r) for r in rows]
