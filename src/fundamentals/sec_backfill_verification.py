"""Read-only verification that a published SEC backfill is point-in-time exact.

For each requested knowledge cutoff the command rebuilds the expected result
from a fresh SEC download through the same ingestion policy, then reads the
published facts back from PostgreSQL and requires:

* the published fact set equals the pipeline's classified facts exactly,
  including each filing's original SEC acceptance time;
* no published fact visible at the cutoff became public after it;
* every published fact belongs to the expected immutable ingestion batches:
  the requested ID for Company Facts and, for issuers with filing-XBRL
  compositions, exactly its source-qualified ID for the composed facts;
* point-in-time selection over the published facts equals the pipeline's.

``--mode refresh`` verifies a recurring refresh instead. A refresh republishes
the complete history under a new batch ID, but facts already stored keep
their earlier batches, so batch membership is checked by lineage rather than
by the supplied ID: every published fact's batch row must match it, and every
supplemental batch must pair with a primary batch of equal metadata. The
report separates facts inserted into this refresh's batches from facts that
remain in earlier batches, and at each historical cutoff this refresh may
contribute no facts at all. This runs after commit, so it detects problems;
the pre-commit proof in ``incremental_publication`` is what prevents them.

The batch table has no issuer column, so a written refresh is tied to the
issuer only through its facts: its batches must hold at least one fact, every
one for the requested issuer. A no-op refresh is rolled back and writes no
batch rows; the verifier then reports ``batch_written: false`` and verifies the
issuer's stored result directly. It does not verify any batch, and the absence
of rows alone cannot show that the publish step ran.

Like ``sec_pipeline_command`` this module needs only ``requests`` and
``psycopg2``; it never writes to the database.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping, Optional, Sequence, Tuple

from .adapters.sec_companyfacts import SOURCE_ADAPTER
from .adapters.sec_downloader import SecDownloader, SecDownloaderConfig
from .adapters.sec_filing_xbrl import SOURCE_ADAPTER as FILING_XBRL_SOURCE_ADAPTER
from .calendar_catalog import SEC_FISCAL_CALENDAR_CATALOG_V1
from .concept_map import concept_map_for_issuer
from .incremental_publication import (
    batch_ids_to_read,
    batch_lineage_problems,
    publication_batch_problems,
    publication_issuer_problems,
    read_batch_fact_ciks,
    read_batch_rows,
    selected_source_keys,
    source_key,
)
from .repository import FundamentalsQuery
from .sec_ingestion import run_sec_ingestion_dry_run
from .sec_pipeline_command import _parse_aware_datetime
from .selection import select_point_in_time
from .store import PostgresFundamentalsRepository, source_qualified_batch_id
from .time_policy import is_aware
from .types import normalize_cik
from .valuation_snapshot import VALUATION_TTM_CONCEPTS

# Large enough to read an issuer's complete FY2020-onward history back.
_READBACK_PERIOD_LIMIT = 10_000


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
    ingestion_batch_ids: Tuple[str, ...]
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
        self._instances = {}

    def fetch_issuer(self, cik: str):
        if self._payload is None:
            self._payload = self._downloader.fetch_issuer(cik)
        return self._payload

    def fetch_filing_instance(self, cik: str, accession_number: str):
        """Cached so every cutoff composes from the same filing documents."""

        key = (cik, accession_number)
        if key not in self._instances:
            self._instances[key] = self._downloader.fetch_filing_instance(cik, accession_number)
        return self._instances[key]


# One definition of "the same fact" for the pre-commit proof and this verifier.
_source_key = source_key
_selected_keys = selected_source_keys


def _read_published(*, cik: str, knowledge_cutoff: datetime, data_vintage_cutoff: datetime, repository):
    calendar_policy = SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(cik)
    concept_map = concept_map_for_issuer(cik)
    return tuple(
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
                supplemental_source_adapters=(
                    (FILING_XBRL_SOURCE_ADAPTER,) if concept_map.balance_compositions_for(cik) else ()
                ),
            )
        )
    )


def _expected_run(*, cik: str, knowledge_cutoff: datetime, ingestion_batch_id: str, downloader):
    return run_sec_ingestion_dry_run(
        downloader=downloader,
        cik=cik,
        calendar_policy=SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(cik),
        concept_map=concept_map_for_issuer(cik),
        ingestion_batch_id=ingestion_batch_id,
        knowledge_cutoff=knowledge_cutoff,
        required_concepts=VALUATION_TTM_CONCEPTS,
        filing_instance_fetcher=getattr(downloader, "fetch_filing_instance", None),
    )


def _verify_cutoff(
    *,
    cik: str,
    knowledge_cutoff: datetime,
    data_vintage_cutoff: datetime,
    ingestion_batch_id: str,
    downloader,
    repository,
) -> CutoffVerification:
    expected_run = _expected_run(
        cik=cik,
        knowledge_cutoff=knowledge_cutoff,
        ingestion_batch_id=ingestion_batch_id,
        downloader=downloader,
    )
    if not expected_run.is_complete:
        issue = expected_run.issues[0]
        return CutoffVerification(
            knowledge_cutoff=knowledge_cutoff,
            expected_fact_count=0,
            published_fact_count=0,
            problems=(f"Pipeline refused at this cutoff ({issue.stage.value}: {issue.code}).",),
        )

    published = _read_published(
        cik=cik,
        knowledge_cutoff=knowledge_cutoff,
        data_vintage_cutoff=data_vintage_cutoff,
        repository=repository,
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
    expected_batches = _expected_batch_ids(cik, ingestion_batch_id)
    batch_by_source = dict(zip(_expected_sources(cik), expected_batches))
    misfiled = sum(
        1 for fact in published
        if batch_by_source.get(fact.lineage.source_adapter) != fact.lineage.ingestion_batch_id
    )
    published_batches = {fact.lineage.ingestion_batch_id for fact in published}
    expected_present = {fact.lineage.ingestion_batch_id for fact in expected_run.classified_facts}
    if not misfiled and published and published_batches != expected_present:
        problems.append(
            "Published ingestion batches "
            f"{', '.join(sorted(published_batches))} differ from the expected "
            f"{', '.join(sorted(expected_present))}."
        )
    if misfiled:
        foreign = sorted({fact.lineage.ingestion_batch_id for fact in published} - set(expected_batches))
        problems.append(
            f"{misfiled} published facts are not in their source's expected ingestion batch"
            + (f" (they belong to other ingestion batches: {', '.join(foreign)})." if foreign else ".")
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


def _expected_sources(cik: str) -> Tuple[str, ...]:
    if concept_map_for_issuer(cik).balance_compositions_for(cik):
        return (SOURCE_ADAPTER, FILING_XBRL_SOURCE_ADAPTER)
    return (SOURCE_ADAPTER,)


def _expected_batch_ids(cik: str, ingestion_batch_id: str) -> Tuple[str, ...]:
    """The primary ID, then one source-qualified ID per supplemental source."""

    return tuple(
        ingestion_batch_id if source == SOURCE_ADAPTER else source_qualified_batch_id(ingestion_batch_id, source)
        for source in _expected_sources(cik)
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
        ingestion_batch_ids=_expected_batch_ids(normalized_cik, ingestion_batch_id),
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


@dataclass(frozen=True)
class RefreshCutoffVerification:
    knowledge_cutoff: datetime
    historical: bool
    expected_fact_count: int
    published_fact_count: int
    # Facts visible at this cutoff that this refresh inserted into its own
    # batches, versus facts that remain in earlier batches.
    this_refresh_fact_count: int
    earlier_batch_fact_count: int
    problems: Tuple[str, ...]

    @property
    def is_verified(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class RefreshVerificationResult:
    cik: str
    ingestion_batch_id: str
    ingestion_batch_ids: Tuple[str, ...]
    concept_map_version: str
    fiscal_calendar_version: str
    data_vintage_cutoff: datetime
    batch_written: bool
    publication_problems: Tuple[str, ...]
    cutoffs: Tuple[RefreshCutoffVerification, ...]

    @property
    def is_verified(self) -> bool:
        return (
            not self.publication_problems
            and bool(self.cutoffs)
            and all(item.is_verified for item in self.cutoffs)
        )

    @property
    def is_no_op(self) -> bool:
        """No rows exist under this batch ID: a no-op refresh is rolled back and writes nothing."""

        return not self.batch_written


class _CachingBatchReader:
    """Read each batch row once per verification, however many cutoffs need it."""

    def __init__(self, reader):
        self._reader = reader
        self._rows = {}
        self._read = set()

    def __call__(self, batch_ids):
        missing = sorted(set(batch_ids) - self._read)
        if missing:
            self._rows.update(self._reader(missing))
            self._read.update(missing)
        return {batch_id: self._rows[batch_id] for batch_id in batch_ids if batch_id in self._rows}


def _verify_refresh_cutoff(
    *,
    cik: str,
    knowledge_cutoff: datetime,
    historical: bool,
    data_vintage_cutoff: datetime,
    ingestion_batch_id: str,
    downloader,
    repository,
    batch_reader,
) -> RefreshCutoffVerification:
    expected_run = _expected_run(
        cik=cik,
        knowledge_cutoff=knowledge_cutoff,
        ingestion_batch_id=ingestion_batch_id,
        downloader=downloader,
    )
    if not expected_run.is_complete:
        issue = expected_run.issues[0]
        return RefreshCutoffVerification(
            knowledge_cutoff=knowledge_cutoff,
            historical=historical,
            expected_fact_count=0,
            published_fact_count=0,
            this_refresh_fact_count=0,
            earlier_batch_fact_count=0,
            problems=(f"Pipeline refused at this cutoff ({issue.stage.value}: {issue.code}).",),
        )
    published = _read_published(
        cik=cik,
        knowledge_cutoff=knowledge_cutoff,
        data_vintage_cutoff=data_vintage_cutoff,
        repository=repository,
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
    problems.extend(batch_lineage_problems(published, batch_reader(batch_ids_to_read(published))))
    this_refresh = frozenset(_expected_batch_ids(cik, ingestion_batch_id))
    in_this_refresh = sum(1 for fact in published if fact.lineage.ingestion_batch_id in this_refresh)
    if historical and in_this_refresh:
        problems.append(
            f"{in_this_refresh} facts in this refresh's batches are visible at this historical cutoff."
        )
    if published and not problems:
        try:
            published_history = select_point_in_time(published, knowledge_cutoff, cik=cik)
        except ValueError:
            problems.append("Point-in-time selection refused the published facts.")
        else:
            if _selected_keys(published_history) != _selected_keys(expected_run.history):
                problems.append("Point-in-time selection differs from the pipeline's selection.")

    return RefreshCutoffVerification(
        knowledge_cutoff=knowledge_cutoff,
        historical=historical,
        expected_fact_count=len(expected_keys),
        published_fact_count=len(published_keys),
        this_refresh_fact_count=in_this_refresh,
        earlier_batch_fact_count=len(published) - in_this_refresh,
        problems=tuple(problems),
    )


def verify_refresh_publication(
    *,
    cik: str,
    ingestion_batch_id: str,
    publish_cutoff: datetime,
    historical_cutoffs: Iterable[datetime],
    data_vintage_cutoff: datetime,
    downloader,
    repository,
    batch_reader: Callable[[Sequence[str]], Mapping[str, tuple]],
    batch_fact_reader: Callable[[Sequence[str]], Mapping[str, Mapping[str, int]]],
) -> RefreshVerificationResult:
    """Verify one committed refresh by lineage; read-only and after the fact."""

    normalized_cik = normalize_cik(cik)
    historical = tuple(historical_cutoffs)
    for value in historical + (publish_cutoff,):
        if not isinstance(value, datetime) or not is_aware(value):
            raise ValueError("cutoffs must be timezone-aware datetimes.")
    if any(value >= publish_cutoff for value in historical):
        raise ValueError("historical cutoffs must precede the publish cutoff.")
    if not isinstance(data_vintage_cutoff, datetime) or not is_aware(data_vintage_cutoff):
        raise ValueError("data_vintage_cutoff must be timezone-aware.")
    if not isinstance(ingestion_batch_id, str) or not ingestion_batch_id.strip():
        raise ValueError("ingestion_batch_id must be non-empty text.")
    batch_ids = _expected_batch_ids(normalized_cik, ingestion_batch_id)
    shared_downloader = _SinglePayloadDownloader(downloader)
    batch_reader = _CachingBatchReader(batch_reader)
    rows = batch_reader(batch_ids)
    batch_written = any(batch_id in rows for batch_id in batch_ids)
    publication_problems: Tuple[str, ...] = ()
    if batch_written:
        publication_problems = publication_batch_problems(batch_ids, rows) + publication_issuer_problems(
            normalized_cik, batch_ids, batch_fact_reader(batch_ids)
        )
    checks = [(cutoff, True) for cutoff in sorted(historical)] + [(publish_cutoff, False)]
    return RefreshVerificationResult(
        cik=normalized_cik,
        ingestion_batch_id=ingestion_batch_id,
        ingestion_batch_ids=batch_ids,
        concept_map_version=concept_map_for_issuer(normalized_cik).version,
        fiscal_calendar_version=SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(normalized_cik).version,
        data_vintage_cutoff=data_vintage_cutoff,
        batch_written=batch_written,
        publication_problems=publication_problems,
        cutoffs=tuple(
            _verify_refresh_cutoff(
                cik=normalized_cik,
                knowledge_cutoff=cutoff,
                historical=is_historical,
                data_vintage_cutoff=data_vintage_cutoff,
                ingestion_batch_id=ingestion_batch_id,
                downloader=shared_downloader,
                repository=repository,
                batch_reader=batch_reader,
            )
            for cutoff, is_historical in checks
        ),
    )


def _refresh_summary(result: RefreshVerificationResult) -> dict:
    return {
        "mode": "refresh",
        "status": "verified" if result.is_verified else "failed",
        "cik": result.cik,
        "batch_id": result.ingestion_batch_id,
        "batch_ids": list(result.ingestion_batch_ids),
        "concept_map_version": result.concept_map_version,
        "calendar_version": result.fiscal_calendar_version,
        "data_vintage_cutoff": result.data_vintage_cutoff.isoformat(),
        "no_op": result.is_no_op,
        # False means no rows exist under this ID: only the issuer's stored
        # result was verified, not a batch.
        "batch_written": result.batch_written,
        "publication_problems": list(result.publication_problems),
        "cutoffs": [
            {
                "knowledge_cutoff": item.knowledge_cutoff.isoformat(),
                "role": "historical" if item.historical else "publish",
                "expected_fact_count": item.expected_fact_count,
                "published_fact_count": item.published_fact_count,
                "facts_inserted_by_this_refresh": item.this_refresh_fact_count,
                "facts_in_earlier_batches": item.earlier_batch_fact_count,
                "problems": list(item.problems),
            }
            for item in result.cutoffs
        ],
    }


def _summary(result: BackfillVerificationResult) -> dict:
    return {
        "status": "verified" if result.is_verified else "failed",
        "cik": result.cik,
        "batch_id": result.ingestion_batch_id,
        "batch_ids": list(result.ingestion_batch_ids),
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
        help=(
            "knowledge cutoff to verify, including timezone; repeat for each cutoff. "
            "In refresh mode these are historical cutoffs the refresh may not change."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("backfill", "refresh"),
        default="backfill",
        help="backfill: every fact is in the supplied batch; refresh: batches are checked by lineage",
    )
    parser.add_argument(
        "--publish-cutoff",
        type=_parse_aware_datetime,
        help="refresh mode only: the refresh's own knowledge cutoff",
    )
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    args = _parser().parse_args(argv)
    try:
        # Passed explicitly, like the publication command, so the read path
        # never falls back to the optional dotenv loader in slim CI jobs.
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL must be set in the process environment.")
        if (args.mode == "refresh") != (args.publish_cutoff is not None):
            raise ValueError("--publish-cutoff is required in refresh mode and only there.")
        downloader = SecDownloader(SecDownloaderConfig.from_environment())
        repository = PostgresFundamentalsRepository(database_url=database_url)
        if args.mode == "refresh":
            result = verify_refresh_publication(
                cik=args.cik,
                ingestion_batch_id=args.batch_id,
                publish_cutoff=args.publish_cutoff,
                historical_cutoffs=args.cutoff,
                data_vintage_cutoff=now(),
                downloader=downloader,
                repository=repository,
                batch_reader=lambda ids: read_batch_rows(ids, database_url=database_url),
                batch_fact_reader=lambda ids: read_batch_fact_ciks(ids, database_url=database_url),
            )
            summary = _refresh_summary(result)
        else:
            result = verify_published_backfill(
                cik=args.cik,
                ingestion_batch_id=args.batch_id,
                knowledge_cutoffs=args.cutoff,
                data_vintage_cutoff=now(),
                downloader=downloader,
                repository=repository,
            )
            summary = _summary(result)
    except (LookupError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "failed", "message": str(error)}, sort_keys=True))
        return 1

    print(json.dumps(summary, sort_keys=True))
    return 0 if result.is_verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
