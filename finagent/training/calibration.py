"""Calibrate M1 and M2 on train out-of-fold probabilities; report reliability on validation.

Usage:
    python -m finagent.training.calibration --version v3

Calibrators map a model's uncalibrated probability vector P (one column per class) to a
calibrated one. They are fitted on **train out-of-fold** predictions only: the 5 company-grouped
folds of finagent.training.m2 (a fold's predictions come from a model fitted without that fold's
companies). Validation is used only to report.

- temperature (blueprint 8.4): softmax(log P / T), one T by NLL; keeps the ranking of classes.
- platt: one sigmoid per class on logit(p_k) (Platt's smoothed targets), then rows renormalised.
- isotonic: one isotonic regression per class on p_k, then rows renormalised.
Platt and isotonic are one-vs-rest, so the top class of a row can change; the report counts the
validation rows where it does.

The method kept for each model is chosen between platt and isotonic (the brief) by
cross-validation on the out-of-fold predictions themselves: fitted on four folds' predictions,
scored by ECE on the fifth, averaged. Temperature is reported alongside as the blueprint's
method. M1 v3's own temperature (T fitted on validation in Phase 3) is shown for reference; its
validation ECE is optimistic.

Nothing here changes the shipped models or the default path: the calibrators are saved beside
them (models/calibration_{model}_{version}.joblib) and not wired into the document channel.
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from finagent.dataset.build import DATASETS_DIR, ROOT, git_state
from finagent.training.m1 import MODELS_DIR, expected_calibration_error, fit_temperature

EPS = 1e-12
METHODS = ("temperature", "platt", "isotonic")
CHOICES = ("platt", "isotonic")


def _normalise(q: np.ndarray) -> np.ndarray:
    total = q.sum(axis=1, keepdims=True)
    uniform = np.full_like(q, 1.0 / q.shape[1])
    return np.where(total > 0, q / np.where(total > 0, total, 1.0), uniform)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p) - np.log1p(-p)


class TemperatureScaling:
    name = "temperature"

    def fit(self, probabilities: np.ndarray, y: Sequence[int]) -> "TemperatureScaling":
        self.temperature = fit_temperature(np.log(np.clip(probabilities, EPS, None)), y)
        return self

    def transform(self, probabilities: np.ndarray) -> np.ndarray:
        z = np.log(np.clip(probabilities, EPS, None)) / self.temperature
        z = np.exp(z - z.max(axis=1, keepdims=True))
        return z / z.sum(axis=1, keepdims=True)


def fit_platt(scores: np.ndarray, positive: np.ndarray) -> tuple[float, float]:
    """Platt (1999): sigmoid(a x + b) by NLL against smoothed targets."""
    from scipy.optimize import minimize

    n_pos = float(positive.sum())
    n_neg = float(len(positive) - n_pos)
    target = np.where(positive, (n_pos + 1) / (n_pos + 2), 1 / (n_neg + 2))

    def loss(params: np.ndarray) -> float:
        z = params[0] * scores + params[1]
        # log(1 + e^z) - t z, stable
        return float(np.sum(np.logaddexp(0.0, z) - target * z))

    result = minimize(loss, x0=np.array([1.0, 0.0]), method="L-BFGS-B")
    return float(result.x[0]), float(result.x[1])


class PlattScaling:
    name = "platt"

    def fit(self, probabilities: np.ndarray, y: Sequence[int]) -> "PlattScaling":
        y = np.asarray(y)
        scores = _logit(probabilities)
        self.params = [fit_platt(scores[:, k], y == k) for k in range(probabilities.shape[1])]
        return self

    def transform(self, probabilities: np.ndarray) -> np.ndarray:
        scores = _logit(probabilities)
        q = np.column_stack([1 / (1 + np.exp(-(a * scores[:, k] + b)))
                             for k, (a, b) in enumerate(self.params)])
        return _normalise(q)


class IsotonicScaling:
    name = "isotonic"

    def fit(self, probabilities: np.ndarray, y: Sequence[int]) -> "IsotonicScaling":
        from sklearn.isotonic import IsotonicRegression

        y = np.asarray(y)
        self.models = [IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
                       .fit(probabilities[:, k], (y == k).astype(float))
                       for k in range(probabilities.shape[1])]
        return self

    def transform(self, probabilities: np.ndarray) -> np.ndarray:
        q = np.column_stack([m.predict(probabilities[:, k]) for k, m in enumerate(self.models)])
        return _normalise(q)


CALIBRATORS = {"temperature": TemperatureScaling, "platt": PlattScaling,
               "isotonic": IsotonicScaling}


def reliability(probabilities: np.ndarray, y: Sequence[int]) -> dict[str, float | None]:
    """Top-label ECE (15 bins), NLL, multiclass Brier score and accuracy."""
    y = np.asarray(y)
    top = probabilities.argmax(axis=1)
    onehot = np.zeros_like(probabilities)
    onehot[np.arange(len(y)), y] = 1.0
    return {
        "ece": expected_calibration_error(probabilities.max(axis=1), top == y),
        "nll": float(-np.mean(np.log(np.clip(probabilities[np.arange(len(y)), y], EPS, None)))),
        "brier": float(np.mean(np.sum((probabilities - onehot) ** 2, axis=1))),
        "accuracy": float(np.mean(top == y)),
    }


def fold_ids(groups: Sequence[str], folds: Sequence[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    """Fold number of each row (the fold whose test part holds it)."""
    ids = np.full(len(groups), -1)
    for f, (_, test) in enumerate(folds):
        ids[test] = f
    return ids


def choose_method(probabilities: np.ndarray, y: Sequence[int], folds: np.ndarray,
                  methods: Sequence[str] = CHOICES) -> tuple[str, dict[str, float]]:
    """The method with the lowest mean ECE when fitted on the other folds' out-of-fold
    predictions and scored on each fold in turn; ties go to the earlier method in `methods`."""
    y = np.asarray(y)
    scores: dict[str, float] = {}
    for method in methods:
        eces = []
        for f in np.unique(folds):
            fit, test = folds != f, folds == f
            calibrator = CALIBRATORS[method]().fit(probabilities[fit], y[fit])
            eces.append(reliability(calibrator.transform(probabilities[test]), y[test])["ece"])
        scores[method] = float(np.mean(eces))
    best = min(methods, key=lambda m: (scores[m], methods.index(m)))
    return best, scores


def calibrate(oof: np.ndarray, oof_y: Sequence[int], folds: np.ndarray, validation: np.ndarray,
              validation_y: Sequence[int], reference: dict[str, np.ndarray] | None = None,
              ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit every method on all out-of-fold predictions, report each on validation, and choose.

    `reference` adds rows to the report that were not fitted here (e.g. a shipped temperature).
    Returns (report, fitted calibrators by method).
    """
    raw_top = validation.argmax(axis=1)
    rows: dict[str, Any] = {"raw": {**reliability(validation, validation_y), "top_changed": 0}}
    for name, p in (reference or {}).items():
        rows[name] = {**reliability(p, validation_y),
                      "top_changed": int(np.sum(p.argmax(axis=1) != raw_top))}
    fitted = {}
    for method in METHODS:
        calibrator = CALIBRATORS[method]().fit(oof, oof_y)
        p = calibrator.transform(validation)
        fitted[method] = calibrator
        rows[method] = {**reliability(p, validation_y),
                        "top_changed": int(np.sum(p.argmax(axis=1) != raw_top))}
    chosen, cv = choose_method(oof, oof_y, folds)
    report = {"validation": rows, "chosen": chosen, "train_cv_ece": cv,
              "oof": reliability(oof, oof_y), "oof_rows": int(len(oof_y)),
              "validation_rows": int(len(validation_y))}
    if "temperature" in fitted:
        report["temperature"] = fitted["temperature"].temperature
    return report, fitted


def m1_out_of_fold(rows, items, groups, classes: Sequence[str], c: float) -> np.ndarray:
    """M1 (TF-IDF + LR at the given C, temperature 1) probabilities for every train row from a
    model fitted on the other company folds; columns in `classes` order."""
    from finagent.document.classify import TfidfLogisticClassifier
    from finagent.training.m2 import align, company_folds

    oof = np.zeros((len(rows), len(classes)))
    for train, test in company_folds(groups):
        model = TfidfLogisticClassifier(c=c).fit([rows[i] for i in train],
                                                 [items[i] for i in train])
        oof[test] = align(model.probabilities([rows[i] for i in test]), model.classes, classes)
    return oof


def _indices(items: Sequence[str], classes: Sequence[str]) -> list[int]:
    position = {c: i for i, c in enumerate(classes)}
    return [position[i] for i in items]


def run(version: str, models_dir: Path = MODELS_DIR, datasets_dir: Path = DATASETS_DIR,
        root: Path = ROOT) -> dict[str, Any]:
    """Calibrate M1 {version} and M2 {version}; save the calibrators and the report."""
    import joblib

    from finagent.training import m1, m2

    dataset = datasets_dir / f"rows_{version}.parquet"
    meta_path = datasets_dir / f"rows_{version}.meta.json"
    rows, items, groups = m2.load_split(dataset, "train")
    val_rows, val_items, _ = m2.load_split(dataset, "validation")
    folds = fold_ids(groups, m2.company_folds(groups))
    commit, dirty = git_state(root, ignore=("datasets", "models"))
    report: dict[str, Any] = {"version": version, "git_commit": commit, "git_dirty": dirty,
                              "dataset_meta_sha256": m1.file_sha256(meta_path), "models": {}}

    # M2: the out-of-fold probabilities of the chosen grid point, saved by training.
    model2 = m2.load_model(models_dir, version, meta_path)
    oof2, classes2, labels2, issuers2 = m2.load_oof(models_dir, version)
    if labels2 != list(items) or issuers2 != list(groups):
        raise ValueError("m2 out-of-fold file does not match the train split")
    val2 = m2.align(model2.probabilities(val_rows), model2.classes, classes2)
    report2, fitted2 = calibrate(oof2, _indices(items, classes2), folds, val2,
                                 _indices(val_items, classes2))
    report["models"][model2.version] = report2
    joblib.dump({"classes": classes2, "calibrators": fitted2, "chosen": report2["chosen"]},
                models_dir / f"calibration_m2_{version}.joblib")

    # M1: refitted per fold at the shipped C; temperature 1 is the uncalibrated model.
    model1 = m1.load_model(models_dir, version, meta_path)
    c = float(model1.pipeline.named_steps["model"].C)
    classes1 = sorted(set(items))
    oof1 = m1_out_of_fold(rows, items, groups, classes1, c)
    shipped_t = model1.temperature
    logits = model1.decision(val_rows)

    def softmax(z: np.ndarray) -> np.ndarray:
        z = np.exp(z - z.max(axis=1, keepdims=True))
        return z / z.sum(axis=1, keepdims=True)

    val1 = m2.align(softmax(logits), model1.classes, classes1)
    shipped = m2.align(softmax(logits / shipped_t), model1.classes, classes1)
    report1, fitted1 = calibrate(oof1, _indices(items, classes1), folds, val1,
                                 _indices(val_items, classes1),
                                 reference={f"shipped temperature {shipped_t:.3f} "
                                            "(fitted on validation)": shipped})
    report1["c"] = c
    report["models"][model1.version] = report1
    joblib.dump({"classes": classes1, "calibrators": fitted1, "chosen": report1["chosen"]},
                models_dir / f"calibration_m1_{version}.joblib")

    (models_dir / f"calibration_{version}.meta.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return report


def report_table(report: dict[str, Any]) -> str:
    """Markdown: validation reliability per model and method."""
    lines = ["| model | probabilities | ECE | NLL | Brier | row accuracy | top class changed |",
             "|---|---|---|---|---|---|---|"]
    for model, entry in report["models"].items():
        for name, r in entry["validation"].items():
            mark = " (chosen)" if name == entry["chosen"] else ""
            lines.append(f"| {model} | {name}{mark} | {r['ece']:.4f} | {r['nll']:.4f} | "
                         f"{r['brier']:.4f} | {r['accuracy']:.4f} | {r['top_changed']} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    args = parser.parse_args(argv)
    report = run(args.version, Path(args.models_dir))
    print(report_table(report))
    for model, entry in report["models"].items():
        print(f"{model}: chosen {entry['chosen']} (train CV ECE {entry['train_cv_ece']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
