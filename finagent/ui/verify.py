"""Verify, by sha256, the files the demo needs (Phase 9 cut-down; BC33). The Dockerfile runs it
at build, so an image with a changed model or evidence manifest is never built:

    python -m finagent.ui.verify

Checks configs/ui.yaml `pinned_files` (evidence manifest, M1 v3, the intent model, the dataset
meta the M1 loader checks) and the retrieval models in `retrieval_models` against
configs/models.yaml (the E4 judge is evaluation only and not shipped). The evidence files
themselves are checked against the manifest by the evidence store when they are read.
"""

import sys
from pathlib import Path

from finagent.config import load_model_pins
from finagent.retrieval.models import file_sha256, problems as pin_problems
from finagent.ui.data import ROOT, UiConfig, load_ui_config


def problems(config: UiConfig, root: Path = ROOT, pins: dict | None = None) -> list[str]:
    """Every pinned file that is missing or whose sha256 differs (empty: all match)."""
    found = []
    for name, sha in sorted(config.pinned_files.items()):
        path = root / name
        if not path.is_file():
            found.append(f"{name}: missing")
        elif file_sha256(path) != sha:
            found.append(f"{name}: sha256 differs from configs/ui.yaml")
    pins = load_model_pins() if pins is None else pins
    for key in config.retrieval_models:
        found += pin_problems(pins[key])
    return found


def main(argv: list[str] | None = None) -> int:
    del argv
    config = load_ui_config()
    found = problems(config)
    for line in found:
        print(f"FAIL {line}")
    if not found:
        print(f"OK: {len(config.pinned_files)} pinned files and "
              f"{len(config.retrieval_models)} retrieval models match their sha256")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
