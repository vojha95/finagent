"""Measured counts for the row dataset (blueprint 9.3, 9.5; Phase 2 exit criterion).

Usage:
    python -m finagent.dataset.report --version v1
reads datasets/rows_{version}.parquet and its meta file and writes docs/dataset_{version}.md.
`python -m finagent.dataset.build` runs this after every build, so rebuilding is one command.

The tag-channel numbers (9.5) re-read the pinned filings of the train and validation companies
as of the meta file's as_of date; held-out companies are never opened.
"""

import argparse
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from finagent.config import HELD_OUT, LineItemSpec, load_concept_map, load_universe
from finagent.dataset.labels import OTHER
from finagent.document import NO_TABLES, STATEMENTS_INCORPORATED_BY_REFERENCE
from finagent.evidence.integrity import read_snapshot, read_snapshot_json
from finagent.evidence.store import EvidenceStore
from finagent.ixbrl.tagstore import build_tag_store
from finagent.tags.channel import AMOUNT_MISMATCH, CORROBORATED, NOT_FOUND_INLINE, read_items

ROOT = Path(__file__).resolve().parents[2]
DATASETS_DIR = ROOT / "datasets"
DOCS_DIR = ROOT / "docs"
MIN_TRAIN_ROWS = 20  # fewer train rows than this: a rare class for macro-F1
COMPARED = (CORROBORATED, AMOUNT_MISMATCH, NOT_FOUND_INLINE)  # Company Facts checked inline
# Location gaps meaning the statements are not in the primary document at all: its inline XBRL
# cannot corroborate Company Facts, so the filing is left out of the 9.5 rate.
NOT_IN_PRIMARY_DOCUMENT = (STATEMENTS_INCORPORATED_BY_REFERENCE, NO_TABLES)


def read_meta(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def label_kind(item: str | None) -> str:
    """item, OTHER, or excluded (an AMBIGUOUS row carries no label)."""
    if item is None or pd.isna(item):
        return "excluded"
    return OTHER if item == OTHER else "item"


def rows_by(rows: pd.DataFrame, index: str, columns: str) -> pd.DataFrame:
    """Row counts index x columns, with totals."""
    return pd.crosstab(rows[index], rows[columns], margins=True, margins_name="total")


def label_kinds(rows: pd.DataFrame) -> pd.DataFrame:
    """Labelled item vs OTHER vs excluded, per split."""
    kinds = rows["item"].map(label_kind).rename("label")
    table = pd.crosstab(rows["split"], kinds, margins=True, margins_name="total")
    return table.reindex(columns=["item", OTHER, "excluded", "total"], fill_value=0)


def label_reasons(rows: pd.DataFrame) -> pd.DataFrame:
    """label_reason counts, per split."""
    return pd.crosstab(rows["label_reason"], rows["split"], margins=True, margins_name="total")


def company_counts(rows: pd.DataFrame) -> pd.DataFrame:
    """Filings and rows per company, with rows per statement."""
    per_statement = pd.crosstab(rows["issuer"], rows["statement"])
    summary = rows.groupby("issuer").agg(split=("split", "first"),
                                         filings=("accession", "nunique"),
                                         rows=("row_id", "size"))
    return summary.join(per_statement).sort_values(["split", "issuer"])


def item_counts(
    rows: pd.DataFrame, concept_map: dict[str, LineItemSpec], min_train: int = MIN_TRAIN_ROWS
) -> pd.DataFrame:
    """Train and validation rows for every concept-map item, zero-row items included."""
    counts = pd.crosstab(rows["item"], rows["split"]) if not rows.empty else pd.DataFrame()
    table = pd.DataFrame({
        "statement": [spec.statement for spec in concept_map.values()],
        "core": [spec.core for spec in concept_map.values()],
    }, index=pd.Index(list(concept_map), name="item"))
    for split in ("train", "validation"):
        column = counts[split] if split in counts else pd.Series(dtype=int)
        table[split] = column.reindex(table.index, fill_value=0).astype(int)
    table["rare"] = table["train"] < min_train
    return table


def location_gaps(meta: dict[str, Any]) -> pd.DataFrame:
    """issuer, fiscal_year, gap_reason for every filing with a statement not located."""
    return pd.DataFrame(meta.get("location_gaps", []),
                        columns=["issuer", "fiscal_year", "gap_reason"])


def missing_statements_table(meta: dict[str, Any]) -> pd.DataFrame:
    """Filings with at least one statement not located, and why, from the build's meta file."""
    missing = pd.DataFrame(meta.get("missing_statements", []),
                           columns=["issuer", "fiscal_year", "statement"])
    if missing.empty:
        return pd.DataFrame(columns=["issuer", "fiscal_year", "statements", "gap_reason"])
    table = (missing.groupby(["issuer", "fiscal_year"])["statement"]
             .agg(lambda s: ", ".join(sorted(s))).rename("statements").reset_index())
    return table.merge(location_gaps(meta), on=["issuer", "fiscal_year"], how="left")


def mark_comparable(outcomes: pd.DataFrame, meta: dict[str, Any]) -> pd.DataFrame:
    """Add gap_reason and comparable: False when the statements are not in the primary document."""
    marked = outcomes.merge(location_gaps(meta), on=["issuer", "fiscal_year"], how="left")
    marked["comparable"] = ~marked["gap_reason"].isin(NOT_IN_PRIMARY_DOCUMENT)
    return marked


def not_comparable_table(marked: pd.DataFrame) -> pd.DataFrame:
    """Per filing left out of the 9.5 rate: its gap reason and its tag-channel reasons."""
    excluded = marked[~marked["comparable"]]
    if excluded.empty:
        return pd.DataFrame()
    return pd.crosstab([excluded["issuer"], excluded["fiscal_year"], excluded["gap_reason"]],
                       excluded["reason"], margins=True, margins_name="total")


def _filings(outcomes: pd.DataFrame) -> int:
    return len(outcomes[["issuer", "fiscal_year"]].drop_duplicates())


def tag_channel_outcomes(
    store: EvidenceStore,
    universe: dict[str, str],
    as_of: date,
    concept_map: dict[str, LineItemSpec] | None = None,
) -> pd.DataFrame:
    """Tag-channel status and reason for every concept-map item of every pinned filing.

    Held-out tickers are skipped unopened; a ticker without pinned evidence is left out.
    """
    specs_by_id = concept_map if concept_map is not None else load_concept_map()
    specs = list(specs_by_id.values())
    records: list[dict[str, Any]] = []
    for ticker, split in universe.items():
        if split == HELD_OUT:
            continue
        try:
            filings = store.load_filings(ticker, as_of)
        except KeyError:
            continue
        if not filings:
            continue
        companyfacts = read_snapshot_json(
            store.evidence_dir, f"companyfacts_{filings[0].cik}.json", store.manifest
        )
        for filing in filings:
            document = read_snapshot(store.evidence_dir, filing.primary_document, store.manifest)
            facts = build_tag_store(document, filing.accession)
            for item, outcome in read_items(filing, specs, companyfacts, facts, as_of).items():
                records.append({
                    "issuer": ticker, "split": split, "fiscal_year": filing.fiscal_year,
                    "item": item, "core": specs_by_id[item].core,
                    "status": outcome.status, "reason": outcome.reason,
                })
    return pd.DataFrame.from_records(
        records, columns=["issuer", "split", "fiscal_year", "item", "core", "status", "reason"]
    )


def tag_disagreement_rate(outcomes: pd.DataFrame) -> pd.DataFrame:
    """9.5: Company Facts vs inline XBRL disagreement, for all items and for core items.

    amount_mismatch_rate = AMOUNT_MISMATCH / item-filings where a single Company Facts value
    was checked inline (CORROBORATED + AMOUNT_MISMATCH + NOT_FOUND_INLINE).
    conflict_share = CONFLICT (any reason) / all item-filings.
    """
    rows = []
    for scope, subset in (("all items", outcomes),
                          ("core items", outcomes[outcomes["core"].astype(bool)])):
        compared = subset["reason"].isin(COMPARED)
        mismatch = int((subset["reason"] == AMOUNT_MISMATCH).sum())
        conflict = int((subset["status"] == "CONFLICT").sum())
        rows.append({
            "scope": scope,
            "item_filings": len(subset),
            "compared": int(compared.sum()),
            "amount_mismatch": mismatch,
            "amount_mismatch_rate": mismatch / compared.sum() if compared.any() else None,
            "conflict": conflict,
            "conflict_share": conflict / len(subset) if len(subset) else None,
        })
    return pd.DataFrame(rows).set_index("scope")


def status_reason_table(outcomes: pd.DataFrame) -> pd.DataFrame:
    """Status x reason over all item-filings, split by train and validation."""
    return pd.crosstab([outcomes["status"], outcomes["reason"]], outcomes["split"],
                       margins=True, margins_name="total")


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def markdown_table(table: pd.DataFrame) -> str:
    """A DataFrame as a GitHub markdown table, index included (multi-level joined by ' / ')."""
    names = [n for n in table.index.names if n is not None]
    header = [" / ".join(map(str, names))] + [str(c) for c in table.columns]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for key, values in zip(table.index, table.itertuples(index=False)):
        label = " / ".join(map(str, key)) if isinstance(key, tuple) else str(key)
        lines.append("| " + " | ".join([label] + [_cell(v) for v in values]) + " |")
    return "\n".join(lines)


def render_markdown(
    meta: dict[str, Any],
    rows: pd.DataFrame,
    concept_map: dict[str, LineItemSpec],
    outcomes: pd.DataFrame,
) -> str:
    """docs/dataset_{version}.md: the measured counts that replace the 9.3 guess."""
    marked = mark_comparable(outcomes, meta)
    comparable, excluded = marked[marked["comparable"]], marked[~marked["comparable"]]
    items = item_counts(rows, concept_map)
    rare = items[items["rare"]]
    missing = missing_statements_table(meta)
    dirty = " (working tree had uncommitted changes)" if meta.get("git_dirty") else ""
    parts = [
        f"# Row dataset {meta['version']}: measured counts",
        "",
        "Generated by `python -m finagent.dataset.build --version "
        f"{meta['version']} --as-of {meta['as_of']}`; do not edit by hand. "
        "Replaces the blueprint 9.3 guess (about 20,000 rows). Train and validation only; "
        "held-out companies are not opened (9.2).",
        "",
        "| Field | Value |", "|---|---|",
        f"| Row dataset | `datasets/rows_{meta['version']}.parquet` (gitignored) |",
        f"| Git commit | `{meta['git_commit']}`{dirty} |",
        f"| Evidence manifest SHA-256 | `{meta['evidence_manifest_sha256']}` |",
        f"| as_of | {meta['as_of']} |",
        f"| Rows | {meta['row_count']} |",
        "",
        "## Universe",
        "",
        markdown_table(pd.DataFrame(meta["universe"]).T.rename_axis("split")),
        "",
        "## Rows per split and statement",
        "",
        markdown_table(rows_by(rows, "split", "statement")),
        "",
        "## Labelled item vs OTHER vs excluded (AMBIGUOUS)",
        "",
        markdown_table(label_kinds(rows)),
        "",
        "## Label reasons",
        "",
        markdown_table(label_reasons(rows)),
        "",
        f"## Rows per item (rare: fewer than {MIN_TRAIN_ROWS} train rows)",
        "",
        f"{len(rare)} of {len(items)} items are rare: "
        + (", ".join(rare.index) if len(rare) else "none") + ".",
        "",
        markdown_table(items),
        "",
        "## Filings with a statement not located",
        "",
        markdown_table(missing.set_index(["issuer", "fiscal_year"])) if not missing.empty else "None.",
        "",
        "## Tag channel: Company Facts vs inline XBRL (9.5)",
        "",
        "`amount_mismatch_rate` = AMOUNT_MISMATCH / (CORROBORATED + AMOUNT_MISMATCH + "
        "NOT_FOUND_INLINE), the item-filings where a single Company Facts value was checked "
        "against inline XBRL. `conflict_share` = CONFLICT (any reason) / all item-filings. "
        f"All {len(concept_map)} concept-map items, core items shown separately. A rounding "
        "tie may go half up or half even.",
        "",
        f"Comparable: {_filings(comparable)} filings, {len(comparable)} item-filings.",
        "",
        markdown_table(tag_disagreement_rate(comparable)),
        "",
        "Status x reason over the comparable item-filings:",
        "",
        markdown_table(status_reason_table(comparable)),
        "",
        "### Not comparable: statements not in the primary document",
        "",
        f"Filings whose location gap is {' or '.join(NOT_IN_PRIMARY_DOCUMENT)} are left out of "
        "the rate above: the primary document's inline XBRL cannot corroborate Company Facts. "
        f"{_filings(excluded)} filings, {len(excluded)} item-filings.",
        "",
        markdown_table(not_comparable_table(marked)) if len(excluded) else "None.",
        "",
        "## Rows per company",
        "",
        markdown_table(company_counts(rows)),
        "",
    ]
    return "\n".join(parts)


def write_report(version: str, evidence_dir: Path, datasets_dir: Path = DATASETS_DIR,
                 docs_dir: Path = DOCS_DIR) -> Path:
    """Read the dataset and its meta file, run the tag channel, write the markdown report."""
    meta = read_meta(datasets_dir / f"rows_{version}.meta.json")
    rows = pd.read_parquet(datasets_dir / f"rows_{version}.parquet")
    concept_map = load_concept_map()
    outcomes = tag_channel_outcomes(EvidenceStore(evidence_dir), load_universe(),
                                    date.fromisoformat(meta["as_of"]), concept_map)
    path = docs_dir / f"dataset_{version}.md"
    path.write_text(render_markdown(meta, rows, concept_map, outcomes), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--evidence", default=str(ROOT / "evidence"))
    args = parser.parse_args(argv)
    print(f"Wrote {write_report(args.version, Path(args.evidence))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
