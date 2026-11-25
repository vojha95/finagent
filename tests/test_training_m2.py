import json

import numpy as np
import pandas as pd
import pytest

from finagent.document.classify import OTHER, SvdBoostedTreeClassifier
from finagent.training.m1 import HAND_WRITTEN, ModelDatasetMismatch, file_sha256
from finagent.training.m2 import (
    CvScore,
    GridPoint,
    align,
    company_folds,
    cross_validate,
    load_model,
    load_oof,
    load_split,
    main,
    model_card,
    save_model,
    select_point,
    train,
    write_model_card,
)

LABELS = {
    "revenue": ["Total net sales", "Total revenue", "Net revenues", "Revenues"],
    "net_income": ["Net income", "Net earnings", "Net income (loss)", "Net earnings attributable"],
    OTHER: ["Fulfillment", "Marketing", "Technology and content", "Other income"],
}
TINY = {"learning_rate": 0.1, "min_child_samples": 1, "class_weight": "balanced",
        "random_state": 0, "n_jobs": 1}
TINY_GRID = {"n_components": (3, 4), "num_leaves": (4,)}


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
    records.append(_record(len(records), "Ambiguous total", None, "train", "AAA"))
    path = tmp_path / f"rows_{version}.parquet"
    pd.DataFrame(records).to_parquet(path, index=False)
    path.with_name(f"rows_{version}.meta.json").write_text(
        json.dumps({"version": version, "git_commit": "0" * 40}))
    return path


def test_load_split_drops_excluded_rows_and_keeps_issuers(tmp_path):
    rows, items, issuers = load_split(_dataset(tmp_path), "train")
    assert len(rows) == len(items) == len(issuers) == 36
    assert None not in items and set(issuers) == {"AAA", "BBB", "DDD"}
    assert load_split(_dataset(tmp_path), "held_out") == ([], [], [])


def test_company_folds_keep_each_company_on_one_side():
    groups = ["A"] * 3 + ["B"] * 2 + ["C"] * 4
    folds = company_folds(groups, n_splits=3)
    seen = np.concatenate([test for _, test in folds])
    assert sorted(seen) == list(range(len(groups)))
    for train, test in folds:
        assert not {groups[i] for i in train} & {groups[i] for i in test}


def test_align_reorders_and_zero_fills_unseen_classes():
    p = np.array([[0.7, 0.3]])
    assert align(p, ["b", "a"], ["a", "b", "c"]).tolist() == [[0.3, 0.7, 0.0]]


def test_select_point_best_macro_f1_then_smaller_model():
    small, large = GridPoint(64, 15, 100), GridPoint(128, 31, 400)
    assert select_point([CvScore(large, 0.9, 0.9), CvScore(small, 0.8, 0.8)]) == large
    assert select_point([CvScore(large, 0.9, 0.9), CvScore(small, 0.9, 0.8)]) == small
    with pytest.raises(ValueError):
        select_point([])


def test_cross_validate_scores_every_grid_point_and_checkpoint(tmp_path):
    rows, items, groups = load_split(_dataset(tmp_path), "train")
    scores, oof, classes = cross_validate(rows, items, groups, TINY_GRID, (5, 10), TINY,
                                          n_splits=3)
    assert classes == sorted(LABELS)
    assert {s.point for s in scores} == set(oof) == {
        GridPoint(c, 4, n) for c in (3, 4) for n in (5, 10)}
    for p in oof.values():
        assert p.shape == (36, 3) and abs(p.sum(axis=1) - 1).max() < 1e-9  # every row predicted


def test_train_save_load_and_oof(tmp_path):
    path = _dataset(tmp_path)
    model, meta, oof = train(path, "vt", TINY_GRID, (5, 10), TINY, n_splits=3, root=tmp_path)
    assert model.version == "m2-svd-lgbm-vt" == meta["model_version"]
    assert meta["train_rows"] == 36 and meta["train_companies"] == 3
    assert meta["validation_rows"]["rows"] == 12 and meta["validation_companies"] == 1
    assert meta["chosen"] in [s["point"] for s in meta["cv"]]
    models = tmp_path / "models"
    save_model(model, meta, models, "vt", oof)
    loaded = load_model(models, "vt", path.with_name("rows_vt.meta.json"))
    assert isinstance(loaded, SvdBoostedTreeClassifier) and loaded.classes == model.classes
    probabilities, classes, labels, issuers = load_oof(models, "vt")
    assert probabilities.shape == (36, 3) and classes == sorted(LABELS)
    assert labels == oof[2] and issuers == oof[3]
    assert meta["dataset_meta_sha256"] == file_sha256(path.with_name("rows_vt.meta.json"))
    path.with_name("rows_vt.meta.json").write_text("{}")
    with pytest.raises(ModelDatasetMismatch):
        load_model(models, "vt", path.with_name("rows_vt.meta.json"))


def test_model_card_and_hand_written_text(tmp_path):
    _, meta, _ = train(_dataset(tmp_path), "vt", TINY_GRID, (5,), TINY, n_splits=3,
                       root=tmp_path)
    card = model_card(meta)
    assert "Model card: M2 vt" in card and "| SVD components | leaves | trees |" in card
    assert "Validation not used" in card and "log_share_of_max" in card
    path = tmp_path / "card.md"
    write_model_card(meta, path)
    path.write_text(path.read_text() + "## Known failure modes\nnotes\n")
    write_model_card(meta, path)
    text = path.read_text()
    assert text.count(HAND_WRITTEN) == 1 and text.endswith("## Known failure modes\nnotes\n")


def test_cli_writes_model_oof_meta_and_card(tmp_path, capsys, monkeypatch):
    import finagent.training.m2 as m2

    monkeypatch.setattr(m2, "GRID", TINY_GRID)
    monkeypatch.setattr(m2, "train", lambda dataset, version: train(
        dataset, version, TINY_GRID, (5,), TINY, n_splits=3, root=tmp_path))
    path = _dataset(tmp_path)
    assert main(["--version", "vt", "--dataset", str(path), "--models-dir",
                 str(tmp_path / "models"), "--card-dir", str(tmp_path / "cards")]) == 0
    for name in ("m2_vt.joblib", "m2_vt.meta.json", "m2_vt.oof.npz"):
        assert (tmp_path / "models" / name).exists()
    assert (tmp_path / "cards" / "m2_vt.md").exists()
    assert "ECE raw" in capsys.readouterr().out
