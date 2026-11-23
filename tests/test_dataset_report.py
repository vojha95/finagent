from datetime import date
from pathlib import Path

import pandas as pd

from finagent.config import LineItemSpec, load_concept_map
from finagent.dataset import AMBIGUOUS, MAPPED, OTHER, UNTAGGED
from finagent.dataset.report import (
    company_counts,
    item_counts,
    label_kind,
    label_kinds,
    label_reasons,
    location_gaps,
    mark_comparable,
    markdown_table,
    missing_statements_table,
    not_comparable_table,
    render_markdown,
    rows_by,
    status_reason_table,
    tag_channel_outcomes,
    tag_disagreement_rate,
)
from finagent.evidence import EvidenceStore

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"


def _spec(item_id, statement="IS", core=False):
    return LineItemSpec(item_id=item_id, label=item_id, statement=statement,
                        period_type="duration", sign="positive", core=core,
                        concepts=(f"us-gaap:{item_id}",))


CONCEPT_MAP = {"revenue": _spec("revenue", core=True), "goodwill": _spec("goodwill", "BS")}


def _row(issuer, split, statement, item, reason, accession="a1"):
    return {"issuer": issuer, "split": split, "statement": statement, "item": item,
            "label_reason": reason, "accession": accession, "row_id": f"{issuer}-{len(item or '')}"}


ROWS = pd.DataFrame([
    _row("AAA", "train", "IS", "revenue", MAPPED),
    _row("AAA", "train", "IS", OTHER, UNTAGGED),
    _row("AAA", "train", "BS", None, AMBIGUOUS, accession="a2"),
    _row("BBB", "validation", "IS", "revenue", MAPPED),
])

def _outcome(issuer, fiscal_year, split, core, status, reason):
    return {"issuer": issuer, "fiscal_year": fiscal_year, "split": split, "core": core,
            "status": status, "reason": reason}


OUTCOMES = pd.DataFrame([
    _outcome("AAA", 2021, "train", True, "VERIFIED", "CORROBORATED"),
    _outcome("AAA", 2021, "train", True, "CONFLICT", "AMOUNT_MISMATCH"),
    _outcome("AAA", 2022, "train", False, "CONFLICT", "NOT_FOUND_INLINE"),
    _outcome("BBB", 2023, "validation", False, "GAP", "NOT_FOUND"),
    _outcome("BBB", 2023, "validation", False, "CONFLICT", "MULTIPLE_VALUES_PRIMARY"),
])
# A filing whose statements are in an exhibit: its tags cannot be corroborated inline.
EXHIBIT_OUTCOMES = pd.DataFrame([
    _outcome("CCC", 2022, "train", True, "CONFLICT", "NOT_FOUND_INLINE"),
    _outcome("CCC", 2022, "train", False, "CONFLICT", "NOT_FOUND_INLINE"),
])

META = {
    "version": "vt", "git_commit": "abc", "git_dirty": True,
    "evidence_manifest_sha256": "f" * 64, "as_of": "2026-11-24", "row_count": len(ROWS),
    "universe": {"train": {"companies": 2, "companies_read": 1, "filings": 2, "rows": 3}},
    "missing_statements": [["AAA", 2021, "IS"], ["AAA", 2021, "CF"], ["CCC", 2022, "BS"]],
    "location_gaps": [["AAA", 2021, "STATEMENT_NOT_LOCATED"],
                      ["CCC", 2022, "STATEMENTS_INCORPORATED_BY_REFERENCE"]],
    "skipped": {},
}


def test_label_kind():
    assert [label_kind(i) for i in ("revenue", OTHER, None)] == ["item", OTHER, "excluded"]


def test_rows_by_has_totals():
    table = rows_by(ROWS, "split", "statement")
    assert table.loc["train", "IS"] == 2 and table.loc["total", "total"] == len(ROWS)


def test_label_kinds_counts_ambiguous_as_excluded():
    table = label_kinds(ROWS)
    assert table.loc["train"].to_dict() == {"item": 1, OTHER: 1, "excluded": 1, "total": 3}
    assert table.loc["validation", "excluded"] == 0


def test_label_reasons_per_split():
    table = label_reasons(ROWS)
    assert table.loc[MAPPED, "train"] == 1 and table.loc[MAPPED, "total"] == 2


def test_company_counts():
    table = company_counts(ROWS)
    assert table.loc["AAA"][["split", "filings", "rows", "IS"]].tolist() == ["train", 2, 3, 2]


def test_item_counts_include_zero_row_items_and_flag_rare():
    table = item_counts(ROWS, CONCEPT_MAP, min_train=1)
    assert table.loc["revenue", ["train", "validation"]].tolist() == [1, 1]
    assert table.loc["goodwill", ["train", "validation"]].tolist() == [0, 0]
    assert table["rare"].to_dict() == {"revenue": False, "goodwill": True}
    assert item_counts(pd.DataFrame(), CONCEPT_MAP)["train"].sum() == 0


def test_missing_statements_table_groups_by_filing():
    table = missing_statements_table(META)
    assert table.to_dict("records") == [
        {"issuer": "AAA", "fiscal_year": 2021, "statements": "CF, IS",
         "gap_reason": "STATEMENT_NOT_LOCATED"},
        {"issuer": "CCC", "fiscal_year": 2022, "statements": "BS",
         "gap_reason": "STATEMENTS_INCORPORATED_BY_REFERENCE"},
    ]
    assert missing_statements_table({}).empty


def test_location_gaps():
    assert location_gaps(META)["gap_reason"].tolist() == [
        "STATEMENT_NOT_LOCATED", "STATEMENTS_INCORPORATED_BY_REFERENCE"]
    assert location_gaps({}).empty


def test_mark_comparable_by_gap_reason_never_by_issuer():
    marked = mark_comparable(pd.concat([OUTCOMES, EXHIBIT_OUTCOMES]), META)
    # AAA 2021 has a gap too, but STATEMENT_NOT_LOCATED keeps it comparable.
    assert marked.groupby("issuer")["comparable"].all().to_dict() == {
        "AAA": True, "BBB": True, "CCC": False}
    assert len(marked) == len(OUTCOMES) + len(EXHIBIT_OUTCOMES)


def test_not_comparable_table():
    table = not_comparable_table(mark_comparable(pd.concat([OUTCOMES, EXHIBIT_OUTCOMES]), META))
    key = ("CCC", 2022, "STATEMENTS_INCORPORATED_BY_REFERENCE")
    assert table.loc[key, "NOT_FOUND_INLINE"] == 2
    assert not_comparable_table(mark_comparable(OUTCOMES, META)).empty


def test_tag_disagreement_rate_denominators():
    rate = tag_disagreement_rate(OUTCOMES)
    every = rate.loc["all items"]
    assert (every["item_filings"], every["compared"], every["amount_mismatch"]) == (5, 3, 1)
    assert every["amount_mismatch_rate"] == 1 / 3
    assert every["conflict_share"] == 3 / 5
    core = rate.loc["core items"]
    assert (core["compared"], core["amount_mismatch_rate"], core["conflict_share"]) == (2, 0.5, 0.5)


def test_tag_disagreement_rate_without_comparisons():
    rate = tag_disagreement_rate(OUTCOMES[OUTCOMES["reason"] == "NOT_FOUND"])
    assert pd.isna(rate.loc["all items", "amount_mismatch_rate"])
    assert pd.isna(rate.loc["core items", "conflict_share"])


def test_status_reason_table():
    table = status_reason_table(OUTCOMES)
    assert table.loc[("CONFLICT", "AMOUNT_MISMATCH"), "train"] == 1
    assert table.loc[("total", ""), "total"] == len(OUTCOMES)


def test_markdown_table():
    text = markdown_table(pd.DataFrame({"n": [1], "r": [0.5], "x": [None]},
                                       index=pd.Index(["a"], name="k")))
    assert text.splitlines() == ["| k | n | r | x |", "|---|---|---|---|", "| a | 1 | 0.5000 | — |"]


def test_render_markdown_sections():
    text = render_markdown(META, ROWS, CONCEPT_MAP, pd.concat([OUTCOMES, EXHIBIT_OUTCOMES]))
    assert "# Row dataset vt: measured counts" in text
    assert "uncommitted changes" in text
    assert "2 of 2 items are rare: revenue, goodwill." in text
    assert "| AAA / 2021 | CF, IS | STATEMENT_NOT_LOCATED |" in text
    assert "Comparable: 3 filings, 5 item-filings." in text
    assert "1 filings, 2 item-filings." in text
    assert "| CCC / 2022 / STATEMENTS_INCORPORATED_BY_REFERENCE | 2 | 2 |" in text
    assert "| all items | 5 | 3 | 1 | 0.3333 | 3 | 0.6000 |" in text  # CCC left out
    assert "## Tag channel: Company Facts vs inline XBRL (9.5)" in text


class _UnopenedStore:
    def load_filings(self, *_args):
        raise AssertionError("evidence was read")


class _NoEvidenceStore:
    def load_filings(self, *_args):
        raise KeyError("no pinned evidence")


def test_tag_channel_outcomes_never_opens_held_out():
    outcomes = tag_channel_outcomes(_UnopenedStore(), {"HHHH": "held_out"}, date(2026, 11, 24),
                                    CONCEPT_MAP)
    assert outcomes.empty and "reason" in outcomes.columns
    assert tag_channel_outcomes(_NoEvidenceStore(), {"ZZZZ": "train"}, date(2026, 11, 24),
                                CONCEPT_MAP).empty


def test_tag_channel_outcomes_on_pinned_amazon():
    store, as_of = EvidenceStore(EVIDENCE_DIR), date(2026, 11, 24)
    concept_map = load_concept_map()
    outcomes = tag_channel_outcomes(store, {"AMZN": "validation"}, as_of, concept_map)
    assert len(outcomes) == len(store.load_filings("AMZN", as_of)) * len(concept_map)
    revenue = outcomes[(outcomes["item"] == "revenue") & (outcomes["fiscal_year"] == 2025)]
    assert revenue["reason"].tolist() == ["CORROBORATED"]
