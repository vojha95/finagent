import numpy as np
import pytest

from finagent.training.calibration import (
    CALIBRATORS,
    IsotonicScaling,
    PlattScaling,
    TemperatureScaling,
    calibrate,
    choose_method,
    fit_platt,
    fold_ids,
    m1_out_of_fold,
    reliability,
    report_table,
)


def _overconfident(n=600, k=3, seed=0):
    """Probabilities that are right ~60% of the time but claim ~95%."""
    rng = np.random.default_rng(seed)
    y = rng.integers(0, k, size=n)
    logits = rng.normal(size=(n, k))
    logits[np.arange(n), y] += 1.0
    z = 8 * logits
    p = np.exp(z - z.max(axis=1, keepdims=True))
    return p / p.sum(axis=1, keepdims=True), y


def test_reliability_hand_computed():
    p = np.array([[0.9, 0.1], [0.9, 0.1]])
    r = reliability(p, [0, 1])
    assert r["ece"] == pytest.approx(0.4) and r["accuracy"] == 0.5
    assert r["nll"] == pytest.approx(-(np.log(0.9) + np.log(0.1)) / 2)
    assert r["brier"] == pytest.approx(((0.1 ** 2 + 0.1 ** 2) + (0.9 ** 2 + 0.9 ** 2)) / 2)


def test_fit_platt_recovers_a_sigmoid():
    rng = np.random.default_rng(1)
    x = rng.normal(size=4000)
    positive = rng.random(4000) < 1 / (1 + np.exp(-(2 * x - 1)))
    a, b = fit_platt(x, positive)
    assert a == pytest.approx(2, abs=0.25) and b == pytest.approx(-1, abs=0.25)


@pytest.mark.parametrize("method", sorted(CALIBRATORS))
def test_calibrators_lower_ece_and_return_distributions(method):
    p, y = _overconfident()
    calibrated = CALIBRATORS[method]().fit(p, y).transform(p)
    assert calibrated.shape == p.shape
    assert abs(calibrated.sum(axis=1) - 1).max() < 1e-9 and (calibrated >= 0).all()
    assert reliability(calibrated, y)["ece"] < reliability(p, y)["ece"]


def test_temperature_keeps_the_ranking():
    p, y = _overconfident()
    scaler = TemperatureScaling().fit(p, y)
    assert scaler.temperature > 1
    assert (scaler.transform(p).argmax(axis=1) == p.argmax(axis=1)).all()


def test_one_vs_rest_rows_without_support_fall_back_to_uniform():
    q = IsotonicScaling().fit(np.array([[0.9, 0.1], [0.8, 0.2]]), [0, 0]).transform(
        np.array([[0.0, 1.0]]))
    assert abs(q.sum() - 1) < 1e-9
    assert PlattScaling().fit(np.array([[0.9, 0.1], [0.2, 0.8]]), [0, 1]).transform(
        np.array([[0.5, 0.5]])).shape == (1, 2)


def test_fold_ids_and_choose_method():
    p, y = _overconfident()
    groups = [f"c{i % 5}" for i in range(len(y))]
    folds = [(np.array([i for i in range(len(y)) if i % 5 != f]),
              np.array([i for i in range(len(y)) if i % 5 == f])) for f in range(5)]
    ids = fold_ids(groups, folds)
    assert set(ids) == set(range(5)) and ids[7] == 2
    best, scores = choose_method(p, y, ids)
    assert best in ("platt", "isotonic") and set(scores) == {"platt", "isotonic"}
    assert scores[best] == min(scores.values())


def test_calibrate_reports_raw_reference_and_every_method():
    p, y = _overconfident()
    val, val_y = _overconfident(300, seed=2)
    ids = np.arange(len(y)) % 5
    report, fitted = calibrate(p, y, ids, val, val_y, reference={"shipped": val})
    assert set(report["validation"]) == {"raw", "shipped", "temperature", "platt", "isotonic"}
    assert report["validation"]["raw"]["top_changed"] == 0
    assert report["validation"]["temperature"]["top_changed"] == 0
    assert report["chosen"] in ("platt", "isotonic") and set(fitted) == set(CALIBRATORS)
    assert report["temperature"] == fitted["temperature"].temperature
    assert report["oof_rows"] == 600 and report["validation_rows"] == 300
    table = report_table({"models": {"m": report}})
    assert f"| m | {report['chosen']} (chosen) |" in table and "| m | raw |" in table


def test_m1_out_of_fold_predicts_every_row_from_other_companies():
    from datetime import date

    from finagent.records import RowInstance

    def row(label, i):
        return RowInstance(row_id=str(i), accession="a", statement="IS", label=label,
                           parent_header=None, indent=0, is_total=False, is_bold=False,
                           position=i, cell_texts=("1",), column_periods=(date(2025, 12, 31),),
                           unit_text="(in millions)", table_index=0, source_row=i,
                           column_spans=((1, 1),))

    labels = [("Total revenue", "revenue"), ("Net sales", "revenue"),
              ("Net income", "net_income"), ("Net earnings", "net_income")] * 5
    rows = [row(text, i) for i, (text, _) in enumerate(labels)]
    items = [item for _, item in labels]
    groups = [f"c{i // 4}" for i in range(len(rows))]
    oof = m1_out_of_fold(rows, items, groups, ["net_income", "revenue"], c=4.0)
    assert oof.shape == (20, 2) and abs(oof.sum(axis=1) - 1).max() < 1e-9
    assert (oof.argmax(axis=1) == [1 if i == "revenue" else 0 for i in items]).all()


def test_run_m3_calibrates_from_the_saved_oof_and_leaves_other_reports(tmp_path, monkeypatch):
    import json

    import pandas as pd

    from finagent.dataset.build import ROOT
    from finagent.training import m2, m3
    from finagent.training.calibration import run_m3
    from tests.test_training_m3 import LABELS, _record

    records = []
    for split, issuer in [("train", f"T{i}") for i in range(5)] + [("validation", "V")]:
        for item, labels in LABELS.items():
            for label in labels:
                records.append(_record(len(records), label, item, split, issuer))
    pd.DataFrame(records).to_parquet(tmp_path / "rows_vt.parquet", index=False)
    (tmp_path / "rows_vt.meta.json").write_text(json.dumps({"version": "vt"}))
    _, items, groups = m2.load_split(tmp_path / "rows_vt.parquet", "train")
    classes = sorted(set(items))
    oof, _ = _overconfident(len(items), len(classes), seed=1)
    names = tuple(classes)
    models = tmp_path / "models"
    models.mkdir()
    m2.save_oof(models / "m3_vt.oof.npz", oof, classes, items, groups)
    (models / "calibration_vt.meta.json").write_text("{}")

    class Fake:
        version, classes = "m3-fake", names

        def probabilities(self, rows):
            return np.full((len(rows), len(self.classes)), 1 / len(self.classes))

    monkeypatch.setattr(m3, "load_model", lambda *args, **kwargs: Fake())
    report = run_m3("vt", models, tmp_path, ROOT)
    entry = report["models"]["m3-fake"]
    assert entry["chosen"] in {"platt", "isotonic"} and entry["oof_rows"] == len(items)
    assert set(entry["validation"]) == {"raw", "temperature", "platt", "isotonic"}
    assert (models / "calibration_m3_vt.joblib").exists()
    assert json.loads((models / "calibration_m3_vt.meta.json").read_text()) == json.loads(
        json.dumps(report, default=str))
    assert (models / "calibration_vt.meta.json").read_text() == "{}"

    m2.save_oof(models / "m3_vt.oof.npz", oof, classes, list(reversed(items)), groups)
    with pytest.raises(ValueError):
        run_m3("vt", models, tmp_path, ROOT)
