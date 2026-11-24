"""Label rule B (BC14) and E1 scoring equivalences (BC13) on synthetic data."""

from datetime import date
from decimal import Decimal

import pytest

from finagent.config import (
    ConfigError,
    Equivalence,
    LabelRule,
    load_concept_map,
    load_e1_equivalences,
    load_label_rules,
)
from finagent.dataset.labels import (
    MAPPED,
    MAPPED_BY_RULE,
    RowLabel,
    apply_label_rules,
    concept_owner,
    label_rows,
)
from finagent.document.channel import FOUND, NOT_FOUND, DocumentReading
from finagent.evaluation.e1 import (
    CORRECT,
    MISSED,
    FieldResult,
    equivalent_items,
    nci_counts,
    nci_status,
    read_results,
    rule_answer_keys,
    score_filing,
    strict_results,
    write_results,
)
from finagent.evidence.store import FilingRecord
from finagent.ixbrl.tagstore import CellAddress, TagFact
from finagent.records import Outcome, Reading, RowInstance

START, END = date(2025, 1, 1), date(2025, 12, 31)
EQUITY_LINE = "us-gaap:IncomeLossFromEquityMethodInvestments"
PRETAX_BEFORE = ("us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAnd"
                 "IncomeLossFromEquityMethodInvestments")
RULE = LabelRule("rule_b", "IS", "pretax_income_before_equity_method", "pretax_income",
                 (EQUITY_LINE,))


def _row(position, statement="IS", accession="A") -> RowInstance:
    return RowInstance(
        row_id=f"{accession}:{statement}:{position}", accession=accession, statement=statement,
        label=f"row {position}", parent_header=None, indent=0, is_total=False, is_bold=False,
        position=position, cell_texts=("1",), column_periods=(END,), unit_text="(in millions)",
        table_index=0, source_row=position, column_spans=(),
    )


def _fact(concept, row=0, amount="1000000", start=START, end=END, nil=False, dims=None,
          accession="A") -> TagFact:
    return TagFact(
        accession=accession, concept=concept, context_id="c", start=start, end=end,
        dimensions=dims or {}, unit="USD", scale=6, sign=1,
        amount=None if nil else Decimal(amount), cell_address=CellAddress(0, row, 1, None),
        decimals="-6", is_nil=nil,
    )


def _labelled(*specs) -> list[tuple[RowInstance, RowLabel]]:
    """specs: (position, statement, label, concepts)."""
    return [(_row(p, s), RowLabel(label, MAPPED, tuple(c))) for p, s, label, c in specs]


# ---------- config loaders ----------

def test_repo_label_rules_and_equivalences_load():
    rules = load_label_rules()
    assert [r.rule_id for r in rules] == ["pretax_before_equity_method_without_equity_line"]
    assert rules[0].unless_below == (EQUITY_LINE,)
    pairs = [e.items for e in load_e1_equivalences()]
    assert pairs == [("net_income", "net_income_including_nci"),
                     ("equity_attributable_to_parent", "total_equity")]


def test_label_rule_loader_rejects_unknown_items_and_statements(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("rules:\n  - {id: x, statement: IS, source_item: nope, target_item: revenue}\n")
    with pytest.raises(ConfigError, match="unknown item"):
        load_label_rules(path, load_concept_map())
    path.write_text("rules:\n  - {id: x, statement: XX, source_item: revenue, target_item: revenue}\n")
    with pytest.raises(ConfigError, match="statement"):
        load_label_rules(path, load_concept_map())


def test_equivalence_loader_rejects_bad_pairs(tmp_path):
    path = tmp_path / "eq.yaml"
    path.write_text("equivalences:\n  - {items: [net_income, net_income], nci_concepts: [a], "
                    "period_type: duration}\n")
    with pytest.raises(ConfigError, match="two different"):
        load_e1_equivalences(path, load_concept_map())
    path.write_text("equivalences:\n  - {items: [net_income, total_equity], nci_concepts: [a], "
                    "period_type: weekly}\n")
    with pytest.raises(ConfigError, match="period_type"):
        load_e1_equivalences(path, load_concept_map())


# ---------- rule B ----------

def test_rule_keeps_the_item_when_an_equity_method_line_follows():
    out = apply_label_rules(_labelled(
        (3, "IS", "pretax_income_before_equity_method", [PRETAX_BEFORE]),
        (5, "IS", "OTHER", [EQUITY_LINE]),
    ), [RULE])
    assert out[0][1].label == "pretax_income_before_equity_method" and out[0][1].rule is None


def test_rule_relabels_when_the_equity_method_line_is_above():
    out = apply_label_rules(_labelled(
        (1, "IS", "OTHER", [EQUITY_LINE]),
        (3, "IS", "pretax_income_before_equity_method", [PRETAX_BEFORE]),
    ), [RULE])
    label = out[1][1]
    assert label.label == "pretax_income" and label.reason == MAPPED_BY_RULE
    assert label.rule == "rule_b" and label.source_item == "pretax_income_before_equity_method"
    assert label.concepts == (PRETAX_BEFORE,)  # the tags themselves are unchanged


def test_rule_relabels_without_any_equity_method_line_and_ignores_other_statements():
    out = apply_label_rules(_labelled(
        (3, "IS", "pretax_income_before_equity_method", [PRETAX_BEFORE]),
        (9, "CF", "OTHER", [EQUITY_LINE]),  # a cash-flow row is not "below it on the IS"
    ), [RULE])
    assert out[0][1].label == "pretax_income"


def test_rule_looks_only_within_the_same_filing():
    labelled = [
        (_row(3, accession="A"), RowLabel("pretax_income_before_equity_method", MAPPED, ())),
        (_row(5, accession="B"), RowLabel("OTHER", MAPPED, (EQUITY_LINE,))),
    ]
    assert apply_label_rules(labelled, [RULE])[0][1].label == "pretax_income"


def test_label_rows_applies_the_rules_from_tags():
    owner = concept_owner(load_concept_map())
    rows = [_row(3), _row(5)]
    facts = [_fact(PRETAX_BEFORE, row=3), _fact(EQUITY_LINE, row=5)]
    assert label_rows(rows, facts, owner, [RULE])[0][1].label == "pretax_income_before_equity_method"
    assert label_rows(rows[:1], facts[:1], owner, [RULE])[0][1].label == "pretax_income"
    assert label_rows(rows[:1], facts[:1], owner, [])[0][1].label == \
        "pretax_income_before_equity_method"


# ---------- BC14 answer key ----------

def _tag(value, item="x", status="VERIFIED") -> Outcome:
    reading = Reading("X", 2025, item, None if value is None else Decimal(value), "tag", status)
    return Outcome(status=status, reason="CORROBORATED", observations=(reading,))


def test_rule_answer_key_uses_the_source_tag_only_for_relabelled_rows():
    tags = {"pretax_income_before_equity_method": _tag("19743000000"),
            "pretax_income": _tag(None, status="GAP")}
    relabelled = RowLabel("pretax_income", MAPPED_BY_RULE, (PRETAX_BEFORE,), "rule_b",
                          "pretax_income_before_equity_method")
    keyed = rule_answer_keys(tags, [relabelled])
    assert keyed["pretax_income"] is tags["pretax_income_before_equity_method"]
    assert rule_answer_keys(tags, [RowLabel("pretax_income", MAPPED, ())]) == tags
    own = dict(tags, pretax_income=_tag("1"))  # a verified tag of its own is kept
    assert rule_answer_keys(own, [relabelled])["pretax_income"] is own["pretax_income"]


# ---------- BC13 ----------

def _filing() -> FilingRecord:
    return FilingRecord("X", "1", "10-K", 2025, START, "CALCULATION", END, date(2026, 2, 1),
                        "A", "x.htm", "u")


NCI = "us-gaap:NetIncomeLossAttributableToNoncontrollingInterest"


@pytest.mark.parametrize("facts, status", [
    ([], "ABSENT"),
    ([_fact(NCI, nil=True)], "NIL"),
    ([_fact(NCI, amount="0")], "ZERO"),
    ([_fact(NCI, amount="5000000")], "REPORTED"),
    ([_fact(NCI, amount="5000000", dims={"ax": "m"})], "ABSENT"),  # dimensional only
    ([_fact(NCI, amount="5000000", start=date(2025, 10, 1))], "ABSENT"),  # a quarter
])
def test_nci_status(facts, status):
    assert nci_status(facts, (NCI,), _filing(), "duration") == status


def test_equivalent_items_only_for_pairs_without_nci():
    eqs = [Equivalence(("net_income", "net_income_including_nci"), (NCI,), "duration"),
           Equivalence(("equity_attributable_to_parent", "total_equity"),
                       ("us-gaap:MinorityInterest",), "instant")]
    facts = [_fact("us-gaap:MinorityInterest", start=None, amount="7000000")]
    twins, statuses = equivalent_items(facts, _filing(), eqs)
    assert twins == {"net_income": "net_income_including_nci",
                     "net_income_including_nci": "net_income"}
    assert statuses[("equity_attributable_to_parent", "total_equity")] == "REPORTED"
    log = [("X", 2025, pair, status) for pair, status in statuses.items()]
    assert nci_counts(log) == {"equity_attributable_to_parent|total_equity": {"REPORTED": 1},
                               "net_income|net_income_including_nci": {"ABSENT": 1}}


def _reading(item, value, row_id=None):
    return DocumentReading(item, END, None if value is None else Decimal(value),
                           FOUND if value is not None else NOT_FOUND, row_id=row_id, scale=6)


def test_score_filing_accepts_the_twin_only_with_equivalents():
    documents = {"net_income": _reading("net_income", None),
                 "net_income_including_nci": _reading("net_income_including_nci", "3108E+6")}
    tags = {"net_income": _tag("3108000000"), "net_income_including_nci": _tag("3108000000")}
    strict = score_filing("X", 2025, documents, tags, on_face={"net_income"})
    assert [r.outcome for r in strict] == [MISSED]
    twins = {"net_income": "net_income_including_nci", "net_income_including_nci": "net_income"}
    bc13 = score_filing("X", 2025, documents, tags, on_face={"net_income"}, equivalents=twins)
    assert [(r.outcome, r.strict) for r in bc13] == [(CORRECT, MISSED)]
    assert [r.outcome for r in strict_results(bc13)] == [MISSED]


def test_score_filing_twin_must_match_the_items_own_answer():
    documents = {"net_income": _reading("net_income", None),
                 "net_income_including_nci": _reading("net_income_including_nci", "9E+6")}
    tags = {"net_income": _tag("3108000000")}
    twins = {"net_income": "net_income_including_nci"}
    result = score_filing("X", 2025, documents, tags, on_face={"net_income"}, equivalents=twins)
    assert result[0].outcome == MISSED and result[0].strict_outcome is None


def test_results_round_trip_keeps_the_strict_outcome(tmp_path):
    path = tmp_path / "r.jsonl"
    results = [FieldResult("X", 2025, "net_income", CORRECT, Decimal("1"), Decimal("1"), MISSED),
               FieldResult("X", 2025, "revenue", CORRECT, Decimal("2"), Decimal("2"))]
    write_results(path, results, {"m": 1})
    _, back = read_results(path)
    assert back == results and [r.strict for r in back] == [MISSED, CORRECT]
