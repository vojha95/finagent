"""The Correlation Agent's view of one filing (blueprint 8.7, 8.8; Phase 5).

A FilingState holds one filing's rows, M1 predictions and document readings for its own fiscal
period, plus the agent's edits so far. Its view combines the readings as the statement set does
(finagent.statements: observe_item, derive_all, evaluate_filing) and turns the identity results
into checks the agent reads (CheckResult).

Two modes (8.7 box):
- product: tags visible. The tag readings, A.2 model inputs and NCI status are those of the
  statement build (FilingInputs). A value read as DISAGREE enters the identities with its
  document value, as a pending document-only value does, and has its own document-vs-tag check
  D:<item>. Only DISAGREE and document-only values can be changed. Under an E8 view that hides
  a channel, derivations leave out only NOT_REPORTED optional terms, as the build does (#147).
- evaluation: tags hidden (E2). The state is built from rows, predictions and the filing alone;
  there is no tag argument. A.2 facts are absent, face blocks and I6 read document cells only,
  and NCI follows BC23: no NCI when the document channel reads no total-equity line and no
  net-income-including-NCI line, and no IS or BS row label mentions a noncontrolling or
  minority interest (#110; configs/correlation_agent.yaml evaluation_nci). Every document value
  can be changed.

Edits are in memory (finagent.verify.edits); evidence is never touched.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from functools import cached_property
from typing import Literal

from finagent.config import AgentConfig
from finagent.document.channel import DocumentReading, read_document_channel
from finagent.evidence.store import FilingRecord
from finagent.ixbrl.tagstore import TagFact
from finagent.records import (CheckResult, DocumentEdit, IdentityResult, Observation, Outcome,
                              Prediction, RowInstance)
from finagent.statements.build import FilingInputs, StatementConfigs
from finagent.statements.derive import derive_all
from finagent.statements.identities import NOT_COMPUTABLE, Context, evaluate_filing
from finagent.statements.verify_set import PENDING, observe_item
from finagent.verify.dual_channel import tag_value, verify_item
from finagent.verify.edits import apply_edits, edited_reading, probability

Mode = Literal["product", "evaluation"]
ABSENT = "ABSENT"
# BC23: the document lines whose absence means "no NCI" in evaluation mode.
NCI_LINES = ("total_equity", "net_income_including_nci")
Transform = tuple[str, Decimal, bool]  # (row id, factor, negate) of a reading


def nci_label_seen(rows: Sequence[RowInstance], agent: AgentConfig) -> bool:
    """BC23 / #110 (evaluation mode): a row label on the configured statements mentions a
    noncontrolling or minority interest (visible text only)."""
    return any(re.search(p, row.label or "", re.IGNORECASE) for row in rows
               if row.statement in agent.nci_label_statements for p in agent.nci_label_patterns)


@dataclass(frozen=True)
class View:
    """The identities of one state of the filing's readings."""

    pool: dict[str, Observation]
    results: tuple[IdentityResult, ...]
    checks: tuple[CheckResult, ...]

    @property
    def residual(self) -> Decimal:
        """R: the sum of every triggering failure's excess over its tolerance."""
        return sum((c.excess for c in self.checks), Decimal(0))

    def check(self, check_id: str) -> CheckResult | None:
        return next((c for c in self.checks if c.check_id == check_id), None)


@dataclass(frozen=True)
class FilingState:
    """One filing's readings and the agent's edits so far (immutable; edits make new states)."""

    filing: FilingRecord
    mode: Mode
    configs: StatementConfigs
    agent: AgentConfig
    rows: tuple[RowInstance, ...]
    predictions: tuple[Prediction, ...]
    original: Mapping[str, DocumentReading]  # the readings the agent starts from
    facts: tuple[TagFact, ...] = ()
    tags: Mapping[str, Outcome] = field(default_factory=dict)
    model_inputs: Mapping[str, Observation] = field(default_factory=dict)
    nci: Mapping[tuple[str, ...], str] = field(default_factory=dict)
    located: tuple[str, ...] = ()
    scale: int = 0
    edits: Mapping[str, DocumentEdit] = field(default_factory=dict)  # item -> agent edit
    # Harness bookkeeping, never read by the agent's decisions: the transform behind each
    # starting reading that is not the row's own cell (an E2 injection), so agent edits compose.
    base: Mapping[str, Transform] = field(default_factory=dict)
    absence_unknown: frozenset[str] = frozenset()  # product mode: as the build's view (#147)

    # ------------------------------------------------------------ construction
    @classmethod
    def product(cls, inputs: FilingInputs, configs: StatementConfigs,
                agent: AgentConfig) -> "FilingState":
        """Product mode: the readings and tags of the statement build (tags visible)."""
        return cls(inputs.filing, "product", configs, agent, inputs.rows, inputs.predictions,
                   dict(inputs.documents), inputs.facts, dict(inputs.tags),
                   dict(inputs.model_inputs), dict(inputs.nci), tuple(inputs.located),
                   inputs.scale, absence_unknown=inputs.absence_unknown)

    @classmethod
    def evaluation(cls, filing: FilingRecord, rows: Sequence[RowInstance],
                   predictions: Sequence[Prediction], configs: StatementConfigs,
                   agent: AgentConfig, injections: Sequence[DocumentEdit] = ()
                   ) -> "FilingState":
        """Evaluation mode: document readings only (no tag argument exists). injections are
        applied first (E2); the agent sees the injected readings without their edit record."""
        specs = list(configs.concept_map.values())
        documents = read_document_channel(rows, predictions, filing, specs)
        scales = [d.scale for d in documents.values() if d.scale]  # the document's own unit
        documents = apply_edits(documents, injections, filing, rows, configs.concept_map)
        base = {i: (d.edit.row_id, d.edit.factor, d.edit.negate)
                for i, d in documents.items() if d.edit is not None}
        # An injected reading looks like the channel's own: no edit record, and M1's
        # probability of the item for the row it reads.
        seen = {i: d if d.edit is None else replace(
                    d, edit=None, original_value=None, original_row_id=None,
                    probability=probability(predictions, d.row_id, i) if d.row_id else None)
                for i, d in documents.items()}
        read = {i for i, d in seen.items() if d.value is not None}
        absent = not read & set(NCI_LINES) and not nci_label_seen(rows, agent)
        nci = {c: ABSENT for c, _ in configs.nci_concepts} if absent else {}
        located = tuple(sorted({r.statement for r in rows}))
        return cls(filing, "evaluation", configs, agent, tuple(rows), tuple(predictions), seen,
                   nci=nci, located=located, scale=max(scales) if scales else 0, base=base)

    # ------------------------------------------------------------ readings
    @cached_property
    def rows_by_id(self) -> dict[str, RowInstance]:
        return {r.row_id: r for r in self.rows}

    def reading(self, item: str) -> DocumentReading:
        edit = self.edits.get(item)
        if edit is None:
            return self.original[item]
        spec = self.configs.concept_map[item]
        return edited_reading(edit, self.rows_by_id, spec, self.original[item])

    @cached_property
    def documents(self) -> dict[str, DocumentReading]:
        return {item: self.reading(item) for item in self.original}

    def transform(self, item: str) -> Transform:
        """The current reading's (row, factor, negate) relative to the row's own cell."""
        edit = self.edits.get(item)
        if edit is not None:
            return edit.row_id, edit.factor, edit.negate
        if item in self.base:
            return self.base[item]
        reading = self.original[item]
        assert reading.row_id is not None
        return reading.row_id, Decimal(1), False

    def with_transform(self, item: str, row_id: str, factor: Decimal, negate: bool,
                       action: str) -> "FilingState":
        """A new state with the item read as row x factor (negated); the edit records the
        actions so far. Back at the starting reading, the edit is dropped."""
        edits = dict(self.edits)
        start = self.base.get(item) or (self.original[item].row_id, Decimal(1), False)
        if (row_id, factor, negate) == start:
            edits.pop(item, None)
        else:
            previous = self.edits.get(item)
            actions = (previous.actions if previous else ()) + (action,)
            edits[item] = DocumentEdit(
                self.filing.accession, self.filing.end, item, row_id, factor, negate,
                probability(self.predictions, row_id, item), actions, "agent")
        return replace(self, edits=edits)

    # ------------------------------------------------------------ what may change
    @cached_property
    def changeable(self) -> frozenset[str]:
        """Items whose document reading the agent may change: every reading in evaluation
        mode; DISAGREE and document-only values in product mode (VERIFIED and tag-only values
        are settled by two channels or have no document reading)."""
        start = replace(self, edits={}).view.pool
        found = set()
        for item, reading in self.original.items():
            if reading.value is None:
                continue
            o = start.get(item)
            if self.mode == "evaluation" or (o is not None and (
                    o.status == "DISAGREE" or o.channels == ("document",))):
                found.add(item)
        return frozenset(found)

    @cached_property
    def disagree(self) -> frozenset[str]:
        """Product mode: the items read as DISAGREE before any edit (each has a D check)."""
        if self.mode != "product":
            return frozenset()
        return frozenset(item for item, reading in self.original.items()
                         if reading.value is not None
                         and verify_item(self.filing, reading, self.tags.get(item)).status
                         == "DISAGREE")

    # ------------------------------------------------------------ identities
    @cached_property
    def view(self) -> View:
        pool = self._observations()
        facts = self.facts if self.mode == "product" else ()  # no tag input in evaluation
        derived, bases = derive_all(
            self.filing, self.configs.derivations, pool, provisional=True, rows=self.rows,
            facts=facts, finance_lease_concepts=self.configs.model_inputs.finance_lease_concepts,
            absence_unknown=self.absence_unknown)
        ctx = Context(self.filing, derived, bases, self.nci, self.scale, self.rows,
                      self.predictions, facts)
        results = tuple(evaluate_filing(self.configs.identities, ctx))
        return View(derived, results, self._checks(results))

    def _observations(self) -> dict[str, Observation]:
        rows_by_id = self.rows_by_id
        pool: dict[str, Observation] = {}
        for item, spec in self.configs.concept_map.items():
            document = self.documents[item]
            tag = self.tags.get(item) if self.mode == "product" else None
            o = observe_item(self.filing, spec, document, tag, rows_by_id, self.located)
            if o.status == "DISAGREE" and document.value is not None:
                # The identities test the document value, as for a pending document-only one.
                o = replace(o, value=document.value, flags=o.flags + (PENDING,))
            pool[item] = o
        if self.mode == "product":
            pool.update(self.model_inputs)
        return pool

    def _checks(self, results: Sequence[IdentityResult]) -> tuple[CheckResult, ...]:
        use = set(self.agent.identities)
        found: list[CheckResult] = []
        for r in results:
            if r.identity_id not in use or r.status == NOT_COMPUTABLE:
                continue
            if r.variants:
                found.extend(CheckResult(f"{r.identity_id}.v{v.variant}", v.status, v.residual,
                                         v.tolerance if v.tolerance is not None
                                         else Decimal(0), frozenset(v.inputs))
                             for v in r.variants)
                continue
            assert r.residual is not None and r.tolerance is not None
            check_id = f"{r.identity_id}:{r.subject}" if r.subject else r.identity_id
            found.append(CheckResult(check_id, r.status, r.residual, r.tolerance,
                                     frozenset(r.inputs)))
        for item in sorted(self.disagree):
            found.append(self.channel_check(item))
        return tuple(found)

    def channel_check(self, item: str) -> CheckResult:
        """D:<item>: the document reading agrees with the tag (8.7, BC15) or not."""
        document = self.documents[item]
        tag = self.tags.get(item)
        amount = tag_value(tag)
        agree = document.value is not None and \
            verify_item(self.filing, document, tag).status == "VERIFIED"
        residual = Decimal(0) if agree or document.value is None or amount is None \
            else document.value - amount
        if not agree and residual == 0:  # no usable comparison: count the whole reading
            residual = abs(document.value or Decimal(0))
        return CheckResult(f"D:{item}", "PASS" if agree else "FAIL", residual, Decimal(0),
                           frozenset({item}))
