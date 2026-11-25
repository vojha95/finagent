"""Phase 4a configuration loaders: model inputs, derivations, identities, forecast needs."""

import textwrap

import pytest

from finagent.config import (
    ConfigError,
    load_derivations,
    load_forecast_inputs,
    load_identities,
    load_model_inputs,
    parse_term,
)


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def test_real_configs_load_and_cross_check():
    inputs = load_model_inputs()
    derivations = load_derivations(model_inputs=inputs)
    library = load_identities(model_inputs=inputs, derivations=derivations)
    needs = load_forecast_inputs(model_inputs=inputs, derivations=derivations)
    assert inputs.inputs["retained_earnings_opening"].period == "opening_instant"
    assert inputs.inputs["dividends_charged_to_re"].dimensions
    assert inputs.inputs["depreciation"].substitute_item == "depreciation_and_amortisation"
    assert inputs.inputs["revenue"].is_item
    assert "us-gaap:LongTermDebtAndCapitalLeaseObligations" in inputs.finance_lease_concepts
    assert derivations.bases["pretax_basis"][0] == "pretax_income"
    face = next(r for r in derivations.rules if r.kind == "face_block_sum")
    assert face.derivation_id == "total_liabilities" and face.include_anchor
    assert {i.identity_id for i in library.identities} == {f"I{n}" for n in range(1, 13)}
    assert {n.need_id for n in needs} >= {"revenue", "dividends_paid", "pretax_basis"}


def test_parse_term():
    assert parse_term("+revenue", "x") == (1, "revenue")
    assert parse_term("-cost_of_revenue", "x") == (-1, "cost_of_revenue")
    with pytest.raises(ConfigError):
        parse_term("revenue", "x")


def test_model_inputs_rejects_bad_entries(tmp_path):
    bad_period = _write(tmp_path, "a.yaml", """
        inputs:
          - {id: x, concepts: [us-gaap:X], period: weekly, sign: positive, tier: required}
    """)
    with pytest.raises(ConfigError, match="period"):
        load_model_inputs(bad_period)
    canonical = _write(tmp_path, "b.yaml", """
        inputs:
          - {id: revenue, concepts: [us-gaap:X], period: duration, sign: positive, tier: required}
    """)
    with pytest.raises(ConfigError, match="canonical"):
        load_model_inputs(canonical)
    unknown = _write(tmp_path, "c.yaml", """
        inputs:
          - {item: not_an_item, tier: required}
    """)
    with pytest.raises(ConfigError, match="unknown item"):
        load_model_inputs(unknown)
    bad_dims = _write(tmp_path, "d.yaml", """
        inputs:
          - {id: x, concepts: [us-gaap:X], period: duration, sign: positive, tier: optional,
             dimensions: {Axis: Member}}
    """)
    with pytest.raises(ConfigError, match="dimensions"):
        load_model_inputs(bad_dims)


def test_derivations_reject_unknown_ids_overwrite_and_bad_patterns(tmp_path):
    inputs = load_model_inputs()
    unknown = _write(tmp_path, "a.yaml", """
        derivations:
          - {id: new_one, kind: formula, terms: ["+revenue", "-nonsense"]}
    """)
    with pytest.raises(ConfigError, match="nonsense"):
        load_derivations(unknown, model_inputs=inputs)
    overwrite = _write(tmp_path, "b.yaml", """
        derivations:
          - {id: gross_profit, kind: formula, terms: ["+revenue", "-cost_of_revenue"]}
    """)
    with pytest.raises(ConfigError, match="unless_presented"):
        load_derivations(overwrite, model_inputs=inputs)
    pattern = _write(tmp_path, "c.yaml", """
        derivations:
          - {id: total_liabilities, kind: face_block_sum, unless_presented: true, statement: BS,
             after_item: total_current_liabilities, stop_patterns: ["(unclosed"]}
    """)
    with pytest.raises(ConfigError, match="stop pattern"):
        load_derivations(pattern, model_inputs=inputs)


def test_identities_reject_unknown_metric_and_bad_check(tmp_path):
    unknown = _write(tmp_path, "a.yaml", """
        identities:
          - {id: I1, kind: sum, terms: ["+total_assets", "-nope"], tolerance: default}
    """)
    with pytest.raises(ConfigError, match="nope"):
        load_identities(unknown)
    check = _write(tmp_path, "b.yaml", """
        identities:
          - id: I12
            kind: sanity
            tolerance: exact
            checks: [{rule: less_than, terms: [revenue]}]
    """)
    with pytest.raises(ConfigError, match="sanity"):
        load_identities(check)


def test_i6_parent_net_income_needs_no_nci(tmp_path):
    """#114 / BC26: I6 lists parent net income as compared only when NCI is known absent."""
    i6 = {s.identity_id: s for s in load_identities().identities}["I6"]
    (item, condition), = i6.items_when_no_nci
    assert item == "net_income" and condition.period_type == "duration"
    assert condition.concepts == ("us-gaap:NetIncomeLossAttributableToNoncontrollingInterest",)
    bad = _write(tmp_path, "a.yaml", """
        identities:
          - id: I6
            kind: cf_top_row
            statement: CF
            items: [net_income_including_nci]
            items_when_no_nci: {net_income: {concepts: [us-gaap:X], period: duration}}
            tolerance: default
    """)
    with pytest.raises(ConfigError, match="items_when_no_nci"):
        load_identities(bad)


def test_forecast_needs_validate(tmp_path):
    bad = _write(tmp_path, "a.yaml", """
        needs:
          - {id: revenue, tier: required, years: 0}
    """)
    with pytest.raises(ConfigError):
        load_forecast_inputs(bad)
