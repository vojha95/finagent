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
