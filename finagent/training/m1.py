"""Train M1 (TF-IDF + logistic regression) on the row dataset (blueprint 8.4 training protocol).

Usage:
    python -m finagent.training.m1 --version v1
reads datasets/rows_v1.parquet, fits on the train split only (excluded rows dropped, OTHER kept),
chooses C on validation macro-F1, fits a temperature on validation NLL, and writes
models/m1_v1.joblib (gitignored), models/m1_v1.meta.json (tracked) and
docs/model_cards/m1_v1.md (Appendix F.3). Held-out companies have no rows in the dataset and are
never read here.

Validation is used for both choices, so validation metrics are optimistic; the held-out E1 run
(Phase 8) is the unbiased number.
"""

import argparse
import hashlib
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from finagent.config import load_concept_map
from finagent.dataset.build import DATASETS_DIR, ROOT, git_state
from finagent.document.classify import OTHER, TfidfLogisticClassifier
from finagent.evaluation.rows import confusion_pairs, per_item_table, row_metrics
from finagent.records import RowInstance

MODELS_DIR = ROOT / "models"
MODEL_CARDS_DIR = ROOT / "docs" / "model_cards"
C_GRID = (0.25, 1.0, 4.0, 16.0, 64.0)
ECE_BINS = 15
HAND_WRITTEN = "<!-- hand-written below: kept when the card is regenerated -->"


class ModelDatasetMismatch(ValueError):
    """The saved model was trained on a different dataset build than the one given."""


def _date(value: Any) -> date | None:
    return None if value is None or pd.isna(value) else date.fromisoformat(str(value)[:10])


def row_from_record(record: dict[str, Any]) -> RowInstance:
    """Rebuild a RowInstance from a dataset row; the dataset's row_text column is the label."""
    parent = record.get("parent_header")
    unit = record.get("unit_text")
    return RowInstance(
        row_id=record["row_id"], accession=record["accession"], statement=record["statement"],
        label=record["row_text"] or "",
        parent_header=None if parent is None or pd.isna(parent) else parent,
        indent=int(record["indent"]), is_total=bool(record["is_total"]),
        is_bold=bool(record["is_bold"]), position=int(record["position"]),
        cell_texts=tuple(str(t) for t in record["cell_texts"]),
        column_periods=tuple(_date(p) for p in record["column_periods"]),
        unit_text=None if unit is None or pd.isna(unit) else unit,
        table_index=int(record["table_index"]), source_row=int(record["source_row"]),
        column_spans=(), column_kinds=tuple(record["column_kinds"]),
    )


def load_rows(path: Path, split: str) -> tuple[list[RowInstance], list[str]]:
    """RowInstances and labels of one split; excluded rows (item None) are dropped."""
    frame = pd.read_parquet(path)
    frame = frame[(frame["split"] == split) & frame["item"].notna()]
    records = frame.to_dict("records")
    return [row_from_record(r) for r in records], [str(r["item"]) for r in records]


def expected_calibration_error(
    confidence: Sequence[float], correct: Sequence[bool], bins: int = ECE_BINS
) -> float | None:
    """Top-label ECE with equal-width bins: sum over bins of share x |accuracy - confidence|."""
    conf = np.asarray(confidence, dtype=float)
    hit = np.asarray(correct, dtype=float)
    if conf.size == 0:
        return None
    index = np.clip(np.ceil(conf * bins).astype(int) - 1, 0, bins - 1)  # (0, 1/b] -> bin 0
    total = 0.0
    for b in range(bins):
        mask = index == b
        if mask.any():
            total += mask.mean() * abs(hit[mask].mean() - conf[mask].mean())
    return float(total)


def _softmax(logits: np.ndarray) -> np.ndarray:
    z = np.exp(logits - logits.max(axis=1, keepdims=True))
    return z / z.sum(axis=1, keepdims=True)


def negative_log_likelihood(logits: np.ndarray, y: Sequence[int], temperature: float) -> float:
    p = _softmax(np.asarray(logits, dtype=float) / temperature)
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), list(y)], 1e-12, None))))


def fit_temperature(
    logits: np.ndarray, y: Sequence[int], bounds: tuple[float, float] = (0.05, 20.0)
) -> float:
    """Temperature minimising mean NLL of softmax(logits / T); bounded search over log T."""
    from scipy.optimize import minimize_scalar

    result = minimize_scalar(
        lambda log_t: negative_log_likelihood(logits, y, float(np.exp(log_t))),
        bounds=(np.log(bounds[0]), np.log(bounds[1])), method="bounded",
    )
    return float(np.exp(result.x))


def calibration(model: TfidfLogisticClassifier, rows: Sequence[RowInstance],
                items: Sequence[str]) -> dict[str, float | None]:
    """Fit the temperature on (rows, items) and report ECE and NLL before and after."""
    classes = list(model.classes)
    known = [i for i, item in enumerate(items) if item in classes]  # unseen items cannot be scored
    logits = model.decision([rows[i] for i in known])
    y = [classes.index(items[i]) for i in known]
    temperature = fit_temperature(logits, y)

    def ece(t: float) -> float | None:
        p = _softmax(logits / t)
        return expected_calibration_error(p.max(axis=1), p.argmax(axis=1) == np.asarray(y))

    return {"temperature": temperature, "ece_before": ece(1.0), "ece_after": ece(temperature),
            "nll_before": negative_log_likelihood(logits, y, 1.0),
            "nll_after": negative_log_likelihood(logits, y, temperature),
            "rows": len(known), "rows_unseen_item": len(items) - len(known)}


def select_c(
    train: tuple[Sequence[RowInstance], Sequence[str]],
    validation: tuple[Sequence[RowInstance], Sequence[str]],
    grid: Sequence[float] = C_GRID,
) -> tuple[float, dict[float, float | None], TfidfLogisticClassifier]:
    """Fit on train for every C; keep the best validation macro-F1 (ties: smaller C)."""
    scores: dict[float, float | None] = {}
    best: tuple[float, TfidfLogisticClassifier] | None = None
    for c in sorted(grid):
        model = TfidfLogisticClassifier(c=c).fit(*train)
        predicted = [p.item for p in model.predict(validation[0])]
        scores[c] = row_metrics(validation[1], predicted).macro_f1
        if best is None or (scores[c] or 0.0) > (scores[best[0]] or 0.0):
            best = (c, model)
    if best is None:
        raise ValueError("empty C grid")
    return best[0], scores, best[1]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_model(model: TfidfLogisticClassifier, meta: dict[str, Any], models_dir: Path,
               version: str) -> Path:
    import joblib

    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / f"m1_{version}.joblib"
    joblib.dump(model, path)
    (models_dir / f"m1_{version}.meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path


def load_model(models_dir: Path, version: str, dataset_meta: Path) -> TfidfLogisticClassifier:
    """Load a saved M1; raises ModelDatasetMismatch if it was trained on another dataset build."""
    import joblib

    meta = json.loads((models_dir / f"m1_{version}.meta.json").read_text(encoding="utf-8"))
    if meta["dataset_meta_sha256"] != file_sha256(dataset_meta):
        raise ModelDatasetMismatch(
            f"m1_{version} was trained on another build of {Path(dataset_meta).name}; retrain it")
    return joblib.load(models_dir / f"m1_{version}.joblib")


def _fmt(x: float | None, digits: int = 3) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def model_card(meta: dict[str, Any]) -> str:
    """The generated part of the F.3 model card."""
    rm, cal = meta["validation_rows"], meta["calibration"]
    lines = [
        f"# Model card: M1 {meta['version']} ({meta['model_version']})",
        "",
        f"Generated by `python -m finagent.training.m1 --version {meta['dataset_version']}`; "
        "the hand-written section at the end is kept on regeneration.",
        "",
        "## Name, task, data",
        "",
        "| Field | Value |", "|---|---|",
        f"| Model | `{meta['model_version']}`, `models/m1_{meta['version']}.joblib` (gitignored) |",
        "| Task | Map a face-statement row to one of the 35 canonical items (Appendix A) or "
        "OTHER; top-3 with probabilities (blueprint 8.4). |",
        "| Input | Visible text only: `[statement] parent header | label` (normalised); no tags, "
        "no figures, no layout features. |",
        "| Features | TF-IDF word 1-2-grams + character (word-bounded) 3-5-grams, sublinear tf. |",
        f"| Classifier | Logistic regression, class_weight=balanced, C={meta['c']} "
        f"(chosen on validation macro-F1 over "
        f"{', '.join(sorted(meta['c_grid'], key=float))}). |",
        f"| Calibration | Temperature T={cal['temperature']:.3f}, fitted on validation NLL. |",
        f"| Training data | `datasets/rows_{meta['dataset_version']}.parquet`, train split only: "
        f"{meta['train_rows']} rows ({meta['train_companies']} companies; IBM contributes none, "
        "BC10), OTHER kept, excluded rows dropped. |",
        f"| Dataset build | meta SHA-256 `{meta['dataset_meta_sha256'][:12]}`, built at "
        f"`{meta['dataset_git_commit'][:7]}` |",
        f"| Trained at | commit `{(meta['git_commit'] or '')[:7]}`"
        f"{' (uncommitted changes)' if meta['git_dirty'] else ''}, "
        f"scikit-learn {meta['sklearn_version']} |",
        "",
        "## C grid (validation macro-F1)",
        "",
        "| C | macro-F1 |", "|---|---|",
        *[f"| {c} | {_fmt(s)} |" for c, s in sorted(meta["c_grid"].items(), key=lambda kv: float(kv[0]))],
        "",
        f"## Metrics on validation ({meta['validation_companies']} companies, "
        f"{rm['rows']} rows)",
        "",
        "Validation chose C and T, so these numbers are optimistic. Held-out: not yet run "
        "(Phase 8, once per model version).",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Row accuracy (OTHER included) | {_fmt(rm['accuracy'])} |",
        f"| Macro-F1 over {rm['items_scored']} items with validation rows (OTHER excluded) | "
        f"{_fmt(rm['macro_f1'])} |",
        f"| OTHER precision / recall / F1 | {_fmt(rm['other']['precision'])} / "
        f"{_fmt(rm['other']['recall'])} / {_fmt(rm['other']['f1'])} |",
        f"| Items with no validation row (rows predicted as them) | "
        f"{', '.join(f'{k} ({v})' for k, v in sorted(rm['unsupported'].items())) or '—'} |",
        f"| ECE (15 bins, top label), before / after temperature | {_fmt(cal['ece_before'], 4)} / "
        f"{_fmt(cal['ece_after'], 4)} |",
        f"| NLL before / after temperature | {_fmt(cal['nll_before'], 4)} / "
        f"{_fmt(cal['nll_after'], 4)} |",
        "",
        "E1 field accuracy with company-bootstrap CI: `docs/e1_validation.md`.",
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


def train(dataset: Path, version: str, grid: Sequence[float] = C_GRID,
          root: Path = ROOT) -> tuple[TfidfLogisticClassifier, dict[str, Any]]:
    """Choose C, fit T and measure on validation; returns the model and its meta record."""
    import sklearn

    meta_path = dataset.with_name(dataset.name.replace(".parquet", ".meta.json"))
    dataset_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    train_rows, train_items = load_rows(dataset, "train")
    val_rows, val_items = load_rows(dataset, "validation")
    c, scores, model = select_c((train_rows, train_items), (val_rows, val_items), grid)
    cal = calibration(model, val_rows, val_items)
    model.temperature = cal["temperature"]
    model.version = f"{TfidfLogisticClassifier.version}-{version}"
    predicted = [p.item for p in model.predict(val_rows)]
    items = list(load_concept_map())
    metrics = row_metrics(val_items, predicted, items)
    commit, dirty = git_state(root, ignore=("datasets", "models"))
    frame = pd.read_parquet(dataset, columns=["split", "issuer"])
    meta = {
        "version": version, "model_version": model.version,
        "dataset_version": dataset_meta["version"], "dataset": dataset.name,
        "dataset_meta_sha256": file_sha256(meta_path),
        "dataset_git_commit": dataset_meta.get("git_commit") or "",
        "git_commit": commit, "git_dirty": dirty, "sklearn_version": sklearn.__version__,
        "c": c, "c_grid": {str(k): v for k, v in scores.items()}, "calibration": cal,
        "train_rows": len(train_rows),
        "train_companies": int(frame[frame.split == "train"].issuer.nunique()),
        "validation_companies": int(frame[frame.split == "validation"].issuer.nunique()),
        "validation_rows": _as_dict(metrics),
        "validation_items": [_as_dict(s) for s in per_item_table(val_items, predicted, items)],
        "validation_confusions": [list(p) for p in confusion_pairs(val_items, predicted)],
    }
    return model, meta


def _as_dict(record: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(record)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--dataset", help="default: datasets/rows_{version}.parquet")
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--card-dir", default=str(MODEL_CARDS_DIR))
    parser.add_argument("--grid", type=float, nargs="+", default=list(C_GRID))
    args = parser.parse_args(argv)

    dataset = Path(args.dataset or DATASETS_DIR / f"rows_{args.version}.parquet")
    model, meta = train(dataset, args.version, args.grid)
    path = save_model(model, meta, Path(args.models_dir), args.version)
    write_model_card(meta, Path(args.card_dir) / f"m1_{args.version}.md")
    cal, rm = meta["calibration"], meta["validation_rows"]
    print(f"Saved {path}")
    print(f"C={meta['c']} grid={meta['c_grid']}")
    print(f"T={cal['temperature']:.4f} ECE {cal['ece_before']:.4f} -> {cal['ece_after']:.4f}")
    print(f"validation rows: accuracy={rm['accuracy']:.4f} macro-F1={rm['macro_f1']:.4f} "
          f"over {rm['items_scored']} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
