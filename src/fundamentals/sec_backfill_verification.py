"""Read-only verification that a published SEC backfill is point-in-time exact.

For each requested knowledge cutoff the command rebuilds the expected result
from a fresh SEC download through the same ingestion policy, then reads the
published facts back from PostgreSQL and requires:

* the published fact set equals the pipeline's classified facts exactly,
  including each filing's original SEC acceptance time;
* no published fact visible at the cutoff became public after it;
* every published fact belongs to the expected immutable ingestion batch;
* point-in-time selection over the published facts equals the pipeline's.

Like ``sec_pipeline_command`` this module needs only ``requests`` and
``psycopg2``; it never writes to the database.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional, Sequence, Tuple

from .adapters.sec_companyfacts import SOURCE_ADAPTER
from .adapters.sec_downloader import SecDownloader, SecDownloaderConfig
from .calendar_catalog import SEC_FISCAL_CALENDAR_CATALOG_V1
from .concept_map import concept_map_for_issuer
from .repository import FundamentalsQuery
from .sec_ingestion import run_sec_ingestion_dry_run
from .sec_pipeline_command import _parse_aware_datetime
from .selection import select_point_in_time
from .store import PostgresFundamentalsRepository
from .time_policy import is_aware
from .types import FinancialFact, FundamentalHistory, normalize_cik
from .valuation_snapshot import VALUATION_TTM_CONCEPTS

# Large enough to read an issuer's complete FY2020-onward history back.
_READBACK_PERIOD_LIMIT = 10_000
_NEUTRAL_INGESTED_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class CutoffVerification:
    knowledge_cutoff: datetime
    expected_fact_count: int
    published_fact_count: int
    problems: Tuple[str, ...]

    @property
    def is_verified(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class BackfillVerificationResult:
    cik: str
    ingestion_batch_id: str
    concept_map_version: str
    fiscal_calendar_version: str
    data_vintage_cutoff: datetime
    cutoffs: Tuple[CutoffVerification, ...]

    @property
    def is_verified(self) -> bool:
        return bool(self.cutoffs) and all(item.is_verified for item in self.cutoffs)


class _SinglePayloadDownloader:
    """Download once so every cutoff is checked against the same SEC snapshot."""

    def __init__(self, downloader):
        self._downloader = downloader
        self._payload = None

    def fetch_issuer(self, cik: str):
        if self._payload is None:
            self._payload = self._downloader.fetch_issuer(cik)
        return self._payload


def _source_key(fact: FinancialFact) -> FinancialFact:
    """The fact without ingestion bookkeeping, which differs between runs."""

    return replace(
        fact,
        lineage=replace(
            fact.lineage,
            ingestion_batch_id="-",
            ingested_at=_NEUTRAL_INGESTED_AT,
        ),
    )


def _selected_keys(history: FundamentalHistory) -> frozenset:
    facts = [
        fact
        for periods in (
            history.income_statement_periods,
            history.balance_sheet_periods,
            history.cash_flow_periods,
        )
        for period in periods
        for fact in period.facts
    ]
    facts.extend(history.cover_facts)
    return frozenset(_source_key(fact) for fact in facts)


def _verify_cutoff(
    *,
    cik: str,
    knowledge_cutoff: datetime,
    data_vintage_cutoff: datetime,
    ingestion_batch_id: str,
    downloader,
    repository,
) -> CutoffVerification:
    calendar_policy = SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(cik)
    concept_map = concept_map_for_issuer(cik)
    expected_run = run_sec_ingestion_dry_run(
        downloader=downloader,
        cik=cik,
        calendar_policy=calendar_policy,
        concept_map=concept_map,
        ingestion_batch_id=ingestion_batch_id,
        knowledge_cutoff=knowledge_cutoff,
        required_concepts=VALUATION_TTM_CONCEPTS,
    )
    if not expected_run.is_complete:
        issue = expected_run.issues[0]
        return CutoffVerification(
            knowledge_cutoff=knowledge_cutoff,
            expected_fact_count=0,
            published_fact_count=0,
            problems=(f"Pipeline refused at this cutoff ({issue.stage.value}: {issue.code}).",),
        )

    published = tuple(
        repository.get_facts(
            FundamentalsQuery(
                cik=cik,
                knowledge_cutoff=knowledge_cutoff,
                data_vintage_cutoff=data_vintage_cutoff,
                concepts=tuple(sorted({rule.canonical_concept for rule in concept_map.rules})),
                source_adapter=SOURCE_ADAPTER,
                concept_map_version=concept_map.version,
                fiscal_calendar_version=calendar_policy.version,
                max_periods_per_statement=_READBACK_PERIOD_LIMIT,
            )
        )
    )

    problems = []
    expected_keys = frozenset(_source_key(fact) for fact in expected_run.classified_facts)
    published_keys = frozenset(_source_key(fact) for fact in published)
    missing = len(expected_keys - published_keys)
    unexpected = len(published_keys - expected_keys)
    if missing or unexpected:
        problems.append(
            f"Published facts differ from the pipeline: {missing} missing, {unexpected} unexpected."
        )
    late = sum(1 for fact in published if fact.provenance.eligible_at > knowledge_cutoff)
    if late:
        problems.append(f"{late} published facts became public after the cutoff.")
    foreign_batches = sorted(
        {fact.lineage.ingestion_batch_id for fact in published} - {ingestion_batch_id}
    )
    if foreign_batches:
        problems.append(
            f"Published facts belong to other ingestion batches: {', '.join(foreign_batches)}."
        )
    if published and not problems:
        try:
            published_history = select_point_in_time(published, knowledge_cutoff, cik=cik)
        except ValueError:
            problems.append("Point-in-time selection refused the published facts.")
        else:
            if _selected_keys(published_history) != _selected_keys(expected_run.history):
                problems.append("Point-in-time selection differs from the pipeline's selection.")

    return CutoffVerification(
        knowledge_cutoff=knowledge_cutoff,
        expected_fact_count=len(expected_keys),
        published_fact_count=len(published_keys),
        problems=tuple(problems),
    )


def verify_published_backfill(
    *,
    cik: str,
    ingestion_batch_id: str,
    knowledge_cutoffs: Iterable[datetime],
    data_vintage_cutoff: datetime,
    downloader,
    repository,
) -> BackfillVerificationResult:
    normalized_cik = normalize_cik(cik)
    cutoffs = tuple(knowledge_cutoffs)
    if not cutoffs or any(not isinstance(value, datetime) or not is_aware(value) for value in cutoffs):
        raise ValueError("knowledge_cutoffs must contain timezone-aware datetimes.")
    if not isinstance(data_vintage_cutoff, datetime) or not is_aware(data_vintage_cutoff):
        raise ValueError("data_vintage_cutoff must be timezone-aware.")
    if not isinstance(ingestion_batch_id, str) or not ingestion_batch_id.strip():
        raise ValueError("ingestion_batch_id must be non-empty text.")
    shared_downloader = _SinglePayloadDownloader(downloader)
    return BackfillVerificationResult(
        cik=normalized_cik,
        ingestion_batch_id=ingestion_batch_id,
        concept_map_version=concept_map_for_issuer(normalized_cik).version,
        fiscal_calendar_version=SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(normalized_cik).version,
        data_vintage_cutoff=data_vintage_cutoff,
        cutoffs=tuple(
            _verify_cutoff(
                cik=normalized_cik,
                knowledge_cutoff=cutoff,
                data_vintage_cutoff=data_vintage_cutoff,
                ingestion_batch_id=ingestion_batch_id,
                downloader=shared_downloader,
                repository=repository,
            )
            for cutoff in sorted(cutoffs)
        ),
    )


def _summary(result: BackfillVerificationResult) -> dict:
    return {
        "status": "verified" if result.is_verified else "failed",
        "cik": result.cik,
        "batch_id": result.ingestion_batch_id,
        "concept_map_version": result.concept_map_version,
        "calendar_version": result.fiscal_calendar_version,
        "data_vintage_cutoff": result.data_vintage_cutoff.isoformat(),
        "cutoffs": [
            {
                "knowledge_cutoff": item.knowledge_cutoff.isoformat(),
                "expected_fact_count": item.expected_fact_count,
                "published_fact_count": item.published_fact_count,
                "problems": list(item.problems),
            }
            for item in result.cutoffs
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a published SEC backfill against fresh pipeline output; read-only."
    )
    parser.add_argument("--cik", required=True, help="SEC issuer CIK")
    parser.add_argument("--batch-id", required=True, help="the published ingestion batch ID")
    parser.add_argument(
        "--cutoff",
        required=True,
        action="append",
        type=_parse_aware_datetime,
        help="knowledge cutoff to verify, including timezone; repeat for each cutoff",
    )
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify_published_backfill(
            cik=args.cik,
            ingestion_batch_id=args.batch_id,
            knowledge_cutoffs=args.cutoff,
            data_vintage_cutoff=now(),
            downloader=SecDownloader(SecDownloaderConfig.from_environment()),
            repository=PostgresFundamentalsRepository(),
        )
    except (LookupError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "failed", "message": str(error)}, sort_keys=True))
        return 1

    print(json.dumps(_summary(result), sort_keys=True))
    return 0 if result.is_verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
