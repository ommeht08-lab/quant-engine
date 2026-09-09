"""Orchestration and controlled publishing for SEC fundamentals ingestion.

This module composes download, extraction, exact fiscal classification,
point-in-time selection, and quarterly assembly. Publishing is a separate,
explicit operation that accepts only a complete dry run; there is no workflow
or valuation behavior here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Iterable, Optional, Tuple

from .adapters.sec_companyfacts import SecIngestionIssueCode, extract_sec_company_facts
from .adapters.sec_downloader import SecDownloadError, SecIssuerPayload
from .concept_map import ConceptMap
from .fiscal_calendar import IssuerFiscalCalendarPolicy, classify_sec_facts
from .quarterly import QuarterlyFundamentals, assemble_quarterly_fundamentals
from .selection import select_point_in_time
from .store import FundamentalsPublishError, append_facts
from .time_policy import is_aware
from .types import FinancialFact, FundamentalHistory, normalize_cik


class SecDryRunIssueStage(str, Enum):
    DOWNLOAD = "download"
    EXTRACTION = "extraction"
    CUTOFF = "cutoff"
    CLASSIFICATION = "classification"
    SELECTION = "selection"
    QUARTERLY_ASSEMBLY = "quarterly_assembly"
    PUBLISH = "publish"


@dataclass(frozen=True)
class SecDryRunIssue:
    stage: SecDryRunIssueStage
    code: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.stage, SecDryRunIssueStage):
            raise ValueError("SecDryRunIssue.stage must be a SecDryRunIssueStage.")
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("SecDryRunIssue.code must be non-empty text.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("SecDryRunIssue.message must be non-empty text.")


@dataclass(frozen=True)
class SecIngestionDryRun:
    cik: str
    knowledge_cutoff: datetime
    downloaded_at: Optional[datetime]
    extracted_fact_count: int = 0
    eligible_fact_count: int = 0
    classified_facts: Tuple[FinancialFact, ...] = field(default_factory=tuple)
    history: Optional[FundamentalHistory] = None
    quarterly: Optional[QuarterlyFundamentals] = None
    issues: Tuple[SecDryRunIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if not isinstance(self.knowledge_cutoff, datetime) or not is_aware(
            self.knowledge_cutoff
        ):
            raise ValueError("knowledge_cutoff must be timezone-aware.")
        if self.downloaded_at is not None and (
            not isinstance(self.downloaded_at, datetime)
            or not is_aware(self.downloaded_at)
        ):
            raise ValueError("downloaded_at must be timezone-aware when present.")
        for field_name in ("extracted_fact_count", "eligible_fact_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer.")
        if self.eligible_fact_count > self.extracted_fact_count:
            raise ValueError("eligible_fact_count cannot exceed extracted_fact_count.")
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "classified_facts", tuple(self.classified_facts))
        if any(not isinstance(issue, SecDryRunIssue) for issue in self.issues):
            raise ValueError("issues must contain only SecDryRunIssue values.")
        if any(not isinstance(fact, FinancialFact) for fact in self.classified_facts):
            raise ValueError("classified_facts must contain only FinancialFact values.")
        succeeded = self.history is not None and self.quarterly is not None
        if succeeded == bool(self.issues):
            raise ValueError("A dry run must contain complete output or refusal issues.")
        if (self.history is None) != (self.quarterly is None):
            raise ValueError("Dry-run history and quarterly output must appear together.")
        if succeeded != bool(self.classified_facts):
            raise ValueError("Successful dry runs must preserve classified source facts.")
        if succeeded and len(self.classified_facts) != self.eligible_fact_count:
            raise ValueError("Successful dry runs must classify every eligible source fact.")

    @property
    def is_complete(self) -> bool:
        return self.history is not None


def _refusal(
    *,
    cik: str,
    knowledge_cutoff: datetime,
    stage: SecDryRunIssueStage,
    code: str,
    message: str,
    downloaded_at: Optional[datetime] = None,
    extracted_fact_count: int = 0,
    eligible_fact_count: int = 0,
) -> SecIngestionDryRun:
    return SecIngestionDryRun(
        cik=cik,
        knowledge_cutoff=knowledge_cutoff,
        downloaded_at=downloaded_at,
        extracted_fact_count=extracted_fact_count,
        eligible_fact_count=eligible_fact_count,
        issues=(SecDryRunIssue(stage, code, message),),
    )


def run_sec_ingestion_dry_run(
    *,
    downloader,
    cik: str,
    calendar_policy: IssuerFiscalCalendarPolicy,
    concept_map: ConceptMap,
    ingestion_batch_id: str,
    knowledge_cutoff: datetime,
    required_concepts: Iterable[str],
    optional_concepts: Iterable[str] = (),
) -> SecIngestionDryRun:
    """Run the complete non-publishing SEC path and return output or one refusal."""

    normalized_cik = normalize_cik(cik)
    if not isinstance(calendar_policy, IssuerFiscalCalendarPolicy):
        raise ValueError("calendar_policy must be an IssuerFiscalCalendarPolicy.")
    if calendar_policy.cik != normalized_cik:
        raise ValueError("calendar_policy must belong to the requested CIK.")
    if not isinstance(concept_map, ConceptMap):
        raise ValueError("concept_map must be a ConceptMap.")
    if not isinstance(ingestion_batch_id, str) or not ingestion_batch_id.strip():
        raise ValueError("ingestion_batch_id must be non-empty text.")
    if not is_aware(knowledge_cutoff):
        raise ValueError("knowledge_cutoff must be timezone-aware.")
    if not hasattr(downloader, "fetch_issuer") or not callable(downloader.fetch_issuer):
        raise ValueError("downloader must provide fetch_issuer(cik).")
    if isinstance(required_concepts, (str, bytes)):
        raise ValueError("required_concepts must be a collection of concept names.")
    try:
        required_concepts = tuple(required_concepts)
    except TypeError:
        raise ValueError("required_concepts must be a collection of concept names.") from None
    if not required_concepts or any(
        not isinstance(concept, str) or not concept.strip()
        for concept in required_concepts
    ):
        raise ValueError("required_concepts must contain non-empty concept names.")
    if len(required_concepts) != len(set(required_concepts)):
        raise ValueError("required_concepts must not contain duplicates.")
    if isinstance(optional_concepts, (str, bytes)):
        raise ValueError("optional_concepts must be a collection of concept names.")
    try:
        optional_concepts = tuple(optional_concepts)
    except TypeError:
        raise ValueError("optional_concepts must be a collection of concept names.") from None
    if any(
        not isinstance(concept, str) or not concept.strip()
        for concept in optional_concepts
    ):
        raise ValueError("optional_concepts must contain only non-empty concept names.")
    if len(optional_concepts) != len(set(optional_concepts)):
        raise ValueError("optional_concepts must not contain duplicates.")
    if set(required_concepts) & set(optional_concepts):
        raise ValueError("required_concepts and optional_concepts must not overlap.")

    try:
        payload = downloader.fetch_issuer(normalized_cik)
    except SecDownloadError as error:
        return _refusal(
            cik=normalized_cik,
            knowledge_cutoff=knowledge_cutoff,
            stage=SecDryRunIssueStage.DOWNLOAD,
            code=error.code.value,
            message=str(error),
        )
    if not isinstance(payload, SecIssuerPayload) or payload.cik != normalized_cik:
        return _refusal(
            cik=normalized_cik,
            knowledge_cutoff=knowledge_cutoff,
            stage=SecDryRunIssueStage.DOWNLOAD,
            code="invalid_payload_bundle",
            message="The downloader returned an invalid issuer payload bundle.",
        )

    extraction = extract_sec_company_facts(
        payload.company_facts,
        payload.submissions,
        expected_cik=normalized_cik,
        concept_map=concept_map,
        ingestion_batch_id=ingestion_batch_id,
        ingested_at=payload.downloaded_at,
        knowledge_cutoff=knowledge_cutoff,
        period_end_floor=min(
            definition.period_start
            for definition in (
                *calendar_policy.fiscal_years,
                *calendar_policy.transitions,
                *calendar_policy.open_fiscal_years,
            )
        ),
    )
    if not extraction.is_complete:
        issue = extraction.issues[0]
        if issue.code is SecIngestionIssueCode.NO_ELIGIBLE_FACTS:
            return _refusal(
                cik=normalized_cik,
                knowledge_cutoff=knowledge_cutoff,
                stage=SecDryRunIssueStage.CUTOFF,
                code=issue.code.value,
                message=issue.message,
                downloaded_at=payload.downloaded_at,
            )
        return _refusal(
            cik=normalized_cik,
            knowledge_cutoff=knowledge_cutoff,
            stage=SecDryRunIssueStage.EXTRACTION,
            code=issue.code.value,
            message=issue.message,
            downloaded_at=payload.downloaded_at,
        )

    eligible_facts = tuple(
        fact for fact in extraction.facts if fact.accepted_at <= knowledge_cutoff
    )
    if not eligible_facts:
        return _refusal(
            cik=normalized_cik,
            knowledge_cutoff=knowledge_cutoff,
            stage=SecDryRunIssueStage.CUTOFF,
            code="no_eligible_facts",
            message="No extracted SEC facts were public by the requested knowledge cutoff.",
            downloaded_at=payload.downloaded_at,
            extracted_fact_count=len(extraction.facts),
        )

    classification = classify_sec_facts(eligible_facts, calendar_policy)
    if not classification.is_complete:
        issue = classification.issues[0]
        return _refusal(
            cik=normalized_cik,
            knowledge_cutoff=knowledge_cutoff,
            stage=SecDryRunIssueStage.CLASSIFICATION,
            code=issue.code.value,
            message=issue.message,
            downloaded_at=payload.downloaded_at,
            extracted_fact_count=len(extraction.facts),
            eligible_fact_count=len(eligible_facts),
        )

    try:
        history = select_point_in_time(
            classification.facts,
            knowledge_cutoff,
            cik=normalized_cik,
        )
    except ValueError:
        return _refusal(
            cik=normalized_cik,
            knowledge_cutoff=knowledge_cutoff,
            stage=SecDryRunIssueStage.SELECTION,
            code="inconsistent_point_in_time_facts",
            message="Eligible classified facts are structurally inconsistent.",
            downloaded_at=payload.downloaded_at,
            extracted_fact_count=len(extraction.facts),
            eligible_fact_count=len(eligible_facts),
        )

    quarterly = assemble_quarterly_fundamentals(
        history,
        required_concepts=required_concepts,
        optional_concepts=optional_concepts,
    )
    if not quarterly.is_complete:
        issue = quarterly.issues[0]
        return _refusal(
            cik=normalized_cik,
            knowledge_cutoff=knowledge_cutoff,
            stage=SecDryRunIssueStage.QUARTERLY_ASSEMBLY,
            code=issue.code.value,
            message=issue.message,
            downloaded_at=payload.downloaded_at,
            extracted_fact_count=len(extraction.facts),
            eligible_fact_count=len(eligible_facts),
        )

    return SecIngestionDryRun(
        cik=normalized_cik,
        knowledge_cutoff=knowledge_cutoff,
        downloaded_at=payload.downloaded_at,
        extracted_fact_count=len(extraction.facts),
        eligible_fact_count=len(eligible_facts),
        classified_facts=classification.facts,
        history=history,
        quarterly=quarterly,
    )


@dataclass(frozen=True)
class SecIngestionPublishResult:
    dry_run: SecIngestionDryRun
    inserted_fact_count: int = 0
    issues: Tuple[SecDryRunIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.dry_run, SecIngestionDryRun) or not self.dry_run.is_complete:
            raise ValueError("Publishing requires one complete SEC ingestion dry run.")
        if (
            isinstance(self.inserted_fact_count, bool)
            or not isinstance(self.inserted_fact_count, int)
            or self.inserted_fact_count < 0
        ):
            raise ValueError("inserted_fact_count must be a non-negative integer.")
        object.__setattr__(self, "issues", tuple(self.issues))
        if any(not isinstance(issue, SecDryRunIssue) for issue in self.issues):
            raise ValueError("issues must contain only SecDryRunIssue values.")
        if self.issues and self.inserted_fact_count:
            raise ValueError("A refused publish cannot report inserted facts.")
        if self.inserted_fact_count > len(self.dry_run.classified_facts):
            raise ValueError("inserted_fact_count cannot exceed the published fact count.")

    @property
    def is_complete(self) -> bool:
        return not self.issues


def publish_sec_ingestion_dry_run(
    dry_run: SecIngestionDryRun,
    *,
    publisher: Callable[[Tuple[FinancialFact, ...]], int] = append_facts,
) -> SecIngestionPublishResult:
    """Atomically publish one previously verified run through an injected seam."""

    if not isinstance(dry_run, SecIngestionDryRun) or not dry_run.is_complete:
        raise ValueError("dry_run must be a complete SecIngestionDryRun.")
    if not callable(publisher):
        raise ValueError("publisher must be callable.")
    try:
        inserted = publisher(dry_run.classified_facts)
    except FundamentalsPublishError:
        return SecIngestionPublishResult(
            dry_run=dry_run,
            issues=(
                SecDryRunIssue(
                    SecDryRunIssueStage.PUBLISH,
                    "publish_failed",
                    "The complete SEC ingestion batch could not be published atomically.",
                ),
            ),
        )
    if (
        isinstance(inserted, bool)
        or not isinstance(inserted, int)
        or inserted < 0
        or inserted > len(dry_run.classified_facts)
    ):
        raise ValueError("publisher must return a non-negative inserted fact count.")
    return SecIngestionPublishResult(dry_run=dry_run, inserted_fact_count=inserted)
