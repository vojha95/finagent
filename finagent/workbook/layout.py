"""The statement-sheet templates of configs/workbook.yaml (Phase 4d; BC21)."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from finagent.config import ConfigError

WORKBOOK_PATH = Path(__file__).resolve().parents[2] / "configs" / "workbook.yaml"
STATEMENT_SHEETS = ("Income Statement", "Balance Sheet", "Cash Flow")
KINDS = ("header", "line", "subtotal", "total", "detail", "pct", "check")
FORMATS = ("dollars", "check", "ratio", "percent", "days", "rate")


@dataclass(frozen=True)
class Term:
    sign: int
    row: str
    optional: bool = False


@dataclass(frozen=True)
class TemplateRow:
    row_id: str
    kind: str
    label: str = ""
    metric: str | None = None
    sign: int = 1
    components: tuple[Term, ...] = ()
    residual: tuple[Term, ...] = ()
    link: tuple[str, str] | None = None
    special: str | None = None
    model: str | None = None
    pct: dict[str, str] = field(default_factory=dict)
    when: str | None = None
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkbookLayout:
    statements: dict[str, tuple[TemplateRow, ...]]
    number_formats: dict[str, str]
    schedule_inputs: tuple[str, ...]


def _terms(raw: object, where: str) -> tuple[Term, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{where}: terms must be a list")
    found = []
    for item in raw:
        text = str(item)
        if text[:1] not in "+-":
            raise ConfigError(f"{where}: term {text!r} needs a sign")
        optional = text.endswith("?")
        found.append(Term(1 if text[0] == "+" else -1, text[1:].rstrip("?"), optional))
    return tuple(found)


def _row(raw: dict, sheet: str) -> TemplateRow:
    where = f"workbook {sheet} row {raw.get('id')!r}"
    if not isinstance(raw.get("id"), str) or raw.get("kind") not in KINDS:
        raise ConfigError(f"{where}: needs an id and a kind in {KINDS}")
    link = None
    if raw.get("link"):
        target_sheet, target_row = str(raw["link"]).split(":")
        link = (target_sheet, target_row)
    if raw.get("special") not in (None, "debt", "equity"):
        raise ConfigError(f"{where}: special must be debt or equity")
    if raw.get("when") not in (None, "cogs", "no_cogs"):
        raise ConfigError(f"{where}: when must be cogs or no_cogs")
    pct = dict(raw.get("pct") or {})
    if raw["kind"] == "pct" and not ({"num", "den"} <= set(pct) or "growth" in pct):
        raise ConfigError(f"{where}: pct needs num and den, or growth")
    return TemplateRow(raw["id"], raw["kind"], str(raw.get("label") or ""), raw.get("metric"),
                       int(raw.get("sign", 1)), _terms(raw.get("components"), where),
                       _terms(raw.get("residual"), where), link, raw.get("special"),
                       raw.get("model"), pct, raw.get("when"),
                       tuple(raw.get("details") or ()))


def load_workbook_layout(path: Path | str = WORKBOOK_PATH) -> WorkbookLayout:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load {path}") from exc
    raw = data.get("statements") or {}
    if set(raw) != set(STATEMENT_SHEETS):
        raise ConfigError(f"workbook statements must list exactly {STATEMENT_SHEETS}")
    statements = {}
    for sheet in STATEMENT_SHEETS:
        rows = tuple(_row(r, sheet) for r in raw[sheet] or [])
        ids = [r.row_id for r in rows]
        if len(set(ids)) != len(ids):
            raise ConfigError(f"workbook sheet {sheet} repeats a row id")
        known = set(ids)
        for r in rows:
            refs = [t.row for t in r.components + r.residual] + list(r.pct.values())
            missing = [x for x in refs if x not in known]
            if missing:
                raise ConfigError(f"workbook {sheet} row {r.row_id}: unknown rows {missing}")
        statements[sheet] = rows
    formats = {str(k): str(v) for k, v in (data.get("number_formats") or {}).items()}
    absent = [f for f in FORMATS if f not in formats]
    if absent:
        raise ConfigError(f"workbook number_formats needs {absent}")
    return WorkbookLayout(statements, formats, tuple(data.get("schedule_inputs") or ()))
