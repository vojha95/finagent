"""Pinned local models: hash check, offline loading, config validation (7.4; D8)."""

import os
from pathlib import Path

import pytest
import yaml

from finagent.config import ConfigError, ModelPin, load_model_pins
from finagent.retrieval import models
from finagent.retrieval.models import (ModelHashError, file_pins, file_sha256, go_offline,
                                       problems, verify)


def pin_for(directory: Path, files: dict[str, bytes]) -> ModelPin:
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(data)
    return ModelPin("embedder", "tiny", "org/tiny", "a" * 40, directory,
                    file_pins(directory, files))


def test_file_pins_are_sha256_and_size(tmp_path):
    pin = pin_for(tmp_path / "m", {"config.json": b"{}", "sub/vocab.txt": b"a\nb\n"})
    assert pin.files["config.json"] == (file_sha256(tmp_path / "m" / "config.json"), 2)
    assert pin.files["sub/vocab.txt"][1] == 4 and pin.size == 6
    assert len(pin.fingerprint) == 64


def test_verify_passes_only_when_every_file_matches_its_pin(tmp_path):
    pin = pin_for(tmp_path / "m", {"config.json": b"{}", "weights.bin": b"abcd"})
    assert problems(pin) == [] and verify(pin) is pin
    (pin.directory / "weights.bin").write_bytes(b"abce")  # same size, one byte differs
    assert problems(pin) == ["embedder: weights.bin sha256 differs from the pin"]
    with pytest.raises(ModelHashError):
        verify(pin)
    (pin.directory / "weights.bin").write_bytes(b"abcdef")
    assert problems(pin) == ["embedder: weights.bin size 6 != pinned 4"]
    (pin.directory / "config.json").unlink()
    assert "embedder: config.json missing" in problems(pin)


def test_load_verifies_before_importing_the_model(tmp_path):
    pin = pin_for(tmp_path / "m", {"config.json": b"{}"})
    (pin.directory / "config.json").write_bytes(b"[]")
    with pytest.raises(ModelHashError):
        models.load_embedder(pin)
    with pytest.raises(ModelHashError):
        models.load_cross_encoder(pin)


def test_go_offline_sets_the_hub_offline(monkeypatch):
    for key in models.OFFLINE_ENV:
        monkeypatch.delenv(key, raising=False)
    go_offline()
    assert all(os.environ[k] == "1" for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"))


def test_main_reports_each_pin_and_fails_on_a_mismatch(tmp_path, monkeypatch, capsys):
    good = pin_for(tmp_path / "good", {"config.json": b"{}"})
    bad = pin_for(tmp_path / "bad", {"config.json": b"{}"})
    (bad.directory / "config.json").write_bytes(b"{ }")
    monkeypatch.setattr(models, "load_model_pins", lambda: {"good": good, "bad": bad})
    assert models.main(["verify", "--model", "good"]) == 0
    assert models.main(["verify"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_configured_pins_are_complete_and_outside_evidence():
    pins = load_model_pins()
    assert set(pins) == {"embedder", "reranker"}
    for pin in pins.values():
        assert len(pin.revision) == 40 and pin.files and pin.size > 0
        assert "evidence" not in pin.directory.parts and pin.directory.parts[-2:-1] == ("hf",)
        assert "model.safetensors" in pin.files and "tokenizer.json" in pin.files


def write_models(tmp_path: Path, **changes) -> Path:
    model = {"id": "tiny", "repo": "org/tiny", "revision": "a" * 40, "dir": "tiny",
             "files": {"config.json": {"sha256": "b" * 64, "size": 2}}}
    model.update(changes)
    (tmp_path / "configs").mkdir(exist_ok=True)
    path = tmp_path / "configs" / "models.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "root": "models/hf",
                                    "models": {"embedder": model}}), encoding="utf-8")
    return path


def test_model_pins_are_validated(tmp_path):
    assert load_model_pins(write_models(tmp_path))["embedder"].directory == \
        (tmp_path / "models" / "hf" / "tiny").resolve()
    bad = [{"revision": "main"}, {"dir": "../../evidence/x"},
           {"files": {"config.json": {"sha256": "short", "size": 2}}},
           {"files": {"config.json": {"sha256": "b" * 64, "size": 0}}},
           {"files": {"../x.json": {"sha256": "b" * 64, "size": 2}}}, {"files": {}}]
    for change in bad:
        with pytest.raises(ConfigError):
            load_model_pins(write_models(tmp_path, **change))


@pytest.mark.skipif(not all(p.directory.is_dir() for p in load_model_pins().values()),
                    reason="the pinned models are not downloaded (models/hf is git-ignored)")
def test_downloaded_models_match_their_pins():
    for pin in load_model_pins().values():
        assert problems(pin) == []
