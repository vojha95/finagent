"""Phase 4a run: validated statement sets for the train and validation companies.

    python -m finagent.statements.report [--as-of 2026-11-24] [--run-id statements-0001]
        [--dataset-version v3] [--splits validation,train] [--no-agent]

Writes runs/{run_id}/statements.json (header: git commit, evidence manifest hash, model version,
as_of), identities.csv and coverage.csv, and docs/statement_set_v1.md. With the Correlation
Agent (Phase 5, on by default; --no-agent reproduces Phase 4a) the statement sets are those
after the agent, and the run also writes agent.json (per filing: actions, CORRECTED / REVIEW
values, trace) and final_check.csv (BC22, per fiscal year). Held-out companies are refused
(9.2). A company that fails to build is reported with its error; the others still run.
"""

import argparse
import csv
import hashlib
import json
import subprocess
import traceback
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import AgentConfig, ForecastNeed, load_agent_config, \
    load_forecast_inputs, load_universe
from finagent.records import IdentityResult, Observation
from finagent.statements.build import StatementConfigs, StatementSet
from finagent.statements.coverage import DISAGREE, PRESENT, Slot, slots, summarise
from finagent.verify.reconcile import FINAL_CHECK_HEADER, Reconciliation, company_statements, \
    final_check_rows, reconciliation_record

ROOT = Path(__file__).resolve().parents[2]
IDENTITY_IDS = tuple(f"I{n}" for n in range(1, 13))
NOTES_MARKER = "<!-- notes: text below this line is kept when the report is regenerated -->"


@dataclass
class RunResult:
    split: str
    sets: dict[str, StatementSet] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    reconciled: dict[str, Reconciliation] = field(default_factory=dict)  # with the agent


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout.strip()


def run_split(store, tickers: Sequence[str], split: str, as_of: date, classifier,
              configs: StatementConfigs, universe, agent: AgentConfig | None = None
              ) -> RunResult:
    """Each company's statement set; after the Correlation Agent when agent is given."""
    result = RunResult(split)
    for ticker in tickers:
        try:
            s, rec = company_statements(store, ticker, as_of, classifier, universe, configs,
                                        agent)
            result.sets[ticker] = s
            if rec is not None:
                result.reconciled[ticker] = rec
        except Exception as exc:  # reported, never hidden: the exit criterion counts them
            result.failures[ticker] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    return result


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def set_record(statement_set: StatementSet, split: str) -> dict[str, Any]:
    return {
        "issuer": statement_set.issuer, "cik": statement_set.cik, "split": split,
        "notes": statement_set.notes,
        "filings": [_json(asdict(f)) for f in statement_set.filings],
        "location_gap_reasons": {str(k): v for k, v in statement_set.gap_reasons.items()},
        "bases": {str(k): v for k, v in statement_set.bases.items()},
        "observations": [_json(asdict(o)) for _, o in sorted(statement_set.observations.items())],
        "identities": [_json(asdict(r)) for r in statement_set.identities],
    }


def write_identities_csv(path: Path, runs: Iterable[RunResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["issuer", "split", "fiscal_year", "accession", "identity_id", "subject",
                         "status", "residual", "tolerance", "missing", "omitted", "provisional",
                         "detail"])
        for run in runs:
            for ticker, statement_set in run.sets.items():
                for r in statement_set.identities:
                    writer.writerow([ticker, run.split, r.fiscal_year, r.accession, r.identity_id,
                                     r.subject or "", r.status, r.residual, r.tolerance,
                                     ";".join(r.missing), ";".join(r.omitted),
                                     ";".join(r.provisional), r.detail])


def write_agent_outputs(out: Path, runs: Iterable[RunResult]) -> None:
    """agent.json (per company and filing: actions, values, trace) and final_check.csv."""
    runs = list(runs)
    companies = [reconciliation_record(rec, r.split) for r in runs
                 for rec in r.reconciled.values()]
    (out / "agent.json").write_text(json.dumps({"companies": companies}, indent=1, default=str),
                                    encoding="utf-8")
    with (out / "final_check.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(FINAL_CHECK_HEADER)
        for r in runs:
            for rec in r.reconciled.values():
                writer.writerows(final_check_rows(rec, r.split))


def write_coverage_csv(path: Path, found: dict[str, tuple[str, list[Slot]]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["issuer", "split", "need", "tier", "fiscal_year", "metric", "outcome",
                         "status", "flags"])
        for ticker, (split, ticker_slots) in found.items():
            for s in ticker_slots:
                writer.writerow([ticker, split, s.need_id, s.tier, s.fiscal_year, s.metric,
                                 s.outcome, s.status or "", ";".join(s.flags)])


# ---------------------------------------------------------------- markdown report


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


def identity_counts(sets: Iterable[StatementSet]) -> dict[str, Counter]:
    counts: dict[str, Counter] = {i: Counter() for i in IDENTITY_IDS}
    for statement_set in sets:
        for r in statement_set.identities:
            counts[r.identity_id][r.status] += 1
    return counts


def _identity_rows(counts: dict[str, Counter]) -> list[list[Any]]:
    return [[i, c["PASS"], c["FAIL"], c["REVIEW"], c["NOT_COMPUTABLE"], sum(c.values())]
            for i, c in counts.items()]


def ebit_bases(sets: Iterable[StatementSet]) -> dict[str, list[tuple[int, str | None]]]:
    """Per issuer, the fiscal years whose EBIT is not a presented operating income (#68):
    (year, chosen basis), the basis being ebit_proxy or None (no EBIT)."""
    found: dict[str, list[tuple[int, str | None]]] = {}
    for s in sets:
        years = [(y, b.get("ebit_basis")) for y, b in sorted(s.bases.items())
                 if b.get("ebit_basis") != "operating_income"]
        if years:
            found[s.issuer] = years
    return found


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def compare_runs(before_dir: Path, coverage_after: Iterable[dict[str, str]],
                 identities_after: Iterable[dict[str, str]]) -> list[str]:
    """Markdown 'before vs after' tables from a previous run's coverage.csv and identities.csv."""
    before_cov = _read_csv(before_dir / "coverage.csv")
    before_ids = _read_csv(before_dir / "identities.csv")
    coverage_after, identities_after = list(coverage_after), list(identities_after)
    lines = [f"Previous run: `{before_dir.name}`. Required-input slots per validation company "
             "(present / slots) and DISAGREE slots; identity status counts.", ""]

    def per_company(rows):
        out: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            if r["split"] == "validation" and r["tier"] == "required":
                out[r["issuer"]]["slots"] += 1
                out[r["issuer"]][r["outcome"]] += 1
        return out

    old, new = per_company(before_cov), per_company(coverage_after)
    rows = [[t, f"{old[t]['PRESENT']}/{old[t]['slots']}", f"{new[t]['PRESENT']}/{new[t]['slots']}",
             old[t][DISAGREE], new[t][DISAGREE]] for t in new]
    lines += _table(["company", "present before", "present after", "DISAGREE before",
                     "DISAGREE after"], rows) + [""]
    for split in ("validation", "train"):
        dis = [sum(1 for r in rows_ if r["split"] == split and r["outcome"] == DISAGREE)
               for rows_ in (before_cov, coverage_after)]
        lines += [f"DISAGREE slots, {split}: {dis[0]} -> {dis[1]}."]
    lines += [""]
    for split in ("validation", "train"):
        counts = [defaultdict(Counter), defaultdict(Counter)]
        for k, rows_ in enumerate((before_ids, identities_after)):
            for r in rows_:
                if r["split"] == split:
                    counts[k][r["identity_id"]][r["status"]] += 1
        table = []
        for i in IDENTITY_IDS:
            b, a = counts[0][i], counts[1][i]
            if b != a:
                fmt = lambda c: "/".join(str(c[s]) for s in ("PASS", "FAIL", "REVIEW",
                                                              "NOT_COMPUTABLE"))
                table.append([i, fmt(b), fmt(a)])
        lines += [f"Identities that changed, {split} (PASS/FAIL/REVIEW/NOT_COMPUTABLE):", ""]
        lines += (_table(["identity", "before", "after"], table) if table else ["None."]) + [""]
    return lines


def _observations(sets: Iterable[StatementSet]) -> list[Observation]:
    return [o for s in sets for o in s.observations.values()]


def _source_ok(o: Observation) -> bool:
    """A value carries its provenance: accession and URL, plus a concept, a row or inputs."""
    if not (o.status and o.accession and o.filing_url):
        return False
    return o.value is None or bool(o.concepts or o.row_id or o.inputs)


def coverage_rows(found: dict[str, tuple[str, list[Slot]]], tier: str) -> list[list[Any]]:
    rows = []
    for ticker, (_, ticker_slots) in found.items():
        s = summarise(ticker_slots, tier)
        other = ", ".join(f"{k} {v}" for k, v in s.other_gaps.items()) or "—"
        rows.append([ticker, s.slots, s.present, f"{s.share_present:.3f}",
                     f"{s.share_present_where_reported:.3f}", s.disagree, s.not_reported, other])
    return rows


def render_report(runs: Sequence[RunResult], header: dict[str, Any], needs: Sequence[ForecastNeed],
                  found: dict[str, tuple[str, list[Slot]]],
                  comparison: Sequence[str] = ()) -> str:
    by_split = {r.split: r for r in runs}
    validation = by_split.get("validation", RunResult("validation"))
    train = by_split.get("train", RunResult("train"))
    all_sets = [s for r in runs for s in r.sets.values()]
    lines = [
        "# Validated statement set v1 (Phase 4a)", "",
        f"Generated by `python -m finagent.statements.report` (run `{header['run_id']}`, commit "
        f"`{header['git_commit']}`{' dirty' if header['git_dirty'] else ''}, evidence manifest "
        f"`{header['evidence_manifest_sha256'][:12]}`, model `{header['model_version']}`, "
        f"as of {header['as_of']}). Outputs: `runs/{header['run_id']}/statements.json`, "
        "`identities.csv`, `coverage.csv`. Blueprint 8.7, 8.9, Appendix A.2, Appendix B; "
        "configs `model_inputs.yaml`, `derivations.yaml`, `identities.yaml`, "
        "`forecast_inputs.yaml`.", "",
        "Train companies' document readings are **in-sample for M1** (it was trained on their "
        "rows); only validation results measure the pipeline. IBM (BC10) has no located "
        "statements, so its values are tag-only.", "",
        ("Correlation Agent (Phase 5): **on**. The statement sets below are after the agent; "
         "`agent.json` and `final_check.csv` hold its actions, values, traces and the final "
         "check (section 9)." if header.get("correlation_agent") else
         "Correlation Agent: off (`--no-agent`); the Phase 4a build."), "",
        "## 1. Exit check", "",
    ]
    ran = {r.split: (len(r.sets), len(r.sets) + len(r.failures)) for r in runs}
    observations = _observations(all_sets)
    missing_source = [o for o in observations if not _source_ok(o)]
    lines += _table(["split", "companies run end to end", "failures"],
                    [[split, f"{done}/{total}", ", ".join(by_split[split].failures) or "—"]
                     for split, (done, total) in ran.items()])
    lines += ["", f"Values: {len(observations)} observations; every one has a status; "
              f"{len(observations) - len(missing_source)} carry their source (accession and URL, "
              f"plus a concept, row or inputs for a value); {len(missing_source)} do not.", ""]

    lines += ["## 2. Forecast-input coverage, validation companies", "",
              "Slots = (need, fiscal year) from `configs/forecast_inputs.yaml`, counted back from "
              "each company's latest fiscal year. Present = an accepted value. DISAGREE (no "
              "accepted value until the Correlation Agent, Phase 5) and NOT_REPORTED (the filer "
              "never reports the item; never 0) are counted apart from the other gaps. "
              "'Where reported' leaves NOT_REPORTED slots out of the denominator.", "",
              "### Required inputs", ""]
    val_found = {t: v for t, v in found.items() if v[0] == "validation"}
    header_row = ["company", "slots", "present", "share", "share where reported", "DISAGREE",
                  "NOT_REPORTED", "other gaps"]
    lines += _table(header_row, coverage_rows(val_found, "required"))
    lines += ["", "### Optional inputs", ""]
    lines += _table(header_row, coverage_rows(val_found, "optional"))
    lines += ["", "### Required inputs not present (validation)", ""]
    gap_rows = [[t, s.fiscal_year, s.need_id, s.metric, s.outcome]
                for t, (_, ss) in val_found.items() for s in ss
                if s.tier == "required" and s.outcome != PRESENT]
    lines += _table(["company", "FY", "need", "metric", "outcome"], gap_rows) if gap_rows \
        else ["None."]

    lines += ["", "## 3. DISAGREE on forecast inputs", ""]
    for split_name, run in (("validation", validation), ("train", train)):
        disagree = [[t, s.fiscal_year, s.need_id, s.tier, _disagree_detail(run.sets[t], s)]
                    for t, (sp, ss) in found.items() if sp == split_name for s in ss
                    if s.outcome == DISAGREE]
        latest = sum(1 for t, (sp, ss) in found.items() if sp == split_name for s in ss
                     if s.outcome == DISAGREE and s.fiscal_year == max(run.sets[t].years()))
        lines += [f"**{split_name}**: {len(disagree)} DISAGREE slots ({latest} in the latest "
                  "fiscal year).", ""]
        if split_name == "validation" and disagree:
            lines += _table(["company", "FY", "need", "tier", "document vs tag"], disagree) + [""]

    lines += ["## 4. Identities I1-I12", "",
              "PASS / FAIL / REVIEW / NOT_COMPUTABLE per filing (I8 per filing and item; I12 per "
              "check). I11 is computed only when distributions are disclosed, and a failure is "
              "REVIEW with its residual (blocks nothing). Failures are reported only; the "
              "Correlation Agent is Phase 5.", ""]
    for split_name, run in (("validation", validation), ("train", train)):
        lines += [f"### {split_name}", ""]
        lines += _table(["identity", "PASS", "FAIL", "REVIEW", "NOT_COMPUTABLE", "results"],
                        _identity_rows(identity_counts(run.sets.values()))) + [""]
    lines += ["### Validation: failing and REVIEW results (I8 shown as counts per company)", ""]
    fails = [[r.issuer, r.fiscal_year, r.identity_id, r.status, r.subject or "", r.residual,
              r.tolerance]
             for s in validation.sets.values() for r in s.identities
             if r.status in ("FAIL", "REVIEW") and r.identity_id != "I8"]
    lines += _table(["company", "FY", "identity", "status", "subject", "residual", "tolerance"],
                    fails) if fails else ["None."]
    i8 = Counter((r.issuer, r.fiscal_year) for s in validation.sets.values()
                 for r in s.identities if r.identity_id == "I8" and r.status == "FAIL")
    lines += ["", "I8 failures (filing t's prior-year column vs filing t-1): "
              + (", ".join(f"{t} FY{y}: {n}" for (t, y), n in sorted(i8.items())) or "none") + ".",
              ""]

    lines += ["## 5. Values by status and reason", ""]
    for split_name, run in (("validation", validation), ("train", train)):
        counts = Counter((o.status, o.reason, o.accepted) for o in _observations(run.sets.values()))
        lines += [f"### {split_name}", ""]
        lines += _table(["status", "reason", "accepted", "values"],
                        [[s, r, a, n] for (s, r, a), n in sorted(counts.items(),
                                                                 key=lambda x: -x[1])]) + [""]

    lines += ["## 6. Flags and rules applied", ""]
    flag_counts = Counter()
    by_flag: dict[str, set[str]] = defaultdict(set)
    for o in observations:
        for flag in o.flags:
            key = flag.split(":")[0] if flag.startswith(("OMITTED", "SUBSTITUTE")) else flag
            flag_counts[key] += 1
            by_flag[key].add(o.issuer)
    lines += _table(["flag", "values", "companies"],
                    [[f, n, ", ".join(sorted(by_flag[f]))] for f, n in sorted(flag_counts.items())])
    substitutes = sorted({o.issuer for o in observations if o.metric == "depreciation"
                          and o.basis == "D&A"})
    sub_years = Counter(o.issuer for o in observations if o.metric == "depreciation"
                        and o.basis == "D&A")
    lines += ["", "**Depreciation basis D&A** (answer 7: depreciation alone not tagged, D&A "
              "used): " + (", ".join(f"{t} ({sub_years[t]} years)" for t in substitutes)
                           or "none") + ".", ""]
    checks = Counter(o.display_check for o in observations if o.display_check)
    lines += ["**display_check** on tag-only A.2 facts (answer 1): "
              + ", ".join(f"{k} {v}" for k, v in sorted(checks.items())) + ".", ""]
    bases = Counter(v.get("pretax_basis") for s in all_sets for v in s.bases.values())
    lines += ["**Pre-tax basis** chosen per filing: "
              + ", ".join(f"{k} {v}" for k, v in sorted(bases.items(), key=str)) + ".", ""]
    proxies = ebit_bases(all_sets)
    lines += ["**EBIT basis** (#68): years without a presented operating income, with the basis "
              "used (`ebit_proxy` = pre-tax basis + interest expense, flagged EBIT_PROXY; `None` = "
              "no EBIT): " + ("; ".join(f"{t} " + ", ".join(f"FY{y} {b}" for y, b in ys)
                                        for t, ys in sorted(proxies.items())) or "none") + ".",
              ""]
    bc15 = [f"{o.issuer} FY{o.fiscal_year} {o.metric}" for o in observations
            if o.reason == "AGREE_WITH_ONE_CANDIDATE"]
    lines += [f"**BC15** (document equals one of several tag values): {len(bc15)} values"
              + (f": {', '.join(bc15)}" if bc15 else "") + ".", ""]

    lines += ["## 7. Train companies (summary)", ""]
    train_found = {t: v for t, v in found.items() if v[0] == "train"}
    lines += _table(header_row, coverage_rows(train_found, "required"))
    lines += [""]
    if comparison:
        lines += ["## 8. Before vs after", ""] + list(comparison)
    if any(r.reconciled for r in runs):
        lines += agent_section(validation, train)
    return "\n".join(lines) + "\n"


def _action_text(a) -> str:
    how = f" -> row {a.row_id.rsplit(':', 2)[-2]}:{a.row_id.rsplit(':', 1)[-1]}" \
        if a.row_id else (f" x{a.factor}" if a.factor is not None else "")
    return f"{a.tool} {a.item}{how}"


def _m(value) -> str:
    return "—" if value is None else f"{value / 10 ** 6:,.0f}"


def agent_rows(rec: Reconciliation) -> list[list[Any]]:
    """Per filing: failing checks before, actions, outcome, values, failing checks after."""
    rows = []
    for r in rec.results:
        before = [c.check_id for c in r.checks_before if c.status == "FAIL"]
        after = [c.check_id for c in r.checks_after if c.status == "FAIL"]
        values = [f"{v.status} {v.item} {_m(v.original_value)} -> {_m(v.final_value)}"
                  if v.status == "CORRECTED" else f"REVIEW {v.item}" for v in r.values]
        rows.append([rec.issuer, r.fiscal_year, ", ".join(before) or "—",
                     "; ".join(_action_text(a) for a in r.actions) or "—", r.stop_reason,
                     "; ".join(values) or "—", ", ".join(after) or "—"])
    return rows


def agent_section(validation: RunResult, train: RunResult) -> list[str]:
    lines = ["## 9. Correlation Agent and final check (Phase 5)", "",
             "Product mode (tags visible), blueprint 8.8; `configs/correlation_agent.yaml`. Per "
             "filing, its own fiscal year: the agent's checks that fail on the readings "
             "(DISAGREE values enter with their document value; `D:<item>` = document vs tag), "
             "the actions applied, why it stopped and the CORRECTED / REVIEW values ($ "
             "millions). The statement set is then rebuilt with the corrections (8.7 again: a "
             "corrected value that agrees with its tag is VERIFIED, flagged CORRECTED, original "
             "reading kept). Final check (BC22): I9 and I10 recomputed on the final values must "
             "be exactly zero; I11 only when computable, REVIEW if non-zero (non-blocking).", "",
             "### Agent, validation filings", ""]
    rows = [row for rec in validation.reconciled.values() for row in agent_rows(rec)]
    lines += _table(["company", "FY", "failing before", "actions", "stop", "values",
                     "failing after"], rows) + [""]
    for name, run in (("validation", validation), ("train", train)):
        results = [r for rec in run.reconciled.values() for r in rec.results]
        values = Counter(v.status for r in results for v in r.values)
        tools = Counter(a.tool for r in results for a in r.actions)
        stops = Counter(r.stop_reason for r in results)
        lines += [f"**{name}**: {len(results)} filings; {sum(1 for r in results if r.actions)} "
                  f"with an action; actions {dict(tools) or '{}'}; values "
                  f"{dict(values) or '{}'}; stop reasons {dict(stops)}; most actions on one "
                  f"filing {max((r.actions_used for r in results), default=0)}.", ""]
    lines += ["### Final check, validation", ""]
    head = ["company", "FY", "origin", "status", "I9", "I10", "I11", "reason / review"]
    rows = []
    for rec in validation.reconciled.values():
        for row in final_check_rows(rec, "validation"):
            (_, _, year, origin, status, reason, i9, r9, i10, r10, i11, r11, review) = row
            rows.append([rec.issuer, year, "yes" if origin else "", status,
                         f"{i9} {r9}".strip(), f"{i10} {r10}".strip(), f"{i11} {r11}".strip(),
                         reason + (f"; review: {review}" if review else "")])
    lines += _table(head, rows) + [""]
    for name, run in (("validation", validation), ("train", train)):
        all_years = Counter(c.status for rec in run.reconciled.values()
                            for c in rec.checks.values())
        origin = Counter(rec.checks[max(rec.checks)].status for rec in run.reconciled.values()
                         if rec.checks)
        lines += [f"**{name}** final check: all years {dict(all_years)}; origin year "
                  f"{dict(origin)}.", ""]
    return lines


def _disagree_detail(statement_set: StatementSet, slot: Slot) -> str:
    o = statement_set.get(slot.fiscal_year, slot.metric)
    return f"{o.document_value} vs {o.tag_value}" if o else ""


def write_report(path: Path, text: str) -> None:
    """Write the generated report, keeping any notes after NOTES_MARKER in the old file."""
    notes = ""
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if NOTES_MARKER in old:
            notes = old.split(NOTES_MARKER, 1)[1]
    path.write_text(text + "\n" + NOTES_MARKER + notes, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    from finagent.evaluation.e1 import pinned_tickers
    from finagent.evidence import EvidenceStore
    from finagent.training.m1 import load_model

    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    parser.add_argument("--run-id", default="statements-0001")
    parser.add_argument("--dataset-version", default="v3")
    parser.add_argument("--splits", default="validation,train")
    parser.add_argument("--report", default=str(ROOT / "docs" / "statement_set_v1.md"))
    parser.add_argument("--compare-run", default=None,
                        help="a previous run id under runs/ for the before-vs-after section")
    parser.add_argument("--no-agent", action="store_true",
                        help="skip the Correlation Agent (reproduces the Phase 4a build)")
    args = parser.parse_args(argv)

    store = EvidenceStore(ROOT / "evidence")
    universe = load_universe()
    classifier = load_model(ROOT / "models", args.dataset_version,
                            ROOT / "datasets" / f"rows_{args.dataset_version}.meta.json")
    configs = StatementConfigs.load()
    needs = load_forecast_inputs()
    agent = None if args.no_agent else load_agent_config()
    runs = []
    for split in args.splits.split(","):
        tickers = pinned_tickers(store, universe, split, args.as_of)  # refuses held_out
        runs.append(run_split(store, tickers, split, args.as_of, classifier, configs, universe,
                              agent))

    header = {
        "run_id": args.run_id, "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "evidence_manifest_sha256": hashlib.sha256(
            (ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest(),
        "model_version": classifier.version, "dataset_version": args.dataset_version,
        "as_of": args.as_of.isoformat(), "correlation_agent": None if agent is None
        else json.loads(json.dumps(asdict(agent), default=str)),
    }
    out = ROOT / "runs" / args.run_id
    out.mkdir(parents=True, exist_ok=True)
    found = {t: (r.split, slots(s, needs, configs.derivations.bases))
             for r in runs for t, s in r.sets.items()}
    document = {"header": header, "failures": {t: e for r in runs for t, e in r.failures.items()},
                "companies": [set_record(s, r.split) for r in runs for s in r.sets.values()]}
    (out / "statements.json").write_text(json.dumps(document, indent=1, default=str),
                                         encoding="utf-8")
    write_identities_csv(out / "identities.csv", runs)
    write_coverage_csv(out / "coverage.csv", found)
    if agent is not None:
        write_agent_outputs(out, runs)
    comparison = compare_runs(ROOT / "runs" / args.compare_run,
                              _read_csv(out / "coverage.csv"),
                              _read_csv(out / "identities.csv")) if args.compare_run else ()
    write_report(Path(args.report), render_report(runs, header, needs, found, comparison))
    for r in runs:
        print(f"{r.split}: {len(r.sets)} built, {len(r.failures)} failed {list(r.failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
