"""Pinned local models (blueprint 8.14; Phase 7.4; D8 approved by Vineet; the E4 judge, D9).

configs/models.yaml pins each model: its Hugging Face repo, the commit (revision), the local
directory under the git-ignored models root (never evidence/), and every file it needs with
its sha256 and size. The weights are downloaded once (`download`) and from then on only read
from disk:

- verify(pin): every pinned file is present with its pinned size and sha256, else
  ModelHashError. Run before every load and by `python -m finagent.retrieval.models verify`.
- load_embedder / load_cross_encoder: verify, then load offline only: HF_HUB_OFFLINE=1 and
  TRANSFORMERS_OFFLINE=1 are set, the model is read from its local directory with
  local_files_only=True, on CPU. No network call is made after the download.

- load_nli_judge: the same for the D9 NLI judge (E4, 7.6; evaluation only), with its label
  names from the pinned config.json.

`verify` checks every pin of configs/models.yaml, the judge included. No external AI API is
called.

    python -m finagent.retrieval.models verify
    python -m finagent.retrieval.models download   # once, with network; then verifies
"""

import argparse
import hashlib
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from finagent.config import ModelPin, load_model_pins

OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}


class ModelHashError(Exception):
    """A pinned model file is missing or its size or sha256 differs from configs/models.yaml."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_pins(directory: Path, names: Iterable[str]) -> dict[str, tuple[str, int]]:
    """sha256 and size of each named file under directory (to write a pin)."""
    return {name: (file_sha256(directory / name), (directory / name).stat().st_size)
            for name in names}


def problems(pin: ModelPin) -> list[str]:
    """Every pinned file that is missing or differs (empty: the model matches its pin)."""
    found = []
    for name, (sha, size) in sorted(pin.files.items()):
        path = pin.directory / name
        if not path.is_file():
            found.append(f"{pin.key}: {name} missing")
        elif path.stat().st_size != size:
            found.append(f"{pin.key}: {name} size {path.stat().st_size} != pinned {size}")
        elif file_sha256(path) != sha:
            found.append(f"{pin.key}: {name} sha256 differs from the pin")
    return found


def verify(pin: ModelPin) -> ModelPin:
    """The pin, after checking every file against it (ModelHashError otherwise)."""
    found = problems(pin)
    if found:
        raise ModelHashError("; ".join(found))
    return pin


def go_offline() -> None:
    """No Hugging Face network access from here on (also for an already imported hub)."""
    os.environ.update(OFFLINE_ENV)
    try:
        import huggingface_hub.constants as constants
        constants.HF_HUB_OFFLINE = True
    except ImportError:  # the hub is not installed: nothing can go online
        pass


def load_embedder(pin: ModelPin) -> Any:
    """The verified sentence-transformers embedder, offline, on CPU."""
    verify(pin)
    go_offline()
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(str(pin.directory), device="cpu", local_files_only=True)


def load_cross_encoder(pin: ModelPin) -> Any:
    """The verified cross-encoder, offline, on CPU."""
    verify(pin)
    go_offline()
    from sentence_transformers import CrossEncoder
    return CrossEncoder(str(pin.directory), device="cpu", local_files_only=True)


def load_nli_judge(pin: ModelPin) -> tuple[Any, dict[int, str]]:
    """The verified NLI cross-encoder (E4 judge, D9), offline, on CPU, with its label names
    by output index (from the pinned config.json, e.g. {0: "contradiction", ...})."""
    model = load_cross_encoder(pin)
    names = {int(k): str(v).lower() for k, v in model.model.config.id2label.items()}
    return model, names


def download(pin: ModelPin) -> ModelPin:
    """Download the pinned files at the pinned revision (network; once), then verify."""
    from huggingface_hub import snapshot_download
    pin.directory.mkdir(parents=True, exist_ok=True)
    snapshot_download(pin.repo, revision=pin.revision, local_dir=str(pin.directory),
                      allow_patterns=sorted(pin.files))
    return verify(pin)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["verify", "download"])
    parser.add_argument("--model", default=None, help="one key of configs/models.yaml")
    args = parser.parse_args(argv)
    pins = load_model_pins()
    chosen = [pins[args.model]] if args.model else list(pins.values())
    failed = 0
    for pin in chosen:
        try:
            (download if args.action == "download" else verify)(pin)
            print(f"{pin.key} {pin.model_id} @ {pin.revision}: OK "
                  f"({len(pin.files)} files, {pin.size:,} bytes; fingerprint "
                  f"{pin.fingerprint[:16]})")
        except ModelHashError as exc:
            failed += 1
            print(f"{pin.key}: FAIL {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
