"""Train M2 (TF-IDF SVD + structure + magnitude features -> LightGBM) on the row dataset.

Usage:
    python -m finagent.training.m2 --version v3
reads datasets/rows_v3.parquet and fits on the train split only (excluded rows dropped, OTHER
kept). Hyperparameters are chosen by 5-fold cross-validation grouped by company **within train**
(GroupKFold over issuers; a company's rows never sit on both sides of a fold): the grid point with
the best out-of-fold macro-F1 wins, ties to the smaller model. Validation is never used for a
choice; it is scored once at the end and reported (blueprint 8.4 training protocol).

Writes models/m2_v3.joblib and models/m2_v3.oof.npz (out-of-fold probabilities of the chosen grid
point, for calibration; both gitignored), models/m2_v3.meta.json (tracked) and
docs/model_cards/m2_v3.md (Appendix F.3). Held-out companies have no rows in the dataset and are
never read here.
"""

import argparse
import itertools
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from finagent.config import load_concept_map
from finagent.dataset.build import DATASETS_DIR, ROOT, git_state
from finagent.document.classify import SvdBoostedTreeClassifier
from finagent.evaluation.rows import confusion_pairs, per_item_table, row_metrics
from finagent.records import RowInstance
from finagent.training.m1 import (
    HAND_WRITTEN,
    MODEL_CARDS_DIR,
    MODELS_DIR,
    ModelDatasetMismatch,
    expected_calibration_error,
    file_sha256,
    row_from_record,
)

N_FOLDS = 5
GRID = {"n_components": (64, 128, 256), "num_leaves": (15, 31)}
CHECKPOINTS = (100, 200, 400, 800)  # n_estimators read from one fit per fold
FIXED = {"learning_rate": 0.05, "min_child_samples": 20, "class_weight": "balanced",
         "random_state": 0, "n_jobs": 4}


@dataclass(frozen=True)
class GridPoint:
    n_components: int
    num_leaves: int
    n_estimators: int

    def size(self) -> tuple[int, int, int]:
        """Tie-break order: fewer trees, then fewer leaves, then fewer components."""
        return self.n_estimators, self.num_leaves, self.n_components


@dataclass(frozen=True)
class CvScore:
    point: GridPoint
    macro_f1: float | None
    accuracy: float | None


def load_split(path: Path, split: str) -> tuple[list[RowInstance], list[str], list[str]]:
    """RowInstances, labels and issuers of one split; excluded rows (item None) are dropped."""
    frame = pd.read_parquet(path)
    frame = frame[(frame["split"] == split) & frame["item"].notna()]
    records = frame.to_dict("records")
    return ([row_from_record(r) for r in records], [str(r["item"]) for r in records],
            [str(r["issuer"]) for r in records])


def company_folds(groups: Sequence[str], n_splits: int = N_FOLDS) -> list[tuple[np.ndarray, np.ndarray]]:
    """GroupKFold over companies: every company's rows fall in exactly one test fold."""
    from sklearn.model_selection import GroupKFold

    index = np.arange(len(groups))
    return [(train, test) for train, test in GroupKFold(n_splits=n_splits).split(index, groups=groups)]


def align(probabilities: np.ndarray, model_classes: Sequence[str],
          classes: Sequence[str]) -> np.ndarray:
    """Columns reordered to `classes`; a class the model never saw gets probability 0."""
    out = np.zeros((probabilities.shape[0], len(classes)))
    position = {c: i for i, c in enumerate(classes)}
    for j, c in enumerate(model_classes):
        out[:, position[c]] = probabilities[:, j]
    return out


def cross_validate(
    rows: Sequence[RowInstance], items: Sequence[str], groups: Sequence[str],
    grid: dict[str, Sequence[int]] = GRID, checkpoints: Sequence[int] = CHECKPOINTS,
    fixed: dict[str, Any] = FIXED, n_splits: int = N_FOLDS,
) -> tuple[list[CvScore], dict[GridPoint, np.ndarray], list[str]]:
    """Out-of-fold macro-F1 for every grid point, and the out-of-fold probabilities.

    Each fold fits TF-IDF, SVD and LightGBM on the fold's training companies only; the
    n_estimators checkpoints are read from one fit with max(checkpoints) rounds.
    """
    classes = sorted(set(items))
    folds = company_folds(groups, n_splits)
    oof: dict[GridPoint, np.ndarray] = {}
    for n_components, num_leaves in itertools.product(grid["n_components"], grid["num_leaves"]):
        staged = {n: np.zeros((len(rows), len(classes))) for n in checkpoints}
        for train, test in folds:
            model = SvdBoostedTreeClassifier(
                n_components=n_components, num_leaves=num_leaves,
                n_estimators=max(checkpoints), **fixed,
            ).fit([rows[i] for i in train], [items[i] for i in train])
            for n, p in model.staged_probabilities([rows[i] for i in test], checkpoints).items():
                staged[n][test] = align(p, model.classes, classes)
        for n, p in staged.items():
            oof[GridPoint(n_components, num_leaves, n)] = p
    scores = []
    for point, p in oof.items():
        metrics = row_metrics(items, [classes[j] for j in p.argmax(axis=1)])
        scores.append(CvScore(point, metrics.macro_f1, metrics.accuracy))
    return scores, oof, classes


def select_point(scores: Sequence[CvScore]) -> GridPoint:
    """Best out-of-fold macro-F1; ties go to the smaller model (GridPoint.size)."""
    if not scores:
        raise ValueError("empty grid")
    best = max((s.macro_f1 or 0.0) for s in scores)
    tied = [s.point for s in scores if abs((s.macro_f1 or 0.0) - best) < 1e-12]
    return min(tied, key=GridPoint.size)


def save_model(model: SvdBoostedTreeClassifier, meta: dict[str, Any], models_dir: Path,
               version: str, oof: tuple[np.ndarray, Sequence[str], Sequence[str],
                                        Sequence[str]] | None = None) -> Path:
    """models/m2_{version}.joblib, .meta.json and (if given) .oof.npz (probabilities, classes,
    labels, issuers)."""
    import joblib

    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / f"m2_{version}.joblib"
    joblib.dump(model, path)
    (models_dir / f"m2_{version}.meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    if oof is not None:
        probabilities, classes, labels, issuers = oof
        np.savez_compressed(models_dir / f"m2_{version}.oof.npz", probabilities=probabilities,
                            classes=np.asarray(classes), labels=np.asarray(labels),
                            issuers=np.asarray(issuers))
    return path


def load_model(models_dir: Path, version: str, dataset_meta: Path) -> SvdBoostedTreeClassifier:
    """Load a saved M2; raises ModelDatasetMismatch if it was trained on another dataset build."""
    import joblib

    meta = json.loads((models_dir / f"m2_{version}.meta.json").read_text(encoding="utf-8"))
    if meta["dataset_meta_sha256"] != file_sha256(dataset_meta):
        raise ModelDatasetMismatch(
            f"m2_{version} was trained on another build of {Path(dataset_meta).name}; retrain it")
    return joblib.load(models_dir / f"m2_{version}.joblib")


def load_oof(models_dir: Path, version: str) -> tuple[np.ndarray, list[str], list[str], list[str]]:
    """(probabilities, classes, labels, issuers) saved by save_model."""
    data = np.load(models_dir / f"m2_{version}.oof.npz")
    return (data["probabilities"], [str(c) for c in data["classes"]],
            [str(c) for c in data["labels"]], [str(c) for c in data["issuers"]])


def _fmt(x: float | None, digits: int = 3) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def model_card(meta: dict[str, Any]) -> str:
    """The generated part of the F.3 model card."""
    rm, point = meta["validation_rows"], meta["chosen"]
    cv_rows = sorted(meta["cv"], key=lambda s: (-(s["macro_f1"] or 0.0),
                                                s["point"]["n_estimators"]))
    lines = [
        f"# Model card: M2 {meta['version']} ({meta['model_version']})",
        "",
        f"Generated by `python -m finagent.training.m2 --version {meta['dataset_version']}`; "
        "the hand-written section at the end is kept on regeneration.",
        "",
        "## Name, task, data",
        "",
        "| Field | Value |", "|---|---|",
        f"| Model | `{meta['model_version']}`, `models/m2_{meta['version']}.joblib` (gitignored) |",
        "| Task | Map a face-statement row to one of the canonical items (Appendix A) or OTHER; "
        "top-3 with probabilities (blueprint 8.4). |",
        "| Input | Visible text and layout only: `[statement] parent header | label`, the row's "
        "indent, total styling and position, and the displayed figures of the rows of the same "
        "statement; no tags (rule 7). |",
        f"| Features | TF-IDF word 1-2-grams + character 3-5-grams -> truncated SVD "
        f"({point['n_components']} components); structure and magnitude features "
        f"({', '.join(meta['structure_features'])}). |",
        f"| Classifier | LightGBM multiclass, class_weight=balanced, num_leaves="
        f"{point['num_leaves']}, n_estimators={point['n_estimators']}, learning_rate="
        f"{meta['fixed']['learning_rate']}, min_child_samples={meta['fixed']['min_child_samples']}"
        f", deterministic, {meta['fixed']['n_jobs']} threads. |",
        f"| Hyperparameters | {meta['n_folds']}-fold cross-validation grouped by company within "
        "train (out-of-fold macro-F1; ties to the smaller model). Validation not used. |",
        "| Calibration | Raw LightGBM probabilities; calibrators are fitted separately on the "
        "out-of-fold probabilities (`python -m finagent.training.calibration`). |",
        f"| Training data | `datasets/rows_{meta['dataset_version']}.parquet`, train split only: "
        f"{meta['train_rows']} rows ({meta['train_companies']} companies), OTHER kept. |",
        f"| Dataset build | meta SHA-256 `{meta['dataset_meta_sha256'][:12]}` |",
        f"| Trained at | commit `{(meta['git_commit'] or '')[:7]}`"
        f"{' (uncommitted changes)' if meta['git_dirty'] else ''}, LightGBM "
        f"{meta['lightgbm_version']}, scikit-learn {meta['sklearn_version']} |",
        "",
        "## Cross-validation grid (train, grouped by company; out-of-fold)",
        "",
        "| SVD components | leaves | trees | macro-F1 | accuracy |", "|---|---|---|---|---|",
        *[f"| {s['point']['n_components']} | {s['point']['num_leaves']} | "
          f"{s['point']['n_estimators']} | {_fmt(s['macro_f1'], 4)} | {_fmt(s['accuracy'], 4)} |"
          for s in cv_rows],
        "",
        f"## Metrics on validation ({meta['validation_companies']} companies, {rm['rows']} rows)",
        "",
        "Scored once after the choice; validation chose nothing for M2. Held-out: not run.",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Row accuracy (OTHER included) | {_fmt(rm['accuracy'])} |",
        f"| Macro-F1 over {rm['items_scored']} items with validation rows (OTHER excluded) | "
        f"{_fmt(rm['macro_f1'])} |",
        f"| OTHER precision / recall / F1 | {_fmt(rm['other']['precision'])} / "
        f"{_fmt(rm['other']['recall'])} / {_fmt(rm['other']['f1'])} |",
        f"| ECE (15 bins, top label), raw probabilities | {_fmt(meta['validation_ece'], 4)} |",
        "",
        "E1 field accuracy with company-bootstrap CI: `docs/phase8_v1.md`.",
        "",
        "## Per-item F1 on validation",
        "",
        "| item | precision | recall | F1 | support | predicted |", "|---|---|---|---|---|---|",
        *[f"| {s['item']} | {_fmt(s['precision'])} | {_fmt(s['recall'])} | {_fmt(s['f1'])} | "
          f"{s['support']} | {s['predicted']} |" for s in meta["validation_items"]],
        "",
        "## Most frequent confusions on validation (true -> predicted)",
        "",
        "| true | predicted | rows |", "|---|---|---|",
        *[f"| {t} | {p} | {n} |" for t, p, n in meta["validation_confusions"]],
        "",
    ]
    return "\n".join(lines)


def write_model_card(meta: dict[str, Any], path: Path) -> Path:
    """Write the generated card; any text after the HAND_WRITTEN marker is kept."""
    kept = HAND_WRITTEN + "\n"
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if HAND_WRITTEN in old:
            kept = HAND_WRITTEN + old.split(HAND_WRITTEN, 1)[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model_card(meta) + "\n" + kept, encoding="utf-8")
    return path


def train(dataset: Path, version: str, grid: dict[str, Sequence[int]] = GRID,
          checkpoints: Sequence[int] = CHECKPOINTS, fixed: dict[str, Any] = FIXED,
          n_splits: int = N_FOLDS, root: Path = ROOT):
    """Cross-validate on train, fit the chosen grid point on all of train, score validation.

    Returns (model, meta, (oof probabilities, classes, train labels, train issuers)).
    """
    import lightgbm
    import sklearn

    from finagent.document.structure import FEATURES

    meta_path = dataset.with_name(dataset.name.replace(".parquet", ".meta.json"))
    dataset_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rows, items, groups = load_split(dataset, "train")
    scores, oof, classes = cross_validate(rows, items, groups, grid, checkpoints, fixed, n_splits)
    point = select_point(scores)
    model = SvdBoostedTreeClassifier(n_components=point.n_components,
                                     num_leaves=point.num_leaves,
                                     n_estimators=point.n_estimators, **fixed).fit(rows, items)
    model.version = f"{SvdBoostedTreeClassifier.version}-{version}"

    val_rows, val_items, val_groups = load_split(dataset, "validation")
    probabilities = model.probabilities(val_rows)
    predicted = [model.classes[j] for j in probabilities.argmax(axis=1)]
    concept_items = list(load_concept_map())
    metrics = row_metrics(val_items, predicted, concept_items)
    commit, dirty = git_state(root, ignore=("datasets", "models"))
    meta = {
        "version": version, "model_version": model.version,
        "dataset_version": dataset_meta["version"], "dataset": dataset.name,
        "dataset_meta_sha256": file_sha256(meta_path),
        "dataset_git_commit": dataset_meta.get("git_commit") or "",
        "git_commit": commit, "git_dirty": dirty, "sklearn_version": sklearn.__version__,
        "lightgbm_version": lightgbm.__version__, "n_folds": n_splits,
        "grid": {k: list(v) for k, v in grid.items()}, "checkpoints": list(checkpoints),
        "fixed": fixed, "chosen": asdict(point), "cv": [asdict(s) for s in scores],
        "structure_features": list(FEATURES), "classes": list(model.classes),
        "train_rows": len(rows), "train_companies": len(set(groups)),
        "validation_companies": len(set(val_groups)),
        "validation_rows": asdict(metrics),
        "validation_ece": expected_calibration_error(
            probabilities.max(axis=1), np.asarray(predicted) == np.asarray(val_items)),
        "validation_items": [asdict(s) for s in per_item_table(val_items, predicted,
                                                               concept_items)],
        "validation_confusions": [list(p) for p in confusion_pairs(val_items, predicted)],
    }
    return model, meta, (oof[point], classes, items, groups)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--dataset", help="default: datasets/rows_{version}.parquet")
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--card-dir", default=str(MODEL_CARDS_DIR))
    args = parser.parse_args(argv)

    dataset = Path(args.dataset or DATASETS_DIR / f"rows_{args.version}.parquet")
    model, meta, oof = train(dataset, args.version)
    path = save_model(model, meta, Path(args.models_dir), args.version, oof)
    write_model_card(meta, Path(args.card_dir) / f"m2_{args.version}.md")
    rm = meta["validation_rows"]
    print(f"Saved {path}")
    print(f"chosen {meta['chosen']} (grouped {meta['n_folds']}-fold CV on train)")
    print(f"validation rows: accuracy={rm['accuracy']:.4f} macro-F1={rm['macro_f1']:.4f} "
          f"over {rm['items_scored']} items; ECE raw {meta['validation_ece']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
