"""Train the intent classifier of the goal compiler (blueprint 8.15 step 3; Phase 6).

    python -m finagent.training.intent --version v1

Reads datasets/intents_v1.parquet (finagent.planner.intent_data), turns every question into its
clause bodies with the compiler's own front end (issuers and years masked, condition removed),
fits TF-IDF + logistic regression on the train families, chooses C on validation macro-F1, fits
a softmax temperature on validation NLL, and chooses the confidence threshold: the lowest grid
value whose accepted validation clauses reach the configured target precision
(configs/planner.yaml compiler.target_precision). It then compiles every validation question
(composites included) with the trained compiler (with the grammar fallback) and with the grammar
alone (M0), and reports goal accuracy. Writes models/intent_v1.joblib (gitignored),
models/intent_v1.meta.json (tracked) and docs/model_cards/intent_v1.md.

Validation chooses C, the temperature and the threshold, so validation numbers are optimistic;
E5 (gold/, hand-written, frozen) is the test. The threshold printed here is copied into
configs/planner.yaml by hand; a test checks that the two agree.
"""

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from finagent.dataset.build import git_state
from finagent.planner.compile import (CompilerResources, GrammarParser, TrainedParser,
                                      compile_question, question_clauses)
from finagent.planner.intent_data import DATASETS_DIR, goals_from_json
from finagent.planner.intents import INTENT_LABELS, IntentClassifier, Tags, vocabulary_tags
from finagent.training.m1 import (HAND_WRITTEN, MODEL_CARDS_DIR, MODELS_DIR, file_sha256,
                                  expected_calibration_error, fit_temperature,
                                  negative_log_likelihood)

ROOT = Path(__file__).resolve().parents[2]
C_GRID = (0.125, 0.25, 0.5, 2.0, 8.0, 32.0)
THRESHOLD_GRID = tuple(round(0.30 + 0.025 * i, 3) for i in range(27))  # 0.30 .. 0.95


@dataclass(frozen=True)
class ClauseRow:
    body: str
    label: str
    family: str
    style: str
    split: str


def clause_rows(frame, resources: CompilerResources) -> tuple[list[ClauseRow], int]:
    """Clause bodies of the single-clause questions, labelled with their family's intent;
    also the number of questions the front end split into more than one clause."""
    rows: list[ClauseRow] = []
    multi = 0
    for record in frame[~frame["composite"]].to_dict("records"):
        clauses, _ = question_clauses(record["question"], resources)
        multi += len(clauses) > 1
        rows += [ClauseRow(c.body, record["intent"], record["family"], record["style"],
                           record["split"]) for c in clauses]
    return rows, multi


def per_class(y_true: Sequence[str], y_pred: Sequence[str]) -> dict[str, dict[str, float]]:
    """Precision, recall, F1 and support per intent (labels absent from y_true have support 0)."""
    from sklearn.metrics import precision_recall_fscore_support

    labels = list(INTENT_LABELS)
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    return {label: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]),
                    "support": int(s[i])} for i, label in enumerate(labels)}


def macro_f1(y_true: Sequence[str], y_pred: Sequence[str]) -> float:
    table = per_class(y_true, y_pred)
    scored = [v["f1"] for v in table.values() if v["support"] > 0]
    return float(np.mean(scored)) if scored else 0.0


def select_c(train: Sequence[ClauseRow], validation: Sequence[ClauseRow],
             grid: Sequence[float] = C_GRID, tags: Tags | None = None
             ) -> tuple[float, dict[str, float], IntentClassifier]:
    """Fit on train for each C; keep the best validation macro-F1 (ties: smaller C)."""
    scores: dict[str, float] = {}
    best: tuple[float, float, IntentClassifier] | None = None
    for c in sorted(grid):
        model = IntentClassifier(c=c, tags=tags).fit([r.body for r in train],
                                                     [r.label for r in train])
        predicted = [label for label, _ in model.predict([r.body for r in validation])]
        scores[str(c)] = macro_f1([r.label for r in validation], predicted)
        if best is None or scores[str(c)] > best[1]:
            best = (c, scores[str(c)], model)
    if best is None:
        raise ValueError("empty C grid")
    return best[0], scores, best[2]


def calibrate(model: IntentClassifier, validation: Sequence[ClauseRow]) -> dict[str, float]:
    """Fit the temperature on validation; ECE and NLL before and after."""
    classes = list(model.classes)
    logits = model.decision([r.body for r in validation])
    y = [classes.index(r.label) for r in validation]
    temperature = fit_temperature(logits, y)

    def ece(t: float) -> float | None:
        z = np.exp(logits / t - (logits / t).max(axis=1, keepdims=True))
        p = z / z.sum(axis=1, keepdims=True)
        return expected_calibration_error(p.max(axis=1), p.argmax(axis=1) == np.asarray(y))

    return {"temperature": temperature, "ece_before": ece(1.0), "ece_after": ece(temperature),
            "nll_before": negative_log_likelihood(logits, y, 1.0),
            "nll_after": negative_log_likelihood(logits, y, temperature)}


def choose_threshold(confidence: Sequence[float], correct: Sequence[bool], target: float,
                     grid: Sequence[float] = THRESHOLD_GRID) -> tuple[float, list[dict]]:
    """The lowest grid threshold whose accepted clauses reach `target` precision; when none
    does, the one with the highest precision (ties: lower threshold). Also the full table."""
    conf, hit = np.asarray(confidence, dtype=float), np.asarray(correct, dtype=bool)
    table = []
    for t in sorted(grid):
        accepted = conf >= t
        precision = float(hit[accepted].mean()) if accepted.any() else 1.0
        table.append({"threshold": t, "coverage": float(accepted.mean()),
                      "precision": precision, "accepted": int(accepted.sum())})
    reaching = [row for row in table if row["precision"] >= target]
    chosen = reaching[0] if reaching else max(table, key=lambda r: (r["precision"],
                                                                     -r["threshold"]))
    return chosen["threshold"], table


def _same_goals(expected: Sequence, found: Sequence) -> bool:
    return sorted(map(repr, expected)) == sorted(map(repr, found))


def expected_category(record: dict[str, Any]) -> str:
    """The expected clarification category of a dataset row, "" for a goal question (a missing
    value read back from parquet is NaN, which is truthy)."""
    value = record.get("clarification")
    return value if isinstance(value, str) else ""


def compile_scores(frame, parser, resources: CompilerResources) -> dict[str, Any]:
    """Compile every validation question: goal accuracy (exact goal set, or a clarification of
    the expected category), by intent and style; clarification recall and false-clarification
    rate; and how each compiled clause was read."""
    by_group: dict[str, list[bool]] = defaultdict(list)
    routes: Counter = Counter()
    reasons: Counter = Counter()
    errors = []
    for record in frame.to_dict("records"):
        expected = goals_from_json(record["goals"])
        category = expected_category(record)
        c = compile_question(record["question"], parser, resources)
        if category:
            ok = c.clarification is not None and c.clarification.category == category
        else:
            ok = c.clarification is None and _same_goals(expected, c.goals)
        group = "composite" if record["composite"] else record["intent"]
        by_group[group].append(ok)
        by_group[f"style:{record['style']}"].append(ok)
        by_group["all"].append(ok)
        if category:
            by_group["clarification_recall"].append(c.clarification is not None)
        else:
            by_group["false_clarification"].append(c.clarification is not None)
        routes.update(p.route for p in c.clauses)
        if c.clarification:
            reasons[c.clarification.reason] += 1
        if not ok and len(errors) < 40:
            errors.append({"question": record["question"], "expected": record["goals"],
                           "expected_clarification": category,
                           "found": [repr(g) for g in c.goals],
                           "clarification": c.clarification.reason if c.clarification else None})
    return {"compiler": parser.name,
            "accuracy": {k: float(np.mean(v)) for k, v in sorted(by_group.items())},
            "counts": {k: len(v) for k, v in sorted(by_group.items())},
            "routes": dict(routes), "clarifications": dict(reasons), "errors": errors}


def train(version: str, datasets_dir: Path = DATASETS_DIR, grid: Sequence[float] = C_GRID,
          resources: CompilerResources | None = None) -> tuple[IntentClassifier, dict[str, Any]]:
    """Choose C, the temperature and the threshold; score the compilers on validation."""
    import pandas as pd
    import sklearn

    resources = resources or CompilerResources.load()
    dataset = datasets_dir / f"intents_{version}.parquet"
    meta_path = dataset.with_name(f"intents_{version}.meta.json")
    frame = pd.read_parquet(dataset)
    rows, multi = clause_rows(frame, resources)
    train_rows = [r for r in rows if r.split == "train"]
    val_rows = [r for r in rows if r.split == "validation"]
    c, c_scores, model = select_c(train_rows, val_rows, grid,
                                  vocabulary_tags(resources.vocabulary))
    cal = calibrate(model, val_rows)
    model.temperature = cal["temperature"]
    predicted = model.predict([r.body for r in val_rows])
    y_val = [r.label for r in val_rows]
    y_hat = [label for label, _ in predicted]
    correct = [a == b for a, b in zip(y_val, y_hat)]
    target = resources.planner.target_precision
    threshold, table = choose_threshold([p for _, p in predicted], correct, target)
    fallback = [r for r, (_, p) in zip(val_rows, predicted) if p < threshold]
    validation = frame[frame["split"] == "validation"]
    m1 = compile_scores(validation, TrainedParser(model, threshold, version), resources)
    m0 = compile_scores(validation, GrammarParser(), resources)
    commit, dirty = git_state(ROOT, ignore=("datasets", "models"))
    meta = {
        "version": version, "model_version": f"{model.version}-{version}",
        "dataset": dataset.name, "dataset_meta_sha256": file_sha256(meta_path),
        "git_commit": commit, "git_dirty": dirty, "sklearn_version": sklearn.__version__,
        "c": c, "c_grid": c_scores, "calibration": cal,
        "train_clauses": len(train_rows), "validation_clauses": len(val_rows),
        "questions_split_into_clauses": multi,
        "validation_accuracy": float(np.mean(correct)),
        "validation_macro_f1": macro_f1(y_val, y_hat),
        "validation_per_class": per_class(y_val, y_hat),
        "confusions": [list(k) + [v] for k, v in
                       Counter((a, b) for a, b in zip(y_val, y_hat) if a != b).most_common(15)],
        "target_precision": target, "threshold": threshold, "threshold_table": table,
        "fallback_rate": len(fallback) / len(val_rows) if val_rows else 0.0,
        "fallback_by_intent": dict(Counter(r.label for r in fallback)),
        "compile_m1": m1, "compile_m0": m0,
    }
    return model, meta


def save(model: IntentClassifier, meta: dict[str, Any], models_dir: Path, version: str) -> Path:
    import joblib

    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / f"intent_{version}.joblib"
    joblib.dump(model, path)
    (models_dir / f"intent_{version}.meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path


def _f(x: float | None, digits: int = 3) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def model_card(meta: dict[str, Any]) -> str:
    """Appendix F.3 card. Lists metrics only: no question text, so the card can be read by an E5
    author without unblinding the set."""
    cal, m1, m0 = meta["calibration"], meta["compile_m1"], meta["compile_m0"]
    row = next(r for r in meta["threshold_table"] if r["threshold"] == meta["threshold"])
    lines = [
        f"# Model card: intent classifier {meta['version']} ({meta['model_version']})",
        "",
        f"Generated by `python -m finagent.training.intent --version {meta['version']}`; "
        "the text after the marker at the end is hand-written.",
        "",
        "| Field | Value |", "|---|---|",
        "| Purpose | Goal kind of one question clause (blueprint 8.15 step 3); eight goal kinds "
        "plus advice and unsupported (CLARIFICATION) |",
        f"| Model | vocabulary phrases replaced by their type, then TF-IDF word 1-2-grams + "
        f"char_wb 2-5-grams, logistic regression "
        f"(class-weighted), C = {meta['c']}, temperature {_f(cal['temperature'])}; "
        f"`models/intent_{meta['version']}.joblib` (gitignored) |",
        f"| Training data | `datasets/{meta['dataset']}` (code-generated from "
        "`configs/intent_templates.yaml`, train-split issuers only), train families: "
        f"{meta['train_clauses']} clauses |",
        f"| Validation | held-out template families: {meta['validation_clauses']} clauses |",
        f"| Threshold | {meta['threshold']} (lowest grid value with accepted-clause precision "
        f">= {meta['target_precision']} on validation: precision {_f(row['precision'])}, "
        f"coverage {_f(row['coverage'])}) |",
        f"| Fallback | below the threshold: regex grammar, then CLARIFICATION; validation "
        f"fallback rate {_f(meta['fallback_rate'])}; no LLM fallback (Phase 8) |",
        f"| Commit | {meta['git_commit']}{' (dirty)' if meta['git_dirty'] else ''}; "
        f"scikit-learn {meta['sklearn_version']} |",
        "",
        "## Validation (template families unseen in training)",
        "",
        f"Clause accuracy {_f(meta['validation_accuracy'])}, macro-F1 "
        f"{_f(meta['validation_macro_f1'])}. ECE before / after temperature "
        f"{_f(cal['ece_before'], 4)} / {_f(cal['ece_after'], 4)}.",
        "",
        "| intent | precision | recall | F1 | support |", "|---|---|---|---|---|",
        *[f"| {k} | {_f(v['precision'])} | {_f(v['recall'])} | {_f(v['f1'])} | {v['support']} |"
          for k, v in meta["validation_per_class"].items()],
        "",
        "## Compiled validation questions: goal accuracy",
        "",
        "Exact goal set (kind, issuer, metric, period, scenario, target, conditional), or a "
        "clarification of the expected category.",
        "",
        "| group | M1 (trained + grammar fallback) | M0 (grammar) | questions |",
        "|---|---|---|---|",
        *[f"| {k} | {_f(v)} | {_f(m0['accuracy'].get(k))} | {m1['counts'][k]} |"
          for k, v in m1["accuracy"].items()],
        "",
        "Validation picks C, the temperature and the threshold, and the generated questions use "
        "the same synonym tables as the slot filler, so these numbers are optimistic. E5 "
        "(hand-written, blind, frozen) is the test.",
        "",
    ]
    return "\n".join(lines)


def write_model_card(meta: dict[str, Any], path: Path) -> Path:
    kept = HAND_WRITTEN + "\n"
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if HAND_WRITTEN in old:
            kept = HAND_WRITTEN + old.split(HAND_WRITTEN, 1)[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model_card(meta) + "\n" + kept, encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--datasets-dir", default=str(DATASETS_DIR))
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--card-dir", default=str(MODEL_CARDS_DIR))
    args = parser.parse_args(argv)
    model, meta = train(args.version, Path(args.datasets_dir))
    path = save(model, meta, Path(args.models_dir), args.version)
    write_model_card(meta, Path(args.card_dir) / f"intent_{args.version}.md")
    print(f"Saved {path}; C={meta['c']} T={meta['calibration']['temperature']:.3f}")
    print(f"validation clauses: accuracy {meta['validation_accuracy']:.4f}, macro-F1 "
          f"{meta['validation_macro_f1']:.4f}")
    print(f"threshold {meta['threshold']} (target precision {meta['target_precision']}), "
          f"fallback rate {meta['fallback_rate']:.4f}")
    for name in ("compile_m1", "compile_m0"):
        print(f"{meta[name]['compiler']}: goal accuracy {meta[name]['accuracy']['all']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
