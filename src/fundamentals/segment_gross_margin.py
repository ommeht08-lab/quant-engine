"""Point-in-time segment gross margins read from SEC periodic filings.

For an issuer whose consolidated revenue mixes businesses with different cost
structures, a gross margin must use one segment's sales and cost of goods
sold on both sides of the ratio. Caterpillar is the approved case: its
consolidated sales and revenues include Financial Products revenue, while
cost of goods sold excludes Financial Products' costs, so only the Machinery,
Energy & Transportation (ME&T) columns form a coherent margin. Those columns
are printed in CAT's 10-K and 10-Q supplemental tables but carry no XBRL, so
they come from ``adapters.sec_filing_document`` rather than Company Facts.

Two operations live here, deliberately separate:

* ``run_segment_document_dry_run`` downloads the issuer's submissions, reads
  every 10-K/10-Q (and amendment) accepted by the cutoff, and returns
  classified, dimensioned facts ready for the existing append-only publisher.
  It never publishes. Its facts' ingestion time (the data vintage) is when
  the last supporting document was captured, never the earlier submissions
  download, so no fact is visible at a vintage before its document existed
  locally.
* ``load_segment_gross_margin_pair`` reads only published facts through the
  repository seam, bounded by the knowledge cutoff (SEC acceptance time) and
  the data-vintage cutoff (ingestion time), and computes the two trailing-year
  margins, or refuses.

Amendment and conflict policy: a later-accepted filing supersedes an earlier
value for the same period (a restatement), but only from its acceptance time
forward; superseded values are reported, never silently dropped. Two values
for one period that cannot be ordered -- the same filing reporting both, or
different filings accepted at the same instant -- refuse.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .adapters.sec_downloader import SecDownloadError
from .adapters.sec_filing_document import (
    SOURCE_ADAPTER,
    FilingDocumentError,
    FilingDocumentReference,
    RowInvariant,
    SupplementalTableRule,
    contains_rule_rows,
    extract_supplemental_facts,
)
from .fiscal_calendar import IssuerFiscalCalendarPolicy
from .repository import FundamentalsQuery, FundamentalsRepositoryUnavailable
from .selection import select_point_in_time
from .store import source_qualified_batch_id
from .time_policy import is_aware
from .types import FinancialFact, normalize_cik

SEGMENT_SALES = "segment_sales"
SEGMENT_COST_OF_GOODS_SOLD = "segment_cost_of_goods_sold"
_PERIODIC_FORMS = frozenset(("10-K", "10-K/A", "10-Q", "10-Q/A"))
_YEAR_TO_DATE = frozenset(("Q1", "Q2YTD", "Q3YTD"))

CAT_MET_SUPPLEMENTAL_RULE = SupplementalTableRule(
    cik="0000018230",
    version="cat-met-supplemental-results-v2",
    title="Supplemental Data for Results of Operations",
    column_groups=(
        "Consolidated",
        "Machinery, Energy & Transportation",
        "Financial Products",
        "Consolidating Adjustments",
    ),
    total_group="Consolidated",
    segment_group="Machinery, Energy & Transportation",
    rows=(
        ("Sales of Machinery, Energy & Transportation", SEGMENT_SALES),
        ("Cost of goods sold", SEGMENT_COST_OF_GOODS_SOLD),
    ),
    # CAT's own XBRL member for ME&T on the axis its filings use.
    dimension=("srt:ProductOrServiceAxis", "cat:MachineryEnergyTransportationMember"),
    scale_label="(Millions of dollars)",
    scale=Decimal(1_000_000),
    # Financial Products never sells ME&T products or books cost of goods
    # sold (a dash in every filing reviewed); consolidating adjustments are
    # small eliminations (at most 9 of 42,767 in FY2023). A value shifted
    # between columns breaks one of these, so the table refuses.
    invariants=(
        RowInvariant(SEGMENT_SALES, ("Financial Products",), True, Decimal("0.01")),
        RowInvariant(SEGMENT_COST_OF_GOODS_SOLD, ("Financial Products",), True, Decimal("0.01")),
    ),
)

# Issuers whose Piotroski gross-margin factor uses a segment basis. Every
# other issuer, and every other factor, keeps consolidated figures.
_SEGMENT_GROSS_MARGIN_RULES: Dict[str, SupplementalTableRule] = {
    CAT_MET_SUPPLEMENTAL_RULE.cik: CAT_MET_SUPPLEMENTAL_RULE,
}


def segment_gross_margin_rule_for(cik: str) -> Optional[SupplementalTableRule]:
    return _SEGMENT_GROSS_MARGIN_RULES.get(normalize_cik(cik))


# Amendments reviewed and confirmed to carry no supplemental table (for
# example a Part III-only 10-K/A), keyed by CIK then accession, each with the
# reviewer's reason. Any other amendment without a recognized table refuses,
# because a restating amendment in an unfamiliar layout must never leave the
# superseded value in place. No CAT amendment has been reviewed yet.
REVIEWED_AMENDMENTS_WITHOUT_TABLE: Dict[str, Mapping[str, str]] = {
    CAT_MET_SUPPLEMENTAL_RULE.cik: {},
}


class SegmentGrossMarginRefusal(ValueError):
    """The segment margin pair cannot be reproduced from point-in-time facts."""


# --------------------------------------------------------------------------
# Ingestion (dry run only)
# --------------------------------------------------------------------------


def _submission_rows(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    filings = payload.get("filings")
    columns = filings.get("recent") if isinstance(filings, Mapping) else payload
    if not isinstance(columns, Mapping):
        raise ValueError("Submissions filing history must be columnar.")
    names = ("accessionNumber", "filingDate", "acceptanceDateTime", "reportDate", "form", "primaryDocument")
    values = [columns.get(name) for name in names]
    if any(not isinstance(column, list) for column in values) or len({len(column) for column in values}) != 1:
        raise ValueError("Submissions columns must be arrays of one length.")
    for index in range(len(values[0])):
        yield {name: column[index] for name, column in zip(names, values)}


def periodic_filing_documents(
    submissions: Sequence[Mapping[str, Any]],
    *,
    cik: str,
    accepted_by: datetime,
    report_date_floor: date,
) -> Tuple[FilingDocumentReference, ...]:
    """Every 10-K/10-Q (and amendment) accepted by ``accepted_by``, from SEC
    submissions metadata; acceptance times are SEC's, never transcribed."""

    cik = normalize_cik(cik)
    if not is_aware(accepted_by):
        raise ValueError("accepted_by must be timezone-aware.")
    entity_name = next((p.get("name") for p in submissions if isinstance(p.get("name"), str)), None)
    if not entity_name:
        raise ValueError("Submissions do not name the issuer.")
    found: Dict[str, FilingDocumentReference] = {}
    for payload in submissions:
        for row in _submission_rows(payload):
            if row["form"] not in _PERIODIC_FORMS:
                continue
            accepted_at = datetime.fromisoformat(str(row["acceptanceDateTime"]).replace("Z", "+00:00"))
            if not is_aware(accepted_at):
                raise ValueError(f"Acceptance time for {row['accessionNumber']} is not timezone-aware.")
            report_date = date.fromisoformat(row["reportDate"])
            if accepted_at > accepted_by or report_date < report_date_floor:
                continue
            accession = row["accessionNumber"]
            document = row["primaryDocument"]
            reference = FilingDocumentReference(
                cik=cik,
                entity_name=entity_name,
                accession_number=accession,
                form_type=row["form"],
                filed_date=date.fromisoformat(row["filingDate"]),
                accepted_at=accepted_at,
                report_date=report_date,
                document_name=document,
                document_url=(
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{document}"
                ),
            )
            if found.setdefault(accession, reference) != reference:
                raise ValueError(f"Submission metadata conflicts for accession {accession}.")
    return tuple(sorted(found.values(), key=lambda item: (item.accepted_at, item.accession_number)))


@dataclass(frozen=True)
class CapturedDocument:
    """One filing document as read: where from, its hash, and when its
    bytes were in hand (UTC)."""

    accession_number: str
    url: str
    sha256: str
    captured_at: datetime


@dataclass(frozen=True)
class SegmentDocumentDryRun:
    """What a dry run read and would publish; nothing here is published.

    Three instants are kept apart: ``submissions_downloaded_at`` (the filing
    list), each document's ``captured_at``, and ``ingested_at``, the data
    vintage stamped on every fact: the latest capture, so no fact is visible
    at a vintage before every document behind the batch was captured.
    Publication is a later, separately approved step that must record these
    facts with this ``ingested_at`` unchanged; it is never the publish time.
    """

    cik: str
    knowledge_cutoff: datetime
    submissions_downloaded_at: Optional[datetime] = None
    ingested_at: Optional[datetime] = None
    facts: Tuple[FinancialFact, ...] = ()
    documents: Tuple[CapturedDocument, ...] = ()
    # (accession, reviewed reason) for each excepted amendment without a table.
    amendments_without_table: Tuple[Tuple[str, str], ...] = ()
    issues: Tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return bool(self.facts) and not self.issues


def _unordered_conflicts(facts: Iterable[FinancialFact]) -> List[str]:
    """Identities with disagreeing values that acceptance time cannot order."""

    by_identity: Dict[Any, Dict[Tuple[datetime, str], set]] = defaultdict(lambda: defaultdict(set))
    for fact in facts:
        by_identity[fact.identity][(fact.provenance.eligible_at, fact.provenance.accession_number)].add(fact.value)
    problems = []
    for identity, groups in by_identity.items():
        by_instant: Dict[datetime, set] = defaultdict(set)
        for (instant, accession), values in groups.items():
            if len(values) > 1:
                problems.append(f"{identity.concept} {identity.period_start}..{identity.period_end} in {accession}")
            by_instant[instant] |= values
        for instant, values in by_instant.items():
            accessions = [accession for (at, accession) in groups if at == instant]
            if len(values) > 1 and len(accessions) > 1:
                problems.append(
                    f"{identity.concept} {identity.period_start}..{identity.period_end} "
                    f"across simultaneous filings {sorted(accessions)}"
                )
    return sorted(problems)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_segment_document_dry_run(
    *,
    downloader,
    rule: SupplementalTableRule,
    calendar_policy: IssuerFiscalCalendarPolicy,
    ingestion_batch_id: str,
    knowledge_cutoff: datetime,
    reviewed_amendments_without_table: Optional[Mapping[str, str]] = None,
    clock: Callable[[], datetime] = _utc_now,
) -> SegmentDocumentDryRun:
    """Read every periodic filing accepted by the cutoff; publish nothing.

    Every document is captured first (``clock`` is read as each one's bytes
    arrive), then all are parsed with the latest capture as the facts'
    ingestion time.

    Any filing without the rule's table refuses the whole run (a layout
    change must be reviewed, not skipped). The one exception is an amendment
    whose accession appears in ``reviewed_amendments_without_table`` (default:
    ``REVIEWED_AMENDMENTS_WITHOUT_TABLE`` for the rule's issuer); it
    contributes nothing and is listed with its reason. An excepted amendment
    that contains the table, or any row the rule reads under an unrecognized
    layout, also refuses: the review cannot be trusted.
    """

    cik = rule.cik
    if calendar_policy.cik != cik:
        raise ValueError("calendar_policy must belong to the rule's issuer.")
    if not is_aware(knowledge_cutoff):
        raise ValueError("knowledge_cutoff must be timezone-aware.")

    downloaded_at: Optional[datetime] = None
    documents: List[CapturedDocument] = []

    def refuse(message: str) -> SegmentDocumentDryRun:
        return SegmentDocumentDryRun(
            cik,
            knowledge_cutoff,
            submissions_downloaded_at=downloaded_at,
            documents=tuple(documents),
            issues=(message,),
        )

    try:
        payload = downloader.fetch_issuer(cik)
    except SecDownloadError as error:
        return refuse(f"download: {error}")
    downloaded_at = payload.downloaded_at
    floor = min(
        definition.period_start
        for definition in (*calendar_policy.fiscal_years, *calendar_policy.transitions, *calendar_policy.open_fiscal_years)
    )
    try:
        filings = periodic_filing_documents(
            payload.submissions, cik=cik, accepted_by=knowledge_cutoff, report_date_floor=floor
        )
    except (ValueError, TypeError, KeyError) as error:
        return refuse(f"submissions: {error}")
    if not filings:
        return refuse("No periodic filing was accepted by the cutoff.")

    if reviewed_amendments_without_table is None:
        reviewed_amendments_without_table = REVIEWED_AMENDMENTS_WITHOUT_TABLE.get(cik, {})
    batch_id = source_qualified_batch_id(ingestion_batch_id, SOURCE_ADAPTER)
    contents: List[bytes] = []
    for filing in filings:
        try:
            url, document = downloader.fetch_filing_document(cik, filing.accession_number, filing.document_name)
        except (SecDownloadError, OSError) as error:
            return refuse(f"{filing.accession_number}: document unavailable ({error})")
        captured_at = clock()
        if not is_aware(captured_at):
            raise ValueError("clock must return timezone-aware datetimes.")
        if url != filing.document_url:
            return refuse(f"{filing.accession_number}: fetched {url}, expected {filing.document_url}")
        documents.append(
            CapturedDocument(filing.accession_number, url, hashlib.sha256(document).hexdigest(), captured_at)
        )
        if captured_at < downloaded_at:
            return refuse(
                f"{filing.accession_number}: captured at {captured_at.isoformat()}, before the submissions "
                f"that list it were downloaded ({downloaded_at.isoformat()}); the clock cannot be trusted."
            )
        contents.append(document)
    ingested_at = max(item.captured_at for item in documents)

    facts: List[FinancialFact] = []
    skipped: List[Tuple[str, str]] = []
    for filing, document in zip(filings, contents):
        try:
            extracted = extract_supplemental_facts(
                document,
                rule,
                filing=filing,
                calendar_policy=calendar_policy,
                ingestion_batch_id=batch_id,
                ingested_at=ingested_at,
            )
        except FilingDocumentError as error:
            return refuse(f"{filing.accession_number}: {error.code.value}: {error}")
        excepted = filing.form_type.endswith("/A") and filing.accession_number in reviewed_amendments_without_table
        if not extracted:
            if excepted and contains_rule_rows(document, rule):
                return refuse(
                    f"{filing.accession_number} is excepted as having no {rule.title!r} table, but it has rows "
                    "the rule reads under an unrecognized layout."
                )
            if excepted:
                skipped.append((filing.accession_number, reviewed_amendments_without_table[filing.accession_number]))
                continue
            return refuse(
                f"{filing.accession_number} ({filing.form_type}) has no recognized {rule.title!r} table"
                + (" and no reviewed exception." if filing.form_type.endswith("/A") else ".")
            )
        if excepted:
            return refuse(f"{filing.accession_number} is excepted as having no {rule.title!r} table, but one was found.")
        facts.extend(extracted)

    conflicts = _unordered_conflicts(facts)
    if conflicts:
        return refuse("Conflicting segment values: " + "; ".join(conflicts))
    return SegmentDocumentDryRun(
        cik=cik,
        knowledge_cutoff=knowledge_cutoff,
        submissions_downloaded_at=downloaded_at,
        ingested_at=ingested_at,
        facts=tuple(facts),
        documents=tuple(documents),
        amendments_without_table=tuple(skipped),
    )


# --------------------------------------------------------------------------
# Point-in-time read
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentMarginComponent:
    concept: str
    fiscal_year: int
    fiscal_period: str
    period_start: date
    period_end: date
    value: Decimal
    accession_number: str
    form_type: str
    accepted_at: datetime
    source_document_url: str
    raw_tag: str
    ingestion_batch_id: str
    ingested_at: datetime
    # Earlier filings for the same period: (accession, value) pairs.
    superseded: Tuple[Tuple[str, Decimal], ...] = ()
    corroborating_accessions: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SegmentGrossMarginPair:
    current: Decimal
    prior: Decimal
    latest_end: date
    prior_end: date
    policy_version: str
    dimension: Tuple[str, str]
    knowledge_cutoff: datetime
    data_vintage_cutoff: datetime
    components: Tuple[SegmentMarginComponent, ...] = field(default_factory=tuple)

    def audit_record(self) -> dict:
        """JSON-safe provenance for the pilot record; exact values as text."""

        return {
            "basis": "segment_sales_and_cost_of_goods_sold",
            "policy_version": self.policy_version,
            "dimension": list(self.dimension),
            "trailing_year_ends": [self.latest_end.isoformat(), self.prior_end.isoformat()],
            "gross_margin_current": str(self.current),
            "gross_margin_prior": str(self.prior),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "data_vintage_cutoff": self.data_vintage_cutoff.isoformat(),
            "source_adapter": SOURCE_ADAPTER,
            "ingestion_batch_ids": sorted({item.ingestion_batch_id for item in self.components}),
            "filing_accessions": sorted({item.accession_number for item in self.components}),
            "components": [
                {
                    "concept": item.concept,
                    "fiscal_year": item.fiscal_year,
                    "fiscal_period": item.fiscal_period,
                    "period_start": item.period_start.isoformat(),
                    "period_end": item.period_end.isoformat(),
                    "value": str(item.value),
                    "accession_number": item.accession_number,
                    "form_type": item.form_type,
                    "accepted_at": item.accepted_at.isoformat(),
                    "source_document_url": item.source_document_url,
                    "raw_tag": item.raw_tag,
                    "ingestion_batch_id": item.ingestion_batch_id,
                    "ingested_at": item.ingested_at.isoformat(),
                    "superseded": [[accession, str(value)] for accession, value in item.superseded],
                    "corroborating_accessions": list(item.corroborating_accessions),
                }
                for item in self.components
            ],
        }


def load_segment_gross_margin_pair(
    repository,
    *,
    rule: SupplementalTableRule,
    fiscal_calendar_version: str,
    knowledge_cutoff: datetime,
    data_vintage_cutoff: datetime,
    latest_end: date,
    prior_end: date,
) -> SegmentGrossMarginPair:
    """The segment gross margins for the trailing years ending ``latest_end``
    and ``prior_end``, or ``SegmentGrossMarginRefusal``."""

    if not is_aware(knowledge_cutoff) or not is_aware(data_vintage_cutoff):
        raise SegmentGrossMarginRefusal("Both cutoffs must be timezone-aware.")
    query = FundamentalsQuery(
        cik=rule.cik,
        knowledge_cutoff=knowledge_cutoff,
        data_vintage_cutoff=data_vintage_cutoff,
        concepts=rule.concepts,
        source_adapter=SOURCE_ADAPTER,
        concept_map_version=rule.version,
        fiscal_calendar_version=fiscal_calendar_version,
        max_periods_per_statement=64,
    )
    try:
        facts = tuple(repository.get_facts(query))
    except FundamentalsRepositoryUnavailable as error:
        raise SegmentGrossMarginRefusal(f"Segment facts are unavailable: {error}") from None

    # The repository applies these bounds; a fact outside them is a defect,
    # so it refuses rather than being filtered out quietly.
    for fact in facts:
        if (
            fact.identity.context.entity_cik != rule.cik
            or fact.identity.context.dimensions != (rule.dimension,)
            or fact.identity.concept not in rule.concepts
            or fact.lineage.source_adapter != SOURCE_ADAPTER
            or fact.lineage.concept_map_version != rule.version
            or fact.lineage.fiscal_calendar_version != fiscal_calendar_version
        ):
            raise SegmentGrossMarginRefusal(f"Repository returned an out-of-scope fact ({fact.raw_tag}).")
        if fact.provenance.eligible_at > knowledge_cutoff or fact.lineage.ingested_at > data_vintage_cutoff:
            raise SegmentGrossMarginRefusal(
                f"Repository returned a fact outside the cutoffs ({fact.provenance.accession_number})."
            )
    conflicts = _unordered_conflicts(facts)
    if conflicts:
        raise SegmentGrossMarginRefusal("Conflicting segment values: " + "; ".join(conflicts))
    history = select_point_in_time(facts, knowledge_cutoff, cik=rule.cik)
    if history.conflicts:
        raise SegmentGrossMarginRefusal("Conflicting segment values within one filing.")

    winners: Dict[Tuple[str, date, date], FinancialFact] = {}
    for period in history.income_statement_periods:
        for fact in period.facts:
            winners[(fact.identity.concept, fact.identity.period_start, fact.identity.period_end)] = fact
    by_identity: Dict[Any, List[FinancialFact]] = defaultdict(list)
    for fact in facts:
        by_identity[fact.identity].append(fact)

    def find(concept: str, *, end: Optional[date] = None, fiscal_year=None, fiscal_period=None) -> FinancialFact:
        matches = [
            fact
            for (name, _start, fact_end), fact in winners.items()
            if name == concept
            and (end is None or fact_end == end)
            and (fiscal_year is None or fact.period.fiscal_year == fiscal_year)
            and (fiscal_period is None or fact.period.fiscal_period in fiscal_period)
        ]
        if len(matches) != 1:
            wanted = f"{concept} ending {end}" if end else f"{concept} {fiscal_period} {fiscal_year}"
            raise SegmentGrossMarginRefusal(f"Segment fact missing or ambiguous at the cutoff: {wanted}.")
        return matches[0]

    used: Dict[Any, FinancialFact] = {}

    def trailing_year(end: date) -> Tuple[Decimal, Decimal]:
        totals = []
        for concept in (SEGMENT_SALES, SEGMENT_COST_OF_GOODS_SOLD):
            annual = [fact for (name, _s, e), fact in winners.items() if name == concept and e == end and fact.period.fiscal_period == "FY"]
            if len(annual) > 1:
                periods = sorted(f"{fact.identity.period_start}..{fact.identity.period_end}" for fact in annual)
                raise SegmentGrossMarginRefusal(f"Ambiguous annual {concept} ending {end}: {periods}.")
            if annual:
                parts = [(annual[0], 1)]
            else:
                ytd = find(concept, end=end, fiscal_period=_YEAR_TO_DATE)
                prior_year = ytd.period.fiscal_year - 1
                parts = [
                    (ytd, 1),
                    (find(concept, fiscal_year=prior_year, fiscal_period=("FY",)), 1),
                    (find(concept, fiscal_year=prior_year, fiscal_period=(ytd.period.fiscal_period,)), -1),
                ]
            for fact, _sign in parts:
                used[fact.identity] = fact
            totals.append(sum((fact.value * sign for fact, sign in parts), Decimal(0)))
        sales, cost = totals
        if sales <= 0 or cost < 0:
            raise SegmentGrossMarginRefusal(f"Segment trailing-year sales or cost ending {end} is invalid.")
        return sales, cost

    margins = []
    for end in (latest_end, prior_end):
        sales, cost = trailing_year(end)
        margins.append((sales - cost) / sales)

    components = []
    for identity, fact in sorted(used.items(), key=lambda item: (item[1].identity.concept, item[1].identity.period_end, item[1].identity.period_start)):
        earlier = [
            other for other in by_identity[identity] if other.provenance.accession_number != fact.provenance.accession_number
        ]
        components.append(
            SegmentMarginComponent(
                concept=fact.identity.concept,
                fiscal_year=fact.period.fiscal_year,
                fiscal_period=fact.period.fiscal_period,
                period_start=fact.identity.period_start,
                period_end=fact.identity.period_end,
                value=fact.value,
                accession_number=fact.provenance.accession_number,
                form_type=fact.provenance.form_type,
                accepted_at=fact.provenance.accepted_at,
                source_document_url=fact.lineage.source_document_url,
                raw_tag=fact.raw_tag,
                ingestion_batch_id=fact.lineage.ingestion_batch_id,
                ingested_at=fact.lineage.ingested_at,
                superseded=tuple(sorted({(o.provenance.accession_number, o.value) for o in earlier if o.value != fact.value})),
                corroborating_accessions=tuple(sorted({o.provenance.accession_number for o in earlier if o.value == fact.value})),
            )
        )
    return SegmentGrossMarginPair(
        current=margins[0],
        prior=margins[1],
        latest_end=latest_end,
        prior_end=prior_end,
        policy_version=rule.version,
        dimension=rule.dimension,
        knowledge_cutoff=knowledge_cutoff,
        data_vintage_cutoff=data_vintage_cutoff,
        components=tuple(components),
    )
