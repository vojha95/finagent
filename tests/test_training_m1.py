import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from finagent.document.classify import OTHER, TfidfLogisticClassifier
from finagent.training.m1 import (
    HAND_WRITTEN,
    ModelDatasetMismatch,
    calibration,
    expected_calibration_error,
    fit_temperature,
    load_model,
    load_rows,
    main,
    model_card,
    negative_log_likelihood,
    row_from_record,
    save_model,
    select_c,
    train,
    write_model_card,
)

LABELS = {
    "revenue": ["Total net sales", "Total revenue", "Net revenues", "Revenues", "Net sales"],
    "net_income": ["Net income", "Net earnings", "Net income (loss)", "Net earnings attributable"],
    OTHER: ["Fulfillment", "Marketing", "Technology and content", "Other income", "Restructuring"],
}


def _record(i, label, item, split, issuer="AAA"):
    return {
        "issuer": issuer, "cik": "1", "fiscal_year": 2025, "accession": f"acc-{issuer}",
        "split": split, "statement": "IS", "row_id": f"acc-{issuer}:IS:{i}", "position": i,
        "row_text": label, "parent_header": None if i % 2 else "Revenues:", "indent": 0,
        "is_total": False, "is_bold": False, "cell_texts": ["1", "2"],
        "column_periods": ["2025-12-31", None], "column_kinds": ["amount", "amount"],
        "unit_text": "(in millions)", "table_index": 3, "source_row": i, "item": item,
        "label_reason": "MAPPED", "label_concepts": [],
    }


def _dataset(tmp_path, version="vt"):
    records = []
    for split, issuer in (("train", "AAA"), ("train", "BBB"), ("validation", "CCC")):
        for item, labels in LABELS.items():
            for label in labels:
                records.append(_record(len(records), label, item, split, issuer))
    records.append(_record(len(records), "Ambiguous total", None, "train"))  # excluded row
    path = tmp_path / f"rows_{version}.parquet"
    pd.DataFrame(records).to_parquet(path, index=False)
    path.with_name(f"rows_{version}.meta.json").write_text(
        json.dumps({"version": version, "git_commit": "0" * 40}))
    return path


def test_row_from_record_rebuilds_visible_fields():
    row = row_from_record(_record(1, "Net sales", "revenue", "train"))
    assert row.label == "Net sales" and row.parent_header is None and row.statement == "IS"
    assert row.column_periods == (date(2025, 12, 31), None)
    assert row.cell_texts == ("1", "2") and row.column_spans == ()
    assert row.column_kinds == ("amount", "amount")


def test_load_rows_drops_excluded_and_keeps_other(tmp_path):
    path = _dataset(tmp_path)
    rows, items = load_rows(path, "train")
    assert len(rows) == len(items) == 2 * sum(len(v) for v in LABELS.values())
    assert OTHER in items and None not in items
    assert all(r.label != "Ambiguous total" for r in rows)
    val_rows, _ = load_rows(path, "validation")
    assert {r.accession for r in val_rows} == {"acc-CCC"}
    assert load_rows(path, "held_out") == ([], [])


def test_expected_calibration_error_hand_computed():
    assert expected_calibration_error([1.0, 1.0], [True, True]) == 0.0
    # one bin: confidence 0.9, accuracy 0.5
    assert expected_calibration_error([0.9, 0.9], [True, False]) == pytest.approx(0.4)
    # two bins, half the rows each: |1 - 0.95| and |0 - 0.55|
    assert expected_calibration_error([0.95, 0.55], [True, False], bins=10) == pytest.approx(
        0.5 * 0.05 + 0.5 * 0.55)
    assert expected_calibration_error([], []) is None


def test_fit_temperature_softens_an_overconfident_model():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 3, size=400)
    logits = rng.normal(size=(400, 3))
    logits[np.arange(400), y] += 1.0  # right on average, but scaled up 10x below
    temperature = fit_temperature(10 * logits, y)
    assert temperature > 1
    assert negative_log_likelihood(10 * logits, y, temperature) < negative_log_likelihood(
        10 * logits, y, 1.0)


def test_select_c_and_calibration(tmp_path):
    path = _dataset(tmp_path)
    train_set, val_set = load_rows(path, "train"), load_rows(path, "validation")
    c, scores, model = select_c(train_set, val_set, grid=(4.0, 0.5))
    assert set(scores) == {0.5, 4.0} and c in scores
    assert scores[c] == max(scores.values())
    assert isinstance(model, TfidfLogisticClassifier)
    cal = calibration(model, *val_set)
    assert cal["temperature"] > 0 and cal["rows"] == len(val_set[0])
    assert cal["nll_after"] <= cal["nll_before"] + 1e-9


def test_save_and_load_model_checks_dataset_hash(tmp_path):
    path = _dataset(tmp_path)
    model = TfidfLogisticClassifier(temperature=2.0).fit(*load_rows(path, "train"))
    from finagent.training.m1 import file_sha256

    meta = {"dataset_meta_sha256": file_sha256(path.with_name("rows_vt.meta.json"))}
    save_model(model, meta, tmp_path / "models", "vt")
    loaded = load_model(tmp_path / "models", "vt", path.with_name("rows_vt.meta.json"))
    assert loaded.temperature == 2.0 and loaded.classes == model.classes
    path.with_name("rows_vt.meta.json").write_text("{}")
    with pytest.raises(ModelDatasetMismatch):
        load_model(tmp_path / "models", "vt", path.with_name("rows_vt.meta.json"))


def test_train_meta_and_model_card(tmp_path):
    path = _dataset(tmp_path)
    model, meta = train(path, "vt", grid=(1.0,), root=tmp_path)
    assert meta["c"] == 1.0 and meta["model_version"] == "m1-tfidf-lr-vt" == model.version
    assert meta["train_rows"] == 28 and meta["train_companies"] == 2
    assert model.temperature == meta["calibration"]["temperature"]
    assert meta["validation_rows"]["rows"] == 14
    card = model_card(meta)
    assert "Model card: M1 vt" in card and "| C | macro-F1 |" in card and "revenue" in card


def test_write_model_card_keeps_hand_written_text(tmp_path):
    path = _dataset(tmp_path)
    _, meta = train(path, "vt", grid=(1.0,), root=tmp_path)
    card = tmp_path / "card.md"
    write_model_card(meta, card)
    card.write_text(card.read_text() + "## Known failure modes\nnotes\n")
    write_model_card(meta, card)
    text = card.read_text()
    assert text.count(HAND_WRITTEN) == 1 and text.endswith("## Known failure modes\nnotes\n")


def test_cli_writes_model_meta_and_card(tmp_path, capsys):
    path = _dataset(tmp_path)
    assert main(["--version", "vt", "--dataset", str(path), "--models-dir",
                 str(tmp_path / "models"), "--card-dir", str(tmp_path / "cards"),
                 "--grid", "1"]) == 0
    assert (tmp_path / "models" / "m1_vt.joblib").exists()
    meta = json.loads((tmp_path / "models" / "m1_vt.meta.json").read_text())
    assert meta["c"] == 1.0 and "ece_after" in meta["calibration"]
    assert (tmp_path / "cards" / "m1_vt.md").exists()
    assert "ECE" in capsys.readouterr().out
