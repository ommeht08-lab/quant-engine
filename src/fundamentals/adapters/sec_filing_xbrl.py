"""Consolidated balances composed from an SEC filing's own XBRL instance.

Company Facts carries only facts without dimensions. Some issuers tag a
consolidated balance-sheet line solely as members of one reporting axis (for
example, Caterpillar reports term debt as Machinery, Energy & Transportation
plus Financial Products on ``srt:ProductOrServiceAxis``). This adapter reads
the filing's XBRL instance and composes such a line only under an explicit,
issuer-scoped ``BalanceCompositionRule``:

* every fact on the rule's axis must carry exactly that one dimension;
* the members present at an instant must equal one declared complete member
  set exactly -- a subset (for example, Financial Products alone) or an
  undeclared member refuses;
* a member reported twice with different values refuses;
* if the filing also reports the line without dimensions, it must equal the
  composed sum, or the composition refuses;
* a declared *equivalent* member (another presentation of the same amount,
  such as a segment-note member mirroring a line-of-business member) is
  excluded from the sum only if it exactly equals its counterpart;
* facts on any other axis are ignored.

Each composed fact keeps its filing provenance: accession, form, amendment
flag, acceptance time, the instance document URL, and the exact contexts,
members, unit, and period it was built from (recorded in ``raw_tag``).
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Dict, Iterable, List, Optional, Tuple

from ..concept_map import BalanceCompositionRule, FactPeriodType
from ..types import FactLineage, StatementKind, normalize_cik
from .sec_companyfacts import SecExtractedFact

SOURCE_ADAPTER = "sec_filing_xbrl"
_XBRLI = "http://www.xbrl.org/2003/instance"
_XBRLDI = "http://xbrl.org/2006/xbrldi"
_US_GAAP_MARKER = "fasb.org/us-gaap"


class FilingXbrlIssueCode(str, Enum):
    INVALID_INSTANCE = "invalid_instance"
    INCOMPLETE_MEMBER_SET = "incomplete_member_set"
    CONFLICTING_COMPONENT = "conflicting_component"
    CONFLICTING_TOTAL = "conflicting_total"
    UNSUPPORTED_COMPONENT = "unsupported_component"


class FilingXbrlError(ValueError):
    def __init__(self, code: FilingXbrlIssueCode, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class XbrlFact:
    taxonomy: Optional[str]
    name: str
    context_id: str
    entity_cik: Optional[str]
    instant: Optional[date]
    dimensions: Tuple[Tuple[str, str], ...]
    typed_dimensions: bool
    unit: Optional[str]
    value: Optional[Decimal]


@dataclass(frozen=True)
class FilingReference:
    """Submission metadata for the filing whose instance is being composed."""

    cik: str
    entity_name: str
    accession_number: str
    form_type: str
    filed_date: date
    accepted_at: datetime
    report_date: date
    primary_document: str
    instance_url: str


def _text(element: ElementTree.Element) -> str:
    return (element.text or "").strip()


def _qname(value: str) -> str:
    return value.strip()


def parse_xbrl_instance(document: bytes) -> Tuple[XbrlFact, ...]:
    """Parse numeric facts, their contexts, and units from an XBRL instance."""

    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as error:
        raise FilingXbrlError(FilingXbrlIssueCode.INVALID_INSTANCE, "XBRL instance is not valid XML.") from error

    contexts: Dict[str, Tuple[Optional[str], Optional[date], Tuple[Tuple[str, str], ...], bool]] = {}
    for context in root.iter(f"{{{_XBRLI}}}context"):
        identifier = context.find(f"{{{_XBRLI}}}entity/{{{_XBRLI}}}identifier")
        instant = context.find(f"{{{_XBRLI}}}period/{{{_XBRLI}}}instant")
        explicit = tuple(
            sorted((_qname(member.get("dimension", "")), _qname(_text(member))) for member in context.iter(f"{{{_XBRLDI}}}explicitMember"))
        )
        typed = any(True for _ in context.iter(f"{{{_XBRLDI}}}typedMember"))
        cik = None
        if identifier is not None and _text(identifier).isdigit():
            cik = normalize_cik(_text(identifier))
        context_id = context.get("id", "")
        if context_id in contexts:
            raise FilingXbrlError(
                FilingXbrlIssueCode.INVALID_INSTANCE, f"XBRL instance defines context {context_id} more than once."
            )
        contexts[context_id] = (
            cik,
            date.fromisoformat(_text(instant)) if instant is not None else None,
            explicit,
            typed,
        )

    units: Dict[str, str] = {}
    for unit in root.iter(f"{{{_XBRLI}}}unit"):
        measures = [_text(measure) for measure in unit.iter(f"{{{_XBRLI}}}measure")]
        units[unit.get("id", "")] = measures[0] if len(measures) == 1 else "/".join(measures)

    facts: List[XbrlFact] = []
    for element in root:
        context_id = element.get("contextRef")
        if not context_id or not element.tag.startswith("{"):
            continue
        namespace, name = element.tag[1:].split("}", 1)
        if context_id not in contexts:
            raise FilingXbrlError(FilingXbrlIssueCode.INVALID_INSTANCE, f"Fact {name} references a missing context.")
        cik, instant, dimensions, typed = contexts[context_id]
        nil = element.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true"
        value: Optional[Decimal] = None
        if not nil and element.get("unitRef"):
            try:
                value = Decimal(_text(element))
            except InvalidOperation:
                value = None
        facts.append(
            XbrlFact(
                taxonomy="us-gaap" if _US_GAAP_MARKER in namespace else None,
                name=name,
                context_id=context_id,
                entity_cik=cik,
                instant=instant,
                dimensions=dimensions,
                typed_dimensions=typed,
                unit=units.get(element.get("unitRef", "")),
                value=value,
            )
        )
    return tuple(facts)


def compose_consolidated_balances(
    facts: Iterable[XbrlFact],
    rules: Iterable[BalanceCompositionRule],
    *,
    filing: FilingReference,
    concept_map_version: str,
    ingestion_batch_id: str,
    ingested_at: datetime,
) -> Tuple[SecExtractedFact, ...]:
    """Compose each rule's consolidated line at every instant it is reported.

    Raises ``FilingXbrlError`` rather than emitting anything ambiguous.
    """

    facts = tuple(facts)
    cik = normalize_cik(filing.cik)
    composed: List[SecExtractedFact] = []
    for rule in rules:
        if rule.cik != cik:
            continue
        candidates = [
            fact
            for fact in facts
            if fact.taxonomy == rule.taxonomy and fact.name == rule.raw_tag and fact.instant is not None
        ]
        components: Dict[date, Dict[str, Tuple[Decimal, str]]] = {}
        totals: Dict[date, Decimal] = {}
        for fact in candidates:
            on_axis = [dimension for dimension in fact.dimensions if dimension[0] == rule.axis]
            if not on_axis and not fact.dimensions and not fact.typed_dimensions:
                if fact.value is None or fact.unit != "iso4217:USD" or fact.entity_cik != cik:
                    raise FilingXbrlError(
                        FilingXbrlIssueCode.UNSUPPORTED_COMPONENT,
                        f"{rule.raw_tag} total at {fact.instant} is not a USD value for the issuer.",
                    )
                prior = totals.setdefault(fact.instant, fact.value)
                if prior != fact.value:
                    raise FilingXbrlError(
                        FilingXbrlIssueCode.CONFLICTING_TOTAL,
                        f"{rule.raw_tag} reports conflicting totals at {fact.instant}.",
                    )
                continue
            if not on_axis:
                continue  # another axis, e.g. a segment-note disclosure
            if len(fact.dimensions) != 1 or fact.typed_dimensions:
                raise FilingXbrlError(
                    FilingXbrlIssueCode.UNSUPPORTED_COMPONENT,
                    f"{rule.raw_tag} member fact {fact.context_id} carries additional dimensions.",
                )
            if fact.value is None or fact.unit != "iso4217:USD" or fact.entity_cik != cik:
                raise FilingXbrlError(
                    FilingXbrlIssueCode.UNSUPPORTED_COMPONENT,
                    f"{rule.raw_tag} member fact {fact.context_id} is not a USD value for the issuer.",
                )
            member = on_axis[0][1]
            members = components.setdefault(fact.instant, {})
            if member in members and members[member][0] != fact.value:
                raise FilingXbrlError(
                    FilingXbrlIssueCode.CONFLICTING_COMPONENT,
                    f"{rule.raw_tag} member {member} reports conflicting values at {fact.instant}.",
                )
            members.setdefault(member, (fact.value, fact.context_id))

        for instant, members in sorted(components.items()):
            for duplicate, counterpart in rule.equivalent_members:
                if duplicate not in members:
                    continue
                if counterpart not in members or members[counterpart][0] != members[duplicate][0]:
                    raise FilingXbrlError(
                        FilingXbrlIssueCode.CONFLICTING_COMPONENT,
                        f"{rule.raw_tag} equivalent member {duplicate} at {instant} "
                        f"does not equal {counterpart}.",
                    )
                del members[duplicate]
            present = frozenset(members)
            if present not in rule.member_sets:
                raise FilingXbrlError(
                    FilingXbrlIssueCode.INCOMPLETE_MEMBER_SET,
                    f"{rule.raw_tag} at {instant} has members {sorted(present)}, "
                    "which is not a declared complete set.",
                )
            total = sum((members[member][0] for member in sorted(present)), Decimal("0"))
            if instant in totals and totals[instant] != total:
                raise FilingXbrlError(
                    FilingXbrlIssueCode.CONFLICTING_TOTAL,
                    f"{rule.raw_tag} consolidated total at {instant} does not equal its members.",
                )
            composed.append(
                SecExtractedFact(
                    entity_cik=cik,
                    entity_name=filing.entity_name,
                    taxonomy=rule.taxonomy,
                    raw_tag=composition_raw_tag(rule, {member: members[member][1] for member in present}),
                    canonical_concept=rule.canonical_concept,
                    statement_kind=StatementKind.BALANCE_SHEET,
                    period_type=FactPeriodType.INSTANT,
                    period_start=None,
                    period_end=instant,
                    value=total,
                    unit="USD",
                    currency="USD",
                    provenance_accession_number=filing.accession_number,
                    form_type=filing.form_type,
                    is_amendment=filing.form_type.endswith("/A"),
                    filed_date=filing.filed_date,
                    accepted_at=filing.accepted_at,
                    report_date=filing.report_date,
                    primary_document=filing.primary_document,
                    filing_fiscal_year=None,
                    filing_fiscal_period=None,
                    frame=None,
                    lineage=FactLineage(
                        source_adapter=SOURCE_ADAPTER,
                        source_document_url=filing.instance_url,
                        concept_map_version=concept_map_version,
                        ingestion_batch_id=ingestion_batch_id,
                        ingested_at=ingested_at,
                    ),
                )
            )
    return tuple(composed)


def composition_raw_tag(rule: BalanceCompositionRule, context_by_member: Dict[str, str]) -> str:
    """Self-describing lineage: the source tag, axis, members, and contexts summed."""

    parts = "+".join(f"{member}@{context_by_member[member]}" for member in sorted(context_by_member))
    return f"{rule.raw_tag}#sum[{rule.axis}:{parts}]"
