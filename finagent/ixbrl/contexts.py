"""Inline XBRL contexts and units (blueprint 8.2)."""

from dataclasses import dataclass, field
from datetime import date

from lxml import etree

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
NS = {"xbrli": XBRLI, "xbrldi": XBRLDI}


class TagParseError(ValueError):
    """Raised when an inline XBRL fact, context or unit cannot be read exactly."""


@dataclass(frozen=True)
class Context:
    """An xbrli:context. For an instant context, start is None and end is the instant."""

    context_id: str
    start: date | None
    end: date
    dimensions: dict[str, str] = field(default_factory=dict)

    @property
    def is_instant(self) -> bool:
        return self.start is None


def _text(element: etree._Element) -> str:
    return "".join(element.itertext()).strip()


def _parse_period(context_id: str, period: etree._Element | None) -> tuple[date | None, date]:
    if period is None:
        raise TagParseError(f"Context {context_id} has no period")
    instant = period.find("xbrli:instant", NS)
    if instant is not None:
        return None, date.fromisoformat(_text(instant))
    start = period.find("xbrli:startDate", NS)
    end = period.find("xbrli:endDate", NS)
    if start is None or end is None:
        raise TagParseError(f"Context {context_id} has neither an instant nor a start and end")
    return date.fromisoformat(_text(start)), date.fromisoformat(_text(end))


def _parse_dimensions(context_id: str, context: etree._Element) -> dict[str, str]:
    """Explicit and typed members from the segment or scenario. Typed members keep their text."""
    dimensions: dict[str, str] = {}
    members = context.xpath(
        "./xbrli:entity/xbrli:segment/*|./xbrli:scenario/*", namespaces=NS
    )
    for member in members:
        local = etree.QName(member).localname
        axis = member.get("dimension")
        if local not in ("explicitMember", "typedMember") or not axis:
            continue
        if axis in dimensions:
            raise TagParseError(f"Context {context_id} repeats dimension {axis}")
        dimensions[axis] = _text(member)
    return dimensions


def parse_contexts(root: etree._Element) -> dict[str, Context]:
    """Every xbrli:context in the document, keyed by id."""
    contexts: dict[str, Context] = {}
    for element in root.iter(f"{{{XBRLI}}}context"):
        context_id = element.get("id")
        if not context_id:
            raise TagParseError("Context without an id")
        start, end = _parse_period(context_id, element.find("xbrli:period", NS))
        contexts[context_id] = Context(
            context_id=context_id,
            start=start,
            end=end,
            dimensions=_parse_dimensions(context_id, element),
        )
    return contexts


def _measure_name(measure: etree._Element) -> str:
    """'iso4217:USD' -> 'USD'; 'xbrli:shares' -> 'shares'."""
    return _text(measure).split(":")[-1]


def _measures(parent: etree._Element) -> str:
    names = [_measure_name(m) for m in parent.findall("xbrli:measure", NS)]
    if not names:
        raise TagParseError("Unit part without a measure")
    return "*".join(names)


def parse_units(root: etree._Element) -> dict[str, str]:
    """Every xbrli:unit as a readable string, e.g. 'USD', 'shares', 'USD/shares'."""
    units: dict[str, str] = {}
    for element in root.iter(f"{{{XBRLI}}}unit"):
        unit_id = element.get("id")
        if not unit_id:
            raise TagParseError("Unit without an id")
        divide = element.find("xbrli:divide", NS)
        if divide is not None:
            numerator = divide.find("xbrli:unitNumerator", NS)
            denominator = divide.find("xbrli:unitDenominator", NS)
            if numerator is None or denominator is None:
                raise TagParseError(f"Unit {unit_id} has an incomplete divide")
            units[unit_id] = f"{_measures(numerator)}/{_measures(denominator)}"
        else:
            units[unit_id] = _measures(element)
    return units
