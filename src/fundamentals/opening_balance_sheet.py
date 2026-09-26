"""Validated opening balance sheet for a nonfinancial company's forecast.

This is the first foundation of a linked three-statement forecast: one
balance-sheet instant, read point-in-time from published SEC facts, with
every line's filing and ingestion provenance. It forecasts nothing.

Contract. A caller names the issuer (CIK), the balance-sheet date, the
knowledge cutoff (a fact counts only once its filing was accepted), the
data-vintage cutoff (and only once it was ingested), the currency, and the
source policy (adapters, concept-map and fiscal-calendar versions), usually
taken from the issuer manifest. The result is either a complete snapshot or
typed refusals; there is no partial output.

Every line is a value the issuer reported for exactly that instant. Nothing
is derived to make the sheet balance: no "other assets", "other liabilities",
or equity plug. Total equity is the reported total including noncontrolling
interests; only when an issuer does not report that line is parent
shareholders' equity used, and then assets = liabilities + equity must still
hold exactly, so an unreported noncontrolling interest or temporary equity
refuses rather than being absorbed.

The snapshot also states whether its reported line items fully itemize each
section. A section is itemized only when every one of its defined line items
was reported for the instant (an explicit zero counts; an absent line is
missing evidence, never a zero) and those values sum exactly to the section.
There is no reviewed absence policy yet, so no line may be skipped. With the
current concept maps real issuers cannot be itemized (goodwill, intangibles,
lease assets, marketable securities, accrued and other liabilities, and
other lines are not mapped), so ``forecast_itemization_gaps`` names the
sections a forecast could not yet roll forward line by line. That gap is
reported, never filled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, Optional, Tuple

from .repository import FundamentalsQuery, FundamentalsRepositoryUnavailable
from .selection import select_point_in_time
from .time_policy import is_aware
from .types import FactIdentity, FinancialFact, StatementKind, SynonymConflict, normalize_cik

OPENING_BALANCE_SHEET_POLICY_VERSION = "opening-balance-sheet-v1"

# Reported lines every snapshot needs, plus one of the two equity lines.
REQUIRED_CONCEPTS = (
    "total_assets",
    "current_assets",
    "cash_and_cash_equivalents",
    "total_liabilities",
    "current_liabilities",
)
EQUITY_CONCEPTS = ("total_equity", "shareholders_equity")  # in order of preference
# Reported line items recorded when present; never required, never derived.
ITEMIZED_CONCEPTS = (
    "accounts_receivable",
    "inventory",
    "property_plant_and_equipment_net",
    "accounts_payable",
    "current_debt",
    "long_term_debt",
    "retained_earnings",
    "cash_and_restricted_cash",
)
_ALL_CONCEPTS = REQUIRED_CONCEPTS + EQUITY_CONCEPTS + ITEMIZED_CONCEPTS

# Line items that must all be reported and sum exactly to each section for a
# line-by-line forecast; a section with any other content, or with any of
# these lines unreported, is an itemization gap.
_SECTION_ITEMS = (
    ("current_assets", ("cash_and_cash_equivalents", "accounts_receivable", "inventory")),
    ("noncurrent_assets", ("property_plant_and_equipment_net",)),
    ("current_liabilities", ("accounts_payable", "current_debt")),
    ("noncurrent_liabilities", ("long_term_debt",)),
)
DEFAULT_MAX_STALENESS_DAYS = 200  # a 10-K is due at most 90 days after a year end


class OpeningBalanceSheetIssueCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    STORE_UNAVAILABLE = "store_unavailable"
    OUT_OF_SCOPE_FACT = "out_of_scope_fact"
    CUTOFF_VIOLATION = "cutoff_violation"
    INCONSISTENT_FACTS = "inconsistent_facts"
    CONFLICTING_VALUE = "conflicting_value"
    MISSING_PERIOD = "missing_period"
    MISSING_COMPONENT = "missing_component"
    MIXED_UNITS = "mixed_units"
    STALE_PERIOD = "stale_period"
    IMBALANCE = "imbalance"
    INCONSISTENT_SUBTOTAL = "inconsistent_subtotal"


@dataclass(frozen=True)
class OpeningBalanceSheetIssue:
    code: OpeningBalanceSheetIssueCode
    message: str
    concepts: Tuple[str, ...] = ()


@dataclass(frozen=True)
class OpeningBalanceSheetRequest:
    cik: str
    period_end: date
    knowledge_cutoff: datetime
    data_vintage_cutoff: datetime
    source_adapter: str
    concept_map_version: str
    fiscal_calendar_version: str
    supplemental_source_adapters: Tuple[str, ...] = ()
    currency: str = "USD"
    max_staleness_days: int = DEFAULT_MAX_STALENESS_DAYS

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        object.__setattr__(self, "supplemental_source_adapters", tuple(self.supplemental_source_adapters))
        if not isinstance(self.period_end, date) or isinstance(self.period_end, datetime):
            raise ValueError("period_end must be a date.")
        if not is_aware(self.knowledge_cutoff) or not is_aware(self.data_vintage_cutoff):
            raise ValueError("Both cutoffs must be timezone-aware.")
        if self.currency != "USD":
            raise ValueError("Only USD balance sheets are supported; facts are stored in USD.")
        if isinstance(self.max_staleness_days, bool) or not isinstance(self.max_staleness_days, int) or (
            self.max_staleness_days <= 0
        ):
            raise ValueError("max_staleness_days must be a positive integer.")

    @classmethod
    def from_policy(cls, policy, *, period_end: date, knowledge_cutoff: datetime, data_vintage_cutoff: datetime):
        """The request for an issuer-manifest policy (``IssuerValuationPolicy``)."""

        if not policy.fiscal_calendar_version:
            raise ValueError("The issuer policy has no fiscal calendar.")
        return cls(
            cik=policy.cik,
            period_end=period_end,
            knowledge_cutoff=knowledge_cutoff,
            data_vintage_cutoff=data_vintage_cutoff,
            source_adapter=policy.source_adapter,
            concept_map_version=policy.concept_map_version,
            fiscal_calendar_version=policy.fiscal_calendar_version,
            supplemental_source_adapters=policy.supplemental_source_adapters,
        )


@dataclass(frozen=True)
class BalanceSheetLine:
    """One reported value and exactly where and when it came from."""

    concept: str
    value: Decimal
    unit: str
    currency: Optional[str]
    raw_tag: str
    accession_number: str
    form_type: str
    is_amendment: bool
    filed_date: date
    accepted_at: Optional[datetime]
    eligible_at: datetime
    source_adapter: str
    source_document_url: str
    concept_map_version: str
    fiscal_calendar_version: Optional[str]
    ingestion_batch_id: str
    ingested_at: datetime

    @classmethod
    def from_fact(cls, fact: FinancialFact) -> "BalanceSheetLine":
        return cls(
            concept=fact.identity.concept,
            value=fact.value,
            unit=fact.identity.unit,
            currency=fact.identity.currency,
            raw_tag=fact.raw_tag,
            accession_number=fact.provenance.accession_number,
            form_type=fact.provenance.form_type,
            is_amendment=fact.provenance.is_amendment,
            filed_date=fact.provenance.filed_date,
            accepted_at=fact.provenance.accepted_at,
            eligible_at=fact.provenance.eligible_at,
            source_adapter=fact.lineage.source_adapter,
            source_document_url=fact.lineage.source_document_url,
            concept_map_version=fact.lineage.concept_map_version,
            fiscal_calendar_version=fact.lineage.fiscal_calendar_version,
            ingestion_batch_id=fact.lineage.ingestion_batch_id,
            ingested_at=fact.lineage.ingested_at,
        )


@dataclass(frozen=True)
class SupersededConflict:
    """An older filing's contradictory values for a line that a later, clean
    filing reported; the later value is the one used."""

    concept: str
    accession_number: str
    raw_tags: Tuple[str, ...]
    values: Tuple[Decimal, ...]
    superseded_by: str


@dataclass(frozen=True)
class OpeningBalanceSheet:
    request: OpeningBalanceSheetRequest
    policy_version: str
    fiscal_year: int
    fiscal_period: str
    equity_concept: str  # "total_equity", or "shareholders_equity" when total is unreported
    lines: Tuple[BalanceSheetLine, ...]
    # Sections with an unreported line item or whose reported line items do
    # not sum exactly to the section.
    forecast_itemization_gaps: Tuple[str, ...]
    # Defined line items of any section that the issuer did not report.
    unreported_itemization_lines: Tuple[str, ...] = ()
    superseded_conflicts: Tuple[SupersededConflict, ...] = ()

    def line(self, concept: str) -> Optional[BalanceSheetLine]:
        return next((item for item in self.lines if item.concept == concept), None)

    def value(self, concept: str) -> Decimal:
        line = self.line(concept)
        if line is None:
            raise KeyError(concept)
        return line.value

    @property
    def total_equity(self) -> Decimal:
        return self.value(self.equity_concept)

    @property
    def is_fully_itemized(self) -> bool:
        """Every section's defined line items were all reported for this
        instant and sum exactly to the section total (noncurrent sections as
        reported total less reported current). It says nothing about whether
        the concept maps name the right lines, and the lines may come from
        different filings, each the latest accepted for that line."""

        return not self.forecast_itemization_gaps


@dataclass(frozen=True)
class OpeningBalanceSheetResult:
    snapshot: Optional[OpeningBalanceSheet] = None
    issues: Tuple[OpeningBalanceSheetIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if (self.snapshot is None) == (not self.issues):
            raise ValueError("A result holds exactly one of a snapshot or refusal issues.")

    @property
    def is_complete(self) -> bool:
        return self.snapshot is not None


def _refuse(code: OpeningBalanceSheetIssueCode, message: str, concepts=()) -> OpeningBalanceSheetResult:
    return OpeningBalanceSheetResult(issues=(OpeningBalanceSheetIssue(code, message, tuple(concepts)),))


def build_opening_balance_sheet(repository, request: OpeningBalanceSheetRequest) -> OpeningBalanceSheetResult:
    """The validated snapshot at ``request.period_end``, or typed refusals."""

    if not isinstance(request, OpeningBalanceSheetRequest):
        raise ValueError("request must be an OpeningBalanceSheetRequest.")
    cutoff = request.knowledge_cutoff
    if request.period_end > cutoff.date():
        return _refuse(OpeningBalanceSheetIssueCode.INVALID_REQUEST, "The balance-sheet date is after the cutoff.")
    age = (cutoff.date() - request.period_end).days
    if age > request.max_staleness_days:
        return _refuse(
            OpeningBalanceSheetIssueCode.STALE_PERIOD,
            f"The balance sheet is {age} days older than the cutoff (limit {request.max_staleness_days}).",
        )

    query = FundamentalsQuery(
        cik=request.cik,
        knowledge_cutoff=cutoff,
        data_vintage_cutoff=request.data_vintage_cutoff,
        concepts=_ALL_CONCEPTS,
        source_adapter=request.source_adapter,
        concept_map_version=request.concept_map_version,
        fiscal_calendar_version=request.fiscal_calendar_version,
        max_periods_per_statement=64,
        supplemental_source_adapters=request.supplemental_source_adapters,
    )
    try:
        facts = tuple(repository.get_facts(query))
    except FundamentalsRepositoryUnavailable as error:
        return _refuse(OpeningBalanceSheetIssueCode.STORE_UNAVAILABLE, f"Fundamentals store unavailable: {error}")

    # The repository applies these bounds; a fact outside them is a defect
    # and refuses rather than being filtered out.
    for fact in facts:
        if (
            fact.identity.context.entity_cik != request.cik
            or fact.identity.concept not in _ALL_CONCEPTS
            or fact.lineage.source_adapter not in query.source_adapters
            or fact.lineage.concept_map_version != request.concept_map_version
            or fact.lineage.fiscal_calendar_version != request.fiscal_calendar_version
        ):
            return _refuse(
                OpeningBalanceSheetIssueCode.OUT_OF_SCOPE_FACT,
                f"Repository returned an out-of-scope fact ({fact.raw_tag}, {fact.provenance.accession_number}).",
            )
        if fact.provenance.eligible_at > cutoff or fact.lineage.ingested_at > request.data_vintage_cutoff:
            return _refuse(
                OpeningBalanceSheetIssueCode.CUTOFF_VIOLATION,
                f"Repository returned a fact outside the cutoffs ({fact.provenance.accession_number}).",
                (fact.identity.concept,),
            )
    try:
        history = select_point_in_time(facts, cutoff, cik=request.cik)
    except ValueError as error:
        return _refuse(OpeningBalanceSheetIssueCode.INCONSISTENT_FACTS, f"Facts are structurally inconsistent: {error}")

    # The selector reports every conflict it saw. One in an older filing that
    # a clean later filing superseded is recorded; one in the latest filing
    # for its line (which then has no winner) refuses.
    winners: Dict[FactIdentity, FinancialFact] = {
        fact.identity: fact for period in history.balance_sheet_periods for fact in period.facts
    }
    superseded, unresolved = [], []
    for conflict in history.conflicts:
        if conflict.identity.period_end != request.period_end:
            continue
        winner = _superseding_winner(conflict, winners.get(conflict.identity), facts)
        if winner is None:
            unresolved.append(conflict)
        else:
            superseded.append(
                SupersededConflict(
                    concept=conflict.identity.concept,
                    accession_number=conflict.accession_number,
                    raw_tags=conflict.raw_tags,
                    values=conflict.values,
                    superseded_by=winner.provenance.accession_number,
                )
            )
    if unresolved:
        return _refuse(
            OpeningBalanceSheetIssueCode.CONFLICTING_VALUE,
            "The latest filing for a line reports contradictory values for the balance-sheet date ("
            + ", ".join(sorted({conflict.accession_number for conflict in unresolved}))
            + ").",
            sorted({conflict.identity.concept for conflict in unresolved}),
        )

    periods = [
        period for period in history.balance_sheet_periods if period.period.period_end == request.period_end
    ]
    if len(periods) != 1:
        return _refuse(
            OpeningBalanceSheetIssueCode.MISSING_PERIOD if not periods else OpeningBalanceSheetIssueCode.INCONSISTENT_FACTS,
            f"Expected one balance-sheet period ending {request.period_end}, found {len(periods)}.",
        )
    period = periods[0]

    by_concept: Dict[str, FinancialFact] = {}
    for fact in period.facts:
        if fact.statement_kind is not StatementKind.BALANCE_SHEET or fact.identity.context.dimensions:
            continue
        if fact.identity.unit != "USD" or fact.identity.currency != request.currency:
            return _refuse(
                OpeningBalanceSheetIssueCode.MIXED_UNITS,
                f"{fact.identity.concept} is reported in {fact.identity.unit}/{fact.identity.currency}, "
                f"not USD/{request.currency}.",
                (fact.identity.concept,),
            )
        if fact.identity.concept in by_concept:
            return _refuse(
                OpeningBalanceSheetIssueCode.MIXED_UNITS,
                f"{fact.identity.concept} has more than one consolidated value at {request.period_end}.",
                (fact.identity.concept,),
            )
        by_concept[fact.identity.concept] = fact

    equity_concept = next((concept for concept in EQUITY_CONCEPTS if concept in by_concept), None)
    missing = [concept for concept in REQUIRED_CONCEPTS if concept not in by_concept]
    if equity_concept is None:
        missing.append("total_equity")
    if missing:
        return _refuse(
            OpeningBalanceSheetIssueCode.MISSING_COMPONENT,
            f"Reported values missing at {request.period_end}: {', '.join(missing)}.",
            missing,
        )

    def value(concept: str) -> Decimal:
        return by_concept[concept].value

    difference = value("total_assets") - value("total_liabilities") - value(equity_concept)
    if difference != 0:
        return _refuse(
            OpeningBalanceSheetIssueCode.IMBALANCE,
            f"Total assets differ from total liabilities plus {equity_concept} by {difference}.",
            ("total_assets", "total_liabilities", equity_concept),
        )

    problems = _subtotal_problems(by_concept)
    if problems:
        return _refuse(OpeningBalanceSheetIssueCode.INCONSISTENT_SUBTOTAL, "; ".join(problems))

    selected = [by_concept[concept] for concept in _ALL_CONCEPTS if concept in by_concept]
    gaps, unreported = _itemization_gaps(by_concept)
    return OpeningBalanceSheetResult(
        snapshot=OpeningBalanceSheet(
            request=request,
            policy_version=OPENING_BALANCE_SHEET_POLICY_VERSION,
            fiscal_year=period.period.fiscal_year,
            fiscal_period=period.period.fiscal_period,
            equity_concept=equity_concept,
            lines=tuple(BalanceSheetLine.from_fact(fact) for fact in selected),
            forecast_itemization_gaps=gaps,
            unreported_itemization_lines=unreported,
            superseded_conflicts=tuple(superseded),
        )
    )


def _superseding_winner(conflict: SynonymConflict, winner: Optional[FinancialFact], facts) -> Optional[FinancialFact]:
    """The selected fact for the conflict's line when it comes from a strictly
    later filing than the conflicted one; otherwise None (unresolved)."""

    if winner is None or winner.provenance.accession_number == conflict.accession_number:
        return None
    conflicted_at = next(
        fact.provenance.eligible_at
        for fact in facts
        if fact.identity == conflict.identity and fact.provenance.accession_number == conflict.accession_number
    )
    later = (winner.provenance.eligible_at, winner.provenance.accession_number) > (
        conflicted_at,
        conflict.accession_number,
    )
    return winner if later else None


def _subtotal_problems(by_concept: Dict[str, FinancialFact]) -> list:
    """Orderings every nonfinancial balance sheet satisfies; no plugs."""

    def value(concept: str) -> Optional[Decimal]:
        fact = by_concept.get(concept)
        return fact.value if fact is not None else None

    total_assets, current_assets = value("total_assets"), value("current_assets")
    total_liabilities, current_liabilities = value("total_liabilities"), value("current_liabilities")
    problems = []
    for name in ("cash_and_cash_equivalents", "cash_and_restricted_cash", "accounts_receivable", "inventory",
                 "property_plant_and_equipment_net", "accounts_payable", "current_debt", "long_term_debt"):
        if value(name) is not None and value(name) < 0:
            problems.append(f"{name} is negative")
    if not 0 <= current_assets <= total_assets:
        problems.append("current assets are not between zero and total assets")
    if not 0 <= current_liabilities <= total_liabilities:
        problems.append("current liabilities are not between zero and total liabilities")
    current_items = sum(value(name) or Decimal(0) for name in ("cash_and_cash_equivalents", "accounts_receivable", "inventory"))
    if current_items > current_assets:
        problems.append("cash, receivables, and inventory exceed current assets")
    if (value("property_plant_and_equipment_net") or 0) > total_assets - current_assets:
        problems.append("net PP&E exceeds noncurrent assets")
    if sum(value(name) or Decimal(0) for name in ("accounts_payable", "current_debt")) > current_liabilities:
        problems.append("payables and current debt exceed current liabilities")
    if (value("long_term_debt") or 0) > total_liabilities - current_liabilities:
        problems.append("long-term debt exceeds noncurrent liabilities")
    combined_cash = value("cash_and_restricted_cash")
    if combined_cash is not None:
        if combined_cash < value("cash_and_cash_equivalents"):
            problems.append("cash, cash equivalents, and restricted cash is below cash and cash equivalents")
        # Restricted cash may be current or noncurrent, so it is bounded only
        # by total assets, alongside the other distinct asset lines.
        other_assets = sum(
            value(name) or Decimal(0)
            for name in ("accounts_receivable", "inventory", "property_plant_and_equipment_net")
        )
        if combined_cash + other_assets > total_assets:
            problems.append("cash and restricted cash, receivables, inventory, and net PP&E exceed total assets")
    return problems


def _itemization_gaps(by_concept: Dict[str, FinancialFact]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """The sections that are not fully itemized, and the unreported lines."""

    totals = {
        "current_assets": by_concept["current_assets"].value,
        "noncurrent_assets": by_concept["total_assets"].value - by_concept["current_assets"].value,
        "current_liabilities": by_concept["current_liabilities"].value,
        "noncurrent_liabilities": by_concept["total_liabilities"].value - by_concept["current_liabilities"].value,
    }
    gaps, unreported = [], []
    for section, items in _SECTION_ITEMS:
        absent = [item for item in items if item not in by_concept]
        if absent:
            unreported.extend(absent)
            gaps.append(section)
        elif sum((by_concept[item].value for item in items), Decimal(0)) != totals[section]:
            gaps.append(section)
    return tuple(gaps), tuple(unreported)
