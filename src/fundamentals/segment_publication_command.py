"""Offline command boundary for publishing segment facts from a saved SEC capture.

Dry-run is the default. The command reads a directory of SEC documents that
were captured earlier (``issuer.json`` with the submissions payload,
``report.json`` recording each document's URL, SHA-256, and capture time, and
the documents themselves), verifies every document against its recorded hash,
and runs ``run_segment_document_dry_run`` over it. Nothing is downloaded.

Timestamps stay truthful. The submissions download time and each document's
SEC capture time are reported from the capture record. The facts' ingestion
time is the latest moment this process read a document, never the earlier
SEC capture time: the data-vintage cutoff describes when facts existed in our
dataset, so stamping them with the capture time would make them retroactively
visible to runs made before they were published.

Any supplied ``--manifest`` must match: the documents and a digest of the
facts (everything but ingestion batch and time), or the command refuses with
a nonzero exit before any database connection. ``--publish`` also needs
``DATABASE_URL`` in the process environment and publishes through
``incremental_publication.publish_incremental``, which proves the increment
before commit; it then reads back the stored batch metadata to report the
batches holding the facts with their own ingestion times (after a replay,
the original batch, not this run's read time). A failure in that read-back
is reported separately from a publication failure: once ``publish_incremental``
has returned, the facts are safely stored (inserted, or a genuine no-op
replay/reuse of an earlier publication), and this command reports
``"status": "published_unverified"`` with the same ``publication`` receipt,
a nonzero exit, and a read-only way to recover the metadata -- never
``"refused"``, and never an instruction to publish again. ``--verify`` is
read-only: it checks that the stored batch ``--batch-id`` holds exactly the
reviewed facts under one ingestion time matching its batch row, then both
cutoffs and their boundaries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .adapters.sec_downloader import SecDownloadError, SecDownloadErrorCode, SecIssuerPayload
from .calendar_catalog import SEC_FISCAL_CALENDAR_CATALOG_V1
from .incremental_publication import IncrementalPublicationError, publish_incremental
from .repository import FundamentalsQuery, FundamentalsRepositoryUnavailable
from .segment_gross_margin import (
    SOURCE_ADAPTER,
    SegmentDocumentDryRun,
    SegmentGrossMarginRefusal,
    SegmentMarginSource,
    load_segment_gross_margin_pair,
    periodic_filing_documents,
    run_segment_document_dry_run,
    segment_gross_margin_rule_for,
)
from .store import FundamentalsPublishError, _source_identity_key, source_qualified_batch_id
from .time_policy import is_aware
from .types import FinancialFact, normalize_cik

_ONE_MICROSECOND = timedelta(microseconds=1)


class SavedCaptureDownloader:
    """Serves a saved SEC capture; a document whose bytes differ from the
    capture record is unavailable, never silently read."""

    def __init__(self, capture_dir: Path):
        self._dir = Path(capture_dir)
        issuer = json.loads((self._dir / "issuer.json").read_text())
        report = json.loads((self._dir / "report.json").read_text())
        self.payload = SecIssuerPayload(
            cik=issuer["cik"],
            company_facts=issuer["company_facts"],
            submissions=tuple(issuer["submissions"]),
            company_facts_url=issuer["company_facts_url"],
            submission_urls=tuple(issuer["submission_urls"]),
            downloaded_at=datetime.fromisoformat(issuer["downloaded_at"]),
        )
        self.captured = {item["accession_number"]: item for item in report["documents"]}

    def fetch_issuer(self, cik: str) -> SecIssuerPayload:
        if normalize_cik(cik) != self.payload.cik:
            raise SecDownloadError(SecDownloadErrorCode.NETWORK_FAILURE, "The capture is for another issuer.")
        return self.payload

    def fetch_filing_document(self, cik: str, accession_number: str, document_name: str) -> Tuple[str, bytes]:
        record = self.captured.get(accession_number)
        path = self._dir / f"{accession_number}-{document_name}"
        if record is None or not path.is_file():
            raise SecDownloadError(SecDownloadErrorCode.NETWORK_FAILURE, f"{accession_number} is not in the capture.")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != record["sha256"]:
            raise SecDownloadError(
                SecDownloadErrorCode.NETWORK_FAILURE, f"{accession_number}: saved bytes differ from the capture record."
            )
        return record["url"], body


def _canonical(value: Any) -> Any:
    """One text form per value: instants in UTC, decimals without exponent
    or trailing zeros (a database round trip changes neither's meaning)."""

    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value if value is None or isinstance(value, (bool, int)) else str(value)


def fact_digest(facts: Iterable[FinancialFact]) -> str:
    """SHA-256 over every fact's stored identity, value, provenance, and
    classification; only the ingestion batch and time are left out, so a
    reviewed dry run and a later publication of the same capture agree, and
    so do facts read back from the store."""

    lines = sorted(
        json.dumps(
            [
                _canonical(_source_identity_key(fact)),
                _canonical(fact.value),
                fact.period.fiscal_year,
                fact.period.fiscal_period,
                fact.period.periodicity,
                fact.provenance.form_type,
                fact.provenance.is_amendment,
                fact.provenance.filed_date.isoformat(),
                _canonical(fact.provenance.accepted_at),
                _canonical(fact.provenance.eligible_at),
                fact.lineage.source_document_url,
            ]
        )
        for fact in facts
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _column(fact: FinancialFact) -> str:
    return fact.raw_tag.rsplit("|column=", 1)[1]


def build_manifest(
    dry_run: SegmentDocumentDryRun,
    downloader: SavedCaptureDownloader,
    source: SegmentMarginSource,
    calendar_version: str,
) -> dict:
    """The reviewable description of exactly what a publication would write."""

    calendar = SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(source.cik)
    floor = min(
        definition.period_start
        for definition in (*calendar.fiscal_years, *calendar.transitions, *calendar.open_fiscal_years)
    )
    filings = {
        item.accession_number: item
        for item in periodic_filing_documents(
            downloader.payload.submissions, cik=source.cik, accepted_by=dry_run.knowledge_cutoff,
            report_date_floor=floor,
        )
    }
    facts_by_accession: Dict[str, List[FinancialFact]] = {}
    for fact in dry_run.facts:
        facts_by_accession.setdefault(fact.provenance.accession_number, []).append(fact)
    documents = []
    for read in dry_run.documents:
        filing = filings[read.accession_number]
        basis = source.basis_for(filing.report_date)
        facts = facts_by_accession.get(read.accession_number, [])
        documents.append(
            {
                "accession_number": read.accession_number,
                "form_type": filing.form_type,
                "report_date": filing.report_date.isoformat(),
                "filed_date": filing.filed_date.isoformat(),
                "sec_accepted_at": filing.accepted_at.isoformat(),
                "document_url": read.url,
                "saved_file": f"{read.accession_number}-{filing.document_name}",
                "sha256": read.sha256,
                "sec_captured_at": downloader.captured[read.accession_number]["captured_at"],
                "offline_read_at": read.captured_at.isoformat(),
                "reporting_basis": basis.name if basis else None,
                "rule_version": basis.rule.version if basis else None,
                "segment_column": basis.rule.segment_group if basis else None,
                "fact_count": len(facts),
                "periods": sorted(
                    {f"{fact.period.fiscal_period} {fact.period.fiscal_year}" for fact in facts}
                ),
            }
        )
    by_identity: Dict[Any, List[FinancialFact]] = {}
    for fact in dry_run.facts:
        by_identity.setdefault(fact.identity, []).append(fact)
    repeated = {identity: facts for identity, facts in by_identity.items() if len(facts) > 1}
    return {
        "cik": source.cik,
        "source_adapter": SOURCE_ADAPTER,
        "concept_map_version": source.version,
        "fiscal_calendar_version": calendar_version,
        "dimension": list(source.dimension),
        "knowledge_cutoff": dry_run.knowledge_cutoff.isoformat(),
        "submissions_downloaded_at": dry_run.submissions_downloaded_at.isoformat()
        if dry_run.submissions_downloaded_at else None,
        "complete": dry_run.is_complete,
        "issues": list(dry_run.issues),
        "document_count": len(dry_run.documents),
        "fact_count": len(dry_run.facts),
        "fact_digest": fact_digest(dry_run.facts),
        "identity_count": len(by_identity),
        "identities_reported_by_more_than_one_filing": len(repeated),
        "identities_with_disagreeing_values": sorted(
            f"{identity.concept} {identity.period_start}..{identity.period_end}"
            for identity, facts in repeated.items()
            if len({fact.value for fact in facts}) > 1
        ),
        "amendments_without_table": [list(item) for item in dry_run.amendments_without_table],
        "documents": documents,
    }


def _saved_capture_dry_run(
    capture_dir: Path, cik: str, knowledge_cutoff: datetime, batch_id: str, clock: Optional[Callable[[], datetime]]
) -> Tuple[SegmentDocumentDryRun, SavedCaptureDownloader, SegmentMarginSource, str]:
    source = segment_gross_margin_rule_for(cik)
    if source is None:
        raise ValueError(f"No segment margin source is approved for CIK {normalize_cik(cik)}.")
    calendar = SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(source.cik)
    downloader = SavedCaptureDownloader(capture_dir)
    kwargs = {"clock": clock} if clock is not None else {}
    dry_run = run_segment_document_dry_run(
        downloader=downloader,
        rule=source,
        calendar_policy=calendar,
        ingestion_batch_id=batch_id,
        knowledge_cutoff=knowledge_cutoff,
        **kwargs,
    )
    return dry_run, downloader, source, calendar.version


def _manifest_mismatches(manifest: Mapping[str, Any], current: Mapping[str, Any]) -> List[str]:
    problems = []
    for key in ("cik", "concept_map_version", "fiscal_calendar_version", "knowledge_cutoff", "fact_count", "fact_digest"):
        if manifest.get(key) != current.get(key):
            problems.append(f"{key}: reviewed {manifest.get(key)!r}, now {current.get(key)!r}")
    reviewed = {(d["accession_number"], d["sha256"]) for d in manifest.get("documents", [])}
    now = {(d["accession_number"], d["sha256"]) for d in current.get("documents", [])}
    if reviewed != now:
        problems.append("documents or their hashes differ from the reviewed manifest")
    return problems


# --------------------------------------------------------------------------
# Read-only verification of a publication
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MarginExpectation:
    """A trailing-year pair to recompute from stored facts and compare."""

    name: str
    knowledge_cutoff: datetime
    latest_end: date
    prior_end: date
    current: Decimal
    prior: Decimal


def verify_segment_publication(
    repository,
    *,
    manifest: Mapping[str, Any],
    batch_id: str,
    batch_rows: Mapping[str, tuple],
    read_at: datetime,
    expectations: Sequence[MarginExpectation],
    expected_ingested_at: Optional[datetime] = None,
) -> dict:
    """Read-only checks of stored facts against a reviewed manifest.

    ``batch_id`` is the stored batch that must hold every reviewed fact (the
    source-qualified ID, for example ``...+sec_filing_document``); after a
    replay under a new ID the facts stay in the original batch, so verify
    that one. The ingestion boundary is taken from the store, never from the
    caller: the batch row's ``ingested_at`` must equal every fact's, and
    ``expected_ingested_at``, when given, must equal it too. Each cutoff is
    tested with the other fixed at a value that admits the batch.
    """

    source = segment_gross_margin_rule_for(manifest["cik"])
    knowledge_cutoff = datetime.fromisoformat(manifest["knowledge_cutoff"])
    checks: List[dict] = []

    def facts_at(knowledge: datetime, vintage: datetime) -> Tuple[FinancialFact, ...]:
        return tuple(
            repository.get_facts(
                FundamentalsQuery(
                    cik=source.cik,
                    knowledge_cutoff=knowledge,
                    data_vintage_cutoff=vintage,
                    concepts=source.concepts,
                    source_adapter=SOURCE_ADAPTER,
                    concept_map_version=source.version,
                    fiscal_calendar_version=manifest["fiscal_calendar_version"],
                    max_periods_per_statement=256,
                )
            )
        )

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    def result(ingested_at: Optional[datetime]) -> dict:
        return {
            "passed": bool(checks) and all(check["passed"] for check in checks),
            "batch_id": batch_id,
            "stored_ingested_at": _canonical(ingested_at),
            "read_at": _canonical(read_at),
            "checks": checks,
        }

    # Everything stored for this issuer, source, and version, as of now.
    stored = facts_at(knowledge_cutoff, read_at)
    reviewed_ids = frozenset(_source_identity_key(fact) for fact in stored)
    record(
        "stored facts equal the reviewed manifest",
        len(stored) == manifest["fact_count"] and fact_digest(stored) == manifest["fact_digest"],
        f"{len(stored)} facts, digest {fact_digest(stored)}",
    )
    batches = sorted({fact.lineage.ingestion_batch_id for fact in stored})
    record("every reviewed fact is in the expected batch", batches == [batch_id], f"stored batches {batches}")
    stamps = sorted({fact.lineage.ingested_at for fact in stored})
    record(
        "every reviewed fact has one ingestion time",
        len(stamps) == 1,
        f"stored ingestion times {[stamp.isoformat() for stamp in stamps]}",
    )
    row = batch_rows.get(batch_id)
    expected_row_prefix = (batch_id, SOURCE_ADAPTER, manifest["concept_map_version"], manifest["fiscal_calendar_version"])
    row_ok = row is not None and tuple(row[:4]) == expected_row_prefix and len(stamps) == 1 and row[4] == stamps[0]
    record(
        "batch row matches the source, versions, and the facts' ingestion time",
        row_ok,
        f"batch row {list(map(str, row)) if row else None}",
    )
    if row is None:
        return result(None)
    # The batch row is the legitimate boundary; facts stamped otherwise have
    # already failed above and are exposed again by the boundary check.
    ingested_at = row[4]
    if expected_ingested_at is not None:
        record(
            "stored ingestion time equals the one the publication reported",
            ingested_at == expected_ingested_at,
            f"stored {ingested_at.isoformat()}, expected {expected_ingested_at.isoformat()}",
        )

    before = [f for f in facts_at(knowledge_cutoff, ingested_at - _ONE_MICROSECOND) if _source_identity_key(f) in reviewed_ids]
    at = [f for f in facts_at(knowledge_cutoff, ingested_at) if _source_identity_key(f) in reviewed_ids]
    record(
        "ingestion boundary: no reviewed fact 1us before the stored ingestion time, all of them at it",
        not before and len(at) == manifest["fact_count"],
        f"{len(before)} visible at {(ingested_at - _ONE_MICROSECOND).isoformat()}, {len(at)} at {ingested_at.isoformat()}",
    )
    for document in manifest["documents"]:
        accepted = datetime.fromisoformat(document["sec_accepted_at"])
        accession = document["accession_number"]
        before = [f for f in facts_at(accepted - _ONE_MICROSECOND, ingested_at) if f.provenance.accession_number == accession]
        at = [f for f in facts_at(accepted, ingested_at) if f.provenance.accession_number == accession]
        record(
            f"acceptance boundary {accession}",
            not before and len(at) == document["fact_count"],
            f"{len(before)} facts 1us before {accepted.isoformat()}, {len(at)} at it (expected {document['fact_count']})",
        )
    for expectation in expectations:
        def pair_at(vintage: datetime):
            return load_segment_gross_margin_pair(
                repository,
                rule=source,
                fiscal_calendar_version=manifest["fiscal_calendar_version"],
                knowledge_cutoff=expectation.knowledge_cutoff,
                data_vintage_cutoff=vintage,
                latest_end=expectation.latest_end,
                prior_end=expectation.prior_end,
            )

        try:
            pair = pair_at(ingested_at)
        except SegmentGrossMarginRefusal as error:
            record(f"margin pair {expectation.name}", False, f"refused: {error}")
        else:
            record(
                f"margin pair {expectation.name}",
                (pair.current, pair.prior) == (expectation.current, expectation.prior),
                f"computed {pair.current} / {pair.prior}; oracle {expectation.current} / {expectation.prior}",
            )
        try:
            pair_at(ingested_at - _ONE_MICROSECOND)
        except SegmentGrossMarginRefusal:
            record(f"margin pair {expectation.name} refuses 1us before the ingestion time", True, "refused")
        else:
            record(
                f"margin pair {expectation.name} refuses 1us before the ingestion time",
                False,
                "computed from facts visible before this batch's ingestion time",
            )
    return result(ingested_at)


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _parse_aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("must be an ISO-8601 datetime with an explicit timezone") from None
    if not is_aware(parsed):
        raise argparse.ArgumentTypeError("must include an explicit timezone")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run, publish, or verify segment facts from a saved SEC capture.")
    parser.add_argument("--capture-dir", required=True, type=Path, help="saved capture: issuer.json, report.json, documents")
    parser.add_argument("--cik", required=True, help="SEC issuer CIK")
    parser.add_argument("--knowledge-cutoff", required=True, type=_parse_aware_datetime)
    parser.add_argument("--batch-id", required=True, help="immutable ingestion batch ID (the source suffix is added)")
    parser.add_argument("--manifest", type=Path, help="reviewed manifest; required for --publish and --verify")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true", help="publish after the manifest matches (needs DATABASE_URL)")
    mode.add_argument("--verify", action="store_true",
                      help="read-only verification of the stored batch --batch-id against the manifest (needs DATABASE_URL)")
    parser.add_argument("--expect-ingested-at", type=_parse_aware_datetime,
                        help="with --verify: the ingestion time the publication reported; must equal the stored one")
    parser.add_argument("--expectations", type=Path, help="JSON list of margin pairs for --verify")
    return parser


def _expectations(path: Optional[Path]) -> List[MarginExpectation]:
    if path is None:
        return []
    return [
        MarginExpectation(
            name=item["name"],
            knowledge_cutoff=datetime.fromisoformat(item["knowledge_cutoff"]),
            latest_end=date.fromisoformat(item["latest_end"]),
            prior_end=date.fromisoformat(item["prior_end"]),
            current=Decimal(item["current"]),
            prior=Decimal(item["prior"]),
        )
        for item in json.loads(path.read_text())
    ]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stored_ingestion(receipt, batch_rows: Mapping[str, tuple]) -> List[dict]:
    """Which stored batches hold the published facts, with their own
    ingestion times: after a replay or reuse that is the original batch, not
    this run's read time."""

    counts: Dict[str, int] = {}
    for batch_id, count in receipt.inserted_by_batch:
        if count:
            counts[batch_id] = counts.get(batch_id, 0) + count
    for batch_id, count in receipt.reused_by_batch:
        counts[batch_id] = counts.get(batch_id, 0) + count
    if receipt.replayed_fact_count:
        for batch_id in receipt.batch_ids:
            counts[batch_id] = counts.get(batch_id, 0) + receipt.replayed_fact_count
    return [
        {
            "batch_id": batch_id,
            "fact_count": count,
            "ingested_at": batch_rows[batch_id][4].astimezone(timezone.utc).isoformat() if batch_id in batch_rows else None,
        }
        for batch_id, count in sorted(counts.items())
    ]


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    clock: Optional[Callable[[], datetime]] = None,
    repository=None,
    batch_row_reader: Optional[Callable[[Sequence[str]], Mapping[str, tuple]]] = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text()) if args.manifest else None
        needs_database = args.publish or args.verify
        if needs_database and manifest is None:
            raise ValueError("--manifest is required for --publish and --verify.")
        if args.expect_ingested_at is not None and not args.verify:
            raise ValueError("--expect-ingested-at applies only to --verify.")
        database_url = os.getenv("DATABASE_URL") if needs_database else None
        if needs_database and not database_url and repository is None:
            raise ValueError("DATABASE_URL must be set in the process environment for --publish or --verify.")
        if needs_database and batch_row_reader is None:
            from .incremental_publication import read_batch_rows

            batch_row_reader = lambda ids: read_batch_rows(ids, database_url=database_url)  # noqa: E731
        stored_batch_id = source_qualified_batch_id(args.batch_id, SOURCE_ADAPTER)

        if args.verify:
            if repository is None:
                from .store import PostgresFundamentalsRepository

                repository = PostgresFundamentalsRepository(database_url=database_url)
            result = verify_segment_publication(
                repository,
                manifest=manifest,
                batch_id=stored_batch_id,
                batch_rows=batch_row_reader([stored_batch_id]),
                read_at=(clock or _utc_now)(),
                expectations=_expectations(args.expectations),
                expected_ingested_at=args.expect_ingested_at,
            )
            print(json.dumps({"status": "verified" if result["passed"] else "failed", **result}, sort_keys=True))
            return 0 if result["passed"] else 1

        dry_run, downloader, source, calendar_version = _saved_capture_dry_run(
            args.capture_dir, args.cik, args.knowledge_cutoff, args.batch_id, clock
        )
        current = build_manifest(dry_run, downloader, source, calendar_version)
        summary: Dict[str, Any] = {
            "status": "dry_run" if dry_run.is_complete else "refused",
            "manifest": current,
            # What a publication from this run would stamp on newly inserted facts.
            "this_run_ingested_at": dry_run.ingested_at.isoformat() if dry_run.ingested_at else None,
            "ingestion_batch_id": dry_run.facts[0].lineage.ingestion_batch_id if dry_run.facts else None,
        }
        if not dry_run.is_complete:
            print(json.dumps(summary, sort_keys=True))
            return 1
        if manifest is not None:
            mismatches = _manifest_mismatches(manifest, current)
            if mismatches:
                summary.update(status="refused", manifest_mismatches=mismatches)
                print(json.dumps(summary, sort_keys=True))
                return 1
            summary["manifest_mismatches"] = []
        if args.publish:
            # publish_incremental's own transaction has already committed or
            # rolled back by the time it returns or raises: a raise here
            # means nothing was written (caught below as a refusal). A
            # return means the facts are safely stored; nothing after this
            # point may report "refused" without discarding that fact.
            receipt = publish_incremental(
                dry_run.facts, knowledge_cutoff=dry_run.knowledge_cutoff, database_url=database_url,
            )
            summary.update(
                status="published",
                publication={
                    "batch_ids": list(receipt.batch_ids),
                    "written_batch_ids": list(receipt.written_batch_ids),
                    "inserted_fact_count": receipt.inserted_fact_count,
                    "reused_fact_count_by_earlier_batch": receipt.reused_fact_count,
                    "replayed_fact_count": receipt.replayed_fact_count,
                    "no_op": receipt.is_no_op,
                },
            )
            holders = sorted(
                {batch_id for batch_id, _count in receipt.reused_by_batch} | set(receipt.batch_ids)
            )
            try:
                rows = batch_row_reader(holders)
            except (LookupError, OSError, FundamentalsRepositoryUnavailable) as error:
                # The publication already committed (or was a genuine no-op
                # replay/reuse of an earlier one); only the metadata read
                # that follows it failed. Report the receipt as it is,
                # never "refused", and point at a read-only recovery step
                # rather than another publish.
                summary.update(
                    status="published_unverified",
                    metadata_read_error=str(error),
                    message=(
                        "Publication succeeded (see 'publication' above) but reading back the stored "
                        "batch metadata failed, so stored_ingestion could not be confirmed here. Do not "
                        "publish again to recover: rerun this command with --verify (read-only) once the "
                        "metadata read is available, or read fundamentals_ingestion_batches directly for "
                        f"{holders!r}."
                    ),
                )
                print(json.dumps(summary, sort_keys=True))
                return 1
            # The stored batches that hold the facts and their own
            # ingestion times; verify against these, not this run's time.
            summary["stored_ingestion"] = _stored_ingestion(receipt, rows)
    except IncrementalPublicationError as error:
        print(json.dumps({"status": "refused", "publication_problems": list(error.problems)}, sort_keys=True))
        return 1
    except (LookupError, ValueError, OSError, FundamentalsPublishError, FundamentalsRepositoryUnavailable) as error:
        print(json.dumps({"status": "refused", "message": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
