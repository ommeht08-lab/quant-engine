"""Strict extraction of SEC Company Facts joined to Submissions metadata.

This module performs no HTTP requests and no database writes. It converts
already-fetched SEC-shaped payloads into immutable source records while
preserving the boundary before issuer fiscal-period classification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import quote

from ..concept_map import ConceptMap, ConceptRule, FactPeriodType
from ..time_policy import is_aware
from ..types import FactLineage, StatementKind, normalize_cik

SOURCE_ADAPTER = "sec_companyfacts"
_ACCESSION_PATTERN = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_DOCUMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_SUPPORTED_FORMS = frozenset(
    ("10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A", "10-QT", "10-QT/A")
)
_SUBMISSION_COLUMNS = (
    "accessionNumber",
    "filingDate",
    "acceptanceDateTime",
    "reportDate",
    "form",
    "primaryDocument",
)


class SecIngestionIssueCode(str, Enum):
    INVALID_PAYLOAD = "invalid_payload"
    ISSUER_MISMATCH = "issuer_mismatch"
    MISSING_SUBMISSION_METADATA = "missing_submission_metadata"
    CONFLICTING_SUBMISSION_METADATA = "conflicting_submission_metadata"
    MALFORMED_FACT = "malformed_fact"
    UNSUPPORTED_UNIT = "unsupported_unit"
    DIMENSIONAL_CONTEXT_UNSUPPORTED = "dimensional_context_unsupported"
    FILING_METADATA_MISMATCH = "filing_metadata_mismatch"
    SYNONYM_CONFLICT = "synonym_conflict"
    NO_MAPPED_FACTS = "no_mapped_facts"
    NO_ELIGIBLE_FACTS = "no_eligible_facts"


@dataclass(frozen=True)
class SecIngestionIssue:
    code: SecIngestionIssueCode
    message: str
    accession_number: Optional[str] = None
    raw_tags: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("SecIngestionIssue.message must be a non-empty string.")
        object.__setattr__(self, "raw_tags", tuple(sorted(set(self.raw_tags))))


@dataclass(frozen=True)
class SecExtractedFact:
    """One joined SEC source record before fiscal-period classification."""

    entity_cik: str
    entity_name: str
    taxonomy: str
    raw_tag: str
    canonical_concept: str
    statement_kind: StatementKind
    period_type: FactPeriodType
    period_start: Optional[date]
    period_end: date
    value: Decimal
    unit: str
    currency: Optional[str]
    provenance_accession_number: str
    form_type: str
    is_amendment: bool
    filed_date: date
    accepted_at: datetime
    report_date: date
    primary_document: str
    filing_fiscal_year: Optional[int]
    filing_fiscal_period: Optional[str]
    frame: Optional[str]
    lineage: FactLineage
    dimensions: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_cik", normalize_cik(self.entity_cik))
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError("SecExtractedFact.value must be a finite Decimal.")
        if not is_aware(self.accepted_at):
            raise ValueError("SecExtractedFact.accepted_at must be timezone-aware.")
        if self.dimensions:
            raise ValueError("SEC Company Facts extraction supports entity-wide facts only.")

    @property
    def source_document_url(self) -> str:
        return self.lineage.source_document_url


@dataclass(frozen=True)
class SecExtractionResult:
    cik: str
    concept_map_version: str
    facts: Tuple[SecExtractedFact, ...] = field(default_factory=tuple)
    issues: Tuple[SecIngestionIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if bool(self.facts) == bool(self.issues):
            raise ValueError("SecExtractionResult must contain either facts or refusal issues.")

    @property
    def is_complete(self) -> bool:
        return bool(self.facts)


@dataclass(frozen=True)
class _SubmissionMetadata:
    accession_number: str
    filed_date: date
    accepted_at: datetime
    report_date: date
    form_type: str
    primary_document: str


class _ExtractionFailure(ValueError):
    def __init__(
        self,
        code: SecIngestionIssueCode,
        message: str,
        *,
        accession_number: Optional[str] = None,
        raw_tags: Tuple[str, ...] = (),
    ):
        super().__init__(message)
        self.issue = SecIngestionIssue(code, message, accession_number, raw_tags)


def _fail(
    code: SecIngestionIssueCode,
    message: str,
    *,
    accession_number: Optional[str] = None,
    raw_tags: Tuple[str, ...] = (),
) -> None:
    raise _ExtractionFailure(
        code,
        message,
        accession_number=accession_number,
        raw_tags=raw_tags,
    )


def _parse_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        _fail(SecIngestionIssueCode.INVALID_PAYLOAD, f"{field_name} must be an ISO date string.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(SecIngestionIssueCode.INVALID_PAYLOAD, f"{field_name} is not a valid ISO date.")


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        _fail(
            SecIngestionIssueCode.INVALID_PAYLOAD,
            f"{field_name} must be an ISO timestamp string.",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(SecIngestionIssueCode.INVALID_PAYLOAD, f"{field_name} is not a valid ISO timestamp.")
    if not is_aware(parsed):
        _fail(SecIngestionIssueCode.INVALID_PAYLOAD, f"{field_name} must be timezone-aware.")
    return parsed


def _parse_decimal(value: Any, accession_number: str, raw_tag: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (int, str, Decimal)):
        _fail(
            SecIngestionIssueCode.MALFORMED_FACT,
            f"{raw_tag} in {accession_number} must use an exact JSON number representation.",
            accession_number=accession_number,
            raw_tags=(raw_tag,),
        )
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        _fail(
            SecIngestionIssueCode.MALFORMED_FACT,
            f"{raw_tag} in {accession_number} has an invalid numeric value.",
            accession_number=accession_number,
            raw_tags=(raw_tag,),
        )
    if not parsed.is_finite():
        _fail(
            SecIngestionIssueCode.MALFORMED_FACT,
            f"{raw_tag} in {accession_number} has a non-finite value.",
            accession_number=accession_number,
            raw_tags=(raw_tag,),
        )
    return parsed


def _columnar_submission_rows(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    filings = payload.get("filings")
    if isinstance(filings, Mapping) and isinstance(filings.get("recent"), Mapping):
        columns = filings["recent"]
    else:
        columns = payload
    if not isinstance(columns, Mapping):
        _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "Submissions filing history must be columnar.")

    values = []
    for column in _SUBMISSION_COLUMNS:
        column_values = columns.get(column)
        if not isinstance(column_values, Sequence) or isinstance(column_values, (str, bytes)):
            _fail(
                SecIngestionIssueCode.INVALID_PAYLOAD,
                f"Submissions column {column!r} must be an array.",
            )
        values.append(column_values)
    lengths = {len(column_values) for column_values in values}
    if len(lengths) != 1:
        _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "Submissions columns have different lengths.")
    for index in range(lengths.pop() if lengths else 0):
        yield {column: columns[column][index] for column in _SUBMISSION_COLUMNS}


def _submission_metadata(
    payloads: Iterable[Mapping[str, Any]],
    expected_cik: str,
    relevant_accessions: FrozenSet[str],
    knowledge_cutoff: Optional[datetime] = None,
    filing_report_date_floor: Optional[date] = None,
) -> Tuple[Dict[str, _SubmissionMetadata], FrozenSet[str], FrozenSet[str]]:
    result: Dict[str, _SubmissionMetadata] = {}
    future_accessions: Set[str] = set()
    outside_window_accessions: Set[str] = set()
    try:
        payload_iterator = iter(payloads)
    except TypeError:
        _fail(
            SecIngestionIssueCode.INVALID_PAYLOAD,
            "Submissions payloads must be an iterable of objects.",
        )
    for payload in payload_iterator:
        if not isinstance(payload, Mapping):
            _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "Each Submissions payload must be an object.")
        submissions_cik = payload.get("cik")
        if submissions_cik is not None:
            if isinstance(submissions_cik, int) and not isinstance(submissions_cik, bool):
                submissions_cik = str(submissions_cik)
            try:
                normalized_submissions_cik = normalize_cik(submissions_cik)
            except ValueError:
                _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "Submissions payload has an invalid CIK.")
            if normalized_submissions_cik != expected_cik:
                _fail(
                    SecIngestionIssueCode.ISSUER_MISMATCH,
                    "Submissions CIK does not match the requested issuer.",
                )
        for row in _columnar_submission_rows(payload):
            accession = row["accessionNumber"]
            if not isinstance(accession, str) or accession not in relevant_accessions:
                continue
            if not _ACCESSION_PATTERN.fullmatch(accession):
                _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "Submissions contains an invalid accession number.")
            form = row["form"]
            if not isinstance(form, str):
                _fail(SecIngestionIssueCode.INVALID_PAYLOAD, f"Form for {accession} must be text.")
            if form not in _SUPPORTED_FORMS:
                continue
            accepted_at = _parse_datetime(row["acceptanceDateTime"], "acceptanceDateTime")
            if knowledge_cutoff is not None and accepted_at > knowledge_cutoff:
                if accession in result or accession in outside_window_accessions:
                    _fail(
                        SecIngestionIssueCode.CONFLICTING_SUBMISSION_METADATA,
                        f"Submission metadata conflicts for accession {accession}.",
                        accession_number=accession,
                    )
                future_accessions.add(accession)
                continue
            report_date = _parse_date(row["reportDate"], "reportDate")
            if filing_report_date_floor is not None and report_date < filing_report_date_floor:
                if accession in result or accession in future_accessions:
                    _fail(
                        SecIngestionIssueCode.CONFLICTING_SUBMISSION_METADATA,
                        f"Submission metadata conflicts for accession {accession}.",
                        accession_number=accession,
                    )
                outside_window_accessions.add(accession)
                continue
            if accession in future_accessions or accession in outside_window_accessions:
                _fail(
                    SecIngestionIssueCode.CONFLICTING_SUBMISSION_METADATA,
                    f"Submission metadata conflicts for accession {accession}.",
                    accession_number=accession,
                )
            document = row["primaryDocument"]
            if not isinstance(document, str) or not _DOCUMENT_PATTERN.fullmatch(document):
                _fail(
                    SecIngestionIssueCode.INVALID_PAYLOAD,
                    f"Primary document for {accession} is invalid.",
                )
            metadata = _SubmissionMetadata(
                accession_number=accession,
                filed_date=_parse_date(row["filingDate"], "filingDate"),
                accepted_at=accepted_at,
                report_date=report_date,
                form_type=form,
                primary_document=document,
            )
            existing = result.setdefault(accession, metadata)
            if existing != metadata:
                _fail(
                    SecIngestionIssueCode.CONFLICTING_SUBMISSION_METADATA,
                    f"Submission metadata conflicts for accession {accession}.",
                    accession_number=accession,
                )
    return result, frozenset(future_accessions), frozenset(outside_window_accessions)


def _source_document_url(cik: str, metadata: _SubmissionMetadata) -> str:
    accession_path = metadata.accession_number.replace("-", "")
    document = quote(metadata.primary_document, safe="._-")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accession_path}/{document}"
    )


def _fact_sort_key(fact: SecExtractedFact) -> Tuple[Any, ...]:
    return (
        fact.statement_kind.value,
        fact.period_end,
        fact.period_start or date.min,
        fact.canonical_concept,
        fact.unit,
        fact.provenance_accession_number,
        fact.taxonomy,
        fact.raw_tag,
        fact.value,
        (fact.filing_fiscal_year is None, fact.filing_fiscal_year or 0),
        fact.filing_fiscal_period or "",
        fact.frame or "",
    )


def _optional_text(value: Any, field_name: str, accession: str, raw_tag: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(
            SecIngestionIssueCode.MALFORMED_FACT,
            f"{field_name} for {raw_tag} in {accession} must be text when present.",
            accession_number=accession,
            raw_tags=(raw_tag,),
        )
    return value


def _optional_fiscal_year(value: Any, accession: str, raw_tag: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(
            SecIngestionIssueCode.MALFORMED_FACT,
            f"fy for {raw_tag} in {accession} must be an integer when present.",
            accession_number=accession,
            raw_tags=(raw_tag,),
        )
    return value


def _validate_geometry(
    rule: ConceptRule,
    entry: Mapping[str, Any],
    period_end: date,
    accession: str,
) -> Optional[date]:
    start_value = entry.get("start")
    if rule.period_type is FactPeriodType.DURATION:
        if start_value is None:
            _fail(
                SecIngestionIssueCode.MALFORMED_FACT,
                f"Duration fact {rule.raw_tag} in {accession} is missing start.",
                accession_number=accession,
                raw_tags=(rule.raw_tag,),
            )
        period_start = _parse_date(start_value, "start")
        if period_start > period_end:
            _fail(
                SecIngestionIssueCode.MALFORMED_FACT,
                f"Fact {rule.raw_tag} in {accession} starts after it ends.",
                accession_number=accession,
                raw_tags=(rule.raw_tag,),
            )
        return period_start
    if start_value is not None:
        _fail(
            SecIngestionIssueCode.MALFORMED_FACT,
            f"Instant fact {rule.raw_tag} in {accession} must not have start.",
            accession_number=accession,
            raw_tags=(rule.raw_tag,),
        )
    return None


def _extract_entry(
    *,
    entry: Mapping[str, Any],
    rule: ConceptRule,
    unit: str,
    cik: str,
    entity_name: str,
    metadata_by_accession: Mapping[str, _SubmissionMetadata],
    concept_map: ConceptMap,
    ingestion_batch_id: str,
    ingested_at: datetime,
) -> Optional[SecExtractedFact]:
    accession = entry.get("accn")
    if not isinstance(accession, str) or not _ACCESSION_PATTERN.fullmatch(accession):
        _fail(SecIngestionIssueCode.MALFORMED_FACT, f"{rule.raw_tag} has an invalid accession.")
    form = entry.get("form")
    if not isinstance(form, str) or not form.strip():
        _fail(
            SecIngestionIssueCode.MALFORMED_FACT,
            f"Form for {rule.raw_tag} in {accession} must be non-empty text.",
            accession_number=accession,
            raw_tags=(rule.raw_tag,),
        )
    if form not in _SUPPORTED_FORMS:
        return None
    metadata = metadata_by_accession.get(accession)
    if metadata is None:
        _fail(
            SecIngestionIssueCode.MISSING_SUBMISSION_METADATA,
            f"No Submissions metadata exists for accession {accession}.",
            accession_number=accession,
            raw_tags=(rule.raw_tag,),
        )
    filed_date = _parse_date(entry.get("filed"), "filed")
    if form != metadata.form_type or filed_date != metadata.filed_date:
        _fail(
            SecIngestionIssueCode.FILING_METADATA_MISMATCH,
            f"Company Facts and Submissions metadata disagree for {accession}.",
            accession_number=accession,
            raw_tags=(rule.raw_tag,),
        )
    if "segment" in entry or "dimensions" in entry:
        _fail(
            SecIngestionIssueCode.DIMENSIONAL_CONTEXT_UNSUPPORTED,
            f"Company Facts entry {rule.raw_tag} in {accession} contains dimensional context.",
            accession_number=accession,
            raw_tags=(rule.raw_tag,),
        )
    if unit not in rule.allowed_units:
        _fail(
            SecIngestionIssueCode.UNSUPPORTED_UNIT,
            f"Unit {unit!r} is not allowed for {rule.raw_tag}.",
            accession_number=accession,
            raw_tags=(rule.raw_tag,),
        )

    period_end = _parse_date(entry.get("end"), "end")
    period_start = _validate_geometry(rule, entry, period_end, accession)
    currency = "USD" if unit == "USD" else None
    lineage = FactLineage(
        source_adapter=SOURCE_ADAPTER,
        source_document_url=_source_document_url(cik, metadata),
        concept_map_version=concept_map.version,
        ingestion_batch_id=ingestion_batch_id,
        ingested_at=ingested_at,
    )
    return SecExtractedFact(
        entity_cik=cik,
        entity_name=entity_name,
        taxonomy=rule.taxonomy,
        raw_tag=rule.raw_tag,
        canonical_concept=rule.canonical_concept,
        statement_kind=rule.statement_kind,
        period_type=rule.period_type,
        period_start=period_start,
        period_end=period_end,
        value=_parse_decimal(entry.get("val"), accession, rule.raw_tag),
        unit=unit,
        currency=currency,
        provenance_accession_number=accession,
        form_type=metadata.form_type,
        is_amendment=metadata.form_type.endswith("/A"),
        filed_date=metadata.filed_date,
        accepted_at=metadata.accepted_at,
        report_date=metadata.report_date,
        primary_document=metadata.primary_document,
        filing_fiscal_year=_optional_fiscal_year(entry.get("fy"), accession, rule.raw_tag),
        filing_fiscal_period=_optional_text(entry.get("fp"), "fp", accession, rule.raw_tag),
        frame=_optional_text(entry.get("frame"), "frame", accession, rule.raw_tag),
        lineage=lineage,
    )


def _reject_synonym_conflicts(facts: Iterable[SecExtractedFact]) -> None:
    groups: Dict[Tuple[Any, ...], list[SecExtractedFact]] = {}
    for fact in facts:
        key = (
            fact.provenance_accession_number,
            fact.canonical_concept,
            fact.period_start,
            fact.period_end,
            fact.unit,
            fact.currency,
        )
        groups.setdefault(key, []).append(fact)
    for (accession, concept, *_geometry), group in groups.items():
        if len({fact.value for fact in group}) > 1:
            tags = tuple(fact.raw_tag for fact in group)
            _fail(
                SecIngestionIssueCode.SYNONYM_CONFLICT,
                f"Mapped synonyms disagree for {concept!r} in accession {accession}.",
                accession_number=accession,
                raw_tags=tags,
            )


def extract_sec_company_facts(
    company_facts: Mapping[str, Any],
    submissions: Iterable[Mapping[str, Any]],
    *,
    expected_cik: str,
    concept_map: ConceptMap,
    ingestion_batch_id: str,
    ingested_at: datetime,
    knowledge_cutoff: Optional[datetime] = None,
    period_end_floor: Optional[date] = None,
) -> SecExtractionResult:
    """Join SEC payloads, excluding future filings before fact consistency checks."""

    normalized_cik = normalize_cik(expected_cik)
    try:
        if not isinstance(ingested_at, datetime) or not is_aware(ingested_at):
            _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "ingested_at must be timezone-aware.")
        if knowledge_cutoff is not None and (
            not isinstance(knowledge_cutoff, datetime) or not is_aware(knowledge_cutoff)
        ):
            _fail(
                SecIngestionIssueCode.INVALID_PAYLOAD,
                "knowledge_cutoff must be timezone-aware when present.",
            )
        if period_end_floor is not None and (
            not isinstance(period_end_floor, date) or isinstance(period_end_floor, datetime)
        ):
            _fail(
                SecIngestionIssueCode.INVALID_PAYLOAD,
                "period_end_floor must be a date when present.",
            )
        if not isinstance(ingestion_batch_id, str) or not ingestion_batch_id.strip():
            _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "ingestion_batch_id must be non-empty.")
        if not isinstance(company_facts, Mapping):
            _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "Company Facts payload must be an object.")
        payload_cik = company_facts.get("cik")
        if isinstance(payload_cik, int) and not isinstance(payload_cik, bool):
            payload_cik = str(payload_cik)
        try:
            actual_cik = normalize_cik(payload_cik)
        except ValueError:
            _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "Company Facts payload has an invalid CIK.")
        if actual_cik != normalized_cik:
            _fail(
                SecIngestionIssueCode.ISSUER_MISMATCH,
                f"Company Facts CIK {actual_cik} does not match requested CIK {normalized_cik}.",
            )
        entity_name = company_facts.get("entityName")
        if not isinstance(entity_name, str) or not entity_name.strip():
            _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "Company Facts entityName must be non-empty.")
        fact_namespaces = company_facts.get("facts")
        if not isinstance(fact_namespaces, Mapping):
            _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "Company Facts facts must be an object.")

        mapped_entries = []
        for rule in concept_map.rules:
            namespace = fact_namespaces.get(rule.taxonomy)
            if namespace is None:
                continue
            if not isinstance(namespace, Mapping):
                _fail(
                    SecIngestionIssueCode.INVALID_PAYLOAD,
                    f"Company Facts namespace {rule.taxonomy!r} must be an object.",
                )
            tag_payload = namespace.get(rule.raw_tag)
            if tag_payload is None:
                continue
            if not isinstance(tag_payload, Mapping):
                _fail(SecIngestionIssueCode.INVALID_PAYLOAD, f"Tag {rule.raw_tag} must be an object.")
            units = tag_payload.get("units")
            if not isinstance(units, Mapping):
                _fail(
                    SecIngestionIssueCode.INVALID_PAYLOAD,
                    f"Tag {rule.raw_tag} units must be an object.",
                )
            for unit, entries in units.items():
                if not isinstance(unit, str):
                    _fail(SecIngestionIssueCode.INVALID_PAYLOAD, "Company Facts unit names must be text.")
                if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
                    _fail(
                        SecIngestionIssueCode.INVALID_PAYLOAD,
                        f"Entries for {rule.raw_tag}/{unit} must be an array.",
                    )
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        _fail(
                            SecIngestionIssueCode.INVALID_PAYLOAD,
                            f"Each {rule.raw_tag}/{unit} entry must be an object.",
                        )
                    raw_period_end = entry.get("end")
                    if period_end_floor is not None and isinstance(raw_period_end, str):
                        try:
                            parsed_period_end = date.fromisoformat(raw_period_end)
                        except ValueError:
                            parsed_period_end = None
                        if parsed_period_end is not None and parsed_period_end < period_end_floor:
                            continue
                    mapped_entries.append((rule, unit, entry))

        relevant_accessions = frozenset(
            entry.get("accn")
            for _rule, _unit, entry in mapped_entries
            if isinstance(entry.get("form"), str)
            and entry.get("form") in _SUPPORTED_FORMS
            and isinstance(entry.get("accn"), str)
        )
        metadata, future_accessions, outside_window_accessions = _submission_metadata(
            submissions,
            normalized_cik,
            relevant_accessions,
            knowledge_cutoff,
            period_end_floor,
        )
        extracted = []
        excluded_future_count = 0
        for rule, unit, entry in mapped_entries:
            accession = entry.get("accn")
            if accession in future_accessions:
                excluded_future_count += 1
                continue
            if accession in outside_window_accessions:
                continue
            fact = _extract_entry(
                entry=entry,
                rule=rule,
                unit=unit,
                cik=normalized_cik,
                entity_name=entity_name,
                metadata_by_accession=metadata,
                concept_map=concept_map,
                ingestion_batch_id=ingestion_batch_id,
                ingested_at=ingested_at,
            )
            if fact is not None:
                extracted.append(fact)
        if not extracted:
            if excluded_future_count:
                _fail(
                    SecIngestionIssueCode.NO_ELIGIBLE_FACTS,
                    "Company Facts payload contains no supported facts public by the knowledge cutoff.",
                )
            _fail(
                SecIngestionIssueCode.NO_MAPPED_FACTS,
                "Company Facts payload contains no supported mapped facts.",
            )
        _reject_synonym_conflicts(extracted)
        return SecExtractionResult(
            cik=normalized_cik,
            concept_map_version=concept_map.version,
            facts=tuple(sorted(extracted, key=_fact_sort_key)),
        )
    except _ExtractionFailure as exc:
        return SecExtractionResult(
            cik=normalized_cik,
            concept_map_version=concept_map.version,
            issues=(exc.issue,),
        )
