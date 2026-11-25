"""Insights run (blueprint 8.13; Phase 7.3).

    python -m finagent.risk.report --run-id insights-0001 [--as-of 2026-11-24]

Validation companies only (held-out companies are refused by the tools). For each company:
- every signal of configs/signals.yaml for every fiscal year of the statement set, and an
  insight card for each fired one (evidence from that year's 10-K by the configured evidence
  version, v1 BM25 or v2 reranked with a threshold, D-EV; quotes or EVIDENCE_GAP), through
  finagent.tools (the planner's own tool); --evidence-version overrides the config;
- the latest year's risk goal answered end to end by the planner (investigate with a
  pre-compiled risk goal: the loop, the gate with the pinned-chunk quote check, render);
- explain answers on the dev companies only (D10: AMZN, MSFT), through the trained compiler:
  nothing about explain is run or tuned on a test company.

Writes runs/<run>/signals.csv, cards.csv, cards.json, answers.txt and docs/insights_v1.md
(notes below the marker are kept). The default-path check is quoted from
runs/<run>/default_path_check.txt when it exists.
"""

import argparse
import csv
import hashlib
import json
import traceback
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import SIGNALS_PATH
from finagent.evaluation.default_path import BASELINE_FORECAST, BASELINE_STATEMENTS
from finagent.model.report import ROOT, _git, _table, write_report
from finagent.records import Compilation, Goal, InsightCard, SignalResult
from finagent.retrieval.report import DEV
from finagent.risk.insights import EVIDENCE_GAP, FIRED, VERIFIED, model_card_made

STATES = ("FIRED", "NOT_FIRED", "GAP", "NOT_APPLIED")
SHORT = {"FIRED": "**F**", "NOT_FIRED": "-", "GAP": "gap", "NOT_APPLIED": "n/a"}
# Explain questions for the dev companies only (D10), phrased as Appendix D.
EXPLAIN_QUESTIONS = (
    "Why did Amazon's free cash flow fall in FY2025?",
    "Why did Amazon's operating income increase in FY2025?",
    "Why did Amazon's operating cash flow increase in FY2025?",
    "Why did Microsoft's revenue increase in fiscal 2026?",
    "Why did Microsoft's operating income increase in fiscal 2026?",
    "Why did Microsoft's free cash flow fall in fiscal 2026?",
)


@dataclass
class CompanyInsights:
    ticker: str
    signals: dict[int, tuple[SignalResult, ...]] = field(default_factory=dict)
    cards: dict[int, tuple[InsightCard, ...]] = field(default_factory=dict)
    answer: str = ""  # the latest year's risk goal, as the planner renders it
    goal_status: str = ""
    gate: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    @property
    def latest(self) -> int | None:
        return max(self.signals) if self.signals else None


def risk_compilation(ticker: str) -> Compilation:
    question = f"What are the main risk signals for {ticker}?"
    return Compilation(question, "precompiled", (Goal(ticker, "latest", "risk"),),
                       issuers=(ticker,))


def company_insights(tools: Any, ticker: str, as_of: date, resources: Any) -> CompanyInsights:
    """Every year's signals and cards, then the latest year's risk goal through the planner."""
    from finagent.planner.investigate import investigate

    c = CompanyInsights(ticker)
    for f in tools.filings(ticker, as_of):
        c.signals[f.fiscal_year], c.cards[f.fiscal_year] = tools.insight_cards(
            ticker, f.fiscal_year, as_of)
    compiled = risk_compilation(ticker)
    run = investigate(compiled.question, as_of, tools=tools, resources=resources,
                      compilation=compiled)
    risk = run.requested[0]
    c.answer, c.goal_status = run.answer, f"{risk.status} {risk.reason}"
    c.gate = [asdict(d) for d in risk.decisions]
    return c


def explain_runs(tools: Any, as_of: date, resources: Any,
                 questions: Sequence[str] = EXPLAIN_QUESTIONS) -> list[tuple[str, Any]]:
    """Explain questions through the trained compiler; dev companies only (D10)."""
    from finagent.planner.investigate import investigate

    found = []
    for q in questions:
        run = investigate(q, as_of, tools=tools, resources=resources)
        issuers = set(run.compilation.issuers)
        if not issuers <= set(DEV):
            raise ValueError(f"explain runs are for the dev companies only: {sorted(issuers)}")
        found.append((q, run))
    return found


# ---------------------------------------------------------------- outputs

def condition_cell(r: SignalResult) -> str:
    return "; ".join(f"{c.condition_id}={c.value:.6g}" for c in r.conditions
                     if c.value is not None)


def signal_rows(companies: Sequence[CompanyInsights]) -> list[list[Any]]:
    return [[c.ticker, f"FY{y}", r.signal_id, r.state, r.reason, r.detail, condition_cell(r)]
            for c in companies for y, results in sorted(c.signals.items()) for r in results]


def card_rows(companies: Sequence[CompanyInsights], low: bool = False) -> list[list[Any]]:
    """One row per card; low (D-EV2): a last column with the gap's low-confidence sentence."""
    rows = []
    for c in companies:
        for y, made in sorted(c.cards.items()):
            for card in made:
                quotes = [f"{q.score:.3f} {q.chunk_id}: {q.text}" for q in card.evidence]
                row = [c.ticker, f"FY{y}", card.signal_id, card.status, card.severity,
                       card.text, *(quotes + ["", ""])[:2], card.detail]
                if low:
                    row.append(card.low_confidence.display() if card.low_confidence else "")
                rows.append(row)
    return rows


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def write_outputs(out: Path, companies: Sequence[CompanyInsights], header: Mapping[str, Any],
                  explains: Sequence[tuple[str, Any]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "signals.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "fiscal_year", "signal", "state", "reason", "detail", "values"])
        w.writerows(signal_rows(companies))
    low = bool(header.get("low_confidence_label"))  # D-EV2: the column only when shown
    with (out / "cards.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "fiscal_year", "signal", "status", "severity", "text", "quote_1",
                    "quote_2", "detail"] + (["low_confidence"] if low else []))
        w.writerows(card_rows(companies, low))
    (out / "cards.json").write_text(json.dumps(_plain({
        "header": header, "companies": [
            {"ticker": c.ticker, "error": c.error, "goal_status": c.goal_status,
             "gate": c.gate, "cards": {y: [asdict(k) for k in v] for y, v in c.cards.items()}}
            for c in companies]}), indent=1), encoding="utf-8")
    lines = []
    for c in companies:
        lines += [f"=== {c.ticker} risk (latest year): {c.goal_status}", c.answer or c.error, ""]
    for q, run in explains:
        lines += [f"=== {q} [{run.status}; compiler {run.compilation.compiler}]", run.answer, ""]
    (out / "answers.txt").write_text("\n".join(lines), encoding="utf-8")


def grid(c: CompanyInsights, ids: Sequence[str]) -> list[str]:
    rows = [[f"FY{y}"] + [SHORT[r.state] for r in results]
            for y, results in sorted(c.signals.items())]
    return _table(["Year", *ids], rows)


def card_section(c: CompanyInsights) -> list[str]:
    year = c.latest
    lines = [f"### {c.ticker} FY{year} (latest 10-K)", ""]
    made = c.cards.get(year or 0, ())
    if not made:
        lines.append("No signal fired.")
    for card in made:
        lines.append(f"- **{card.signal_id} {card.name}** ({card.severity}; {card.status}): "
                     + card.text.split("): ", 1)[1])
        for q in card.evidence:
            lines.append(f"  - Quote ({q.section}, score {q.score:.2f}, `{q.chunk_id}`): "
                         f"\"{q.text}\"")
        if card.status == EVIDENCE_GAP:
            lines.append(f"  - EVIDENCE_GAP ({card.detail})")
            if card.low_confidence is not None:  # D-EV2: shown, never evidence
                lines.append(f"  - {card.low_confidence.display()}")
        elif model_card_made(card):
            lines.append(f"  - Evidence: {card.detail} (#180)")
    others = [r for r in c.signals.get(year or 0, ()) if r.state != FIRED]
    lines += ["", "Not fired or not computable: " + "; ".join(
        f"{r.signal_id} {r.state}" + (f" ({r.reason})" if r.reason else "") for r in others)
        + ".", "", f"Planner answer: {c.goal_status}; gate decisions: "
        + ", ".join(f"{d['metric']} {'accepted' if d['accepted'] else d['code']}"
                    for d in c.gate) + ".", ""]
    return lines


def counts(companies: Sequence[CompanyInsights]) -> list[str]:
    by_state: Counter[str] = Counter()
    cards_by: Counter[str] = Counter()
    for c in companies:
        for results in c.signals.values():
            by_state.update(r.state for r in results)
        for made in c.cards.values():
            cards_by.update(kind(k) for k in made)
    latest: Counter[str] = Counter(kind(k) for c in companies
                                   for k in c.cards.get(c.latest or 0, ()))
    return [f"Signal evaluations (every company, year and signal): "
            + ", ".join(f"{s} {by_state[s]}" for s in STATES) + ".",
            f"Cards (every year): {sum(cards_by.values())}, of which quoted "
            f"{cards_by['quoted']}, EVIDENCE_GAP {cards_by[EVIDENCE_GAP]} and model-derived "
            f"(no filing evidence expected, #180) {cards_by['model']}. Latest year: "
            f"{sum(latest.values())} cards, {latest['quoted']} quoted, "
            f"{latest[EVIDENCE_GAP]} EVIDENCE_GAP, {latest['model']} model-derived."]


def low_confidence_counts(companies: Sequence[CompanyInsights]) -> str:
    """D-EV2: how many EVIDENCE_GAP cards show a low-confidence sentence (statuses unchanged)."""
    gaps = [k for c in companies for made in c.cards.values() for k in made
            if k.status == EVIDENCE_GAP]
    latest = [k for c in companies for k in c.cards.get(c.latest or 0, ())
              if k.status == EVIDENCE_GAP]
    shown = sum(k.low_confidence is not None for k in gaps)
    shown_latest = sum(k.low_confidence is not None for k in latest)
    return (f"Low-confidence sentences (D-EV2; shown on EVIDENCE_GAP cards, never evidence): "
            f"{shown} of {len(gaps)} EVIDENCE_GAP cards (latest year {shown_latest} of "
            f"{len(latest)}). The others have no passage below the threshold with a sentence "
            "that passes the rules.")


def kind(card: Any) -> str:
    """quoted, EVIDENCE_GAP or model (a model-derived card, #180)."""
    if model_card_made(card):
        return "model"
    return "quoted" if card.status == VERIFIED else EVIDENCE_GAP


def explain_section(explains: Sequence[tuple[str, Any]]) -> list[str]:
    lines = ["## Explain answers (dev companies only: AMZN, MSFT; D10)", ""]
    for q, run in explains:
        lines += [f"**{q}** ({run.status}; compiler {run.compilation.compiler})", "", "```",
                  run.answer, "```", ""]
    return lines


def evidence_header(spec: Any) -> dict[str, Any]:
    """The run header's evidence fields (D-EV): version, variant, order and reranker gate."""
    return {"evidence_version": spec.version, "evidence_variant": spec.variant,
            "evidence_order": spec.order, "min_rerank_score": spec.min_rerank_score,
            "low_confidence_label": spec.low_confidence_label}


V1_EVIDENCE = (
    "Evidence: the signal's frozen query, BM25 only (dense retrieval is 7.4), over the text of "
    "Item 7 and Item 8 with its notes of the same 10-K; whole sentences of 6 to 60 "
    "words (longer ones skipped, #173), with an anchor term, not about an earlier year "
    "only; score = share of the query's IDF weight in the sentence; the best two at or "
    "above the threshold are quoted, otherwise the card is EVIDENCE_GAP. The planner's "
    "gate rebuilds every quoted chunk from the pinned document (#157). An R10 card is "
    "model-derived: no search, no quote, labelled 'model-derived; no filing evidence "
    "expected' (#180).")


def evidence_method(header: Mapping[str, Any]) -> str:
    """The Method paragraph on evidence for the run's evidence version (v1 when absent)."""
    if header.get("evidence_version", "v1") == "v1":
        return V1_EVIDENCE
    low = header.get("low_confidence_label")
    shown = "" if not low else (
        f" D-EV2 (Vineet, option b): an EVIDENCE_GAP card also shows, labelled '{low}', the "
        "sentence v2 would have quoted first without the reranker gate: the first sentence, in "
        "the passages' rank order, of a passage below the threshold that passes every sentence "
        "rule and the IDF score threshold. It is not evidence: the card stays EVIDENCE_GAP, "
        "the gate never checks or counts it, and explain answers never use it.")
    return (
        f"Evidence ({header['evidence_version']}, post-hoc, D-EV; docs/evidence_v2.md): the "
        f"signal's frozen query, searched with {header['evidence_variant']} over the text of "
        "Item 7 and Item 8 with its notes of the same 10-K. Only passages whose cross-encoder "
        f"score is at least {header['min_rerank_score']:.3f} may supply a quote. That threshold "
        "was chosen by a rule written before the curve, leave one company out on the E3 labels "
        "(e3-0002). Sentences are taken in the passages' rank order and must be whole sentences "
        "of 6 to 60 words (longer ones skipped, #173), with an anchor term, not about an earlier "
        "year only, with at least the IDF score threshold. The best two are quoted, otherwise "
        "the card is EVIDENCE_GAP. The planner's gate rebuilds every quoted chunk from the "
        "pinned document (#157). An R10 card is model-derived: no search, no quote, labelled "
        "'model-derived; no filing evidence expected' (#180)." + shown)


def render_report(companies: Sequence[CompanyInsights], header: Mapping[str, Any],
                  explains: Sequence[tuple[str, Any]], ids: Sequence[str],
                  check: str | None) -> str:
    title = "# Insights v1: risk signals and insight cards (Phase 7.3)"
    if header.get("evidence_version", "v1") != "v1":
        title = (f"# Insights with evidence {header['evidence_version']} (post-hoc, D-EV): risk "
                 "signals and insight cards")
        if header.get("low_confidence_label"):
            title += ", with low-confidence sentences (D-EV2)"
    lines = [title, "",
             f"Run `{header['run_id']}`; git `{header['git_commit']}`"
             f"{' (dirty)' if header['git_dirty'] else ''}; evidence manifest sha256 "
             f"`{header['evidence_manifest_sha256'][:12]}`; as of {header['as_of']}; "
             f"signals.yaml sha256 `{header['signals_sha256'][:12]}`; query templates sha256 "
             f"`{header['templates_sha256'][:12]}`; quote threshold {header['min_score']} "
             "(provisional, chosen on the dev companies only).", "",
             "## Method", "",
             "Signals: Appendix C.2 thresholds as written (D5), on the validated statement set's "
             "accepted inputs (the credit ratios' inputs); R5 = funded debt / (EBIT + D&A from "
             "the cash flow statement) (D2); R10 on the Moderate scenario from the latest 10-K "
             "(D3); R2 and R9 GAP NO_COGS without a cost-of-sales line (D4); severity R7, R10, "
             "R11 high, the others medium (D5); D&A falls back to depreciation + amortisation "
             "when both are read, else GAP (#179). A missing input is never read as not fired. "
             "Every fired measure is recomputed a second way in Decimal by the risk tool.", "",
             evidence_method(header), "",
             "## Summary", "", *counts(companies),
             *([low_confidence_counts(companies)] if header.get("low_confidence_label")
               else []), ""]
    for c in companies:
        if c.error:
            lines += [f"## {c.ticker}: ERROR", "", "```", c.error.splitlines()[0], "```", ""]
            continue
        lines += [f"## {c.ticker}", "", "Signals per year (**F** fired, - not fired, gap = "
                  "not computable, n/a = not applicable):", "", *grid(c, ids), "",
                  *card_section(c)]
    lines += explain_section(explains)
    if check:
        lines += [f"## Default path ({BASELINE_STATEMENTS}, {BASELINE_FORECAST})", "", "```",
                  *[line for line in check.splitlines() if line.startswith(
                      ("STATEMENTS", "FORECAST", "CHECK", "UNCHANGED", "CHANGED"))], "```",
                  ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from finagent.evaluation.e1 import pinned_tickers
    from finagent.planner.compile import CompilerResources
    from finagent.retrieval.templates import load_query_templates
    from finagent.tools import FinAgentTools

    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    parser.add_argument("--run-id", default="insights-0001")
    parser.add_argument("--report", default=str(ROOT / "docs" / "insights_v1.md"))
    parser.add_argument("--evidence-version", default=None,
                        help="v1 or v2 (D-EV); default: configs/signals.yaml evidence_version")
    args = parser.parse_args(argv)
    tools = FinAgentTools.default()
    if args.evidence_version:
        tools.signal_config = tools.signal_config.with_evidence(args.evidence_version)
    resources = CompilerResources.load()
    companies = []
    for ticker in pinned_tickers(tools.store, tools.universe, "validation", args.as_of):
        try:
            companies.append(company_insights(tools, ticker, args.as_of, resources))
        except Exception as exc:  # reported, never hidden
            companies.append(CompanyInsights(ticker, error=f"{type(exc).__name__}: {exc}\n"
                                                           f"{traceback.format_exc()}"))
    explains = explain_runs(tools, args.as_of, resources)
    templates = load_query_templates()
    header = {
        "run_id": args.run_id, "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "evidence_manifest_sha256": hashlib.sha256(
            (ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest(),
        "as_of": args.as_of.isoformat(),
        "signals_sha256": hashlib.sha256(SIGNALS_PATH.read_bytes()).hexdigest(),
        "templates_sha256": templates.sha256,
        "min_score": tools.signal_config.evidence.min_score,
        "model_version": tools.classifier.version,
        **evidence_header(tools.signal_config.evidence)}
    out = ROOT / "runs" / args.run_id
    write_outputs(out, companies, header, explains)
    check = out / "default_path_check.txt"
    write_report(Path(args.report), render_report(
        companies, header, explains, list(tools.signal_config.signals),
        check.read_text(encoding="utf-8") if check.exists() else None))
    for c in companies:
        print(c.ticker, c.goal_status, c.error.splitlines()[0] if c.error else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
