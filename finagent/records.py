"""Shared records and the outcome vocabulary (blueprint sections 6.1 and 13)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

# Blueprint 6.1 outcome statuses.
Status = Literal[
    "VERIFIED",
    "CORRECTED",
    "REVIEW",
    "MISSING_PRIMARY",
    "CONFLICT",
    "INVALID",
    "GAP",
    "EVIDENCE_GAP",
    "SKIPPED",
    "CLARIFICATION",
    "COMPLETE",
    "PARTIAL",
    # Blueprint 8.7 dual-channel results.
    "DISAGREE",
    "SINGLE_CHANNEL",
]

Channel = Literal["document", "tag"]

# Where a tag-channel reading came from. Both views are the filer's own tags (8.6 naming note).
TagSource = Literal["companyfacts", "inline", "inline_only"]


@dataclass(frozen=True)
class Reading:
    """One reading of a line item for one filing (blueprint 13, plus provenance fields)."""

    issuer: str
    fiscal_year: int
    item: str
    value: Decimal | None  # None means absent; never 0 for a missing value
    channel: Channel
    status: Status
    concept: str | None = None
    row_id: str | None = None
    unit: str | None = None
    start: date | None = None
    end: date | None = None
    accession: str | None = None
    source: TagSource | None = None
    single_source: bool = False


@dataclass(frozen=True)
class Outcome:
    """Result of a tool step (blueprint 13). reason is a stable code; detail is free text.
    observations are Readings (channel tools) or AnswerObservations (planner tools). gaps:
    (metric, reason) for metrics a multi-metric planner tool could not establish (Phase 6)."""

    status: Status
    reason: str
    observations: tuple[Any, ...] = field(default_factory=tuple)
    detail: str = ""
    gaps: tuple[tuple[str, str], ...] = ()

    @property
    def value(self) -> Decimal | None:
        """The single value carried by the outcome, or None when there is not exactly one."""
        values = {r.value for r in self.observations if r.value is not None}
        return values.pop() if len(values) == 1 else None


Statement = Literal["IS", "BS", "CF"]
# A row column: a period amount, or a percentage sub-column that is never an amount (BC11).
ColumnKind = Literal["amount", "percent"]


@dataclass(frozen=True)
class RowInstance:
    """One row of a face statement in the clean document (blueprint 8.3, 13).

    cell_texts, column_periods, column_spans and column_kinds are aligned: one entry per
    column. A column is a period column ("amount") or a percentage sub-column ("percent",
    e.g. "% Sales" next to each year; BC11): a percent column has period None and is never read
    as an amount. An empty column_kinds means every column is an amount column. A cell with no visible figure is "" (missing is never zero). table_index, source_row and
    column_spans address the row in the document grid, so evaluation can compare it with the
    tag store without the document channel ever seeing a tag.
    """

    row_id: str
    accession: str
    statement: Statement
    label: str  # "" for an unlabelled row (e.g. a total line whose label cell is blank)
    parent_header: str | None
    indent: int  # indentation level within the statement, 0 = outermost
    is_total: bool
    is_bold: bool
    position: int  # order among the statement's value rows, from 0
    cell_texts: tuple[str, ...]
    column_periods: tuple[date | None, ...]
    unit_text: str | None
    table_index: int
    source_row: int
    column_spans: tuple[tuple[int, int], ...]
    column_kinds: tuple[ColumnKind, ...] = ()
    # Enclosing section headers, nearest first: the parent header, then each header above it
    # with a smaller indent (e.g. "Net Income Attributable to ..." then "Per Share of Common
    # Stock"). Used by the normaliser's per-share scale exemption (8.5, #53); () when unknown.
    header_chain: tuple[str, ...] = ()

    def is_amount_column(self, column: int) -> bool:
        """True for a period column; False for a percentage sub-column (BC11)."""
        return not self.column_kinds or self.column_kinds[column] == "amount"


@dataclass(frozen=True)
class Prediction:
    """A classifier's top candidates for one row (blueprint 8.4, 13), most probable first."""

    row_id: str
    candidates: tuple[tuple[str, float], ...]  # (canonical item or OTHER, probability)
    model_version: str

    @property
    def item(self) -> str:
        return self.candidates[0][0]


Classification = Literal[
    "REPORTED_FACT",
    "CALCULATION",
    "MODEL_ASSUMPTION",
    "CALCULATION_FROM_MODEL_ASSUMPTION",
    "EVIDENCE_GAP",
]


@dataclass(frozen=True)
class Observation:
    """One value of the validated statement set (blueprint 8.9, 13), with its provenance.

    status is the 8.7 result (VERIFIED, SINGLE_CHANNEL, DISAGREE, GAP, CONFLICT, INVALID);
    reason is the final reason code (e.g. AGREE, TAG_ONLY, IDENTITY_FAILED, NOT_REPORTED,
    INPUT_GAP) and verification the 8.7 combination it came from. value is the value used in
    sums: the tag amount when VERIFIED (the filer's exact figure), the single channel's value
    otherwise, None when there is none (never 0 for a missing value). accepted says whether the
    value may be used downstream; a DISAGREE value never is. validated is True only when both
    channels agree (VERIFIED).
    """

    issuer: str
    cik: str
    fiscal_year: int
    metric: str
    unit: str
    value: Decimal | None
    classification: Classification
    status: Status
    reason: str
    accepted: bool
    accession: str
    filing_url: str
    filed: date
    fiscal_period_start: date | None
    fiscal_period_end: date
    channels: tuple[Channel, ...] = ()
    verification: str | None = None
    basis: str | None = None
    statement: str | None = None
    row_id: str | None = None
    row_label: str | None = None
    concepts: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    document_value: Decimal | None = None
    scale: int | None = None  # displayed power of ten of document_value
    tag_value: Decimal | None = None
    flags: tuple[str, ...] = ()
    candidates: tuple[Decimal, ...] = ()  # other tag candidates (BC15) or conflicting values
    display_check: str | None = None
    original_value: Decimal | None = None  # the year's own filing value, when RESTATED
    original_accession: str | None = None
    detail: str = ""
    # Face-block sums (derivations.yaml face_block_sum): each component row used, as
    # (row_id, row label, value), so the workbook can show the total as a live sum (Phase 4d).
    components: tuple[tuple[str, str, Decimal], ...] = ()
    # Correlation Agent (8.8; decision 3, 2026-11-25): a value whose document reading the agent
    # changed carries the flag CORRECTED, the action(s) and the original reading (value, row).
    correction: str = ""
    corrected_from_value: Decimal | None = None
    corrected_from_row: str | None = None

    @property
    def validated(self) -> bool:
        return self.status == "VERIFIED"

    @property
    def presented(self) -> bool:
        """Some channel read a value (whether or not it was accepted)."""
        return self.status not in ("GAP",)


# REVIEW: a failure reported with its residual that blocks nothing (Appendix B "or REVIEW with
# residual"; blueprint 6.1), e.g. I11 when other charges to retained earnings are not disclosed.
IdentityStatus = Literal["PASS", "FAIL", "REVIEW", "NOT_COMPUTABLE"]


@dataclass(frozen=True)
class VariantCheck:
    """One computed variant of a sum identity (e.g. I4 variant 2), for acceptance (#69)."""

    variant: int  # 1-based, in config order
    status: IdentityStatus  # PASS / FAIL / REVIEW
    residual: Decimal
    inputs: tuple[str, ...]  # reported metrics the variant rests on
    tolerance: Decimal | None = None


@dataclass(frozen=True)
class IdentityResult:
    """One identity evaluated for one filing (blueprint 13, Appendix B).

    residual and tolerance are exact Decimals (None when not computable). operands lists the
    (signed term, value) pairs used; omitted the optional operands the filer does not present
    (left out, never 0); missing the operands without an accepted value; provisional the
    single-channel document values the identity tests; inputs the reported metrics it rests on
    (derived operands expanded to their inputs).
    """

    issuer: str
    fiscal_year: int
    identity_id: str
    residual: Decimal | None
    tolerance: Decimal | None
    status: IdentityStatus
    accession: str = ""
    subject: str | None = None  # I8 item, I12 check
    operands: tuple[tuple[str, Decimal], ...] = ()
    omitted: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    provisional: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    detail: str = ""
    # Every computed variant of a variant identity (I4), not only the one reported (#69).
    variants: tuple[VariantCheck, ...] = ()


# ---------------------------------------------------------------- integrated forecast (8.10)

AssumptionStatus = Literal["SET", "CAPPED", "BOUNDED", "DEFAULT", "NOT_APPLIED", "GAP"]
ForecastStatus = Literal["VERIFIED", "INVALID", "GAP"]
CheckStatus = Literal["PASS", "FAIL"]


@dataclass(frozen=True)
class InputRef:
    """One accepted value of the validated statement set that a driver or opening line uses."""

    metric: str
    fiscal_year: int
    value: Decimal
    status: str  # VERIFIED / SINGLE_CHANNEL
    reason: str
    accession: str
    filed: date
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelAssumption:
    """A forecast driver (blueprint 6.2 MODEL_ASSUMPTION) with its inputs and basis.

    value is a float (model calculation); raw_value is the value before a cap or bound. A driver
    that does not apply to the filer (e.g. inventory for a filer that reports none) is
    NOT_APPLIED with value None, never 0; a driver that cannot be established is GAP.
    """

    driver_id: str
    value: float | None
    basis: str  # e.g. CAGR_4Y, LATEST, MEDIAN_3Y, DEFAULT_RATE, STATUTORY_FALLBACK, NOT_REPORTED
    status: AssumptionStatus
    inputs: tuple[InputRef, ...] = ()
    raw_value: float | None = None
    bounds: tuple[float, float] | None = None
    flags: tuple[str, ...] = ()
    detail: str = ""
    classification: str = "MODEL_ASSUMPTION"


@dataclass(frozen=True)
class OpeningLine:
    """One line of the origin-year balance sheet: reported (accepted input) or a CALCULATION of
    reported values (e.g. other assets = total assets - the listed assets), never a plug."""

    line: str
    value: float
    classification: str  # REPORTED_FACT or CALCULATION
    inputs: tuple[InputRef, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class ProjectedYear:
    """One projected fiscal year: line -> amount (CALCULATION_FROM_MODEL_ASSUMPTION). Lines a
    filer does not have (NOT_APPLIED drivers) are absent, never 0."""

    fiscal_year: int
    lines: dict[str, float]
    flags: tuple[str, ...] = ()
    classification: str = "CALCULATION_FROM_MODEL_ASSUMPTION"


@dataclass(frozen=True)
class ModelCheck:
    """One model check (Appendix B F1-F5) for one projected year."""

    check_id: str
    fiscal_year: int
    status: CheckStatus
    residual: float
    tolerance: float
    detail: str = ""


@dataclass(frozen=True)
class Forecast:
    """The integrated forecast for one company. years is empty unless status is VERIFIED (a
    failed check shows no partial numbers; 8.10)."""

    issuer: str
    cik: str
    as_of: date
    origin_year: int | None
    status: ForecastStatus
    reasons: tuple[str, ...]
    assumptions: tuple[ModelAssumption, ...]
    opening: tuple[OpeningLine, ...]
    years: tuple[ProjectedYear, ...]
    checks: tuple[ModelCheck, ...]
    switches: dict[str, object]
    flags: tuple[str, ...] = ()
    scenario: str = "Base"  # blueprint 8.11 scenario name (Base = the 4b forecast)


# ---------------------------------------------------------------- Correlation Agent (8.8)

EditSource = Literal["agent", "injection"]


@dataclass(frozen=True)
class DocumentEdit:
    """An in-memory change to one document reading: a Correlation Agent correction or an E2
    injection (blueprint 8.8, 10.2). Applied by finagent.verify.edits.apply_edits right after
    the document channel reads the filing; never written to evidence/.

    The reading becomes: the row's value for the period (normalised as the document channel
    does), times factor, negated when negate. Every edited value is therefore a displayed cell
    value, rescaled or sign-reversed: never a plug, never a value for a blank cell.
    """

    accession: str
    period_end: date
    item: str
    row_id: str
    factor: Decimal = Decimal(1)  # cumulative rescale: 1000 or 0.001 per rescale action
    negate: bool = False
    probability: float | None = None  # M1 p(item | row)
    actions: tuple[str, ...] = ()  # the actions that produced it, in order
    source: EditSource = "agent"
    detail: str = ""


@dataclass(frozen=True)
class CheckResult:
    """One check the agent reads for one filing: an identity (each computed variant of a
    variant identity; each I12 check), or in product mode the document-vs-tag check of a value
    read as DISAGREE (check_id D:<item>). inputs are the reported metrics it rests on."""

    check_id: str
    status: str  # PASS / FAIL / REVIEW
    residual: Decimal
    tolerance: Decimal
    inputs: frozenset[str]

    @property
    def excess(self) -> Decimal:
        """How far a FAIL is beyond its tolerance; 0 for any other status."""
        if self.status != "FAIL":
            return Decimal(0)
        return max(abs(self.residual) - self.tolerance, Decimal(0))


@dataclass(frozen=True)
class AgentAction:
    """One applied action (swap, rescale, flip_sign) with its score inputs."""

    tool: str
    item: str
    row_id: str | None
    factor: Decimal | None
    value_before: Decimal | None
    value_after: Decimal | None
    probability: float
    residual_before: Decimal
    residual_after: Decimal
    score: float


@dataclass(frozen=True)
class AgentValue:
    """The agent's outcome for one value (blueprint 6.1): CORRECTED (the reading was changed and
    every check it enters passes) or REVIEW (inconsistent and unresolved; original kept)."""

    issuer: str
    fiscal_year: int
    item: str
    status: Literal["CORRECTED", "REVIEW"]
    original_value: Decimal | None
    original_row_id: str | None
    final_value: Decimal | None
    final_row_id: str | None
    actions: tuple[str, ...] = ()
    checks: tuple[tuple[str, str, Decimal], ...] = ()  # (check id, status, residual) entered
    detail: str = ""


@dataclass(frozen=True)
class TraceStep:
    """One step of an agent or planner trace (blueprint 13, 8.17)."""

    step: int
    tool: str
    arguments: dict[str, Any]
    eligible_tools: tuple[str, ...]
    decision: str
    status: str
    reused_by: tuple[str, ...] = ()
    observations: tuple[dict[str, Any], ...] = ()
    latency_ms: float = 0.0
    goal: dict[str, Any] | None = None  # planner steps: the goal the action ran for (8.17)
    reason: str = ""
    as_of: str = ""
    gate: tuple[dict[str, Any], ...] = ()  # planner steps: the gate's decision per observation


AgentStop = Literal["NO_FAILING_CHECK", "NO_ADMISSIBLE_ACTION", "ACTION_LIMIT"]


@dataclass(frozen=True)
class AgentResult:
    """The Correlation Agent's run on one filing (its own fiscal year)."""

    issuer: str
    fiscal_year: int
    accession: str
    mode: str  # product / evaluation
    as_of: date
    values: tuple[AgentValue, ...]
    edits: tuple[DocumentEdit, ...]
    actions: tuple[AgentAction, ...]
    checks_before: tuple[CheckResult, ...]
    checks_after: tuple[CheckResult, ...]
    trace: tuple[TraceStep, ...]
    stop_reason: str

    @property
    def actions_used(self) -> int:
        return len(self.actions)


@dataclass(frozen=True)
class StatementsCheck:
    """The final check of one fiscal year's statements goal (blueprint 8.8; BC22, BC24): I9
    and I10 recomputed on the final statement values must be within tolerance, exactly zero
    unless an operand is a face-block sum (half a unit per rounded component) (FAIL -> INVALID,
    NOT_COMPUTABLE -> GAP); I11 counts only when computable and a non-zero I11 is REVIEW."""

    issuer: str
    fiscal_year: int
    status: Literal["VERIFIED", "INVALID", "GAP"]
    reason: str
    identities: tuple["IdentityResult", ...]
    review: tuple[str, ...] = ()


# ---------------------------------------------------------------- planner (8.15)

GoalKind = Literal["fact", "statements", "forecast", "credit", "liquidity", "debt", "explain",
                   "risk"]
Period = int | str  # a fiscal year (the filer's own numbering, BC2) or "latest"
Target = int | str | None  # None: first projected year; a fiscal year; "+2" / "+3" (relative)
# classifier: the trained intent model; grammar: the regex fallback; formula: a ratio written as
# its formula (#228), read by rule before the classifier.
Route = Literal["classifier", "grammar", "formula"]


@dataclass(frozen=True)
class Goal:
    """One typed goal (blueprint 13). metric "" means the kind's default set; scenario is set
    for forecast, liquidity and debt goals only (Base when the question names none); target is
    the projected fiscal year of a forecast, liquidity or debt goal."""

    issuer: str
    period: Period
    kind: GoalKind
    metric: str = ""
    scenario: str = ""
    target: Target = None
    conditional: bool = False


@dataclass(frozen=True)
class Action:
    """One planner action (blueprint 13, 8.15 registry): run `name` for a goal of `kind` whose
    status is `trigger`, once `prerequisite` (a goal kind) is VERIFIED; lowest priority first."""

    name: str
    kind: GoalKind
    trigger: str
    prerequisite: str = ""
    priority: int = 0


@dataclass(frozen=True)
class Clarification:
    """Why a question was not compiled into goals (blueprint 6.1 CLARIFICATION). reason is a
    stable code (configs/intents.yaml clarifications), category its E5 group."""

    reason: str
    category: str
    message: str
    clause: str = ""
    status: Status = "CLARIFICATION"


@dataclass(frozen=True)
class ClauseParse:
    """One clause compiled without its issuers: the goal kind and slots, and how it was read
    (the trained classifier with its confidence, or the regex grammar fallback)."""

    text: str
    kind: GoalKind
    metrics: tuple[str, ...] = ()
    period: Period = "latest"
    scenario: str = ""
    target: Target = None
    conditional: bool = False
    route: Route = "classifier"
    confidence: float | None = None


@dataclass(frozen=True)
class DroppedClause:
    """A clause with no request content (#232): no company, metric, period, scenario or request
    word. It is dropped, not failed, and recorded in the trace; numbers lists any figures it
    contained (they were not used)."""

    text: str
    numbers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Quote:
    """A quoted sentence cited by an answer (Phase 7.3): one whole sentence of a text chunk,
    verbatim, with the chunk's section and sha256 (the gate rebuilds the chunk from the pinned
    document and checks both, #157) and its relevance score (finagent.retrieval.quotes)."""

    chunk_id: str
    accession: str
    text: str
    section: str = ""
    score: float = 0.0
    chunk_sha256: str = ""


@dataclass(frozen=True)
class LowConfidenceSentence:
    """D-EV2 (Vineet, option b): on an evidence-v2 EVIDENCE_GAP card, the sentence v2 would
    have quoted first without the reranker gate: the best candidate (same sentence rules and
    IDF score threshold, the chunks' reranked order) of a passage below min_rerank_score. It is
    shown under `label` and is never evidence: never a card's quote, never checked or counted
    as a quote by the gate, never support for an explain answer; the card stays EVIDENCE_GAP."""

    quote: Quote
    rerank_score: float  # the passage's cross-encoder score, below the threshold
    threshold: float  # min_rerank_score
    label: str  # configs/signals.yaml evidence v2 low_confidence_label

    def display(self) -> str:
        """The label, where the sentence is from, why it is not evidence, then the sentence
        verbatim (the card text and the planner's renderer both show this)."""
        return (f"{self.label} ({self.quote.section}, chunk {self.quote.chunk_id}; passage "
                f"reranker score {self.rerank_score:.3f}, below the threshold "
                f"{self.threshold:.3f}; not evidence): \"{self.quote.text}\"")


# ---------------------------------------------------------------- risk signals (8.13; 7.3)

# A signal's evaluation (Appendix C.2): FIRED, NOT_FIRED, GAP (an input or measure could not be
# established, never read as not fired) or NOT_APPLIED (the filer has no such line).
SignalState = Literal["FIRED", "NOT_FIRED", "GAP", "NOT_APPLIED"]


@dataclass(frozen=True)
class ConditionResult:
    """One condition of a signal for one fiscal year: the measure, its value and the test."""

    condition_id: str  # e.g. dso_change
    label: str
    state: SignalState
    value: float | None = None
    unit: str = ""
    op: str = ""
    threshold: float = 0.0
    reason: str = ""  # a stable code when not FIRED / NOT_FIRED (e.g. INPUT_GAP, NO_COGS)
    detail: str = ""
    values: tuple[tuple[int, float], ...] = ()  # (fiscal year, value) behind the measure


@dataclass(frozen=True)
class SignalResult:
    """One signal (Appendix C.2) for one issuer and fiscal year."""

    signal_id: str
    name: str
    issuer: str
    fiscal_year: int
    state: SignalState
    severity: str
    data: str  # history | scenario
    conditions: tuple[ConditionResult, ...] = ()
    reason: str = ""
    detail: str = ""
    scenario: str = ""
    components: tuple[tuple[str, str], ...] = ()  # composite: (signal id, state)


@dataclass(frozen=True)
class InsightCard:
    """A fired signal with its numbers and quoted evidence (blueprint 13 InsightCard, plus
    provenance and scenario). status: VERIFIED (1-2 quotes above the threshold) or
    EVIDENCE_GAP (none: the filing does not explain the movement). The text is a template
    filled with numbers; no model writes free text on a card (8.13)."""

    signal_id: str
    name: str
    issuer: str
    fiscal_year: int
    metrics: tuple[ConditionResult, ...]
    severity: str
    evidence: tuple[Quote, ...]
    status: Status
    accession: str
    filing_url: str
    filed: date
    fiscal_period_end: date | None
    text: str
    scenario: str = ""
    query: str = ""
    detail: str = ""
    # D-EV2: an EVIDENCE_GAP card's low-confidence sentence (shown, never evidence).
    low_confidence: LowConfidenceSentence | None = None


# ---------------------------------------------------------------- retrieval (8.14; Phase 7.2)

ChunkKind = Literal["text", "table"]


@dataclass(frozen=True)
class Section:
    """One section of a 10-K's clean document: an Item, an Item 8 note, or UNKNOWN (text before
    the first Item heading, or a document with none). Blocks are positions in the block list
    of finagent.retrieval.sections (end exclusive)."""

    section_id: str  # ITEM_7, ITEM_8/NOTE_06, UNKNOWN
    heading: str  # the heading text as shown, "" for UNKNOWN
    start_block: int
    end_block: int
    words: int


@dataclass(frozen=True)
class Chunk:
    """A retrievable passage: paragraphs merged at sentence boundaries, or one data table.
    chunk_id = {accession}:{section}:{ordinal within the section}; sha256 of the text."""

    chunk_id: str
    accession: str
    section_id: str
    ordinal: int
    kind: ChunkKind
    text: str
    words: int
    sha256: str


@dataclass(frozen=True)
class Hit:
    """One search result: a chunk of the filing searched, its score and rank (1 = best) under
    the retrieval variant (7.4: bm25 score, dense cosine, RRF score or cross-encoder score)."""

    chunk: Chunk
    score: float
    rank: int
    issuer: str
    fiscal_year: int
    filed: date
    filing_url: str
    variant: str = "bm25"


@dataclass(frozen=True)
class AnswerObservation:
    """One answer value checked by the observation gate (blueprint 13 Observation, 8.16).

    value is a Decimal for a reported fact or a statement residual, a float for a model output
    or a ratio, and None only for a ratio that is not meaningful (flag NOT_MEANINGFUL, reason in
    detail). Provenance is the filing the value cites: its own 10-K for a reported value (the
    latest filing for a restated year, #117, with the original alongside), the fiscal year's
    10-K for a ratio or residual, the origin 10-K for a projection (fiscal_period_end None).
    validated: both channels agree (facts) or the value passed its recomputation (calculations,
    model outputs). flags carry SINGLE_CHANNEL, CORRECTED, RESTATED, SINGLE_CHANNEL_INPUTS..."""

    issuer: str
    cik: str
    fiscal_year: int
    metric: str
    unit: str
    value: Decimal | float | None
    classification: Classification
    accession: str
    filing_url: str
    filed: date
    fiscal_period_end: date | None
    basis: str
    validated: bool
    channels: tuple[Channel, ...] = ()
    flags: tuple[str, ...] = ()
    scenario: str = ""
    detail: str = ""
    original_value: Decimal | None = None
    original_accession: str | None = None
    sources: tuple[str, ...] = ()  # accessions of the inputs, when they differ from accession
    quotes: tuple[Quote, ...] = ()
    # D-EV2: shown beside an EVIDENCE_GAP flag; not a quote (the gate checks `quotes` only).
    low_confidence: LowConfidenceSentence | None = None


@dataclass(frozen=True)
class Compilation:
    """A compiled question: goals, or a clarification (never both)."""

    question: str
    compiler: str
    goals: tuple[Goal, ...] = ()
    clarification: Clarification | None = None
    issuers: tuple[str, ...] = ()
    comparison: bool = False
    clauses: tuple[ClauseParse, ...] = ()
    held_out: tuple[str, ...] = ()  # resolved issuers in the held-out split (no data loaded)
    dropped: tuple[DroppedClause, ...] = ()  # clauses with no request content (#232)
