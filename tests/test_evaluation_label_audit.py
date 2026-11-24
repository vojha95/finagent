"""Label audit (blueprint 9.5) on synthetic rows, facts and predictions."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from finagent.config import HeldOutError
from finagent.evaluation.label_audit import (
    AGREE,
    AGREE_AS_SHOWN,
    DIFFER,
    NO_AMOUNT,
    NO_FACT,
    TagCheck,
    audit_units,
    breakdown,
    class_counts,
    decision_groups,
    disagreement_pool,
    has_judgements,
    judged_rows,
    label_error_rate,
    load_pool,
    render_report,
    tag_check,
    top3,
    validate_judgements,
    write_units,
)
from finagent.ixbrl import TagFact
from finagent.ixbrl.tagstore import CellAddress
from finagent.records import Prediction, RowInstance

END = date(2025, 12, 31)


def _row(row_id="A:BS:0", label="Inventories", cells=("2,420", "2,177"), unit="(in millions)",
         parent=None, table=3, source=7, accession="A") -> RowInstance:
    return RowInstance(
        row_id=row_id, accession=accession, statement="BS", label=label, parent_header=parent,
        indent=0, is_total=False, is_bold=False, position=0, cell_texts=cells,
        column_periods=(END, date(2024, 12, 31)), unit_text=unit, table_index=table,
        source_row=source, column_spans=(), column_kinds=("amount", "amount"),
    )


def _fact(concept="us-gaap:InventoryNet", amount="2420000000", table=3, row=7, end=END,
          dimensions=None) -> TagFact:
    return TagFact(
        accession="A", concept=concept, context_id="c", start=None, end=end,
        dimensions=dimensions or {}, unit="USD", scale=6, sign=1, amount=Decimal(amount),
        cell_address=CellAddress(table, row, 1, None), decimals="-6",
    )


# ---------- tag check ----------

def test_tag_check_agree_uses_latest_column_and_magnitude():
    check = tag_check(_row(cells=("(2,420)", "2,177")), [_fact()])
    assert check.status == AGREE and check.period == END and check.shown == "(2,420)"
    assert check.tag_values == ("us-gaap:InventoryNet=2420000000",)


def test_tag_check_differ_when_no_fact_equals_the_shown_amount():
    assert tag_check(_row(), [_fact(amount="2177000000")]).status == DIFFER


def test_tag_check_any_match_counts_when_the_label_carries_a_figure():
    facts = [_fact(), _fact(concept="us-gaap:InventoryNoncurrent", amount="2194000000")]
    assert tag_check(_row(), facts).status == AGREE


def test_tag_check_agree_as_shown_for_a_per_share_row_read_with_the_unit_scale():
    row = _row(label="- Basic", cells=("$ 6.65", "$ 9.72"))
    fact = _fact(concept="us-gaap:EarningsPerShareBasic", amount="6.65")
    assert tag_check(row, [fact]).status == AGREE_AS_SHOWN


def test_tag_check_ignores_other_rows_periods_and_dimensional_facts():
    facts = [_fact(row=8), _fact(end=date(2024, 12, 31)), _fact(dimensions={"ax": "m"})]
    assert tag_check(_row(), facts).status == NO_FACT


def test_tag_check_no_amount_for_blank_or_undated_cells():
    assert tag_check(_row(cells=("", "2,177")), [_fact()]).status == NO_AMOUNT
    undated = RowInstance(**{**_row().__dict__, "column_periods": (None, None)})
    assert tag_check(undated, [_fact()]).status == NO_AMOUNT


# ---------- pool and units ----------

def _prediction(row_id: str, *candidates: tuple[str, float]) -> Prediction:
    return Prediction(row_id, tuple(candidates), "test")


def _dataset(n_years=3, issuer="XCO", label="OTHER", text="Inventories") -> pd.DataFrame:
    return pd.DataFrame([{
        "issuer": issuer, "fiscal_year": 2021 + i, "statement": "BS", "position": 2,
        "row_text": text, "item": label, "label_reason": "UNMAPPED_CONCEPT",
        "label_concepts": ["us-gaap:PrepaidExpenseCurrent"], "row_id": f"{issuer}{i}:BS:2",
    } for i in range(n_years)])


def _pool(statuses=(AGREE, AGREE, AGREE), m1_item="inventory", label="OTHER",
          issuer="XCO") -> pd.DataFrame:
    data = _dataset(len(statuses), issuer=issuer, label=label)
    rows = [_row(row_id=r, accession=r.split(":")[0]) for r in data["row_id"]]
    m1 = [_prediction(r.row_id, (m1_item, 0.9), ("OTHER", 0.1)) for r in rows]
    m0 = [_prediction(r.row_id, ("OTHER", 1.0)) for r in rows]
    checks = {r.row_id: TagCheck(s, END, "2,420", ()) for r, s in zip(rows, statuses)}
    return disagreement_pool(data, rows, m1, m0, checks)


def test_top3_formats_the_three_best_candidates():
    p = _prediction("r", ("inventory", 0.912), ("OTHER", 0.05), ("revenue", 0.03), ("x", 0.0))
    assert top3(p) == "inventory 0.91; OTHER 0.05; revenue 0.03"


def test_pool_keeps_only_disagreements():
    assert len(_pool()) == 3
    assert _pool(m1_item="OTHER").empty


def test_pool_requires_aligned_inputs():
    data = _dataset(2)
    with pytest.raises(ValueError):
        disagreement_pool(data, [_row()], [], [], {})


def test_one_unit_per_pattern_covering_every_row():
    units = audit_units(_pool())
    assert len(units) == 1
    unit = units.iloc[0]
    assert unit["n_rows"] == 3 and unit["fiscal_years"] == "2021 2022 2023"
    assert unit["unit_id"] == "U001" and unit["class"] == "" and unit["reviewer"] == ""
    assert sorted(unit["row_ids"].split()) == ["XCO0:BS:2", "XCO1:BS:2", "XCO2:BS:2"]


def test_a_year_whose_tag_check_differs_is_its_own_unit():
    units = audit_units(_pool((AGREE, DIFFER, AGREE)))
    assert sorted(units["n_rows"]) == [1, 2]
    single = units[units["n_rows"] == 1].iloc[0]
    assert single["tag_check"] == DIFFER and single["fiscal_years"] == "2022"


def test_units_are_in_a_fixed_order():
    a = pd.concat([_pool(), _pool(m1_item="revenue")])
    b = pd.concat([_pool(m1_item="revenue"), _pool()])
    pd.testing.assert_frame_equal(audit_units(a), audit_units(b))


# ---------- CSV, judgements and report ----------

def _judged() -> pd.DataFrame:
    units = audit_units(pd.concat([
        _pool(),                                      # 3 rows
        _pool((AGREE,), m1_item="revenue", issuer="YCO"),        # 1 row
        _pool((AGREE, AGREE), m1_item="ppe_net", issuer="ZCO"),  # 2 rows
    ]))
    units["class"] = ["LABEL_ERROR", "TAXONOMY_AMBIGUITY", "NEEDS_FINANCE_REVIEW"][: len(units)]
    units["reason"] = "because"
    units["pattern_group"] = ["", "G1", "G1"][: len(units)]
    units["proposed_resolution"] = ["", "", "leave OTHER"][: len(units)]
    return units


def test_write_units_refuses_to_overwrite_judgements(tmp_path):
    path = tmp_path / "audit.csv"
    units = audit_units(_pool())
    write_units(units, path)
    assert not has_judgements(path)
    write_units(units, path)  # an unjudged file may be regenerated
    judged = units.assign(**{"class": "MODEL_ERROR"})
    judged.to_csv(path, index=False)
    assert has_judgements(path)
    with pytest.raises(FileExistsError):
        write_units(units, path)


def test_validate_judgements_flags_unknown_class_and_missing_reason():
    units = _judged()
    assert validate_judgements(units) == []
    units.loc[0, "class"] = "MAYBE"
    units.loc[1, "reason"] = " "
    problems = validate_judgements(units)
    assert len(problems) == 2 and "MAYBE" in problems[0] and "no reason" in problems[1]


def test_judged_rows_expand_every_unit_to_its_rows():
    rows = judged_rows(_judged())
    assert len(rows) == 6 and rows["row_id"].is_unique


def test_label_error_rate_is_an_exact_count():
    units = _judged()
    errors = int(units.loc[units["class"] == "LABEL_ERROR", "n_rows"].sum())
    review = int(units.loc[units["class"] == "NEEDS_FINANCE_REVIEW", "n_rows"].sum())
    rate = label_error_rate(units, split_rows=100)
    assert rate["label_errors"] == errors and rate["pool_rows"] == 6
    assert rate["share_of_pool"] == errors / 6 and rate["share_of_split"] == errors / 100
    assert rate["share_of_split_if_review_were_label_errors"] == (errors + review) / 100


def test_class_counts_and_breakdowns_add_up():
    units = _judged()
    counts = class_counts(units)
    assert counts.loc["total", "rows"] == 6 and counts.loc["total", "units"] == len(units)
    assert counts.loc["MODEL_ERROR", "rows"] == 0
    assert breakdown(units, "issuer")["rows"].sum() == 6
    assert breakdown(units, "label")["rows"].sum() == 6


def test_decision_groups_cover_only_ambiguity_and_review():
    groups = decision_groups(_judged())
    assert list(groups["pattern_group"]) == ["G1"]
    group = groups.iloc[0]
    assert group["rows"] == 3 and group["proposed_resolution"] == "leave OTHER"


def test_render_report_has_every_section():
    text = render_report(_judged(), 100, "| tag table |", {"version": "vX", "preamble": "pre"})
    for heading in ("# Label quality audit, dataset vX", "## Counts per class",
                    "## Label-error rate", "## Breakdown by company", "## Breakdown by labelled item",
                    "## Company Facts vs inline XBRL", "| tag table |", "## For decision",
                    "## Full table", "not measured", "Label errors:"):
        assert heading in text


def test_load_pool_refuses_the_held_out_split():
    with pytest.raises(HeldOutError):
        load_pool("v2", split="held_out")
