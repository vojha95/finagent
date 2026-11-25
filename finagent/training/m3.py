"""Train M3 (the pinned bge-small-en-v1.5 fine-tuned on serialised rows) on the row dataset.

Usage:
    python -m finagent.training.m3 --version v3 [--budget-minutes 90]
    python -m finagent.training.m3 --version v3 --stage driver --budget-minutes 60

`--stage driver` (#277, option 2) runs each remaining out-of-fold fold, then the final fit,
in its own fresh process (`--stage fold`), and projects the total against --budget-minutes
before every launch.

reads datasets/rows_v3.parquet and trains on the train split only (excluded rows dropped, OTHER
kept). Validation is never used for a choice; it is scored once at the end and reported.

1. Settings and early stopping, on a company-grouped split within train: the train companies'
   fold SELECTION_FOLD of the 5 GroupKFold folds of finagent.training.m2 is held out. For each
   of the SETTINGS (class weighting at a fixed learning rate) the encoder is trained on the
   other train companies and scored (macro-F1, OTHER excluded) on the held-out companies after
   every epoch; a run stops when macro-F1 has not improved for PATIENCE epochs. The best
   (setting, epoch) wins; ties go to fewer epochs, then the earlier setting.
2. Out-of-fold probabilities for calibration (the Part 1 procedure): the other four folds are
   trained with the chosen setting for the chosen number of epochs; the selection fold's
   predictions are those of its selection run at that epoch.
3. The final model: all train companies, the chosen setting and number of epochs.

Every run's learning-rate schedule spans MAX_EPOCHS epochs and stops at the chosen epoch, so the
final model is trained exactly as the run that chose it. After step 1, and after each fold of
step 2, the remaining time is estimated from the measured times; if the run would exceed
--budget-minutes it stops and says so. The selection result and each fold's probabilities are
checkpointed in models/m3_v3.work/ as they finish, so a stopped or crashed run resumes where it
left off; the directory is removed once the model is saved. Each fitted model's GPU memory is
released before the next fit (#276).

Writes models/m3_v3.safetensors and models/m3_v3.oof.npz (gitignored), models/m3_v3.meta.json
(tracked; with the weights' sha256 and the base model's pin) and docs/model_cards/m3_v3.md.
Held-out companies have no rows in the dataset and are never read here.
"""

import argparse
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from finagent.config import ModelPin, load_concept_map, load_model_pins
from finagent.dataset.build import DATASETS_DIR, ROOT, git_state
from finagent.document.encoder import EncoderRowClassifier, pick_device
from finagent.evaluation.rows import confusion_pairs, per_item_table, row_metrics
from finagent.records import RowInstance
from finagent.training.m1 import (
    MODEL_CARDS_DIR,
    MODELS_DIR,
    ModelDatasetMismatch,
    expected_calibration_error,
    file_sha256,
)
from finagent.training.m2 import company_folds, load_split, save_oof, write_model_card

N_FOLDS = 5
SELECTION_FOLD = 0
# The settings tried, in tie-break order. OTHER is about 70% of the train rows, so the class
# weighting sets the balance between OTHER and the items; the learning rate is fixed (1e-4, a
# usual rate for a 384-wide encoder) to keep the run small.
SETTINGS = ({"learning_rate": 1e-4, "class_weight": "balanced"},
            {"learning_rate": 1e-4, "class_weight": "sqrt"},
            {"learning_rate": 1e-4, "class_weight": None})
MAX_EPOCHS = 5
PATIENCE = 2
FIXED = {"batch_size": 32, "max_length": 128, "warmup": 0.06, "weight_decay": 0.01,
         "dropout": 0.1, "seed": 0}
BASE_PIN = "embedder"  # configs/models.yaml: the pinned bge-small-en-v1.5 (D8)
PRECISION = "fp32"


class BudgetExceeded(RuntimeError):
    """The estimated total training time exceeds the budget; nothing after selection ran."""


class ModelFileMismatch(ValueError):
    """The saved weights' sha256 differs from the one in the meta.json."""


class StageDone(RuntimeError):
    """`max_new` out-of-fold folds were trained in this process; the rest (and the final fit,
    which runs only in a process that trains no fold) are left for a fresh process (#277)."""

    def __init__(self, folds_left: int):
        super().__init__(f"{folds_left} out-of-fold folds left")
        self.folds_left = folds_left


FOLD_TIMES_FILE = "fold_seconds.json"


def load_fold_times(work_dir: Path | None) -> dict[int, float]:
    """Seconds of each trained out-of-fold fold, from any process (empty without a ledger)."""
    path = None if work_dir is None else work_dir / FOLD_TIMES_FILE
    if path is None or not path.exists():
        return {}
    return {int(k): float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def record_fold_time(work_dir: Path, fold: int, seconds: float) -> None:
    times = {**load_fold_times(work_dir), fold: seconds}
    (work_dir / FOLD_TIMES_FILE).write_text(
        json.dumps({str(k): v for k, v in sorted(times.items())}, indent=2) + "\n",
        encoding="utf-8")


def folds_missing(work_dir: Path, n_splits: int) -> list[int]:
    return [f for f in range(n_splits) if not (work_dir / f"fold_{f}.npy").exists()]


@dataclass(frozen=True)
class EpochScore:
    setting: int  # index into the settings tried
    learning_rate: float
    class_weight: str | None
    epoch: int
    macro_f1: float | None
    accuracy: float | None
    seconds: float  # wall time of the run up to the end of this epoch's scoring

    def options(self) -> dict[str, Any]:
        return {"learning_rate": self.learning_rate, "class_weight": self.class_weight}


def base_model(key: str = BASE_PIN) -> ModelPin:
    """The verified pin of the base encoder, with Hugging Face network access switched off."""
    from finagent.retrieval.models import go_offline, verify

    pin = verify(load_model_pins()[key])
    go_offline()
    return pin


def early_stopping_run(
    base: Path, rows: Sequence[RowInstance], items: Sequence[str], classes: Sequence[str],
    train: np.ndarray, test: np.ndarray, setting: int, options: dict[str, Any],
    max_epochs: int = MAX_EPOCHS, patience: int = PATIENCE, fixed: dict[str, Any] = FIXED,
    device: str | None = None, log: Callable[[str], None] = print,
) -> tuple[list[EpochScore], dict[int, np.ndarray]]:
    """Train on `train` with `options` (learning_rate, class_weight), score `test` after every
    epoch, stop after `patience` epochs without a better macro-F1. Returns the scores and the
    test probabilities of every epoch."""
    test_rows, test_items = [rows[i] for i in test], [items[i] for i in test]
    scores: list[EpochScore] = []
    probabilities: dict[int, np.ndarray] = {}
    start = time.perf_counter()

    def on_epoch(epoch: int, model: EncoderRowClassifier) -> bool:
        p = model.probabilities(test_rows)
        metrics = row_metrics(test_items, [classes[j] for j in p.argmax(axis=1)])
        probabilities[epoch] = p
        scores.append(EpochScore(setting, options["learning_rate"], options["class_weight"],
                                 epoch, metrics.macro_f1, metrics.accuracy,
                                 time.perf_counter() - start))
        log(f"  setting {setting} {options} epoch {epoch}: macro-F1 {metrics.macro_f1:.4f} "
            f"accuracy {metrics.accuracy:.4f} ({scores[-1].seconds:.0f} s)")
        best = max(scores, key=lambda s: (s.macro_f1 or 0.0, -s.epoch))
        return epoch - best.epoch >= patience

    model = EncoderRowClassifier(base, epochs=max_epochs, device=device, **options, **fixed)
    model.fit([rows[i] for i in train], [items[i] for i in train], classes=classes,
              on_epoch=on_epoch)
    model.release()
    return scores, probabilities


def select_setting(scores: Sequence[EpochScore]) -> EpochScore:
    """Best held-out macro-F1; ties go to fewer epochs, then the earlier setting."""
    if not scores:
        raise ValueError("no selection scores")
    return min(scores, key=lambda s: (-(s.macro_f1 or 0.0), s.epoch, s.setting))


def estimate_seconds(scores: Sequence[EpochScore], train_rows: int, fold_train_rows: int,
                     epochs: int, folds_left: int) -> float:
    """Time for `folds_left` fold fits and the final fit of `epochs` epochs, from the
    selection runs' measured seconds per epoch (scaled to the number of training rows)."""
    per_epoch = max(s.seconds / s.epoch for s in scores)
    return per_epoch * epochs * (folds_left + train_rows / fold_train_rows)


def out_of_fold(
    base: Path, rows: Sequence[RowInstance], items: Sequence[str], classes: Sequence[str],
    folds: Sequence[tuple[np.ndarray, np.ndarray]], setting: EpochScore,
    done: dict[int, np.ndarray], max_epochs: int = MAX_EPOCHS, fixed: dict[str, Any] = FIXED,
    device: str | None = None, log: Callable[[str], None] = print,
    work_dir: Path | None = None, budget: Callable[[float, int], None] | None = None,
    max_new: int | None = None,
) -> np.ndarray:
    """Probabilities for every train row from a model fitted without its fold's companies.

    `done` holds folds already predicted (the selection fold at the chosen epoch). With a
    `work_dir`, each fold's probabilities are saved as fold_{f}.npy when predicted (and its
    seconds in fold_seconds.json) and read back on a later run. `budget(fold_seconds,
    folds_left)` is called after each trained fold and may raise BudgetExceeded. After
    `max_new` trained folds, StageDone is raised (one fold per fresh process, #277).
    """
    oof = np.zeros((len(rows), len(classes)))
    left = [f for f in range(len(folds)) if f not in done and not
            (work_dir is not None and (work_dir / f"fold_{f}.npy").exists())]
    for f, (train, test) in enumerate(folds):
        saved = None if work_dir is None else work_dir / f"fold_{f}.npy"
        if f in done:
            oof[test] = done[f]
            continue
        if saved is not None and saved.exists():
            oof[test] = np.load(saved)
            log(f"  fold {f}: read from {saved.name}")
            continue
        start = time.perf_counter()
        model = EncoderRowClassifier(base, epochs=max_epochs, device=device,
                                     **setting.options(), **fixed).fit(
            [rows[i] for i in train], [items[i] for i in train], classes=classes,
            stop_after=setting.epoch)
        oof[test] = model.probabilities([rows[i] for i in test])
        model.release()
        seconds = time.perf_counter() - start
        if saved is not None:
            np.save(saved, oof[test])
            record_fold_time(work_dir, f, seconds)
        left.remove(f)
        log(f"  fold {f}: {len(test)} rows predicted ({seconds:.0f} s)")
        if max_new is not None:
            max_new -= 1
            if max_new <= 0:
                raise StageDone(len(left))
        if budget is not None and left:
            budget(seconds, len(left))
    return oof


SELECTION_FILE = "selection.json"


def selection_signature(settings: Sequence[dict[str, Any]], max_epochs: int, patience: int,
                        fixed: dict[str, Any], n_splits: int, selection_fold: int,
                        dataset_meta_sha256: str, base_revision: str) -> dict[str, Any]:
    """What a saved selection must match to be reused."""
    return {"settings": [dict(s) for s in settings], "max_epochs": max_epochs,
            "patience": patience, "fixed": dict(fixed), "n_splits": n_splits,
            "selection_fold": selection_fold, "dataset_meta_sha256": dataset_meta_sha256,
            "base_revision": base_revision}


def save_selection(work_dir: Path, signature: dict[str, Any], scores: Sequence[EpochScore],
                   seconds: float) -> Path:
    """The selection stage's scores and wall time, so a later run can resume after it."""
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / SELECTION_FILE
    path.write_text(json.dumps({"signature": signature, "scores": [asdict(s) for s in scores],
                                "seconds": seconds}, indent=2, default=str) + "\n",
                    encoding="utf-8")
    return path


def load_selection(work_dir: Path, signature: dict[str, Any],
                   ) -> tuple[list[EpochScore], float] | None:
    """(scores, seconds) of a saved selection with the same signature, else None."""
    path = work_dir / SELECTION_FILE
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["signature"] != json.loads(json.dumps(signature, default=str)):
        return None
    return [EpochScore(**s) for s in data["scores"]], float(data["seconds"])


def save_model(model: EncoderRowClassifier, meta: dict[str, Any], models_dir: Path,
               version: str, oof: tuple[np.ndarray, Sequence[str], Sequence[str],
                                        Sequence[str]] | None = None) -> Path:
    """models/m3_{version}.safetensors, .meta.json (with the weights' sha256) and .oof.npz."""
    models_dir.mkdir(parents=True, exist_ok=True)
    path = model.save(models_dir / f"m3_{version}.safetensors")
    meta = {**meta, "weights": path.name, "weights_sha256": file_sha256(path),
            "weights_bytes": path.stat().st_size}
    (models_dir / f"m3_{version}.meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    if oof is not None:
        save_oof(models_dir / f"m3_{version}.oof.npz", *oof)
    return path


def load_meta(models_dir: Path, version: str) -> dict[str, Any]:
    return json.loads((models_dir / f"m3_{version}.meta.json").read_text(encoding="utf-8"))


def load_model(models_dir: Path, version: str, dataset_meta: Path,
               device: str | None = None) -> EncoderRowClassifier:
    """Load a saved M3 after three checks: the dataset build (ModelDatasetMismatch), the weights'
    sha256 (ModelFileMismatch) and the base model's pin (ModelHashError)."""
    meta = load_meta(models_dir, version)
    if meta["dataset_meta_sha256"] != file_sha256(dataset_meta):
        raise ModelDatasetMismatch(
            f"m3_{version} was trained on another build of {Path(dataset_meta).name}; retrain it")
    path = models_dir / meta["weights"]
    if file_sha256(path) != meta["weights_sha256"]:
        raise ModelFileMismatch(f"{path.name}: sha256 differs from m3_{version}.meta.json")
    pin = base_model(meta["base"]["key"])
    if pin.revision != meta["base"]["revision"]:
        raise ModelFileMismatch(f"base model pin {pin.revision} != trained on "
                                f"{meta['base']['revision']}")
    model = EncoderRowClassifier.load(pin.directory, path, meta["classes"], meta["params"],
                                      device=device)
    model.version = meta["model_version"]
    model.epochs_trained = meta["chosen"]["epoch"]
    return model


def _fmt(x: float | None, digits: int = 3) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def model_card(meta: dict[str, Any]) -> str:
    """The generated part of the F.3 model card."""
    rm, chosen, fx = meta["validation_rows"], meta["chosen"], meta["fixed"]
    t = meta["timings_s"]
    lines = [
        f"# Model card: M3 {meta['version']} ({meta['model_version']})",
        "",
        f"Generated by `python -m finagent.training.m3 --version {meta['dataset_version']}`; "
        "the hand-written section at the end is kept on regeneration.",
        "",
        "## Name, task, data",
        "",
        "| Field | Value |", "|---|---|",
        f"| Model | `{meta['model_version']}`, `models/{meta['weights']}` (gitignored), "
        f"sha256 `{meta['weights_sha256'][:16]}` |",
        "| Task | Map a face-statement row to one of the canonical items (Appendix A) or OTHER; "
        "top-3 with probabilities (blueprint 8.4). |",
        "| Input | The serialised visible row only: `[STMT] cash flow [PARENT] investing "
        "activities [LABEL] purchases of property and equipment [INDENT] 1 [TOTAL] 0`; no tags "
        "(rule 7). |",
        f"| Base encoder | `{meta['base']['model_id']}` at revision "
        f"`{meta['base']['revision'][:12]}` (pinned in configs/models.yaml, read offline; no "
        f"download), {meta['base_parameters_m']:.1f}M parameters |",
        f"| Head and loss | dropout {fx['dropout']} + linear layer on [CLS]; cross-entropy, "
        f"class weighting `{chosen['class_weight']}` |",
        f"| Training | AdamW, learning rate {chosen['learning_rate']:g}, weight decay "
        f"{fx['weight_decay']}, batch {fx['batch_size']}, max length {fx['max_length']} tokens "
        f"(dynamic padding), warm-up {fx['warmup']:.0%} then linear decay over "
        f"{meta['max_epochs']} epochs, stopped after {chosen['epoch']} epochs; gradient clip 1.0; "
        f"seed {fx['seed']}; {meta['device']}, {meta['precision']} |",
        f"| Settings and early stopping | {len(meta['settings'])} settings (below), up to "
        f"{meta['max_epochs']} epochs, patience {meta['patience']}, scored on a company-grouped "
        f"split within train ({meta['selection_companies']} held-out train companies, "
        f"{meta['selection_rows']} rows; macro-F1, OTHER excluded). Validation not used. |",
        "| Calibration | Raw softmax probabilities; calibrators are fitted separately on the "
        f"out-of-fold probabilities ({meta['n_folds']} company-grouped folds; `python -m "
        "finagent.training.calibration --version v3 --model m3`). |",
        f"| Training data | `datasets/rows_{meta['dataset_version']}.parquet`, train split only: "
        f"{meta['train_rows']} rows ({meta['train_companies']} companies), OTHER kept. |",
        f"| Dataset build | meta SHA-256 `{meta['dataset_meta_sha256'][:12]}` |",
        f"| Trained at | commit `{(meta['git_commit'] or '')[:7]}`"
        f"{' (uncommitted changes)' if meta['git_dirty'] else ''}, torch "
        f"{meta['torch_version']}, transformers {meta['transformers_version']} |",
        f"| Wall time | selection {t['selection'] / 60:.1f} min, out-of-fold "
        f"{t['out_of_fold'] / 60:.1f} min, final fit {t['final'] / 60:.1f} min; total "
        f"{t['total'] / 60:.1f} min |",
        "",
        "## Settings and early stopping (company-grouped split within train)",
        "",
        "| setting | learning rate | class weighting | epoch | macro-F1 | accuracy | seconds |",
        "|---|---|---|---|---|---|---|",
        *[f"| {s['setting']} | {s['learning_rate']:g} | {s['class_weight']} | {s['epoch']} | "
          f"{_fmt(s['macro_f1'], 4)} | {_fmt(s['accuracy'], 4)} | {s['seconds']:.0f} |"
          for s in meta["selection"]],
        "",
        f"Chosen: setting {chosen['setting']} (learning rate {chosen['learning_rate']:g}, class "
        f"weighting `{chosen['class_weight']}`), {chosen['epoch']} epochs. Out-of-fold over "
        f"train ({meta['n_folds']} company folds) at that setting: macro-F1 "
        f"{_fmt(meta['oof_rows']['macro_f1'], 4)}, accuracy "
        f"{_fmt(meta['oof_rows']['accuracy'], 4)}.",
        "",
        f"## Metrics on validation ({meta['validation_companies']} companies, {rm['rows']} rows)",
        "",
        "Scored once after the choice; validation chose nothing for M3. Held-out: not run.",
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
        *[f"| {t_} | {p} | {n} |" for t_, p, n in meta["validation_confusions"]],
        "",
    ]
    return "\n".join(lines)


def train(dataset: Path, version: str, settings: Sequence[dict[str, Any]] = SETTINGS,
          max_epochs: int = MAX_EPOCHS, patience: int = PATIENCE,
          fixed: dict[str, Any] = FIXED, n_splits: int = N_FOLDS,
          selection_fold: int = SELECTION_FOLD, budget_minutes: float | None = None,
          base_key: str = BASE_PIN, device: str | None = None, root: Path = ROOT,
          log: Callable[[str], None] = print, work_dir: Path | None = None,
          max_new_folds: int | None = None):
    """Select on the grouped split, predict out of fold, fit on all of train, score validation.

    With a `work_dir`, the selection result and each fold's out-of-fold probabilities are saved
    there as they finish, and a later run with the same settings and dataset resumes from them
    (after a crash or a budget stop). `budget_minutes` bounds this run's own wall time.
    `max_new_folds`: train at most that many folds here, then raise StageDone; the final fit
    runs only in a call that trains no fold (one fold per fresh process, #277).

    Returns (model, meta, (oof probabilities, classes, train labels, train issuers)).
    """
    import torch
    import transformers

    started = time.perf_counter()
    pin = base_model(base_key)
    device = device or pick_device()
    meta_path = dataset.with_name(dataset.name.replace(".parquet", ".meta.json"))
    dataset_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rows, items, groups = load_split(dataset, "train")
    classes = sorted(set(items))
    folds = company_folds(groups, n_splits)
    sel_train, sel_test = folds[selection_fold]
    log(f"train {len(rows)} rows, {len(set(groups))} companies; selection split holds out "
        f"{len({groups[i] for i in sel_test})} companies ({len(sel_test)} rows); device {device}")

    signature = selection_signature(settings, max_epochs, patience, fixed, n_splits,
                                    selection_fold, file_sha256(meta_path), pin.revision)
    resumed = None if work_dir is None else load_selection(work_dir, signature)
    done: dict[int, np.ndarray] = {}
    if resumed is None:
        scores: list[EpochScore] = []
        selection_probabilities: dict[int, dict[int, np.ndarray]] = {}
        for index, options in enumerate(settings):
            found, probabilities = early_stopping_run(pin.directory, rows, items, classes,
                                                      sel_train, sel_test, index, options,
                                                      max_epochs, patience, fixed, device, log)
            scores.extend(found)
            selection_probabilities[index] = probabilities
        setting = select_setting(scores)
        selection_seconds = time.perf_counter() - started
        done[selection_fold] = selection_probabilities[setting.setting][setting.epoch]
        if work_dir is not None:
            save_selection(work_dir, signature, scores, selection_seconds)
            np.save(work_dir / f"fold_{selection_fold}.npy", done[selection_fold])
    else:
        scores, selection_seconds = resumed
        setting = select_setting(scores)
        log(f"selection read from {work_dir / SELECTION_FILE} (an earlier run; the selection "
            f"fold is predicted again unless saved)")
    selected = time.perf_counter()
    log(f"chosen setting {setting.setting} {setting.options()}, {setting.epoch} epochs "
        f"(macro-F1 {setting.macro_f1:.4f}); selection {selection_seconds / 60:.1f} min")

    if resumed is None:
        remaining = estimate_seconds(scores, len(rows), len(sel_train), setting.epoch,
                                     n_splits - 1)
        total_estimate = (selected - started) + remaining
        log(f"estimated total {total_estimate / 60:.0f} min")
        if budget_minutes is not None and total_estimate > budget_minutes * 60:
            raise BudgetExceeded(f"estimated total {total_estimate / 60:.0f} min > budget "
                                 f"{budget_minutes:.0f} min; selection: "
                                 f"{[asdict(s) for s in scores]}")

    def budget(fold_seconds: float, folds_left: int) -> None:
        """After each fold: this run's time so far plus the remaining folds and the final fit
        (on all train rows), at the last fold's measured time."""
        elapsed = time.perf_counter() - started
        remaining = fold_seconds * (folds_left + len(rows) / len(sel_train))
        log(f"  estimated time left {remaining / 60:.0f} min (run so far "
            f"{elapsed / 60:.0f} min)")
        if budget_minutes is not None and elapsed + remaining > budget_minutes * 60:
            raise BudgetExceeded(f"estimated {(elapsed + remaining) / 60:.0f} min for this run > "
                                 f"budget {budget_minutes:.0f} min; folds saved in {work_dir}")

    oof = out_of_fold(pin.directory, rows, items, classes, folds, setting, done, max_epochs,
                      fixed, device, log, work_dir, budget, max_new_folds)
    oofed = time.perf_counter()
    # Out-of-fold time: the fold ledger when there is one (folds trained in any process),
    # else this run's own time.
    fold_times = load_fold_times(work_dir)
    oof_seconds = sum(fold_times.values()) if fold_times else oofed - selected
    model = EncoderRowClassifier(pin.directory, epochs=max_epochs, device=device,
                                 **setting.options(), **fixed).fit(
        rows, items, classes=classes, stop_after=setting.epoch)
    model.version = f"{EncoderRowClassifier.version}-{version}"
    finished = time.perf_counter()
    log(f"final fit {(finished - oofed) / 60:.1f} min")

    val_rows, val_items, val_groups = load_split(dataset, "validation")
    probabilities = model.probabilities(val_rows)
    predicted = [model.classes[j] for j in probabilities.argmax(axis=1)]
    concept_items = list(load_concept_map())
    metrics = row_metrics(val_items, predicted, concept_items)
    commit, dirty = git_state(root, ignore=("datasets", "models"))
    oof_metrics = row_metrics(items, [classes[j] for j in oof.argmax(axis=1)])
    meta = {
        "version": version, "model_version": model.version,
        "base": {"key": pin.key, "model_id": pin.model_id, "repo": pin.repo,
                 "revision": pin.revision, "fingerprint": pin.fingerprint},
        "base_parameters_m": sum(p.numel() for p in model.encoder.parameters()) / 1e6,
        "dataset_version": dataset_meta["version"], "dataset": dataset.name,
        "dataset_meta_sha256": file_sha256(meta_path),
        "dataset_git_commit": dataset_meta.get("git_commit") or "",
        "git_commit": commit, "git_dirty": dirty, "torch_version": torch.__version__,
        "transformers_version": transformers.__version__, "device": device,
        "precision": PRECISION, "n_folds": n_splits, "selection_fold": selection_fold,
        "selection_companies": len({groups[i] for i in sel_test}),
        "selection_rows": int(len(sel_test)), "settings": [dict(s) for s in settings],
        "max_epochs": max_epochs, "patience": patience, "fixed": fixed,
        "selection": [asdict(s) for s in scores], "chosen": asdict(setting),
        "params": {**fixed, **setting.options(), "epochs": max_epochs},
        "classes": list(model.classes), "train_rows": len(rows),
        "train_companies": len(set(groups)),
        "oof_rows": {"accuracy": oof_metrics.accuracy, "macro_f1": oof_metrics.macro_f1},
        "timings_s": {"selection": selection_seconds, "out_of_fold": oof_seconds,
                      "final": finished - oofed, "total": selection_seconds + oof_seconds
                      + (time.perf_counter() - oofed)},
        "fold_seconds": {str(k): v for k, v in sorted(fold_times.items())},
        "selection_resumed": resumed is not None,
        "validation_companies": len(set(val_groups)),
        "validation_rows": asdict(metrics),
        "validation_ece": expected_calibration_error(
            probabilities.max(axis=1), np.asarray(predicted) == np.asarray(val_items)),
        "validation_items": [asdict(s) for s in per_item_table(val_items, predicted,
                                                               concept_items)],
        "validation_confusions": [list(p) for p in confusion_pairs(val_items, predicted)],
    }
    return model, meta, (oof, classes, items, groups)


FOLD_EXIT = 3  # --stage fold: one fold trained in this process; more stages are left
FINAL_OVERHEAD_S = 60.0  # validation scoring and saving after the final fit


def drive(run_stage: Callable[[], int], work_dir: Path, n_splits: int, final_factor: float,
          cap_seconds: float, first_fold_seconds: float, log: Callable[[str], None] = print,
          clock: Callable[[], float] = time.perf_counter) -> int:
    """One stage per fresh process until the model is saved (#277, option 2).

    `run_stage` launches one `--stage fold` process and returns its exit code (FOLD_EXIT: a
    fold was trained; 0: the final fit ran and the model is saved). Before each launch the
    total is projected: time used so far + (folds left + final_factor) x the last process's
    seconds (before any, `first_fold_seconds`) + FINAL_OVERHEAD_S. If that exceeds
    `cap_seconds`, nothing is launched and 2 is returned."""
    start = clock()
    per_fold = first_fold_seconds
    while True:
        left = len(folds_missing(work_dir, n_splits))
        used = clock() - start
        projected = used + per_fold * (left + final_factor) + FINAL_OVERHEAD_S
        log(f"driver: {left} folds left; used {used / 60:.1f} min; projected total "
            f"{projected / 60:.1f} min (cap {cap_seconds / 60:.0f} min; {per_fold:.0f} s per "
            "fold)")
        if projected > cap_seconds:
            log(f"STOPPED: projected {projected / 60:.1f} min > cap {cap_seconds / 60:.0f} min; "
                f"folds saved in {work_dir}")
            return 2
        began = clock()
        code = run_stage()
        seconds = clock() - began
        if code == 0:
            log(f"driver: final fit done; used {(clock() - start) / 60:.1f} min")
            return 0
        if code != FOLD_EXIT:
            log(f"driver: stage failed (exit {code}) after {seconds:.0f} s")
            return code
        per_fold = seconds


def first_fold_estimate(work_dir: Path) -> float:
    """The slowest recorded fold, else the selection's slowest seconds per epoch x the chosen
    epochs (the driver's first projection)."""
    times = load_fold_times(work_dir)
    if times:
        return max(times.values())
    data = json.loads((work_dir / SELECTION_FILE).read_text(encoding="utf-8"))
    scores = [EpochScore(**s) for s in data["scores"]]
    return max(s.seconds / s.epoch for s in scores) * select_setting(scores).epoch


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--dataset", help="default: datasets/rows_{version}.parquet")
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--card-dir", default=str(MODEL_CARDS_DIR))
    parser.add_argument("--budget-minutes", type=float, default=90.0)
    parser.add_argument("--stage", choices=("all", "fold", "driver"), default="all",
                        help="all: one process; fold: train the next missing out-of-fold fold "
                             "(or, when none is missing, the final fit) and exit; driver: run "
                             "`fold` in fresh processes within --budget-minutes (#277)")
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        print(message, flush=True)

    dataset = Path(args.dataset or DATASETS_DIR / f"rows_{args.version}.parquet")
    work_dir = Path(args.models_dir) / f"m3_{args.version}.work"
    if args.stage == "driver":
        import subprocess
        import sys

        _, _, groups = load_split(dataset, "train")
        folds = company_folds(groups, N_FOLDS)
        factor = len(groups) / float(np.mean([len(train_) for train_, _ in folds]))
        command = [sys.executable, "-m", "finagent.training.m3", "--version", args.version,
                   "--stage", "fold", "--dataset", str(dataset), "--models-dir",
                   str(args.models_dir), "--card-dir", str(args.card_dir)]
        return drive(lambda: subprocess.run(command).returncode, work_dir, N_FOLDS, factor,
                     args.budget_minutes * 60, first_fold_estimate(work_dir), log)
    try:
        if args.stage == "fold":
            model, meta, oof = train(dataset, args.version, budget_minutes=None, log=log,
                                     work_dir=work_dir, max_new_folds=1)
        else:
            model, meta, oof = train(dataset, args.version, budget_minutes=args.budget_minutes,
                                     log=log, work_dir=work_dir)
    except StageDone as exc:
        log(f"STAGE DONE: {exc}; the next stage runs in a fresh process")
        return FOLD_EXIT
    except BudgetExceeded as exc:
        log(f"STOPPED: {exc}")
        return 2
    path = save_model(model, meta, Path(args.models_dir), args.version, oof)
    import shutil

    shutil.rmtree(work_dir, ignore_errors=True)  # the checkpoints are in the saved model now
    meta = load_meta(Path(args.models_dir), args.version)
    write_model_card(meta, Path(args.card_dir) / f"m3_{args.version}.md", render=model_card)
    rm = meta["validation_rows"]
    log(f"Saved {path} (sha256 {meta['weights_sha256'][:16]})")
    log(f"chosen {meta['chosen']}; total {meta['timings_s']['total'] / 60:.1f} min")
    log(f"validation rows: accuracy={rm['accuracy']:.4f} macro-F1={rm['macro_f1']:.4f} "
        f"over {rm['items_scored']} items; ECE raw {meta['validation_ece']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
