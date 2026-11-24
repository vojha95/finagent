import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

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
from finagent.dataset.build import (
    DATASETS_DIR,
    DatasetBuild,
    build_dataset,
    dataset_meta,
    measured_counts,
    universe_counts,
    write_meta,
)
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


UNIVERSE = {"AAA": "train", "IBMX": "train", "BBB": "validation", "HHHH": "held_out"}


def _synthetic_build():
    rows = pd.DataFrame({"split": ["train", "train", "validation"], "item": ["revenue", OTHER, None]})
    return DatasetBuild(rows=rows, missing_statements=[("IBMX", 2021, "IS")],
                        location_gaps=[("IBMX", 2021, "STATEMENTS_INCORPORATED_BY_REFERENCE")],
                        held_out_excluded=["HHHH"], filings={"AAA": 2, "IBMX": 1, "BBB": 1})


def test_universe_counts_per_split():
    counts = universe_counts(_synthetic_build(), UNIVERSE)
    assert counts["train"] == {"companies": 2, "companies_read": 2, "filings": 3, "rows": 2}
    assert counts["held_out"] == {"companies": 1, "companies_read": 0, "filings": 0, "rows": 0}


def test_dataset_meta_records_code_evidence_and_counts(tmp_path):
    (tmp_path / "manifest.json").write_bytes(b"{}")
    meta = dataset_meta(_synthetic_build(), UNIVERSE, "vt", date(2026, 11, 24), tmp_path)
    assert meta["evidence_manifest_sha256"] == (
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a")  # sha256(b"{}")
    assert (meta["as_of"], meta["row_count"], meta["version"]) == ("2026-11-24", 3, "vt")
    assert meta["missing_statements"] == [["IBMX", 2021, "IS"]]
    assert meta["location_gaps"] == [["IBMX", 2021, "STATEMENTS_INCORPORATED_BY_REFERENCE"]]
    assert len(meta["git_commit"]) == 40 and isinstance(meta["git_dirty"], bool)


def test_dataset_meta_without_git(tmp_path):
    (tmp_path / "manifest.json").write_bytes(b"{}")
    meta = dataset_meta(_synthetic_build(), UNIVERSE, "vt", date(2026, 11, 24), tmp_path,
                        root=tmp_path)
    assert meta["git_commit"] is None and meta["git_dirty"] is None


def test_dataset_meta_dirty_flag_ignores_build_outputs(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path,
                       check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "manifest.json").write_bytes(b"{}")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    (tmp_path / "datasets").mkdir()
    (tmp_path / "datasets" / "rows_vt.parquet").write_bytes(b"x")
    meta = dataset_meta(_synthetic_build(), UNIVERSE, "vt", date(2026, 11, 24), tmp_path,
                        root=tmp_path)
    assert meta["git_dirty"] is False
    (tmp_path / "code.py").write_text("x = 1\n")
    meta = dataset_meta(_synthetic_build(), UNIVERSE, "vt", date(2026, 11, 24), tmp_path,
                        root=tmp_path)
    assert meta["git_dirty"] is True


def test_write_meta_matches_parquet_row_count(tmp_path):
    build = _synthetic_build()
    (tmp_path / "manifest.json").write_bytes(b"{}")
    build.rows.to_parquet(tmp_path / "rows_vt.parquet", index=False)
    write_meta(tmp_path / "rows_vt.meta.json",
               dataset_meta(build, UNIVERSE, "vt", date(2026, 11, 24), tmp_path))
    meta = json.loads((tmp_path / "rows_vt.meta.json").read_text())
    assert meta["row_count"] == len(pd.read_parquet(tmp_path / "rows_vt.parquet"))


@pytest.mark.parametrize("version", ["v1"])
def test_built_meta_and_parquet_agree(version):
    parquet, meta = DATASETS_DIR / f"rows_{version}.parquet", DATASETS_DIR / f"rows_{version}.meta.json"
    if not (parquet.exists() and meta.exists()):
        pytest.skip(f"rows_{version} not built")
    recorded = json.loads(meta.read_text())
    rows = pd.read_parquet(parquet, columns=["split"])
    assert recorded["row_count"] == len(rows)
    assert {s: c["rows"] for s, c in recorded["universe"].items() if c["rows"]} == (
        rows["split"].value_counts().to_dict())
