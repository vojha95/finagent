"""Every number in docs/final_results.md appears in a source cited on the same line (Phase 10).

A plain string match, line by line: each line that states a number must name at least one
committed source file (a path such as `docs/e6_results.md` or `eval/experiments.csv`), and each
number on it must appear in one of those files. Identifiers are not numbers: run ids, commits,
dates, issue and blueprint-change references, evaluation, model, signal and check names, fiscal
years, versions and section references are masked first. Thousands separators and spaces around
"/" are ignored on both sides; a sign is not compared.

    python tests/test_final_results.py      # prints the lines and numbers checked
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "final_results.md"

SOURCE = re.compile(r"(?<![\w/.-])((?:docs|eval|configs|gold)/[\w./-]+\.(?:md|csv|yaml|jsonl))")
MASKS = [
    r"`[^`]*`",                                            # code: paths, commands
    r"\b(?:docs|eval|configs|gold|runs|finagent|tests)/[\w./-]*",
    r"\b\d{4}-\d{2}-\d{2}\b",                              # dates
    r"\b(?:[a-z][a-z0-9]*-)+\d{4}(?:/[a-z]+)?\b",          # run ids: e1-0009, serving-0002
    r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b",  # commits
    r"#\d+",                                               # issues
    r"\bBC\d+\b",
    r"\b[EMRIFDQ]\d+\b",                                   # E1, M3, R10, I10, F5, D10, Q19
    r"\bFY\d{4}\b",
    r"\bv\d+(?:\.\d+)*\b",                                 # versions
    r"\b(?:[Pp]hases?|[Ss]ections?|[Bb]lueprint|Appendix|[Rr]ows?)\s+\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?",
    r"@\d+",                                               # MRR@5, Recall@5
    r"\^\d+",                                              # pass^3
    r"\bPython 3\.\d+\b",
]
FRACTION = re.compile(r"(?<![\d.])\d+/\d+(?![\d.])")     # after normalise: no separators
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?:e-?\d+)?(?![\w.]\d)")


def normalise(text: str) -> str:
    text = text.replace("−", "-").replace("–", "-")
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)           # 1,150 -> 1150
    return re.sub(r"\s*/\s*", "/", text)                     # 28 / 32 -> 28/32


def numbers(line: str) -> list[str]:
    masked = line
    for pattern in MASKS:
        masked = re.sub(pattern, " ", masked)
    masked = normalise(masked)
    found = FRACTION.findall(masked)
    masked = FRACTION.sub(" ", masked)
    return found + NUMBER.findall(masked)


def appears(token: str, text: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(token)}(?![\d]|\.\d)", text) is not None


def check(doc: Path = DOC, root: Path = ROOT) -> tuple[list[str], int, int]:
    """Return (problems, lines checked, numbers checked)."""
    problems, lines, count, cache = [], 0, 0, {}
    for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("#") or re.fullmatch(r"[|\s:-]*", line):
            continue
        tokens = [normalise(t) for t in numbers(line)]
        if not tokens:
            continue
        lines += 1
        sources = sorted(set(SOURCE.findall(line)))
        if not sources:
            problems.append(f"line {n}: numbers {tokens} but no source cited")
            continue
        texts = []
        for s in sources:  # a file still to be written (e.g. a pending write-up) proves nothing
            if s not in cache:
                path = root / s
                cache[s] = normalise(path.read_text(encoding="utf-8")) if path.exists() else None
            if cache[s] is not None:
                texts.append(cache[s])
        if not texts:
            problems.append(f"line {n}: no cited source exists ({', '.join(sources)})")
            continue
        for t in tokens:
            count += 1
            if not any(appears(t, x) for x in texts):
                problems.append(f"line {n}: {t} is not in {', '.join(sources)}")
    return problems, lines, count


def test_every_number_is_in_its_cited_source():
    problems, lines, count = check()
    assert lines > 0 and count > 0
    assert not problems, "\n".join(problems)


def test_the_check_catches_a_wrong_number_and_a_missing_source(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "src.md").write_text("accuracy 0.913 [0.846, 0.961]; 28 / 32 origins; 1,150")
    doc = tmp_path / "docs" / "final.md"
    doc.write_text(
        "# Title 0.5\n"
        "| a | b |\n|---|---|\n"
        "| ok | 0.913 [0.846, 0.961], 28/32, 1150 (`docs/src.md`) |\n"
        "| wrong | 0.914 (`docs/src.md`) |\n"
        "| unsourced | 0.913 |\n"
        "| identifiers only | e1-0009 at 089962e on 2026-11-24 (#309, BC37, E1, M3, FY2025, v3) |\n"
        "| pending file beside a real one | 28/32 (`docs/src.md`; `docs/later.md`) |\n"
        "| pending file only | 0.913 (`docs/later.md`) |\n")
    problems, lines, count = check(doc, tmp_path)
    assert lines == 5 and count == 7
    assert problems == ["line 5: 0.914 is not in docs/src.md",
                        "line 6: numbers ['0.913'] but no source cited",
                        "line 9: no cited source exists (docs/later.md)"]


if __name__ == "__main__":
    found, checked_lines, checked = check()
    print(f"{DOC.relative_to(ROOT)}: {checked} numbers on {checked_lines} lines checked "
          f"against their cited sources; {len(found)} problems")
    print("\n".join(found))
    raise SystemExit(1 if found else 0)
