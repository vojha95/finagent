"""LibreOffice recalculation check of the built workbooks (Phase 4d, requirement 8).

    python -m finagent.workbook.recalc [--run-id workbooks-0001] [--tickers MSFT,CVX]

For each workbook and each scenario: copy the workbook with the scenario selector set, let
LibreOffice (headless) recalculate it and save it as xlsx, read the cached values back and
compare every formula cell with the expected value in <TICKER>.manifest.json ($0.01 for
dollars, 1e-9 for ratios, exact for text). A cell with no cached value after conversion counts
as "not recalculated". When soffice is not installed, the check is reported as PENDING.
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
SOFFICE_CANDIDATES = ("/Applications/LibreOffice.app/Contents/MacOS/soffice",)
TOLERANCES = {"dollar": 0.01, "ratio": 1e-9, "exact": 0.0}
PENDING, PASS, FAIL = "PENDING", "PASS", "FAIL"


def find_soffice() -> str | None:
    found = os.environ.get("FINAGENT_SOFFICE") or shutil.which("soffice")
    if found:
        return found
    return next((c for c in SOFFICE_CANDIDATES if Path(c).exists()), None)


def recalculate(soffice: str, source: Path, out_dir: Path) -> Path:
    """Convert with LibreOffice headless (formulas are recalculated on load; the workbook has
    no cached values and requests a full calculation on load)."""
    profile = out_dir / "lo_profile"
    subprocess.run([soffice, f"-env:UserInstallation=file://{profile}", "--headless",
                    "--calc", "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir",
                    str(out_dir / "out"), str(source)],
                   check=True, capture_output=True, timeout=600)
    return out_dir / "out" / source.name


def compare_cells(values_wb, manifest: dict[str, Any], scenario: str) -> list[str]:
    problems = []
    for key, cell in manifest["cells"].items():
        expected = cell["expected"]
        if scenario not in expected and "*" not in expected:
            continue
        want = expected.get(scenario, expected.get("*"))
        sheet, coord = key.rsplit("!", 1)
        got = values_wb[sheet][coord].value
        tol = TOLERANCES[cell["tolerance"]]
        if got is None:
            problems.append(f"{key}: not recalculated (no cached value)")
        elif isinstance(want, str) or isinstance(got, str):
            if got != want:
                problems.append(f"{key}: {got!r} != {want!r}")
        elif abs(float(got) - float(want)) > tol:
            problems.append(f"{key}: {got!r} != {want!r} (tolerance {tol:g})")
    return problems


def check_workbook(path: Path, soffice: str | None) -> dict[str, Any]:
    manifest = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    if soffice is None:
        return {"status": PENDING, "reason": "LibreOffice (soffice) is not installed"}
    results: dict[str, Any] = {}
    for scenario in manifest["scenarios"]:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            wb = load_workbook(path)
            if manifest["selector"]:
                sheet, coord = manifest["selector"]
                wb[sheet][coord].value = scenario
            copy = work / path.name
            wb.save(copy)
            converted = recalculate(soffice, copy, work)
            values = load_workbook(converted, data_only=True)
            problems = compare_cells(values, manifest, scenario)
        results[scenario] = {"mismatches": len(problems), "first": problems[:10]}
    status = PASS if all(r["mismatches"] == 0 for r in results.values()) else FAIL
    return {"status": status, "scenarios": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="workbooks-0001")
    parser.add_argument("--tickers", default="")
    args = parser.parse_args(argv)
    out = ROOT / "runs" / args.run_id
    wanted = {t for t in args.tickers.split(",") if t}
    soffice = find_soffice()
    report = {"soffice": soffice, "workbooks": {}}
    for path in sorted(out.glob("*.xlsx")):
        if wanted and path.stem not in wanted:
            continue
        report["workbooks"][path.stem] = check_workbook(path, soffice)
        print(path.stem, report["workbooks"][path.stem]["status"])
    (out / "recalc.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0 if all(r["status"] != FAIL for r in report["workbooks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
