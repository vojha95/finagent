import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from finagent.document.classify import OTHER
from finagent.training import m3
from finagent.training.m1 import HAND_WRITTEN, ModelDatasetMismatch
from finagent.training.m2 import company_folds, load_oof, load_split
from finagent.training.m3 import (
    BudgetExceeded,
    EpochScore,
    ModelFileMismatch,
    base_model,
    early_stopping_run,
    estimate_seconds,
    load_meta,
    load_model,
    main,
    load_selection,
    model_card,
    out_of_fold,
    save_model,
    save_selection,
    select_setting,
    selection_signature,
    train,
)
from tests.test_document_encoder import tiny_base

LABELS = {
    "revenue": ["Total net sales", "Total revenue", "Net revenues", "Revenues"],
    "net_income": ["Net income", "Net earnings", "Net income (loss)", "Net earnings attributable"],
    OTHER: ["Fulfillment", "Marketing", "Technology and content", "Other income"],
}
FIXED = {"batch_size": 8, "max_length": 32, "warmup": 0.1, "weight_decay": 0.01, "dropout": 0.1,
         "seed": 0}


def _record(i, label, item, split, issuer):
    return {
        "issuer": issuer, "cik": "1", "fiscal_year": 2025, "accession": f"acc-{issuer}",
        "split": split, "statement": "IS", "row_id": f"acc-{issuer}:IS:{i}", "position": i,
        "row_text": label, "parent_header": None, "indent": 0, "is_total": False,
        "is_bold": False, "cell_texts": [str(10 + i), str(20 + i)],
        "column_periods": ["2025-12-31", "2024-12-31"], "column_kinds": ["amount", "amount"],
        "unit_text": "(in millions)", "table_index": 3, "source_row": i, "item": item,
        "label_reason": "MAPPED", "label_concepts": [],
    }


def _dataset(tmp_path, version="vt"):
    records = []
    for split, issuer in (("train", "AAA"), ("train", "BBB"), ("train", "DDD"),
                          ("validation", "CCC")):
        for item, labels in LABELS.items():
            for label in labels:
                records.append(_record(len(records), label, item, split, issuer))
    path = tmp_path / f"rows_{version}.parquet"
    pd.DataFrame(records).to_parquet(path, index=False)
    path.with_name(f"rows_{version}.meta.json").write_text(
        json.dumps({"version": version, "git_commit": "0" * 40}))
    return path


@pytest.fixture(scope="module")
def pin(tmp_path_factory):
    return SimpleNamespace(key="tiny", model_id="tiny-bert", repo="local/tiny-bert",
                           revision="r0", fingerprint="f" * 64,
                           directory=tiny_base(tmp_path_factory.mktemp("tiny_bert")))


@pytest.fixture
def offline_pin(monkeypatch, pin):
    monkeypatch.setattr(m3, "base_model", lambda key=m3.BASE_PIN: pin)
    return pin


SETTINGS = ({"learning_rate": 5e-3, "class_weight": "balanced"},
            {"learning_rate": 5e-3, "class_weight": None})


def score(setting, epoch, f1, seconds=10.0):
    return EpochScore(setting, 1e-4, ("balanced", "sqrt", None)[setting], epoch, f1, 0.5, seconds)


def test_select_setting_prefers_best_f1_then_fewer_epochs_then_earlier_setting():
    assert select_setting([score(0, 2, 0.8), score(1, 3, 0.9)]) == score(1, 3, 0.9)
    tied = [score(2, 3, 0.9), score(1, 4, 0.9), score(2, 2, 0.9), score(1, 2, 0.9)]
    assert select_setting(tied) == score(1, 2, 0.9)
    assert score(2, 1, 0.5).options() == {"learning_rate": 1e-4, "class_weight": None}
    with pytest.raises(ValueError):
        select_setting([])


def test_estimate_seconds_scales_folds_and_final_fit():
    scores = [score(0, 1, 0.5, 80.0), score(0, 2, 0.6, 150.0)]
    # 80 s per epoch (the slowest), 3 epochs, 4 fold fits + a final fit on 1.25x the rows
    assert estimate_seconds(scores, 1250, 1000, 3, 4) == pytest.approx(80 * 3 * 5.25)


def test_base_model_verifies_the_pin_and_goes_offline(monkeypatch):
    import os

    calls = []
    fake = SimpleNamespace(key="embedder")
    monkeypatch.setattr(m3, "load_model_pins", lambda: {"embedder": fake})
    monkeypatch.setattr("finagent.retrieval.models.verify", lambda p: calls.append(p) or p)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert base_model() is fake and calls == [fake]
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_early_stopping_run_stops_after_patience_and_keeps_every_epoch(tmp_path, pin):
    rows, items, groups = load_split(_dataset(tmp_path), "train")
    classes = sorted(set(items))
    train_idx, test_idx = company_folds(groups, 3)[0]
    scores, probabilities = early_stopping_run(
        pin.directory, rows, items, classes, train_idx, test_idx, 1, SETTINGS[1], max_epochs=6,
        patience=1, fixed=FIXED, device="cpu", log=lambda m: None)
    assert {(s.setting, s.class_weight) for s in scores} == {(1, None)}
    epochs = [s.epoch for s in scores]
    assert epochs == list(range(1, len(epochs) + 1)) and sorted(probabilities) == epochs
    best = max(scores, key=lambda s: (s.macro_f1, -s.epoch))
    assert len(epochs) == 6 or epochs[-1] - best.epoch == 1
    assert probabilities[1].shape == (len(test_idx), len(classes))


def test_out_of_fold_reuses_done_folds_and_fills_the_rest(tmp_path, pin):
    rows, items, groups = load_split(_dataset(tmp_path), "train")
    classes = sorted(set(items))
    folds = company_folds(groups, 3)
    done = np.full((len(folds[0][1]), len(classes)), 1 / len(classes))
    setting = EpochScore(0, 5e-3, "sqrt", 2, 0.5, 0.5, 1.0)
    oof = out_of_fold(pin.directory, rows, items, classes, folds, setting,
                      {0: done}, max_epochs=3, fixed=FIXED, device="cpu", log=lambda m: None)
    assert np.allclose(oof[folds[0][1]], done)
    assert np.allclose(oof.sum(axis=1), 1.0)
    assert not np.allclose(oof[folds[1][1]], 1 / len(classes))


def test_train_save_load_and_card(tmp_path, offline_pin):
    dataset = _dataset(tmp_path)
    model, meta, oof = train(dataset, "vt", settings=SETTINGS, max_epochs=3,
                             patience=1, fixed=FIXED, n_splits=3, device="cpu",
                             log=lambda m: None)
    assert model.version == "m3-bge-small-ft-vt" and meta["model_version"] == model.version
    assert meta["chosen"] == min(meta["selection"], key=lambda s: (-s["macro_f1"], s["epoch"],
                                                                   s["setting"]))
    assert {s["setting"] for s in meta["selection"]} == {0, 1}
    assert meta["params"]["class_weight"] == meta["chosen"]["class_weight"]
    assert meta["train_rows"] == 36 and meta["validation_companies"] == 1
    assert meta["base"]["revision"] == "r0" and meta["precision"] == "fp32"
    assert set(meta["timings_s"]) == {"selection", "out_of_fold", "final", "total"}
    probabilities, classes, labels, issuers = oof
    assert probabilities.shape == (36, 3) and np.allclose(probabilities.sum(axis=1), 1.0)

    models_dir = tmp_path / "models"
    path = save_model(model, meta, models_dir, "vt", oof)
    saved = load_meta(models_dir, "vt")
    assert saved["weights"] == path.name and len(saved["weights_sha256"]) == 64
    assert load_oof(models_dir, "vt", "m3")[1] == list(classes)
    meta_path = dataset.with_name("rows_vt.meta.json")
    loaded = load_model(models_dir, "vt", meta_path, device="cpu")
    val_rows, _, _ = load_split(dataset, "validation")
    assert loaded.version == model.version
    assert np.allclose(loaded.probabilities(val_rows), model.probabilities(val_rows), atol=1e-6)
    card = model_card(saved)
    assert "## Settings and early stopping" in card and "fp32" in card

    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ModelFileMismatch):
        load_model(models_dir, "vt", meta_path, device="cpu")
    meta_path.write_text(json.dumps({"version": "vt", "git_commit": "1" * 40}))
    with pytest.raises(ModelDatasetMismatch):
        load_model(models_dir, "vt", meta_path, device="cpu")


def test_selection_checkpoint_round_trip_and_signature_check(tmp_path):
    signature = selection_signature(SETTINGS, 3, 1, FIXED, 3, 0, "abc", "r0")
    scores = [score(0, 1, 0.5), score(1, 1, 0.6)]
    save_selection(tmp_path, signature, scores, 12.5)
    assert load_selection(tmp_path, signature) == (scores, 12.5)
    other = selection_signature(SETTINGS, 4, 1, FIXED, 3, 0, "abc", "r0")
    assert load_selection(tmp_path, other) is None
    assert load_selection(tmp_path / "missing", signature) is None


def test_out_of_fold_saves_folds_resumes_and_checks_the_budget(tmp_path, pin):
    rows, items, groups = load_split(_dataset(tmp_path), "train")
    classes = sorted(set(items))
    folds = company_folds(groups, 3)
    setting = EpochScore(0, 5e-3, "sqrt", 1, 0.5, 0.5, 1.0)
    work = tmp_path / "work"
    work.mkdir()
    calls = []

    def over_budget(seconds, left):
        calls.append(left)
        raise BudgetExceeded("stop")

    with pytest.raises(BudgetExceeded):
        out_of_fold(pin.directory, rows, items, classes, folds, setting, {}, max_epochs=2,
                    fixed=FIXED, device="cpu", log=lambda m: None, work_dir=work,
                    budget=over_budget)
    assert calls == [2] and sorted(p.name for p in work.iterdir()) == ["fold_0.npy"]
    first = np.load(work / "fold_0.npy")
    messages = []
    oof = out_of_fold(pin.directory, rows, items, classes, folds, setting, {}, max_epochs=2,
                      fixed=FIXED, device="cpu", log=messages.append, work_dir=work,
                      budget=lambda seconds, left: calls.append(left))
    assert np.array_equal(oof[folds[0][1]], first) and messages[0] == "  fold 0: read from fold_0.npy"
    assert calls == [2, 1] and len(list(work.iterdir())) == 3


def test_train_resumes_from_a_saved_selection(tmp_path, offline_pin):
    dataset = _dataset(tmp_path)
    work = tmp_path / "work"
    messages = []
    signature = selection_signature(SETTINGS, 2, 1, FIXED, 3, 0,
                                    m3.file_sha256(dataset.with_name("rows_vt.meta.json")), "r0")
    save_selection(work, signature, [EpochScore(1, 5e-3, None, 2, 0.9, 0.9, 30.0),
                                     EpochScore(0, 5e-3, "balanced", 1, 0.5, 0.5, 15.0)], 45.0)
    _, meta, _ = train(dataset, "vt", settings=SETTINGS, max_epochs=2, patience=1, fixed=FIXED,
                       n_splits=3, device="cpu", log=messages.append, work_dir=work)
    assert meta["selection_resumed"] and meta["chosen"]["setting"] == 1
    assert meta["chosen"]["epoch"] == 2 and meta["timings_s"]["selection"] == 45.0
    assert any(m.startswith("selection read from") for m in messages)
    assert sorted(p.name for p in work.iterdir()) == ["fold_0.npy", "fold_1.npy", "fold_2.npy",
                                                      "selection.json"]


def test_train_stops_before_out_of_fold_when_over_budget(tmp_path, offline_pin):
    with pytest.raises(BudgetExceeded):
        train(_dataset(tmp_path), "vt", settings=SETTINGS[:1], max_epochs=2, patience=1,
              fixed=FIXED, n_splits=3, budget_minutes=0.0, device="cpu", log=lambda m: None)


def test_main_writes_model_meta_oof_and_card(tmp_path, offline_pin, monkeypatch):
    original = m3.train
    monkeypatch.setattr(m3, "train", lambda dataset, version, budget_minutes, log, work_dir:
                        original(dataset, version, settings=SETTINGS[:1], max_epochs=2,
                                 patience=1, fixed=FIXED, n_splits=3,
                                 budget_minutes=budget_minutes, device="cpu", log=log,
                                 work_dir=work_dir))
    models_dir, cards = tmp_path / "models", tmp_path / "cards"
    cards.mkdir()
    (cards / "m3_vt.md").write_text(f"old\n{HAND_WRITTEN}\nkept note\n")
    assert main(["--version", "vt", "--dataset", str(_dataset(tmp_path)),
                 "--models-dir", str(models_dir), "--card-dir", str(cards)]) == 0
    # the work directory (checkpoints) is removed once the model is saved
    assert {p.name for p in models_dir.iterdir()} == {"m3_vt.safetensors", "m3_vt.meta.json",
                                                      "m3_vt.oof.npz"}
    card = (cards / "m3_vt.md").read_text()
    assert card.startswith("# Model card: M3 vt") and card.endswith("kept note\n")
    assert main(["--version", "vt", "--dataset", str(_dataset(tmp_path)),
                 "--models-dir", str(models_dir), "--card-dir", str(cards),
                 "--budget-minutes", "0"]) == 2
