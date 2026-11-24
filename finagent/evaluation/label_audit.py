"""Label quality audit (blueprint 9.5): every row where M1 disagrees with its tag-derived label.

The pool is every row of one split (validation) whose M1 prediction differs from the label (OTHER
included on either side). Rows are grouped into audit units: one unit per row pattern (company,
statement, label, label->prediction pair), judged once and applied to all of that pattern's rows
(the same row in each fiscal year). A row whose tag check differs from the rest of its pattern
(e.g. the tag misaligned in one year only) is a unit of its own.

The tag check compares the row's tagged non-dimensional facts at its latest dated column with the
amount shown there (in magnitude, at the displayed precision): AGREE (some fact equals it; a label
may carry its own tagged figures), AGREE_AS_SHOWN (equal only without the table's unit scale: a
per-share or count row), DIFFER, NO_FACT (no fact tagged in that row for that period) or
NO_AMOUNT (the cell shows no readable amount).

The hand judgement (class, reason, pattern group, proposed resolution, reviewer) lives in a
tracked CSV, never in code. Usage:
    python -m finagent.evaluation.label_audit --version v2 --units    # writes eval/label_audit_v2.csv
    python -m finagent.evaluation.label_audit --version v2 --report   # writes docs/label_audit_v2.md
Held-out companies are never read (blueprint 9.2).

Lives in evaluation, not document: the tag check reads the tag store (rule 7).
"""

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from finagent.config import HELD_OUT, HeldOutError, load_universe
from finagent.document.normalize import normalise_cell
from finagent.ixbrl import TagFact
from finagent.records import Prediction, RowInstance
from finagent.verify.dual_channel import within_precision

ROOT = Path(__file__).resolve().parents[2]

CLASSES = ("MODEL_ERROR", "LABEL_ERROR", "TAXONOMY_AMBIGUITY", "NEEDS_FINANCE_REVIEW")
DECISION_CLASSES = ("TAXONOMY_AMBIGUITY", "NEEDS_FINANCE_REVIEW")
AGREE, AGREE_AS_SHOWN, DIFFER, NO_FACT, NO_AMOUNT = (
    "AGREE", "AGREE_AS_SHOWN", "DIFFER", "NO_FACT", "NO_AMOUNT"
)
PATTERN = ["issuer", "statement", "row_text", "label", "pred_m1"]
JUDGEMENT = ["class", "reason", "pattern_group", "proposed_resolution", "reviewer"]
SORT = ["issuer", "statement", "position", "row_text", "label", "pred_m1", "first_year"]


@dataclass(frozen=True)
class TagCheck:
    status: str  # AGREE / AGREE_AS_SHOWN / DIFFER / NO_FACT / NO_AMOUNT
    period: date | None
    shown: str | None  # the cell text at the latest dated column
    tag_values: tuple[str, ...]  # concept=value of the facts compared


def _latest_column(row: RowInstance) -> int | None:
    dated = [c for c, p in enumerate(row.column_periods) if p is not None and row.is_amount_column(c)]
    return max(dated, key=lambda c: row.column_periods[c]) if dated else None


def tag_check(row: RowInstance, facts: Iterable[TagFact]) -> TagCheck:
    """Tagged facts in the row's cells at its latest dated column vs the amount shown there."""
    column = _latest_column(row)
    if column is None:
        return TagCheck(NO_AMOUNT, None, None, ())
    period = row.column_periods[column]
    shown = row.cell_texts[column]
    in_row = [
        f for f in facts
        if f.cell_address is not None and f.cell_address.table_index == row.table_index
        and f.cell_address.row_index == row.source_row and not f.dimensions
        and f.amount is not None and f.end == period
    ]
    values = tuple(sorted(f"{f.concept}={f.amount}" for f in in_row))
    value = normalise_cell(row, column)
    if value.value is None:
        return TagCheck(NO_AMOUNT, period, shown, values)
    if not in_row:
        return TagCheck(NO_FACT, period, shown, values)
    # Any match counts: a label may carry its own tagged figures ("less allowances: $442").
    if any(within_precision(abs(value.value), value.scale, abs(f.amount)) for f in in_row):
        return TagCheck(AGREE, period, shown, values)
    shown_figure = abs(value.value).scaleb(-value.scale)
    if value.scale and any(within_precision(shown_figure, 0, abs(f.amount)) for f in in_row):
        return TagCheck(AGREE_AS_SHOWN, period, shown, values)  # per-share or count row
    return TagCheck(DIFFER, period, shown, values)


def top3(prediction: Prediction) -> str:
    return "; ".join(f"{item} {p:.2f}" for item, p in prediction.candidates[:3])


def disagreement_pool(
    rows: pd.DataFrame,
    instances: Sequence[RowInstance],
    m1: Sequence[Prediction],
    m0: Sequence[Prediction],
    checks: Mapping[str, TagCheck],
) -> pd.DataFrame:
    """One record per row whose M1 prediction differs from its label.

    rows: dataset records (issuer, fiscal_year, statement, row_text, parent_header, position,
    item, label_concepts, row_id), aligned with instances, m1 and m0. checks: row_id -> TagCheck.
    """
    if not len(rows) == len(instances) == len(m1) == len(m0):
        raise ValueError("rows, instances and predictions must be aligned")
    records = []
    for record, row, p1, p0 in zip(rows.to_dict("records"), instances, m1, m0):
        if p1.item == record["item"]:
            continue
        check = checks.get(row.row_id, TagCheck(NO_FACT, None, None, ()))
        records.append({
            "row_id": row.row_id, "issuer": record["issuer"],
            "fiscal_year": int(record["fiscal_year"]), "statement": record["statement"],
            "position": int(record["position"]), "parent_header": row.parent_header or "",
            "row_text": record["row_text"] or "", "label": record["item"],
            "label_reason": record["label_reason"],
            "concepts": ", ".join(record["label_concepts"]), "pred_m1": p1.item,
            "m1_top3": top3(p1), "pred_m0": p0.item, "tag_check": check.status,
            "period": check.period.isoformat() if check.period else "",
            "shown": check.shown or "", "tag_values": "; ".join(check.tag_values),
        })
    return pd.DataFrame.from_records(records)


def _unit_record(rows: pd.DataFrame) -> dict[str, Any]:
    latest = rows.sort_values("fiscal_year").iloc[-1]
    record = {c: latest[c] for c in (
        "issuer", "statement", "position", "parent_header", "row_text", "label", "label_reason",
        "concepts",
        "pred_m1", "m1_top3", "pred_m0", "tag_check", "period", "shown", "tag_values")}
    years = sorted(int(y) for y in rows["fiscal_year"])
    record.update({"first_year": years[0], "fiscal_years": " ".join(map(str, years)),
                   "n_rows": len(rows), "row_ids": " ".join(sorted(rows["row_id"]))})
    return record


def audit_units(pool: pd.DataFrame) -> pd.DataFrame:
    """Audit units in a fixed order, with empty judgement columns.

    One unit per pattern for the rows sharing the pattern's most common tag-check status (ties:
    alphabetical); every other row of the pattern is a unit of its own.
    """
    units = []
    for _, rows in pool.groupby(PATTERN, sort=False, dropna=False):
        counts = rows["tag_check"].value_counts()
        majority = sorted(counts[counts == counts.max()].index)[0]
        units.append(_unit_record(rows[rows["tag_check"] == majority]))
        for _, single in rows[rows["tag_check"] != majority].iterrows():
            units.append(_unit_record(single.to_frame().T))
    frame = pd.DataFrame.from_records(units).sort_values(SORT, kind="mergesort")
    frame.insert(0, "unit_id", [f"U{i:03d}" for i in range(1, len(frame) + 1)])
    for column in JUDGEMENT:
        frame[column] = ""
    return frame.drop(columns=["first_year"]).reset_index(drop=True)


def has_judgements(path: Path) -> bool:
    if not path.exists():
        return False
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    return any(frame.get(c, pd.Series(dtype=str)).str.strip().ne("").any() for c in JUDGEMENT)


def write_units(units: pd.DataFrame, path: Path) -> None:
    """Write the unit CSV; refuses to overwrite one that already carries judgements."""
    if has_judgements(path):
        raise FileExistsError(f"{path} already has judgements; move it aside to regenerate")
    units.to_csv(path, index=False)


def validate_judgements(units: pd.DataFrame) -> list[str]:
    """Problems with the hand judgements: unknown class, missing reason."""
    problems = []
    for record in units.to_dict("records"):
        if record["class"] not in CLASSES:
            problems.append(f"{record['unit_id']}: class {record['class']!r} not in {CLASSES}")
        elif not str(record["reason"]).strip():
            problems.append(f"{record['unit_id']}: no reason")
    return problems


def judged_rows(units: pd.DataFrame) -> pd.DataFrame:
    """One record per pool row with its unit's judgement (row_ids expanded)."""
    expanded = units.assign(row_id=units["row_ids"].str.split()).explode("row_id")
    return expanded.drop(columns=["row_ids"]).reset_index(drop=True)


def class_counts(units: pd.DataFrame) -> pd.DataFrame:
    rows = judged_rows(units)
    table = pd.DataFrame({
        "units": units["class"].value_counts(),
        "rows": rows["class"].value_counts(),
    }).reindex(list(CLASSES)).fillna(0).astype(int)
    table.loc["total"] = table.sum()
    table["share_of_rows"] = table["rows"] / table.loc["total", "rows"]
    return table


def label_error_rate(units: pd.DataFrame, split_rows: int) -> dict[str, Any]:
    """Exact label-error count over the pool and over all rows of the split.

    The split share bounds achievable row accuracy from the label side; label errors the model
    agrees with are not in the pool, so it is a lower bound on the split's label-error rate.
    NEEDS_FINANCE_REVIEW rows are reported separately as the most they could add.
    """
    rows = judged_rows(units)
    errors = int((rows["class"] == "LABEL_ERROR").sum())
    review = int((rows["class"] == "NEEDS_FINANCE_REVIEW").sum())
    pool = len(rows)
    return {
        "label_errors": errors, "pool_rows": pool, "split_rows": split_rows,
        "share_of_pool": errors / pool if pool else None,
        "share_of_split": errors / split_rows if split_rows else None,
        "needs_review_rows": review,
        "share_of_split_if_review_were_label_errors":
            (errors + review) / split_rows if split_rows else None,
    }


def breakdown(units: pd.DataFrame, by: str) -> pd.DataFrame:
    """Rows per class by `by` (issuer or label), pool rows as the total column."""
    rows = judged_rows(units)
    table = pd.crosstab(rows[by], rows["class"]).reindex(columns=list(CLASSES), fill_value=0)
    table["rows"] = table.sum(axis=1)
    return table.sort_values(["rows"], ascending=False, kind="mergesort")


def decision_groups(units: pd.DataFrame) -> pd.DataFrame:
    """TAXONOMY_AMBIGUITY and NEEDS_FINANCE_REVIEW units grouped by pattern_group."""
    subset = units[units["class"].isin(DECISION_CLASSES)].copy()
    subset["pattern_group"] = subset["pattern_group"].replace("", "(ungrouped)")
    groups = []
    for name, group in subset.groupby("pattern_group", sort=True):
        resolution = next((r for r in group["proposed_resolution"] if str(r).strip()), "")
        groups.append({
            "pattern_group": name, "classes": ", ".join(sorted(set(group["class"]))),
            "units": ", ".join(group["unit_id"]), "rows": int(group["n_rows"].sum()),
            "companies": ", ".join(sorted(set(group["issuer"]))),
            "example": f"{group.iloc[0]['issuer']} {group.iloc[0]['statement']} "
                       f"\"{group.iloc[0]['row_text']}\": {group.iloc[0]['label']} -> "
                       f"{group.iloc[0]['pred_m1']}",
            "proposed_resolution": resolution,
        })
    return pd.DataFrame.from_records(groups)


def _cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown_table(frame: pd.DataFrame, index: bool = False) -> str:
    frame = frame.reset_index() if index else frame
    head = "| " + " | ".join(map(str, frame.columns)) + " |"
    rule = "|" + "---|" * len(frame.columns)
    body = ["| " + " | ".join(_cell(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([head, rule, *body])


def render_report(
    units: pd.DataFrame, split_rows: int, tag_channel_table: str, context: Mapping[str, str]
) -> str:
    """docs/label_audit_{version}.md from the judged units."""
    rate = label_error_rate(units, split_rows)
    counts = class_counts(units)
    full = units[["unit_id", "issuer", "statement", "parent_header", "row_text", "shown", "label",
                  "label_reason", "concepts", "m1_top3", "pred_m0", "tag_check", "tag_values", "fiscal_years",
                  "n_rows", "class", "reason", "reviewer"]]
    parts = [
        f"# Label quality audit, dataset {context['version']} (blueprint 9.5)",
        "",
        context.get("preamble", ""),
        "",
        "## Counts per class",
        "",
        markdown_table(counts, index=True),
        "",
        "## Label-error rate",
        "",
        f"- Label errors: **{rate['label_errors']}** rows of the {rate['pool_rows']} "
        f"disagreements (**{rate['share_of_pool']:.3f}**), an exact count (every disagreement "
        "judged).",
        f"- Over all {rate['split_rows']} validation rows: **{rate['share_of_split']:.4f}**. "
        "This is the share of rows whose label is wrong where the model disagrees; it caps "
        "row accuracy measured against these labels from the label side.",
        f"- If every NEEDS_FINANCE_REVIEW row ({rate['needs_review_rows']}) turned out to be a "
        f"label error: {rate['share_of_split_if_review_were_label_errors']:.4f}.",
        "- **Caveat:** label errors the model agrees with are not in the pool and are not "
        "measured, so the split rate is a lower bound.",
        "",
        "Label errors:",
        "",
        markdown_table(units.loc[units["class"] == "LABEL_ERROR",
                                 ["unit_id", "issuer", "statement", "row_text", "concepts",
                                  "fiscal_years", "n_rows", "reason", "proposed_resolution"]]),
        "",
        "## Breakdown by company (rows)",
        "",
        markdown_table(breakdown(units, "issuer"), index=True),
        "",
        "## Breakdown by labelled item (rows)",
        "",
        markdown_table(breakdown(units, "label"), index=True),
        "",
        "## Company Facts vs inline XBRL disagreement (from the dataset report)",
        "",
        tag_channel_table,
        "",
        "## For decision: taxonomy ambiguity and finance review",
        "",
        "Grouped by pattern; one proposed resolution each (map, merge items, separate item, leave "
        "OTHER). Nothing has been changed.",
        "",
        markdown_table(decision_groups(units)),
        "",
        "## Full table (one row per audit unit)",
        "",
        "`shown` and `tag_values` are for the latest year of the unit; `n_rows` is the number of "
        "pool rows (fiscal years) the judgement applies to. `reviewer` is for Vineet.",
        "",
        markdown_table(full),
        "",
    ]
    return "\n".join(parts)


# ---------- I/O: the real dataset ----------

def load_pool(version: str, split: str = "validation") -> tuple[pd.DataFrame, int]:
    """Disagreement pool for one split of rows_{version} and the split's row count."""
    import sys

    from finagent.evidence.integrity import read_snapshot
    from finagent.evidence.store import EvidenceStore
    from finagent.ixbrl import build_tag_store
    from finagent.training.m1 import load_rows

    if split == HELD_OUT:
        raise HeldOutError("the label audit never reads held-out companies")
    path = ROOT / "datasets" / f"rows_{version}.parquet"
    frame = pd.read_parquet(path)
    frame = frame[(frame["split"] == split) & frame["item"].notna()].reset_index(drop=True)
    universe = load_universe()
    held = sorted({i for i in frame["issuer"] if universe.get(i) == HELD_OUT})
    if held:
        raise HeldOutError(f"held-out companies in the {split} rows: {held}")
    instances, _ = load_rows(path, split)
    sys.path.insert(0, str(ROOT / "eval"))
    from e1_document import load_classifier  # noqa: E402

    m1 = load_classifier("m1", version).predict(instances)
    m0 = load_classifier("m0", version).predict(instances)
    store = EvidenceStore(ROOT / "evidence")
    checks: dict[str, TagCheck] = {}
    by_accession: dict[str, list[RowInstance]] = {}
    for row, p, item in zip(instances, m1, frame["item"]):
        if p.item != item:
            by_accession.setdefault(row.accession, []).append(row)
    for issuer in sorted(set(frame["issuer"])):
        for record in store.load_filings(issuer, date.fromisoformat(_as_of(version))):
            rows = by_accession.get(record.accession)
            if not rows:
                continue
            document = read_snapshot(store.evidence_dir, record.primary_document, store.manifest)
            facts = build_tag_store(document, record.accession)
            for row in rows:
                checks[row.row_id] = tag_check(row, facts)
    return disagreement_pool(frame, instances, m1, m0, checks), len(frame)


def _as_of(version: str) -> str:
    import json

    meta = json.loads((ROOT / "datasets" / f"rows_{version}.meta.json").read_text())
    return meta["as_of"]


def _tag_channel_section(version: str) -> str:
    text = (ROOT / "docs" / f"dataset_{version}.md").read_text(encoding="utf-8")
    start = text.index("## Tag channel: Company Facts vs inline XBRL (9.5)")
    end = text.index("Status x reason", start)
    return text[start:end].split("\n", 1)[1].strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--units", action="store_true", help="write eval/label_audit_{version}.csv")
    parser.add_argument("--report", action="store_true", help="write docs/label_audit_{version}.md")
    args = parser.parse_args(argv)
    csv_path = ROOT / "eval" / f"label_audit_{args.version}.csv"
    if args.units:
        pool, split_rows = load_pool(args.version)
        units = audit_units(pool)
        write_units(units, csv_path)
        print(f"{len(pool)} disagreements of {split_rows} rows -> {len(units)} units: {csv_path}")
    if args.report:
        units = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
        units["n_rows"] = units["n_rows"].astype(int)
        problems = validate_judgements(units)
        if problems:
            raise SystemExit("judgements incomplete:\n" + "\n".join(problems[:20]))
        split_rows = int(pd.read_parquet(ROOT / "datasets" / f"rows_{args.version}.parquet")
                         .query("split == 'validation' and item.notna()", engine="python").shape[0])
        context = {"version": args.version, "preamble": PREAMBLE.format(version=args.version)}
        path = ROOT / "docs" / f"label_audit_{args.version}.md"
        path.write_text(render_report(units, split_rows, _tag_channel_section(args.version),
                                      context), encoding="utf-8")
        print(f"Wrote {path}")
    return 0


PREAMBLE = (
    "Generated by `python -m finagent.evaluation.label_audit --version {version} --report` from "
    "the hand judgements in `eval/label_audit_{version}.csv` (first pass by Claude; the `reviewer` "
    "column is for Vineet). Pool: every validation row of `rows_{version}` where M1 "
    "(`m1-tfidf-lr-{version}`) disagrees with the tag-derived label, OTHER included on either "
    "side. Each row pattern (company, statement, label, label->prediction) is judged once and "
    "applied to all its fiscal years; a year whose tag check differs from the rest of its pattern "
    "is judged on its own. Validation only; held-out not read.\n\n"
    "Classes: MODEL_ERROR (label right, model wrong); LABEL_ERROR (the tag or row-to-tag "
    "alignment is wrong); TAXONOMY_AMBIGUITY (the row is legitimately two items or the item "
    "definitions overlap); NEEDS_FINANCE_REVIEW (not decided without a finance judgement).\n\n"
    "Tag check (latest dated column): AGREE = a tagged non-dimensional fact in the row equals "
    "the shown amount in magnitude at the displayed precision; AGREE_AS_SHOWN = equal only "
    "without the table's unit scale (a per-share or count row); DIFFER = none equals it; "
    "NO_FACT = nothing tagged in that row for that period; NO_AMOUNT = no readable amount shown."
)


if __name__ == "__main__":
    raise SystemExit(main())
