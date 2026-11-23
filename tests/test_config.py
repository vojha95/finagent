from pathlib import Path

import pytest

from finagent.config import ConfigError, core_items, load_concept_map

CORE = {
    "revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "capital_expenditure",
    "cash_and_cash_equivalents",
    "accounts_receivable",
    "inventory",
    "accounts_payable",
}


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "map.yaml"
    path.write_text(body, encoding="utf-8")
    return path


ITEM = """
  - id: {id}
    label: L
    statement: IS
    period_type: duration
    sign: positive
    core: true
    concepts: [{concepts}]
"""


def test_repo_concept_map_core_items() -> None:
    specs = load_concept_map()
    assert {s.item_id for s in core_items(specs)} == CORE


def test_repo_concept_map_period_types() -> None:
    specs = load_concept_map()
    instants = {"cash_and_cash_equivalents", "accounts_receivable", "inventory", "accounts_payable"}
    for item_id in CORE:
        expected = "instant" if item_id in instants else "duration"
        assert specs[item_id].period_type == expected, item_id


def test_repo_concept_map_prototype_candidates() -> None:
    specs = load_concept_map()
    assert "us-gaap:SalesRevenueNet" in specs["revenue"].concepts
    assert "us-gaap:AccountsNotesAndLoansReceivableNetCurrent" in specs["accounts_receivable"].concepts
    assert "us-gaap:PaymentsToAcquireProductiveAssets" in specs["capital_expenditure"].concepts


def test_repo_concept_map_splits_nci_quantities() -> None:
    specs = load_concept_map()
    assert specs["net_income"].concepts == ("us-gaap:NetIncomeLoss",)
    assert specs["net_income_including_nci"].concepts == ("us-gaap:ProfitLoss",)
    assert specs["total_equity"].concepts == (
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    )
    assert specs["equity_attributable_to_parent"].concepts == ("us-gaap:StockholdersEquity",)
    assert not specs["net_income_including_nci"].core


def test_load_valid(tmp_path: Path) -> None:
    path = _write(tmp_path, "items:" + ITEM.format(id="a", concepts="us-gaap:X, us-gaap:Y"))
    specs = load_concept_map(path)
    assert specs["a"].concepts == ("us-gaap:X", "us-gaap:Y")
    assert core_items(specs) == [specs["a"]]


def test_duplicate_id_rejected(tmp_path: Path) -> None:
    body = "items:" + ITEM.format(id="a", concepts="us-gaap:X") + ITEM.format(id="a", concepts="us-gaap:Y")
    with pytest.raises(ConfigError, match="Duplicate"):
        load_concept_map(_write(tmp_path, body))


def test_concept_in_two_items_rejected(tmp_path: Path) -> None:
    body = "items:" + ITEM.format(id="a", concepts="us-gaap:X") + ITEM.format(id="b", concepts="us-gaap:X")
    with pytest.raises(ConfigError, match="both"):
        load_concept_map(_write(tmp_path, body))


def test_bad_period_type_rejected(tmp_path: Path) -> None:
    body = "items:" + ITEM.format(id="a", concepts="us-gaap:X").replace("duration", "annual")
    with pytest.raises(ConfigError, match="period_type"):
        load_concept_map(_write(tmp_path, body))


def test_unprefixed_concept_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="prefix"):
        load_concept_map(_write(tmp_path, "items:" + ITEM.format(id="a", concepts="Revenues")))


def test_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_concept_map(tmp_path / "absent.yaml")
