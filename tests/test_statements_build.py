"""Statement-set assembly: restatements, NOT_REPORTED, substitution, refusals, end to end."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from statements_support import FILING, START, fact, obs, pending, tag_only

from finagent.config import HeldOutError
from finagent.statements.build import (
    FilingAnalysis,
    StatementConfigs,
    _comparative_record,
    analyse_filing,
    build_statement_set,
    never_reported,
    nci_statuses,
    select_face_value,
    substitute,
)
from finagent.statements.verify_set import FROM_LATER_FILING, RESTATED, observe_comparative

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
