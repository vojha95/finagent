from datetime import date
from decimal import Decimal
from pathlib import Path

from finagent.document.channel import FOUND, NOT_FOUND, DocumentReading
from finagent.document.classify import RulesClassifier
from finagent.evaluation.e1 import (
    CORRECT,
    MISSED,
    PERIOD,
    SCALE,
    SIGN,
    WRONG_ROW,
    FieldResult,
    bootstrap_ci,
    classify_field,
    error_taxonomy,
    field_accuracy,
    mcnemar,
    paired_bootstrap_difference,
    pinned_tickers,
    run_e1,
    score_filing,
)
from finagent.evidence import EvidenceStore
from finagent.records import Outcome, Reading, RowInstance

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"
END, PRIOR = date(2025, 12, 31), date(2024, 12, 31)


def _doc(value, scale=6, row_id=None):
    return DocumentReading("revenue", END, value, FOUND if value is not None else NOT_FOUND,
                           row_id=row_id, scale=scale)


def _row(texts):
    return RowInstance(
        row_id="r", accession="a", statement="IS", label="Revenue", parent_header=None, indent=0,
        is_total=False, is_bold=False, position=0, cell_texts=texts, column_periods=(PRIOR, END),
        unit_text="(in millions)", table_index=0, source_row=0, column_spans=((1, 1), (2, 2)),
    )


def _tag(value, status="VERIFIED"):
    reading = Reading("X", 2025, "revenue", value, "tag", status)
    return Outcome(status=status, reason="CORROBORATED", observations=(reading,))


def test_classify_field_taxonomy():
    tag = Decimal("4791000000")
    assert classify_field(_doc(Decimal("4791E+6")), tag) == CORRECT
    assert classify_field(_doc(None), tag) == MISSED
    assert classify_field(_doc(Decimal("-4791E+6")), tag) == SIGN
    assert classify_field(_doc(Decimal("4791E+3"), scale=3), tag) == SCALE
    assert classify_field(_doc(Decimal("17E+6")), tag) == WRONG_ROW
    row = _row(("4,791", "5,000"))
    assert classify_field(_doc(Decimal("5000E+6")), tag, row) == PERIOD


def test_score_filing_uses_verified_face_items_only():
    documents = {"revenue": _doc(Decimal("1E+6")), "net_income": _doc(None)}
    tags = {"revenue": _tag(Decimal("1000000")), "net_income": _tag(Decimal(5), "CONFLICT")}
    results = score_filing("X", 2025, documents, tags)
    assert [(r.item, r.outcome) for r in results] == [("revenue", CORRECT)]
    assert score_filing("X", 2025, documents, tags, on_face={"net_income"}) == []


def _results(outcomes, issuer="X"):
    return [FieldResult(issuer, 2025, f"i{n}", o, None, Decimal(1)) for n, o in enumerate(outcomes)]


def test_accuracy_taxonomy_and_bootstrap():
    results = _results([CORRECT, CORRECT, MISSED, SIGN]) + _results([CORRECT], "Y")
    assert field_accuracy(results) == 0.6
    assert error_taxonomy(results) == {MISSED: 1, SIGN: 1}
    low, high = bootstrap_ci(results, draws=200)
    assert 0 <= low <= 0.6 <= high <= 1
    assert field_accuracy([]) is None and bootstrap_ci([]) is None


def test_mcnemar():
    a = _results([CORRECT] * 10)
    b = _results([CORRECT] * 2 + [MISSED] * 8)
    only_a, only_b, p = mcnemar(a, b)
    assert (only_a, only_b) == (8, 0) and p < 0.01
    assert mcnemar(a, a) == (0, 0, 1.0)


def test_paired_bootstrap_difference_resamples_companies_together():
    a = _results([CORRECT, MISSED, MISSED, CORRECT]) + _results([MISSED, CORRECT], "Y")
    b = _results([CORRECT, CORRECT, CORRECT, CORRECT]) + _results([CORRECT, CORRECT], "Y")
    point, (low, high) = paired_bootstrap_difference(a, b, draws=500)
    assert point == 3 / 6 and 0 < low <= point <= high <= 1  # b never worse on any company
    same, (lo, hi) = paired_bootstrap_difference(a, a, draws=100)
    assert same == lo == hi == 0
    assert paired_bootstrap_difference(a, _results([CORRECT], "Z")) is None  # nothing shared


def test_pinned_tickers_and_run_on_amazon():
    store = EvidenceStore(EVIDENCE_DIR)
    universe = {"AMZN": "validation", "ZZZZ": "validation", "AAPL": "train"}  # ZZZZ: not in the universe, never pinned
    assert pinned_tickers(store, universe, "validation", date(2026, 12, 31)) == ["AMZN"]
    results = run_e1(store, ["AMZN"], date(2026, 12, 31), RulesClassifier())
    revenue = [r for r in results if r.item == "revenue"]
    assert len(revenue) == 5 and all(r.outcome == CORRECT for r in revenue)
    assert field_accuracy(results) > 0.8  # 10.1 starting target for M0


class _UnopenedStore:
    """Fails if anything is loaded: a held-out guard must refuse before reading evidence."""

    def load_filings(self, *_args):
        raise AssertionError("evidence was read")


def test_run_e1_and_pinned_tickers_refuse_held_out_without_flag():
    import pytest

    from finagent.config import HeldOutError

    universe = {"HHHH": "held_out", "AMZN": "validation"}
    with pytest.raises(HeldOutError):
        run_e1(_UnopenedStore(), ["HHHH"], date(2026, 12, 31), RulesClassifier(),
               universe=universe)
    with pytest.raises(HeldOutError):
        pinned_tickers(_UnopenedStore(), universe, "held_out", date(2026, 12, 31))
    # With the flag the guard lets the call through (here it reaches the store).
    with pytest.raises(AssertionError, match="evidence was read"):
        pinned_tickers(_UnopenedStore(), universe, "held_out", date(2026, 12, 31),
                       include_held_out=True)


def test_e1_command_refuses_held_out_split_without_flag():
    import importlib.util

    import pytest

    from finagent.config import HeldOutError

    path = Path(__file__).resolve().parents[1] / "eval" / "e1_document.py"
    spec = importlib.util.spec_from_file_location("e1_document", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(HeldOutError):
        module.main(["--split", "held_out"])


def test_coverage_per_item_and_result_round_trip(tmp_path):
    from finagent.evaluation.e1 import coverage, per_item_accuracy, read_results, write_results

    results = [
        FieldResult("A", 2025, "revenue", CORRECT, Decimal("10"), Decimal("10")),
        FieldResult("A", 2025, "net_income", MISSED, None, Decimal("3.5")),
        FieldResult("B", 2024, "revenue", SIGN, Decimal("-1"), Decimal("1")),
    ]
    assert coverage(results) == 2 / 3 and coverage([]) is None
    assert per_item_accuracy(results) == {"net_income": (0, 1), "revenue": (1, 2)}
    path = tmp_path / "runs" / "m0.jsonl"
    write_results(path, results, {"git_commit": "abc"})
    header, loaded = read_results(path)
    assert header == {"git_commit": "abc"} and loaded == results


def _e1_module():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "eval" / "e1_document.py"
    spec = importlib.util.spec_from_file_location("e1_document", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_e1_command_compare_needs_same_commit(tmp_path):
    import pytest

    from finagent.evaluation.e1 import write_results

    module = _e1_module()
    a = [FieldResult("A", 2025, "revenue", MISSED, None, Decimal("1")),
         FieldResult("A", 2025, "net_income", CORRECT, Decimal("1"), Decimal("1"))]
    b = [FieldResult("A", 2025, "revenue", CORRECT, Decimal("1"), Decimal("1")),
         FieldResult("A", 2025, "net_income", CORRECT, Decimal("1"), Decimal("1"))]
    path = module.results_path("m0", "vt", "validation", tmp_path)
    write_results(path, a, {"git_commit": "abc", "model_version": "m0-rules"})
    result = module.compare(b, path, "abc")
    assert (result["baseline_only_correct"], result["this_only_correct"]) == (0, 1)
    assert result["against"] == "m0-rules" and result["p_value"] == 1.0
    assert result["difference"] == 0.5 and len(result["ci95_difference"]) == 2
    with pytest.raises(module.CompareError):
        module.compare(b, path, "def")
    with pytest.raises(module.CompareError):
        module.compare(b, tmp_path / "missing.jsonl", "abc")


def test_e1_command_loads_m0_and_saved_m1(tmp_path):
    import json

    import pytest

    from finagent.document.classify import TfidfLogisticClassifier
    from finagent.training.m1 import file_sha256, save_model

    module = _e1_module()
    assert isinstance(module.load_classifier("m0", "vt"), RulesClassifier)
    with pytest.raises(SystemExit):
        module.load_classifier("m1", "vt", tmp_path / "models", tmp_path)
    (tmp_path / "rows_vt.meta.json").write_text(json.dumps({"version": "vt"}))
    revenue = RowInstance(
        row_id="r1", accession="a", statement="IS", label="Revenue", parent_header=None,
        indent=0, is_total=False, is_bold=False, position=0, cell_texts=("1",),
        column_periods=(END,), unit_text=None, table_index=0, source_row=0, column_spans=())
    marketing = RowInstance(
        row_id="r2", accession="a", statement="IS", label="Marketing", parent_header=None,
        indent=0, is_total=False, is_bold=False, position=1, cell_texts=("1",),
        column_periods=(END,), unit_text=None, table_index=0, source_row=1, column_spans=())
    model = TfidfLogisticClassifier().fit([revenue, marketing], ["revenue", "OTHER"])
    save_model(model, {"dataset_meta_sha256": file_sha256(tmp_path / "rows_vt.meta.json")},
               tmp_path / "models", "vt")
    loaded = module.load_classifier("m1", "vt", tmp_path / "models", tmp_path)
    assert loaded.classes == ("OTHER", "revenue")


def test_e1_command_loads_saved_m2(tmp_path):
    import json

    import pytest

    from finagent.document.classify import SvdBoostedTreeClassifier
    from finagent.training.m1 import file_sha256
    from finagent.training.m2 import save_model

    module = _e1_module()
    with pytest.raises(SystemExit):
        module.load_classifier("m2", "vt", tmp_path / "models", tmp_path)
    (tmp_path / "rows_vt.meta.json").write_text(json.dumps({"version": "vt"}))
    rows = [RowInstance(
        row_id=f"r{i}", accession="a", statement="IS", label=label, parent_header=None,
        indent=0, is_total=False, is_bold=False, position=i, cell_texts=("1",),
        column_periods=(END,), unit_text=None, table_index=0, source_row=i, column_spans=())
        for i, label in enumerate(["Revenue", "Net sales", "Marketing", "Fulfillment"])]
    model = SvdBoostedTreeClassifier(n_components=2, num_leaves=4, n_estimators=5,
                                     min_child_samples=1, n_jobs=1).fit(
        rows, ["revenue", "revenue", "OTHER", "OTHER"])
    save_model(model, {"dataset_meta_sha256": file_sha256(tmp_path / "rows_vt.meta.json")},
               tmp_path / "models", "vt")
    loaded = module.load_classifier("m2", "vt", tmp_path / "models", tmp_path)
    assert isinstance(loaded, SvdBoostedTreeClassifier) and loaded.classes == ("OTHER", "revenue")


def test_e1_command_row_level_on_dataset(tmp_path):
    import pandas as pd

    module = _e1_module()
    assert module.row_level(RulesClassifier(), "vt", "validation", tmp_path) is None
    record = {
        "issuer": "A", "cik": "1", "fiscal_year": 2025, "accession": "a", "split": "validation",
        "statement": "IS", "row_id": "a:IS:0", "position": 0, "row_text": "Total revenues",
        "parent_header": None, "indent": 0, "is_total": True, "is_bold": False,
        "cell_texts": ["1"], "column_periods": ["2025-12-31"], "column_kinds": ["amount"],
        "unit_text": None, "table_index": 0, "source_row": 0, "item": "revenue",
        "label_reason": "MAPPED", "label_concepts": [],
    }
    pd.DataFrame([record]).to_parquet(tmp_path / "rows_vt.parquet", index=False)
    rows = module.row_level(RulesClassifier(), "vt", "validation", tmp_path)
    assert rows["metrics"]["rows"] == 1 and rows["metrics"]["items_scored"] == 1
    assert module.row_level(RulesClassifier(), "vt", "train", tmp_path) is None


def test_e1_command_record_appends_f2_row(tmp_path):
    import csv

    module = _e1_module()
    path = tmp_path / "experiments.csv"
    path.write_text("run_id,date,eval_id,component,version,single_change,dataset_version,split,"
                    "metrics,git_commit,evidence_manifest_hash,conclusion\n")
    run_id = module.record(path, "m1-tfidf-lr-v1", "validation", "v1", {"field_accuracy": 0.9},
                           "abc", "def", "M1 replaces M0", "keep")
    rows = list(csv.DictReader(path.open()))
    assert run_id == "e1-0001" and rows[0]["run_id"] == run_id
    assert rows[0]["dataset_version"] == "v1" and rows[0]["single_change"] == "M1 replaces M0"
    assert rows[0]["conclusion"] == "keep" and '"field_accuracy": 0.9' in rows[0]["metrics"]
    with path.open("a") as handle:  # another evaluation's row does not advance the E1 count
        handle.write("e5-0001,2026-01-01,E5,planner,m1,x,v3,test,{},abc,def,y\n")
    assert module.record(path, "m2", "validation", "v1", {}, "abc", "def", "x", "y") == "e1-0002"
