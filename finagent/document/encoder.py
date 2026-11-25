"""M3: a small pretrained encoder fine-tuned on serialised rows (blueprint 8.4, D3).

The row is written as text from its visible fields only (rule 7), in the blueprint's form:

    [STMT] cash flow [PARENT] investing activities [LABEL] purchases of property and equipment
    [INDENT] 1 [TOTAL] 0

The markers are plain text for the encoder's own tokenizer (no new tokens). The encoder's [CLS]
vector goes through dropout and one linear layer to the classes (canonical items and OTHER);
the loss is cross-entropy, class-weighted "balanced" (like M1 and M2), by its square root, or
unweighted (a setting chosen by finagent.training.m3). Probabilities are the softmax of the logits, uncalibrated; calibrators are fitted outside this
package (finagent.training.calibration).

The base encoder is read from a local directory only (the pinned bge-small-en-v1.5 under the
git-ignored models root; finagent.training.m3 verifies its pin first). Training and inference
run on MPS when available, otherwise CPU, in fp32. Seeds are fixed (Python, NumPy, torch, the
data order); MPS kernels are not guaranteed to be bitwise deterministic.
"""

import math
import random
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from finagent.document.classify import normalise_label, top_predictions
from finagent.records import Prediction, RowInstance

STATEMENT_NAMES = {"IS": "income statement", "BS": "balance sheet", "CF": "cash flow"}
HEAD_PREFIX = "head."
ENCODER_PREFIX = "encoder."

# on_epoch(epoch, model) -> True to stop training after this epoch (early stopping)
EpochCallback = Callable[[int, "EncoderRowClassifier"], bool]


def serialise_row(row: RowInstance) -> str:
    """The blueprint's serialised row: statement, parent header, label, indent, total flag."""
    return (f"[STMT] {STATEMENT_NAMES[row.statement]} "
            f"[PARENT] {normalise_label(row.parent_header)} "
            f"[LABEL] {normalise_label(row.label)} "
            f"[INDENT] {row.indent} [TOTAL] {int(row.is_total)}")


def pick_device() -> str:
    """'mps' when Apple's Metal backend is available, otherwise 'cpu'."""
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def seed_everything(seed: int) -> None:
    """Python, NumPy and torch (every device) random generators."""
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.backends.mps.is_available():
        torch.mps.manual_seed(seed)


CLASS_WEIGHTS = ("balanced", "sqrt", None)


def release_memory() -> None:
    """Collect unreferenced tensors (the optimiser and scheduler hold reference cycles) and
    return cached MPS memory to the system, so consecutive fits do not accumulate memory."""
    import gc

    import torch

    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


def balanced_weights(items: Sequence[str], classes: Sequence[str]) -> np.ndarray:
    """n / (k * n_c) for the k classes present in `items` (sklearn's "balanced"); 0 for a class
    with no rows (it then adds nothing to the loss)."""
    counts = np.array([sum(1 for i in items if i == c) for c in classes], dtype=float)
    present = int((counts > 0).sum())
    return np.where(counts > 0, len(items) / (present * np.where(counts > 0, counts, 1.0)), 0.0)


def class_weights(items: Sequence[str], classes: Sequence[str], mode: str | None) -> np.ndarray:
    """Loss weight per class: "balanced", its square root ("sqrt"; a milder tilt towards rare
    items), or None (every row counts the same)."""
    if mode not in CLASS_WEIGHTS:
        raise ValueError(f"unknown class weighting {mode!r}")
    if mode is None:
        return np.ones(len(classes))
    weights = balanced_weights(items, classes)
    return np.sqrt(weights) if mode == "sqrt" else weights


def linear_schedule(step: int, warmup_steps: int, total_steps: int) -> float:
    """Learning-rate factor: linear warm-up to 1, then linear decay to 0 at total_steps."""
    if step < warmup_steps:
        return (step + 1) / warmup_steps
    return max(0.0, (total_steps - step) / max(1, total_steps - warmup_steps))


class EncoderRowClassifier:
    """M3: fine-tuned encoder on serialised rows -> linear head on [CLS]."""

    version = "m3-bge-small-ft"

    def __init__(self, base: Path, learning_rate: float = 1e-4, epochs: int = 5,
                 class_weight: str | None = "balanced", batch_size: int = 32,
                 max_length: int = 128, warmup: float = 0.06, weight_decay: float = 0.01,
                 dropout: float = 0.1, seed: int = 0, device: str | None = None) -> None:
        if class_weight not in CLASS_WEIGHTS:
            raise ValueError(f"unknown class weighting {class_weight!r}")
        self.base = Path(base)
        self.params = {"learning_rate": learning_rate, "epochs": epochs,
                       "class_weight": class_weight, "batch_size": batch_size,
                       "max_length": max_length, "warmup": warmup,
                       "weight_decay": weight_decay, "dropout": dropout, "seed": seed}
        self.device = device or pick_device()
        self.tokenizer = None
        self.encoder = None
        self.head = None
        self._classes: tuple[str, ...] = ()
        self.epochs_trained = 0

    # --- building -------------------------------------------------------------------------
    def _build(self, classes: Sequence[str], pretrained: bool) -> None:
        import torch
        from transformers import AutoConfig, AutoModel, AutoTokenizer

        self._classes = tuple(classes)
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.base), local_files_only=True)
        if pretrained:
            self.encoder = AutoModel.from_pretrained(str(self.base), local_files_only=True)
        else:
            self.encoder = AutoModel.from_config(
                AutoConfig.from_pretrained(str(self.base), local_files_only=True))
        self.head = torch.nn.Linear(self.encoder.config.hidden_size, len(self._classes))
        self.encoder.to(self.device)
        self.head.to(self.device)

    @property
    def classes(self) -> tuple[str, ...]:
        return self._classes

    def _encode(self, rows: Sequence[RowInstance]) -> list[list[int]]:
        texts = [serialise_row(r) for r in rows]
        return self.tokenizer(texts, truncation=True,
                              max_length=self.params["max_length"])["input_ids"]

    def _batch(self, ids: Sequence[list[int]]):
        """Padded input ids and attention mask on the device (padding to the longest row)."""
        import torch

        width = max(len(x) for x in ids)
        pad = self.tokenizer.pad_token_id
        input_ids = torch.full((len(ids), width), pad, dtype=torch.long)
        mask = torch.zeros((len(ids), width), dtype=torch.long)
        for i, x in enumerate(ids):
            input_ids[i, :len(x)] = torch.tensor(x, dtype=torch.long)
            mask[i, :len(x)] = 1
        return input_ids.to(self.device), mask.to(self.device)

    def _logits(self, input_ids, mask, train: bool):
        import torch

        hidden = self.encoder(input_ids=input_ids, attention_mask=mask).last_hidden_state[:, 0]
        if train:
            hidden = torch.nn.functional.dropout(hidden, p=self.params["dropout"], training=True)
        return self.head(hidden)

    # --- training -------------------------------------------------------------------------
    def fit(self, rows: Iterable[RowInstance], items: Iterable[str],
            classes: Sequence[str] | None = None, stop_after: int | None = None,
            on_epoch: EpochCallback | None = None) -> "EncoderRowClassifier":
        """Fine-tune from the base encoder.

        The learning-rate schedule always spans `epochs` epochs (warm-up, then linear decay);
        training stops after `stop_after` epochs (default: all) or when `on_epoch` returns True,
        so a run stopped at epoch k matches a shorter run with the same settings. `classes`
        fixes the output columns (default: the classes in `items`).
        """
        import torch

        rows, items = list(rows), list(items)
        classes = sorted(set(items)) if classes is None else list(classes)
        if len(set(items)) < 2:
            raise ValueError("M3 needs at least two classes to train")
        p = self.params
        seed_everything(p["seed"])
        self._build(classes, pretrained=True)
        position = {c: i for i, c in enumerate(classes)}
        targets = torch.tensor([position[i] for i in items], dtype=torch.long)
        ids = self._encode(rows)
        weight = torch.tensor(class_weights(items, classes, p["class_weight"]),
                              dtype=torch.float32, device=self.device)
        steps_per_epoch = math.ceil(len(rows) / p["batch_size"])
        total = steps_per_epoch * p["epochs"]
        warmup = max(1, int(round(p["warmup"] * total)))
        decay, no_decay = [], []
        for module in (self.encoder, self.head):
            for name, param in module.named_parameters():
                (no_decay if name.endswith("bias") or "LayerNorm" in name else decay).append(param)
        optimiser = torch.optim.AdamW([{"params": decay, "weight_decay": p["weight_decay"]},
                                       {"params": no_decay, "weight_decay": 0.0}],
                                      lr=p["learning_rate"])
        scheduler = torch.optim.lr_scheduler.LambdaLR(
            optimiser, lambda step: linear_schedule(step, warmup, total))
        order_rng = np.random.default_rng(p["seed"])
        last = p["epochs"] if stop_after is None else min(stop_after, p["epochs"])
        self.epochs_trained = 0
        for epoch in range(1, last + 1):
            self.encoder.train()
            order = order_rng.permutation(len(rows))
            for start in range(0, len(rows), p["batch_size"]):
                batch = order[start:start + p["batch_size"]]
                input_ids, mask = self._batch([ids[i] for i in batch])
                logits = self._logits(input_ids, mask, train=True)
                loss = torch.nn.functional.cross_entropy(
                    logits, targets[batch].to(self.device), weight=weight)
                optimiser.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(decay + no_decay, 1.0)
                optimiser.step()
                scheduler.step()
            self.epochs_trained = epoch
            if on_epoch is not None and on_epoch(epoch, self):
                break
        optimiser.zero_grad(set_to_none=True)
        del optimiser, scheduler, weight, targets
        release_memory()
        return self

    def release(self) -> None:
        """Drop the encoder and head (a trainer's fold model after it has predicted)."""
        self.encoder = self.head = None
        release_memory()

    # --- inference ------------------------------------------------------------------------
    def logits(self, rows: Sequence[RowInstance], batch_size: int = 128) -> np.ndarray:
        """Raw class scores, one column per class in `classes`, rows in input order."""
        import torch

        ids = self._encode(rows)
        order = sorted(range(len(ids)), key=lambda i: len(ids[i]))
        out = np.zeros((len(ids), len(self._classes)))
        self.encoder.eval()
        with torch.no_grad():
            for start in range(0, len(order), batch_size):
                chunk = order[start:start + batch_size]
                input_ids, mask = self._batch([ids[i] for i in chunk])
                out[chunk] = self._logits(input_ids, mask, train=False).float().cpu().numpy()
        return out

    def probabilities(self, rows: Sequence[RowInstance]) -> np.ndarray:
        """softmax(logits), one row per input row (uncalibrated)."""
        z = self.logits(rows)
        z = np.exp(z - z.max(axis=1, keepdims=True))
        return z / z.sum(axis=1, keepdims=True)

    def predict(self, rows: Sequence[RowInstance]) -> list[Prediction]:
        if not rows:
            return []
        return top_predictions(rows, self.probabilities(rows), self.classes, self.version)

    # --- saving ---------------------------------------------------------------------------
    def save(self, path: Path) -> Path:
        """Encoder and head weights in one safetensors file (classes and settings go in the
        caller's meta.json)."""
        from safetensors.torch import save_file

        tensors = {ENCODER_PREFIX + k: v.detach().cpu().contiguous()
                   for k, v in self.encoder.state_dict().items()}
        tensors.update({HEAD_PREFIX + k: v.detach().cpu().contiguous()
                        for k, v in self.head.state_dict().items()})
        path.parent.mkdir(parents=True, exist_ok=True)
        save_file(tensors, str(path))
        return path

    @classmethod
    def load(cls, base: Path, path: Path, classes: Sequence[str], params: dict[str, Any],
             device: str | None = None) -> "EncoderRowClassifier":
        """Rebuild from the base directory's config and tokenizer, then the saved weights."""
        from safetensors.torch import load_file

        model = cls(base, device=device, **params)
        model._build(classes, pretrained=False)
        tensors = load_file(str(path))
        model.encoder.load_state_dict(
            {k[len(ENCODER_PREFIX):]: v for k, v in tensors.items()
             if k.startswith(ENCODER_PREFIX)})
        model.head.load_state_dict(
            {k[len(HEAD_PREFIX):]: v for k, v in tensors.items() if k.startswith(HEAD_PREFIX)})
        model.encoder.to(model.device)
        model.head.to(model.device)
        return model
