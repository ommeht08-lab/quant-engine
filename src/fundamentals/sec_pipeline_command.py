"""Offline command boundary for SEC ingestion and controlled publication.

Dry-run is the default. Network access and database writes live only behind
this operator-invoked module; request handlers never import or call it.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Sequence

from .adapters.sec_downloader import SecDownloader, SecDownloaderConfig
from .calendar_catalog import SEC_FISCAL_CALENDAR_CATALOG_V1
from .concept_map import SEC_CONCEPT_MAP_V2
from .sec_ingestion import (
    SecIngestionDryRun,
    SecIngestionPublishResult,
    publish_sec_ingestion_dry_run,
    run_sec_ingestion_dry_run,
)
from .store import append_facts
from .time_policy import is_aware
from .types import normalize_cik
from .valuation_snapshot import VALUATION_TTM_CONCEPTS


@dataclass(frozen=True)
class OfflineSecIngestionRequest:
    """One reproducible operator request; publication must be explicit."""

    cik: str
    knowledge_cutoff: datetime
    ingestion_batch_id: str
    publish: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if not isinstance(self.knowledge_cutoff, datetime) or not is_aware(
            self.knowledge_cutoff
        ):
            raise ValueError("knowledge_cutoff must be a timezone-aware datetime.")
        if (
            not isinstance(self.ingestion_batch_id, str)
            or not self.ingestion_batch_id.strip()
            or self.ingestion_batch_id != self.ingestion_batch_id.strip()
        ):
            raise ValueError("ingestion_batch_id must be non-empty, trimmed text.")
        if not isinstance(self.publish, bool):
            raise ValueError("publish must be boolean.")


@dataclass(frozen=True)
class OfflineSecIngestionResult:
    request: OfflineSecIngestionRequest
    dry_run: SecIngestionDryRun
    publish_result: Optional[SecIngestionPublishResult] = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, OfflineSecIngestionRequest):
            raise ValueError("request must be an OfflineSecIngestionRequest.")
        if not isinstance(self.dry_run, SecIngestionDryRun):
            raise ValueError("dry_run must be a SecIngestionDryRun.")
        if self.publish_result is not None and not isinstance(
            self.publish_result, SecIngestionPublishResult
        ):
            raise ValueError("publish_result must be a SecIngestionPublishResult.")
        if not self.request.publish and self.publish_result is not None:
            raise ValueError("A dry-run-only request cannot contain a publish result.")
        if self.request.publish and self.dry_run.is_complete != (
            self.publish_result is not None
        ):
            raise ValueError("A complete publish request must contain its publish result.")

    @property
    def is_complete(self) -> bool:
        if not self.dry_run.is_complete:
            return False
        return self.publish_result is None or self.publish_result.is_complete


def run_offline_sec_ingestion(
    request: OfflineSecIngestionRequest,
    *,
    downloader,
    publisher: Optional[Callable] = None,
) -> OfflineSecIngestionResult:
    """Execute one offline run, publishing only after a complete dry run."""

    if not isinstance(request, OfflineSecIngestionRequest):
        raise ValueError("request must be an OfflineSecIngestionRequest.")
    policy = SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(request.cik)
    dry_run = run_sec_ingestion_dry_run(
        downloader=downloader,
        cik=request.cik,
        calendar_policy=policy,
        concept_map=SEC_CONCEPT_MAP_V2,
        ingestion_batch_id=request.ingestion_batch_id,
        knowledge_cutoff=request.knowledge_cutoff,
        required_concepts=VALUATION_TTM_CONCEPTS,
    )
    if not request.publish or not dry_run.is_complete:
        return OfflineSecIngestionResult(request=request, dry_run=dry_run)
    publish_result = publish_sec_ingestion_dry_run(
        dry_run,
        publisher=publisher or append_facts,
    )
    return OfflineSecIngestionResult(
        request=request,
        dry_run=dry_run,
        publish_result=publish_result,
    )


def _parse_aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError(
            "must be an ISO-8601 datetime with an explicit timezone"
        ) from None
    if not is_aware(parsed):
        raise argparse.ArgumentTypeError("must include an explicit timezone")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download and validate SEC fundamentals offline; dry-run by default."
    )
    parser.add_argument("--cik", required=True, help="SEC issuer CIK")
    parser.add_argument(
        "--knowledge-cutoff",
        required=True,
        type=_parse_aware_datetime,
        help="point-in-time cutoff, including timezone",
    )
    parser.add_argument("--batch-id", required=True, help="immutable ingestion batch ID")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="publish the verified batch; omission guarantees no database write",
    )
    return parser


def _summary(result: OfflineSecIngestionResult) -> dict:
    dry_run = result.dry_run
    summary = {
        "mode": "publish" if result.request.publish else "dry_run",
        "status": "complete" if result.is_complete else "refused",
        "cik": result.request.cik,
        "knowledge_cutoff": result.request.knowledge_cutoff.isoformat(),
        "batch_id": result.request.ingestion_batch_id,
        "calendar_version": SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(
            result.request.cik
        ).version,
        "concept_map_version": SEC_CONCEPT_MAP_V2.version,
        "downloaded_at": (
            dry_run.downloaded_at.isoformat() if dry_run.downloaded_at is not None else None
        ),
        "extracted_fact_count": dry_run.extracted_fact_count,
        "eligible_fact_count": dry_run.eligible_fact_count,
        "classified_fact_count": len(dry_run.classified_facts),
    }
    if dry_run.issues:
        issue = dry_run.issues[0]
        summary["refusal"] = {
            "stage": issue.stage.value,
            "code": issue.code,
            "message": issue.message,
        }
    if result.publish_result is not None:
        summary["inserted_fact_count"] = result.publish_result.inserted_fact_count
        if result.publish_result.issues:
            issue = result.publish_result.issues[0]
            summary["refusal"] = {
                "stage": issue.stage.value,
                "code": issue.code,
                "message": issue.message,
            }
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        request = OfflineSecIngestionRequest(
            cik=args.cik,
            knowledge_cutoff=args.knowledge_cutoff,
            ingestion_batch_id=args.batch_id,
            publish=args.publish,
        )
        downloader = SecDownloader(SecDownloaderConfig.from_environment())
        publisher = None
        if request.publish:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError(
                    "DATABASE_URL must be set in the process environment for --publish."
                )
            publisher = lambda facts: append_facts(facts, database_url=database_url)
        result = run_offline_sec_ingestion(
            request,
            downloader=downloader,
            publisher=publisher,
        )
    except (LookupError, ValueError) as error:
        print(json.dumps({"status": "refused", "message": str(error)}, sort_keys=True))
        return 1

    print(json.dumps(_summary(result), sort_keys=True))
    return 0 if result.is_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
