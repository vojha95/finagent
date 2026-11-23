from datetime import date
from pathlib import Path

import pandas as pd

from finagent.config import load_concept_map
from finagent.dataset import (
    AMBIGUOUS,
    DIMENSIONAL_ONLY,
    MAPPED,
    OTHER,
    UNMAPPED_CONCEPT,
    UNTAGGED,
    concept_owner,
    facts_by_row,
    label_row,
    label_rows,
)
from finagent.dataset.build import build_dataset, measured_counts
from finagent.evidence import EvidenceStore
from finagent.ixbrl.tagstore import CellAddress, TagFact

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"
OWNER = {"us-gaap:Revenues": "revenue", "us-gaap:NetIncomeLoss": "net_income"}


def _fact(concept, row=0, dims=None, nil=False, table=0):
    return TagFact(
        accession="a", concept=concept, context_id="c", start=None, end=date(2025, 12, 31),
        dimensions=dims or {}, unit="USD", scale=6, sign=1,
        amount=None if nil else 1, cell_address=CellAddress(table, row, 3, "x"), is_nil=nil,
    )


def test_label_row_reasons():
    assert label_row([_fact("us-gaap:Revenues")], OWNER).label == "revenue"
    assert label_row([_fact("us-gaap:Revenues")], OWNER).reason == MAPPED

    other = label_row([_fact("amzn:FulfillmentExpense")], OWNER)
    assert (other.label, other.reason) == (OTHER, UNMAPPED_CONCEPT)

    segment = label_row([_fact("us-gaap:Revenues", dims={"srt:ProductOrServiceAxis": "x"})], OWNER)
    assert (segment.label, segment.reason) == (OTHER, DIMENSIONAL_ONLY)

    assert (label_row([], OWNER).label, label_row([], OWNER).reason) == (OTHER, UNTAGGED)

    both = label_row([_fact("us-gaap:Revenues"), _fact("us-gaap:NetIncomeLoss")], OWNER)
    assert both.label is None and both.reason == AMBIGUOUS  # 9.4: excluded


def test_nil_facts_do_not_label_a_row():
    assert label_row([_fact("us-gaap:Revenues", nil=True)], OWNER).reason == DIMENSIONAL_ONLY


def test_facts_by_row_groups_by_table_and_row():
    grouped = facts_by_row([_fact("a:X", row=1), _fact("a:Y", row=1), _fact("a:Z", row=2, table=4)])
    assert {k: len(v) for k, v in grouped.items()} == {(0, 1): 2, (4, 2): 1}


def test_concept_owner_covers_every_concept():
    concept_map = load_concept_map()
    owner = concept_owner(concept_map)
    assert owner["us-gaap:PaymentsToAcquireProductiveAssets"] == "capital_expenditure"
    assert len(owner) == sum(len(s.concepts) for s in concept_map.values())


def test_build_dataset_on_pinned_amazon():
    store = EvidenceStore(EVIDENCE_DIR)
    build = build_dataset(store, {"AMZN": "validation", "ZZZZ": "train"}, date(2026, 12, 31))
    rows = build.rows
    assert set(rows["fiscal_year"]) == {2021, 2022, 2023, 2024, 2025}
    assert set(rows["statement"]) == {"IS", "BS", "CF"}
    assert set(rows["split"]) == {"validation"}
    assert "ZZZZ" in build.skipped and not build.missing_statements

    fy2025 = rows[rows["fiscal_year"] == 2025]
    by_text = fy2025.set_index(["statement", "row_text"])["item"]
    assert by_text[("IS", "Total net sales")] == "revenue"
    assert by_text[("CF", "Purchases of property and equipment")] == "capital_expenditure"
    assert by_text[("IS", "Fulfillment")] == OTHER

    counts = measured_counts(rows)
    assert counts.loc["All", "All"] == len(rows)


def test_label_rows_pairs_rows_with_labels():
    store = EvidenceStore(EVIDENCE_DIR)
    record = store.load_filings("MSFT", date(2026, 12, 31))[-1]
    build = build_dataset(store, {"MSFT": "validation"}, date(2026, 12, 31))
    msft = build.rows[build.rows["accession"] == record.accession]
    segment = msft[(msft["statement"] == "IS") & (msft["row_text"] == "Product")]
    assert set(segment["label_reason"]) == {DIMENSIONAL_ONLY}
    assert label_rows([], [], OWNER) == []


def test_measured_counts_empty():
    assert measured_counts(pd.DataFrame()).empty


class _UnopenedStore:
    def load_filings(self, *_args):
        raise AssertionError("evidence was read")


def test_build_dataset_skips_held_out_unopened_by_default():
    build = build_dataset(_UnopenedStore(), {"HHHH": "held_out"}, date(2026, 12, 31),
                          concept_map=load_concept_map())
    assert build.rows.empty and build.held_out_excluded == ["HHHH"] and not build.skipped
