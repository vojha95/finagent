"""The serving build (BC36, D2b): one pinned run for every company the app serves.

    python -m finagent.ui.serving build --run-id serving-0001 [--as-of 2026-11-24]
    python -m finagent.ui.serving compare --run-id serving-0001

build: for every company of configs/universe.yaml (validation, train and held-out) whose 10-Ks
are pinned, the post-agent statement set as of the snapshot is built once, then fed
to the same per-company functions and writers as the pinned runs, one folder each:
- statements/ (finagent.statements.report): statements.json, identities.csv, coverage.csv,
  agent.json, final_check.csv;
- forecast/ (finagent.model.scenarios): scenarios.json, drivers.csv, projections.csv,
  checks.csv, every scenario of configs/scenarios.yaml;
- credit/ (finagent.credit.report): credit.json, liquidity.csv, debt_service.csv, ratios.csv;
- insights/ (finagent.risk.report, the configured evidence version with the D-EV2 display):
  cards.json, signals.csv, cards.csv, answers.txt (no explain runs);
- workbooks/ (finagent.workbook.build and .recalc): <ticker>.xlsx, <ticker>.manifest.json,
  summary.json, recalc.json (LibreOffice recalculation);
- ask/ (BC37, proposed; finagent.ui.snapshot): <ticker>.pkl, the reconciliation exactly as
  built (written before any later stage runs), and snapshot.json (the build's fingerprint at
  its start, each pickle's sha256, and anything that changed during the build). The app's Ask
  page reuses them at this as_of when the fingerprint matches the current one.
serving.json records each company's split, per-stage seconds and errors. Nothing is written
under docs/.

Held-out companies (D2b): the held-out E1 run is finished (e1-0009, e1-0010), so this build and
the app may read them (include_held_out). They are never used for debugging, tuning,
evaluation or any change: a failure is recorded here and logged, never fixed.

compare: the validation companies' records in serving-<n> against the pinned runs the app read
before (statements-0009, forecast-0007, credit-0003, insights-0004, workbooks-0009), field by
field. Run headers are not compared. A difference is a known #196 wording change (the nil text,
implemented after statements-0009, forecast-0007 and workbooks-0009), a timing (the agent
trace's measured latency_ms) or unexplained; the proof is 0 unexplained differences. Writes
runs/serving-<n>/compare.txt.
"""

import argparse
import csv
import hashlib
import json
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import HELD_OUT, load_universe

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
SPLITS = ("validation", "train", HELD_OUT)
PINNED = {"statements": "statements-0009", "forecast": "forecast-0007",
          "credit": "credit-0003", "insights": "insights-0004", "workbooks": "workbooks-0009"}
# The #196 nil wording (decision b, #197): the old and the new text of a nil value (the
# statement basis and detail, the workbook note, the forecast's opening-line detail).
OLD_NIL = ("nil: no channel read it in FY", "treated as nil for FY",
           "(not read that year; read in other years; #152)")
NEW_NIL = ("not read in FY", "treated as nil;")
TIMING = "latency_ms"  # a measured wall-clock time (the agent trace): never reproducible


@dataclass
class Company:
    """One company of the serving build: its outputs, timings and error."""

    ticker: str
    split: str
    seconds: dict[str, float] = field(default_factory=dict)
    error: str = ""


def _git(*args: str) -> str:
    from finagent.model.report import _git as git

    return git(*args)


def header(run_id: str, as_of: date, part: str, **extra: Any) -> dict[str, Any]:
    """The header every pinned run writes, with the serving run id and its part."""
    return {"run_id": f"{run_id}/{part}", "git_commit": _git("rev-parse", "--short", "HEAD"),
            "git_dirty": bool(_git("status", "--porcelain")),
            "evidence_manifest_sha256": hashlib.sha256(
                (ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest(),
            "as_of": as_of.isoformat(), **extra}


def served(store: Any, universe: Mapping[str, str], as_of: date) -> list[tuple[str, str]]:
    """(ticker, split) of every company with pinned 10-Ks, validation first (D2b)."""
    from finagent.evaluation.e1 import pinned_tickers

    return [(t, split) for split in SPLITS
            for t in pinned_tickers(store, dict(universe), split, as_of, include_held_out=True)]


def timed(company: Company, stage: str, work: Callable[[], Any]) -> Any:
    """Run one stage; its seconds are recorded and an error stops the company's later stages."""
    started = time.perf_counter()
    try:
        return work()
    except Exception as exc:  # recorded, never hidden (held-out: logged, never fixed)
        company.error = company.error or (f"{stage}: {type(exc).__name__}: {exc}\n"
                                          f"{traceback.format_exc()}")
        return None
    finally:
        company.seconds[stage] = round(time.perf_counter() - started, 2)


def build(run_id: str, as_of: date, only: Sequence[str] = ()) -> list[Company]:
    """The serving run (module docstring). only: a subset of tickers, to check the build
    before the pinned run (which serves every company)."""
    from finagent.config import (load_agent_config, load_forecast_config, load_forecast_inputs,
                                 load_scenarios)
    from finagent.credit.report import CompanyCredit, company_credit
    from finagent.credit.report import write_outputs as write_credit
    from finagent.model.scenarios import ScenarioRun, scenario_run
    from finagent.model.scenarios import write_outputs as write_forecast
    from finagent.planner.compile import CompilerResources
    from finagent.risk.report import CompanyInsights, company_insights, evidence_header
    from finagent.risk.report import write_outputs as write_insights
    from finagent.statements.build import StatementConfigs
    from finagent.statements.coverage import slots
    from finagent.statements.replay import FULL
    from finagent.statements.report import (RunResult, set_record, write_agent_outputs,
                                            write_coverage_csv, write_identities_csv)
    from finagent.tools import FinAgentTools
    from finagent.verify.reconcile import reconcile_company
    from finagent.workbook.build import company_workbook, labels
    from finagent.workbook.layout import load_workbook_layout
    from finagent.workbook.recalc import check_workbook, find_soffice

    from finagent.ui.snapshot import fingerprint, write_index, write_reconciliation

    out = RUNS / run_id
    if out.exists():
        raise SystemExit(f"{out} exists: a serving run is never overwritten; use a new --run-id")
    start_fingerprint = fingerprint()  # BC37: compared again at the end of the build
    tools = FinAgentTools.default(include_held_out=True)
    universe, configs, agent = tools.universe, StatementConfigs.load(), load_agent_config()
    needs, config, specs = load_forecast_inputs(), load_forecast_config(), load_scenarios()
    layout, item_labels, resources = load_workbook_layout(), labels(), CompilerResources.load()
    parts = {p: out / p for p in ("statements", "forecast", "credit", "insights", "workbooks",
                                  "ask")}
    for p in parts.values():
        p.mkdir(parents=True)
    model = tools.classifier.version
    wb_header = header(run_id, as_of, "workbooks", model_version=model, correlation_agent=True)
    runs = {split: RunResult(split) for split in SPLITS}
    forecasts: list[ScenarioRun] = []
    credits: list[CompanyCredit] = []
    insights: list[CompanyInsights] = []
    workbooks: dict[str, Any] = {}
    snapshots: dict[str, str] = {}  # ticker -> ask/<ticker>.pkl sha256 (BC37)
    companies = []
    for ticker, split in served(tools.store, universe, as_of):
        if only and ticker not in only:
            continue
        c = Company(ticker, split)
        companies.append(c)
        rec = timed(c, "statements", lambda: reconcile_company(
            tools.store, ticker, as_of, tools.classifier, universe, configs, agent,
            include_held_out=True))
        if rec is None:
            runs[split].failures[ticker] = c.error
            print(ticker, split, "ERROR", c.error.splitlines()[0], flush=True)
            continue
        written = timed(c, "ask_snapshot", lambda: write_reconciliation(parts["ask"], rec))
        if written is not None:
            snapshots[ticker] = written
        s = rec.statement_set
        runs[split].sets[ticker], runs[split].reconciled[ticker] = s, rec
        tools.reconciliations[(ticker, as_of, FULL)] = rec  # the same set for every part
        forecasts.append(timed(c, "forecast", lambda: scenario_run(
            ticker, split, s, rec, as_of, needs, config, specs)))
        credits.append(timed(c, "credit", lambda: company_credit(ticker, split, s, as_of, specs))
                       or CompanyCredit(ticker, split, error=c.error))
        insights.append(timed(c, "insights", lambda: company_insights(
            tools, ticker, as_of, resources)) or CompanyInsights(ticker, error=c.error))
        workbooks[ticker] = timed(c, "workbook", lambda: company_workbook(
            parts["workbooks"], ticker, split, s, rec, as_of, wb_header, needs, config, specs,
            layout, item_labels)) or {"split": split, "error": c.error}
        print(ticker, split, {k: v for k, v in c.seconds.items()},
              c.error.splitlines()[0] if c.error else "", flush=True)

    ordered = [runs[s] for s in SPLITS]
    statements_header = header(run_id, as_of, "statements", model_version=model,
                               dataset_version="v3", correlation_agent=True)
    (parts["statements"] / "statements.json").write_text(json.dumps({
        "header": statements_header,
        "failures": {t: e for r in ordered for t, e in r.failures.items()},
        "companies": [set_record(s, r.split) for r in ordered for s in r.sets.values()]},
        indent=1, default=str), encoding="utf-8")
    write_identities_csv(parts["statements"] / "identities.csv", ordered)
    write_coverage_csv(parts["statements"] / "coverage.csv", {
        t: (r.split, slots(s, needs, configs.derivations.bases))
        for r in ordered for t, s in r.sets.items()})
    write_agent_outputs(parts["statements"], ordered)
    write_forecast(parts["forecast"], [f for f in forecasts if f is not None],
                   header(run_id, as_of, "forecast", model_version=model,
                          scenarios=[s.name for s in specs]), [s.name for s in specs])
    write_credit(parts["credit"], credits, header(
        run_id, as_of, "credit", model_version=model,
        default_headroom_basis=config.headroom_basis))
    write_insights(parts["insights"], insights, header(
        run_id, as_of, "insights", model_version=model,
        **evidence_header(tools.signal_config.evidence)), [])
    (parts["workbooks"] / "summary.json").write_text(json.dumps(
        {"header": wb_header, "companies": workbooks}, indent=1, default=str), encoding="utf-8")
    soffice = find_soffice()
    recalc = {"soffice": soffice, "workbooks": {}}
    for path in sorted(parts["workbooks"].glob("*.xlsx")):
        c = next(x for x in companies if x.ticker == path.stem)
        recalc["workbooks"][path.stem] = timed(c, "recalc", lambda: check_workbook(
            path, soffice)) or {"status": "ERROR", "reason": c.error}
    (parts["workbooks"] / "recalc.json").write_text(json.dumps(recalc, indent=1),
                                                    encoding="utf-8")
    index = write_index(parts["ask"], run_id, as_of, snapshots, start_fingerprint, fingerprint())
    write_summary(out, run_id, as_of, companies, forecasts, workbooks, recalc, specs,
                  {"companies": len(snapshots),
                   "changed_during_build": index["changed_during_build"]})
    return companies


def scenario_status(forecasts: Sequence[Any], names: Sequence[str]
                    ) -> dict[str, dict[str, dict[str, int]]]:
    """Per scenario and split: how many companies end VERIFIED, GAP, INVALID or ERROR."""
    found: dict[str, dict[str, dict[str, int]]] = {n: {} for n in names}
    for r in forecasts:
        if r is None:
            continue
        for n in names:
            status = r.status(n)
            split = found[n].setdefault(r.split, {})
            split[status] = split.get(status, 0) + 1
    return found


def write_summary(out: Path, run_id: str, as_of: date, companies: Sequence[Company],
                  forecasts: Sequence[Any], workbooks: Mapping[str, Any],
                  recalc: Mapping[str, Any], specs: Sequence[Any],
                  ask: Mapping[str, Any] | None = None) -> None:
    names = [s.name for s in specs]
    errored = {c.ticker for c in companies if c.error}
    document = {
        "header": header(run_id, as_of, "serving", policy="BC36 (D2b): held-out companies are "
                         "served; never debugged, tuned or evaluated"),
        "companies": [{"ticker": c.ticker, "split": c.split, "seconds": c.seconds,
                       "error": c.error} for c in companies],
        "scenario_status": scenario_status(forecasts, names),
        "built_without_error": len(companies) - len(errored),
        "errors": sorted(errored),
        "workbook_status": {t: w.get("status", "ERROR") for t, w in workbooks.items()},
        "recalc": {t: r.get("status") for t, r in recalc["workbooks"].items()},
    }
    if ask is not None:
        document["ask_snapshot"] = dict(ask)
    (out / "serving.json").write_text(json.dumps(document, indent=1, default=str),
                                      encoding="utf-8")


# ---------------------------------------------------------------- compare (validation proof)

Difference = tuple[str, Any, Any]  # path, pinned, serving


def differences(a: Any, b: Any, path: str = "") -> list[Difference]:
    """Every leaf that differs between two JSON-like values, by path."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b), key=str)
                for d in differences(a.get(k, "<absent>"), b.get(k, "<absent>"),
                                     f"{path}.{k}")]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in differences(x, y, f"{path}[{i}]")]
    return [] if a == b else [(path, a, b)]


def known_wording(a: Any, b: Any) -> bool:
    """A #196 nil wording change: the old wording replaced by the new, nothing else."""
    if not (isinstance(a, str) and isinstance(b, str)):
        return False
    return any(x in a for x in OLD_NIL) and any(x in b for x in NEW_NIL)


def kind(d: Difference) -> str:
    """wording (#196), timing (a measured latency) or unexplained."""
    if known_wording(d[1], d[2]):
        return "wording"
    return "timing" if d[0].endswith(f".{TIMING}") else "unexplained"


def _rows(path: Path, ticker: str, key: str = "ticker") -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [r for r in csv.DictReader(handle) if r.get(key) == ticker]


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _by(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    return {str(i[key]): i for i in items}


def artifact_pairs(serving: Path, ticker: str) -> list[tuple[str, Any, Any]]:
    """(artifact, pinned record, serving record) for one validation company."""
    p = {k: RUNS / v for k, v in PINNED.items()}
    s = {k: serving / k for k in PINNED}
    pairs: list[tuple[str, Any, Any]] = []
    st_a = _by(_json(p["statements"] / "statements.json")["companies"], "issuer")
    st_b = _by(_json(s["statements"] / "statements.json")["companies"], "issuer")
    pairs.append(("statements.json", st_a.get(ticker), st_b.get(ticker)))
    ag_a = _by(_json(p["statements"] / "agent.json")["companies"], "issuer")
    ag_b = _by(_json(s["statements"] / "agent.json")["companies"], "issuer")
    pairs.append(("agent.json", ag_a.get(ticker), ag_b.get(ticker)))
    for name, key in (("identities.csv", "issuer"), ("coverage.csv", "issuer"),
                      ("final_check.csv", "ticker")):
        pairs.append((name, _rows(p["statements"] / name, ticker, key),
                      _rows(s["statements"] / name, ticker, key)))
    fc_a = _by(_json(p["forecast"] / "scenarios.json")["companies"], "ticker")
    fc_b = _by(_json(s["forecast"] / "scenarios.json")["companies"], "ticker")
    pairs.append(("scenarios.json", fc_a.get(ticker), fc_b.get(ticker)))
    for name in ("drivers.csv", "projections.csv", "checks.csv"):
        pairs.append((name, _rows(p["forecast"] / name, ticker), _rows(s["forecast"] / name,
                                                                        ticker)))
    cr_a = _by(_json(p["credit"] / "credit.json")["companies"], "ticker")
    cr_b = _by(_json(s["credit"] / "credit.json")["companies"], "ticker")
    pairs.append(("credit.json", cr_a.get(ticker), cr_b.get(ticker)))
    for name in ("liquidity.csv", "debt_service.csv", "ratios.csv"):
        pairs.append((name, _rows(p["credit"] / name, ticker), _rows(s["credit"] / name,
                                                                      ticker)))
    in_a = _by(_json(p["insights"] / "cards.json")["companies"], "ticker")
    in_b = _by(_json(s["insights"] / "cards.json")["companies"], "ticker")
    pairs.append(("cards.json", in_a.get(ticker), in_b.get(ticker)))
    for name in ("signals.csv", "cards.csv"):
        pairs.append((name, _rows(p["insights"] / name, ticker), _rows(s["insights"] / name,
                                                                        ticker)))
    pairs.append(("workbook manifest", _json(p["workbooks"] / f"{ticker}.manifest.json"),
                  _json(s["workbooks"] / f"{ticker}.manifest.json")))
    pairs.append(("workbook summary", _json(p["workbooks"] / "summary.json")["companies"].get(
        ticker), _json(s["workbooks"] / "summary.json")["companies"].get(ticker)))
    pairs.append(("recalc", _json(p["workbooks"] / "recalc.json")["workbooks"].get(ticker),
                  _json(s["workbooks"] / "recalc.json")["workbooks"].get(ticker)))
    return pairs


def compare(run_id: str, universe: Mapping[str, str] | None = None) -> tuple[list[str], int]:
    """(report lines, unexplained differences) for the validation companies (module docstring)."""
    universe = universe or load_universe()
    serving = RUNS / run_id
    lines, unexplained, compared = [], 0, 0
    for ticker in sorted(t for t, s in universe.items() if s == "validation"):
        for artifact, a, b in artifact_pairs(serving, ticker):
            if a is None or b is None:
                lines.append(f"{ticker} {artifact}: missing in "
                             f"{'the pinned run' if a is None else run_id}")
                unexplained += 1
                continue
            found = differences(a, b)
            compared += 1
            kinds = [kind(d) for d in found]
            other = [d for d, k in zip(found, kinds) if k == "unexplained"]
            unexplained += len(other)
            if found:
                lines.append(f"{ticker} {artifact}: {kinds.count('wording')} known #196 "
                             f"wording, {kinds.count('timing')} timing (latency_ms), "
                             f"{len(other)} unexplained {[d[0] for d in other][:5]}")
                lines.extend(f"  UNEXPLAINED {d[0]}: {str(d[1])[:160]!r} -> {str(d[2])[:160]!r}"
                             for d in other[:10])
    lines.append(f"COMPARED {compared} artifacts of the validation companies against "
                 f"{', '.join(PINNED.values())}; {unexplained} unexplained differences")
    lines.append("MATCH" if unexplained == 0 else "DIFFERENT")
    return lines, unexplained


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build")
    p.add_argument("--run-id", required=True)
    p.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    p.add_argument("--tickers", default="", help="a subset, to check the build first")
    p = sub.add_parser("compare")
    p.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        companies = build(args.run_id, args.as_of,
                          tuple(t for t in args.tickers.split(",") if t))
        print(f"{len(companies)} companies; errors: {[c.ticker for c in companies if c.error]}")
        return 0
    lines, unexplained = compare(args.run_id)
    (RUNS / args.run_id / "compare.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if unexplained else 0


if __name__ == "__main__":
    raise SystemExit(main())
