"""Static checks of the demo Dockerfile (Phase 9 cut-down; BC33). The image is not built here (no
Docker on the development Mac); these checks stand in for a linter and tie the image to the
environment the tests ran in."""

import json
import re
from importlib import metadata
from pathlib import Path

import pytest

from finagent.ui import data as d

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
IGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
REQUIREMENTS = ROOT / "docker" / "requirements-demo.txt"


def instructions() -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments), with line continuations joined and comments dropped."""
    text = re.sub(r"\\\n", " ", DOCKERFILE)
    found = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            word, _, rest = line.partition(" ")
            found.append((word.upper(), rest.strip()))
    return found


def pins() -> dict[str, str]:
    found = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, sep, version = line.partition("==")
            assert sep, f"unpinned requirement: {line}"
            found[name] = version
    return found


def test_base_image_and_instructions():
    ins = instructions()
    assert ins[0] == ("FROM", "python:3.11-slim")
    assert not any(word == "ADD" for word, _ in ins)
    assert [w for w, _ in ins].count("CMD") + [w for w, _ in ins].count("ENTRYPOINT") == 1
    users = [arg for word, arg in ins if word == "USER"]
    assert users and users[-1] not in ("root", "0")
    assert ("EXPOSE", "7860") in ins


def test_entrypoint_is_streamlit_on_port_7860():
    entry = next(arg for word, arg in instructions() if word == "ENTRYPOINT")
    args = json.loads(entry)
    assert args[:3] == ["streamlit", "run", "finagent/ui/app.py"]
    assert "--server.port=7860" in args and "--server.address=0.0.0.0" in args


def test_pip_installs_are_pinned_cpu_only_and_cacheless():
    runs = [arg for word, arg in instructions() if word == "RUN"]
    installs = [part for arg in runs for part in arg.split("&&") if "pip install" in part]
    assert installs and all("--no-cache-dir" in part for part in installs)
    torch = next(part for part in installs if "torch" in part)
    assert re.search(r"torch==[\d.]+", torch)
    assert "--index-url https://download.pytorch.org/whl/cpu" in torch
    assert installs.index(torch) == 0  # before anything that depends on torch


def test_models_verified_and_evidence_read_only_at_build():
    runs = " ".join(arg for word, arg in instructions() if word == "RUN")
    assert "python -m finagent.ui.verify" in runs
    assert "chmod -R a-w evidence" in runs
    env = " ".join(arg for word, arg in instructions() if word == "ENV")
    assert "HF_HUB_OFFLINE=1" in env


def test_requirements_match_the_tested_environment():
    for name, version in pins().items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            pytest.fail(f"{name} is pinned but not installed in the test environment")
        assert installed == version, f"{name}: pinned {version}, tested with {installed}"
    torch = re.search(r"torch==([\d.]+)", DOCKERFILE).group(1)
    assert metadata.version("torch").split("+")[0] == torch


def test_sklearn_pin_matches_the_shipped_models():
    sklearn = pins()["scikit-learn"]
    for meta in ("m1_v3.meta.json", "intent_v3.meta.json"):
        data = json.loads((ROOT / "models" / meta).read_text(encoding="utf-8"))
        assert data["sklearn_version"] == sklearn


def test_copied_runs_are_the_latest_and_not_ignored():
    copied = set(re.findall(r"COPY runs/([a-z0-9]+-\d{4})/", DOCKERFILE))
    for run in copied:
        assert f"!runs/{run}" in IGNORE
    runs = d.latest_runs(d.load_ui_config())
    if any(v is None for v in runs.values()):
        pytest.skip("pinned runs are git-ignored; run where they exist")
    assert {p.name for p in runs.values()} <= copied
    assert "e7-0001" in copied


def test_every_copy_source_exists_locally():
    missing = []
    for word, arg in instructions():
        if word != "COPY":
            continue
        for source in arg.split()[:-1]:
            if not (ROOT / source.rstrip("/")).exists():
                missing.append(source)
    if missing and not (ROOT / "models" / "m1_v3.joblib").exists():
        pytest.skip("models and runs are git-ignored; run where they are pinned")
    assert not missing, missing
