"""Statement-set assembly: restatements, NOT_REPORTED, substitution, refusals, end to end."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from statements_support import FILING, START, fact, obs, pending, row, tag_only

from finagent.config import DerivationSpec, Derivations, HeldOutError
from finagent.statements.build import (
    DOCUMENT_HIDDEN,
    TAG_HIDDEN,
    FilingAnalysis,
    StatementConfigs,
    _comparative_record,
    absence_unknown,
    analyse_filing,
    assemble_year,
    build_statement_set,
    located_statements,
    never_read_reason,
    never_reported,
    nci_statuses,
    nil_components,
    nil_unread_year,
    read_years,
    select_face_value,
    substitute,
)
from finagent.statements.derive import INPUT_GAP, NIL_UNREAD_YEAR
from finagent.statements.replay import FULL, NO_COMPANYFACTS, NO_DOCUMENT, UNTAGGED
from finagent.statements.verify_set import (FROM_LATER_FILING, NOT_REPORTED, RESTATED,
                                            observe_comparative)

ROOT = Path(__file__).resolve().parents[1]
LATER = "0000999999-27-000001"


def _later(value, **fields):
    return replace(obs("revenue", value, **fields), accession=LATER)


def test_select_face_value_restated_keeps_the_original_alongside():
    original = obs("revenue", 100)
    assert select_face_value(original, [_later(100)]) is original
    restated = select_face_value(original, [_later(90), _later(110)])
    assert restated.value == Decimal(110) and RESTATED in restated.flags
    assert (restated.original_value, restated.original_accession) == (Decimal(100),
                                                                      FILING.accession)
    assert restated.accession == LATER


def test_select_face_value_from_a_later_filing_when_the_own_value_is_not_accepted():
    disagree = obs("revenue", status="DISAGREE", accepted=False, reason="VALUES_DIFFER")
    chosen = select_face_value(disagree, [_later(100)])
    assert chosen.value == Decimal(100) and FROM_LATER_FILING in chosen.flags
    unchecked = observe_comparative(replace(pending("revenue", 100), accession=LATER))
    assert select_face_value(disagree, [unchecked]) is disagree


def _analysis(observations, comparatives=None):
    return FilingAnalysis(FILING, ("IS",), None, observations, comparatives or {}, [], {}, 6)


def test_never_reported_looks_at_every_filing_and_column():
    absent = _analysis({"inventory": obs("inventory")})
    assert never_reported([absent], "inventory")
    shown_later = _analysis({"inventory": obs("inventory")},
                            {2024: {"inventory": obs("inventory", 5)}})
    assert not never_reported([absent, shown_later], "inventory")
    assert not never_reported([_analysis({"inventory": obs("inventory", 5)})], "inventory")


def test_absence_unknown_follows_the_channels_that_could_show_each_item():
    """#147: canonical items can be shown by both channels, A.2 inputs by the tags alone."""
    configs = StatementConfigs.load()
    assert absence_unknown(FULL, configs) == absence_unknown(NO_COMPANYFACTS, configs) == set()
    hidden_document = absence_unknown(NO_DOCUMENT, configs)
    assert "revenue" in hidden_document and "commercial_paper" not in hidden_document
    untagged = absence_unknown(UNTAGGED, configs)
    assert {"revenue", "commercial_paper", "nci_net_income"} <= untagged


def test_never_read_reason_names_the_hidden_channel():
    """#147: NOT_REPORTED only when every channel that could show the item is visible."""
    unknown = frozenset({"revenue"})
    assert never_read_reason("revenue") == NOT_REPORTED
    assert never_read_reason("commercial_paper", UNTAGGED, unknown) == NOT_REPORTED
    assert never_read_reason("revenue", UNTAGGED, unknown) == TAG_HIDDEN
    assert never_read_reason("revenue", NO_DOCUMENT, unknown) == DOCUMENT_HIDDEN


def test_located_statements_need_rows():
    """#151: a statement located without rows for the period is no reading."""
    found = {"IS": [], "BS": [row(0, "Total assets")]}
    assert located_statements(found) == ("BS",)
    assert located_statements(found, NO_DOCUMENT) == ()


DEBT = DerivationSpec("funded_debt", "formula", ((1, "long_term_debt"),),
                      ((1, "long_term_debt_current"),))


def _debt_configs():
    configs = StatementConfigs.load()
    current = configs.model_inputs.inputs["long_term_debt_current"]  # an A.2 input
    return replace(configs, concept_map={"long_term_debt": configs.concept_map["long_term_debt"]},
                   model_inputs=replace(configs.model_inputs,
                                        inputs={"long_term_debt_current": current}),
                   derivations=Derivations({}, (DEBT,)))


def test_assemble_year_under_a_view_that_hides_the_tags():
    """#147: an item nothing reads is TAG_HIDDEN, never NOT_REPORTED, and a sum with it is a
    GAP; with both channels visible the same readings give NOT_REPORTED and the sum without it."""
    analyses = [_analysis({"long_term_debt": obs("long_term_debt", 100),
                           "long_term_debt_current": obs("long_term_debt_current")})]
    never = {"long_term_debt_current"}
    values, _ = assemble_year(0, analyses, _debt_configs(), never, UNTAGGED)
    assert values["long_term_debt_current"].reason == TAG_HIDDEN
    assert (values["funded_debt"].reason, values["funded_debt"].value) == (INPUT_GAP, None)
    for view in (FULL, NO_DOCUMENT):  # the tag channel, the only one that could show it, is seen
        values, _ = assemble_year(0, analyses, _debt_configs(), never, view)
        assert values["long_term_debt_current"].reason == NOT_REPORTED
        assert values["funded_debt"].value == Decimal(100)
        assert "OMITTED:long_term_debt_current" in values["funded_debt"].flags


NIL_DEBT = replace(DEBT, unread_year_nil=True)


def _year(fiscal_year, observations, comparatives=None):
    filing = replace(FILING, fiscal_year=fiscal_year)
    return FilingAnalysis(filing, ("BS",), None, observations, comparatives or {}, [], {}, 6)


def test_read_years_and_nil_components():
    """#152: the years any channel read a component (own column or a later prior-year column)."""
    analyses = [_year(2023, {"x": obs("x")}), _year(2024, {"x": obs("x")}, {2023: {}}),
                _year(2025, {"x": obs("x", 5)}, {2024: {"x": obs("x", 4)}})]
    assert read_years(analyses, "x") == {2024, 2025}
    assert read_years(analyses[:2], "x") == frozenset()
    configs = StatementConfigs.load()
    assert nil_components(configs.derivations) == (
        "long_term_debt_current", "short_term_borrowings", "commercial_paper")
    assert nil_components(Derivations({}, (DEBT,))) == ()


def test_nil_unread_year_keeps_status_and_names_the_years():
    nil = nil_unread_year(obs("long_term_debt_current"), 2023, [2024, 2025])
    assert (nil.value, nil.status, nil.classification, nil.reason, nil.accepted) == (
        Decimal(0), "GAP", "MODEL_ASSUMPTION", NIL_UNREAD_YEAR, True)
    assert NIL_UNREAD_YEAR in nil.flags
    assert nil.basis == "nil: no channel read it in FY2023; read in FY2024, FY2025 (#152)"


def test_assemble_year_nil_for_an_unread_year_read_in_another_year():
    """#152 (Vineet): unread this year (NEITHER) but read in another year -> nil, flagged, in
    the sum; never read -> NOT_REPORTED and left out; hidden channel -> no nil (#147)."""
    configs = replace(_debt_configs(), derivations=Derivations({}, (NIL_DEBT,)))
    analyses = [_year(2024, {"long_term_debt": obs("long_term_debt", 100),
                             "long_term_debt_current": obs("long_term_debt_current")}),
                _year(2025, {"long_term_debt": obs("long_term_debt", 90),
                             "long_term_debt_current": tag_only("long_term_debt_current", 7)})]
    read = {"long_term_debt_current": read_years(analyses, "long_term_debt_current")}
    values, _ = assemble_year(0, analyses, configs, set(), FULL, read)
    current, debt = values["long_term_debt_current"], values["funded_debt"]
    assert (current.value, current.reason) == (Decimal(0), NIL_UNREAD_YEAR)
    assert current.basis == "nil: no channel read it in FY2024; read in FY2025 (#152)"
    assert debt.value == Decimal(100) and debt.status == "VERIFIED"
    assert debt.flags == ("NIL_UNREAD_YEAR:long_term_debt_current",)
    assert debt.inputs == ("long_term_debt", "long_term_debt_current")
    values, _ = assemble_year(1, analyses, configs, set(), FULL, read)
    assert values["funded_debt"].value == Decimal(97)  # the year it was read: as reported
    # Never read: stays a gap (NOT_REPORTED), never nil; the sum leaves it out as before.
    never = [_year(2024, {"long_term_debt": obs("long_term_debt", 100),
                          "long_term_debt_current": obs("long_term_debt_current")})]
    values, _ = assemble_year(0, never, configs, {"long_term_debt_current"}, FULL,
                              {"long_term_debt_current": frozenset()})
    assert values["long_term_debt_current"].value is None
    assert values["long_term_debt_current"].reason == NOT_REPORTED
    assert values["funded_debt"].flags == ("OMITTED:long_term_debt_current",)
    # The tags hidden: a tag-only input's absence is unknown, so no nil and no partial sum.
    values, _ = assemble_year(0, analyses, configs, set(), UNTAGGED, read)
    assert values["long_term_debt_current"].value is None
    assert values["funded_debt"].reason == INPUT_GAP
    # A reading that was not accepted is a reading: never nil.
    analyses[0].observations["long_term_debt_current"] = obs(
        "long_term_debt_current", status="DISAGREE", accepted=False, reason="VALUES_DIFFER")
    read = {"long_term_debt_current": read_years(analyses, "long_term_debt_current")}
    values, _ = assemble_year(0, analyses, configs, set(), FULL, read)
    assert values["long_term_debt_current"].status == "DISAGREE"
    assert values["funded_debt"].reason == INPUT_GAP


def test_substitute_depreciation_with_d_and_a():
    untagged = obs("depreciation")
    stand_in = tag_only("depreciation_and_amortisation", 70)
    used = substitute(untagged, stand_in, "D&A")
    assert (used.metric, used.value, used.basis) == ("depreciation", Decimal(70), "D&A")
    assert "SUBSTITUTE:depreciation_and_amortisation" in used.flags
    tagged = tag_only("depreciation", 50)
    assert substitute(tagged, stand_in, "D&A") is tagged
    assert substitute(untagged, pending("depreciation_and_amortisation", 70), "D&A") is untagged


def test_comparative_record_keeps_the_later_filing():
    prior = replace(FILING, fiscal_year=2024, start=date(2024, 1, 1), end=date(2024, 12, 31))
    record = _comparative_record(FILING, prior)
    assert (record.fiscal_year, record.start, record.end) == (2024, prior.start, prior.end)
    assert (record.accession, record.filed) == (FILING.accession, FILING.filed)


def test_nci_statuses():
    facts = [fact("us-gaap:MinorityInterest", 5)]
    found = nci_statuses(facts, FILING, ((("us-gaap:MinorityInterest",), "instant"),
                                         (("us-gaap:NciNet",), "duration")))
    assert found == {("us-gaap:MinorityInterest",): "REPORTED", ("us-gaap:NciNet",): "ABSENT"}
    assert START  # duration facts would need the filing's start


def test_refuses_held_out_tickers_without_reading_evidence():
    with pytest.raises(HeldOutError):
        build_statement_set(None, "COST", date(2026, 11, 24), None, universe={"COST": "held_out"})


class _LateStore:
    def load_filings(self, ticker, as_of):
        return [replace(FILING, filed=date(2027, 1, 1))]


def test_refuses_filings_filed_after_as_of():
    with pytest.raises(ValueError, match="after"):
        build_statement_set(_LateStore(), "SYNT", date(2026, 11, 24), None,
                            universe={"SYNT": "train"}, configs=StatementConfigs.load())
    with pytest.raises(ValueError, match="after"):
        analyse_filing(None, replace(FILING, filed=date(2027, 1, 1)), [], None, {},
                       StatementConfigs.load(), date(2026, 11, 24))


def test_configs_load():
    configs = StatementConfigs.load()
    assert "revenue" in configs.concept_map and configs.nci_concepts
    assert configs.label_rules


MODEL = ROOT / "models" / "m1_v3.joblib"


@pytest.mark.skipif(not MODEL.exists(), reason="M1 v3 model file not present")
def test_end_to_end_validation_company():
    from finagent.evidence import EvidenceStore
    from finagent.training.m1 import load_model

    store = EvidenceStore(ROOT / "evidence")
    classifier = load_model(ROOT / "models", "v3", ROOT / "datasets" / "rows_v3.meta.json")
    statement_set = build_statement_set(store, "MSFT", date(2026, 11, 24), classifier)
    assert statement_set.years() == sorted(statement_set.years())
    for observation in statement_set.observations.values():
        assert observation.status and observation.accession and observation.filing_url
        if observation.value is None:
            assert not observation.accepted
    i9 = [r for r in statement_set.identities if r.identity_id == "I9"]
    assert len(i9) == len(statement_set.years()) and all(r.status == "PASS" for r in i9)
    latest = max(statement_set.years())
    assert statement_set.get(latest, "revenue").status == "VERIFIED"
