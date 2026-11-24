"""Evidence views for replays (blueprint 10.6 E8; reviewer decision #119; Phase 6.3).

An EvidenceView says which evidence the statement build may read. The default view (FULL) reads
everything and leaves every path exactly as before. The E8 conditions:

- complete: FULL;
- tag primary hidden: Company Facts are not read (the inline tags still are);
- both tag sources hidden (#119): a fully untagged filing. Hidden are the tag channel (Company
  Facts and inline tags), the A.2 model inputs read from tags, the BC14 label rule and the BC6
  tagged-cell counts given to statement location. NCI follows the BC23 visible-label rule (no
  NCI only when the document reads no total-equity or NCI net-income line and no IS or BS row
  label mentions a noncontrolling or minority interest). If location then fails, that is a
  legitimate GAP;
- document reading hidden: the document channel reads no rows (every value can only be
  tag-only, flagged single-channel).

Model outputs built from a view that hides a whole channel carry SINGLE_CHANNEL_INPUTS.
Nothing here writes to evidence/.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from finagent.ixbrl import TagFact
from finagent.records import RowInstance

SINGLE_CHANNEL_INPUTS = "SINGLE_CHANNEL_INPUTS"
ABSENT = "ABSENT"
NCI_LINES = ("total_equity", "net_income_including_nci")  # BC23 (as finagent.verify.workspace)


@dataclass(frozen=True)
class EvidenceView:
    """Which evidence the build may read; hashable, so it can key a cache."""

    companyfacts: bool = True
    inline_tags: bool = True
    document: bool = True

    @property
    def complete(self) -> bool:
        return self.companyfacts and self.inline_tags and self.document

    @property
    def untagged(self) -> bool:
        """Both tag sources hidden: a fully untagged filing (#119)."""
        return not self.companyfacts and not self.inline_tags

    @property
    def single_channel(self) -> bool:
        """A whole channel is hidden, so every value rests on one channel."""
        return self.untagged or not self.document

    @property
    def name(self) -> str:
        hidden = [n for n, shown in (("companyfacts", self.companyfacts),
                                     ("inline_tags", self.inline_tags),
                                     ("document", self.document)) if not shown]
        return "complete" if not hidden else "hidden:" + "+".join(hidden)


FULL = EvidenceView()
NO_COMPANYFACTS = EvidenceView(companyfacts=False)
UNTAGGED = EvidenceView(companyfacts=False, inline_tags=False)
NO_DOCUMENT = EvidenceView(document=False)
E8_CONDITIONS = (FULL, NO_COMPANYFACTS, UNTAGGED, NO_DOCUMENT)


def view_companyfacts(view: EvidenceView, companyfacts: dict[str, Any]) -> dict[str, Any]:
    """The Company Facts the build may read ({} when hidden)."""
    return companyfacts if view.companyfacts else {}


def view_facts(view: EvidenceView, facts: list[TagFact]) -> list[TagFact]:
    """The inline tag facts the build may read ([] when hidden)."""
    return facts if view.inline_tags else []


def view_rows(view: EvidenceView, rows: list[RowInstance]) -> list[RowInstance]:
    """The statement rows the document channel may read ([] when the document is hidden)."""
    return rows if view.document else []


def untagged_nci(documents: Mapping[str, Any], rows: Sequence[RowInstance],
                 nci_concepts: Sequence[tuple[tuple[str, ...], str]]
                 ) -> dict[tuple[str, ...], str]:
    """NCI status without tags (BC23 with #110): every NCI concept list ABSENT when the document
    reads neither NCI line and no IS / BS label mentions an NCI; else unknown ({})."""
    from finagent.config import load_agent_config
    from finagent.verify.workspace import nci_label_seen

    read = {item for item, d in documents.items() if getattr(d, "value", None) is not None}
    if read & set(NCI_LINES) or nci_label_seen(rows, load_agent_config()):
        return {}
    return {concepts: ABSENT for concepts, _ in nci_concepts}
