import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


class EvidenceIntegrityError(ValueError):
    """Raised when pinned evidence is missing or fails integrity checks."""


class EvidenceNotPinnedError(EvidenceIntegrityError):
    """A filing required by the selection rules has no pinned snapshot (an evidence gap)."""


class InsufficientFilingsError(EvidenceIntegrityError):
    """Fewer annual filings are visible as of the date than the selection rules require."""


def load_manifest(evidence_dir: Path) -> dict[str, dict[str, str]]:
    manifest_path = evidence_dir / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceIntegrityError(f"Cannot load evidence manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise EvidenceIntegrityError("Evidence manifest must be a JSON object")
    return manifest


def read_snapshot(
    evidence_dir: Path,
    manifest_name: str,
    manifest: dict[str, dict[str, str]] | None = None,
) -> bytes:
    """Read a gzipped snapshot and verify its decompressed SHA-256."""
    active_manifest = manifest if manifest is not None else load_manifest(evidence_dir)
    entry = active_manifest.get(manifest_name)
    if entry is None:
        raise EvidenceIntegrityError(f"Snapshot not in manifest: {manifest_name}")

    snapshot_name = entry.get("file")
    expected_sha256 = entry.get("sha256")
    if not snapshot_name or not expected_sha256:
        raise EvidenceIntegrityError(f"Invalid manifest entry: {manifest_name}")

    snapshot_path = evidence_dir / snapshot_name
    try:
        with gzip.open(snapshot_path, "rb") as handle:
            payload = handle.read()
    except (OSError, EOFError) as exc:
        raise EvidenceIntegrityError(f"Cannot decompress snapshot: {snapshot_name}") from exc

    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise EvidenceIntegrityError(
            f"SHA-256 mismatch for {manifest_name}: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    return payload


def read_snapshot_json(
    evidence_dir: Path,
    manifest_name: str,
    manifest: dict[str, dict[str, str]] | None = None,
) -> Any:
    payload = read_snapshot(evidence_dir, manifest_name, manifest)
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceIntegrityError(
            f"Snapshot is not valid UTF-8 JSON: {manifest_name}"
        ) from exc
