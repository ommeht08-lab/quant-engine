"""Publishing segment facts from a saved SEC capture: dry run, manifest, verification."""

import hashlib
import io
import json
import os
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

import psycopg2
import pytest

from src.fundamentals import segment_publication_command as command
from src.fundamentals.incremental_publication import IncrementalPublicationReceipt
from src.fundamentals.repository import FundamentalsRepositoryUnavailable, InMemoryFundamentalsRepository
from src.fundamentals.segment_publication_command import (
    MarginExpectation,
    fact_digest,
    main,
    verify_segment_publication,
)
from src.fundamentals.store import ensure_schema
from tests.fundamentals.segment_margin_fixtures import INGESTED_AT, PILOT_CUTOFF
from tests.fundamentals.test_segment_gross_margin import PILOT_ROWS, _documents, _row, _submissions

CUTOFF = PILOT_CUTOFF.isoformat()
BATCH = "cat-segment-test"
PILOT_PAIR = MarginExpectation(
    "pilot", PILOT_CUTOFF, date(2024, 6, 30), date(2023, 6, 30),
    Decimal(21605) / Decimal(63025), Decimal(18803) / Decimal(61793),
)


def _capture(tmp_path, documents=None):
    """A saved capture of the pilot's three filings, as the live run writes it."""

    documents = documents or _documents()
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "issuer.json").write_text(json.dumps({
        "cik": "0000018230", "company_facts": {}, "submissions": [_submissions(*[_row(f) for f in PILOT_ROWS])],
        "company_facts_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000018230.json",
        "submission_urls": ["https://data.sec.gov/submissions/CIK0000018230.json"],
        "downloaded_at": INGESTED_AT.isoformat(),
    }))
    records = []
    for index, filing in enumerate(PILOT_ROWS):
        body = documents[filing.accession_number]
        (tmp_path / f"{filing.accession_number}-{filing.document_name}").write_bytes(body)
        records.append({
            "accession_number": filing.accession_number, "url": filing.document_url,
            "sha256": hashlib.sha256(body).hexdigest(),
            "captured_at": (INGESTED_AT + timedelta(seconds=index + 1)).isoformat(),
        })
    (tmp_path / "report.json").write_text(json.dumps({"documents": records}))
    return tmp_path


class _Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


def _run(argv, **kwargs):
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(argv, **kwargs)
    return code, json.loads(out.getvalue())


def _args(capture, *extra):
    return ["--capture-dir", str(capture), "--cik", "18230", "--knowledge-cutoff", CUTOFF, "--batch-id", BATCH, *extra]


def test_dry_run_reports_original_capture_times_and_offline_ingestion(tmp_path):
    read_start = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    code, result = _run(_args(_capture(tmp_path)), clock=_Clock(read_start))

    assert code == 0 and result["status"] == "dry_run"
    manifest = result["manifest"]
    assert (manifest["document_count"], manifest["fact_count"], manifest["issues"]) == (3, 14, [])
    assert manifest["submissions_downloaded_at"] == INGESTED_AT.isoformat()
    documents = manifest["documents"]
    assert {d["accession_number"]: d["sec_captured_at"] for d in documents} == {
        filing.accession_number: (INGESTED_AT + timedelta(seconds=n)).isoformat()
        for n, filing in enumerate(PILOT_ROWS, start=1)
    }
    assert [d["offline_read_at"] for d in documents] == [
        (read_start + timedelta(seconds=n)).isoformat() for n in (1, 2, 3)
    ]
    # Ingestion is when this process read the last document, never the capture.
    assert result["this_run_ingested_at"] == (read_start + timedelta(seconds=3)).isoformat()
    assert {d["reporting_basis"] for d in documents} == {"ME&T"}
    assert {d["rule_version"] for d in documents} == {"cat-met-supplemental-results-v3"}
    assert result["ingestion_batch_id"] == BATCH + "+sec_filing_document"


def test_the_fact_digest_ignores_only_ingestion_batch_and_time(tmp_path):
    capture = _capture(tmp_path)
    _, early = _run(_args(capture), clock=_Clock(datetime(2026, 10, 1, tzinfo=timezone.utc)))
    _, late = _run(_args(capture), clock=_Clock(datetime(2027, 1, 1, tzinfo=timezone.utc)))
    assert early["manifest"]["fact_digest"] == late["manifest"]["fact_digest"]

    dry_run, *_ = command._saved_capture_dry_run(capture, "18230", PILOT_CUTOFF, BATCH, None)
    facts = dry_run.facts
    shifted = tuple(
        replace(f, provenance=replace(f.provenance, accepted_at=f.provenance.accepted_at.astimezone(timezone(timedelta(hours=-4)))),
                value=Decimal(f"{f.value}.00"))
        for f in facts
    )
    assert fact_digest(shifted) == fact_digest(facts)  # a database round trip changes neither
    assert fact_digest(facts[1:]) != fact_digest(facts)
    assert fact_digest((replace(facts[0], value=facts[0].value + 1),) + facts[1:]) != fact_digest(facts)


def test_a_saved_document_that_differs_from_its_capture_record_refuses(tmp_path):
    capture = _capture(tmp_path)
    victim = next(capture.glob("0000018230-24-000045-*"))
    victim.write_bytes(victim.read_bytes().replace(b"19,816", b"19,817"))
    code, result = _run(_args(capture))
    assert code == 1 and result["status"] == "refused"
    assert "saved bytes differ from the capture record" in result["manifest"]["issues"][0]


def test_publish_requires_a_manifest_and_a_database_url(tmp_path, monkeypatch):
    capture = _capture(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    code, result = _run(_args(capture, "--publish"))
    assert code == 1 and "--manifest is required" in result["message"]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_run(_args(capture))[1]["manifest"]))
    code, result = _run(_args(capture, "--manifest", str(manifest), "--publish"))
    assert code == 1 and "DATABASE_URL must be set" in result["message"]


def _no_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(command, "publish_incremental", lambda *a, **k: pytest.fail("must not publish"))
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: pytest.fail("must not connect"))


def test_a_supplied_manifest_that_does_not_match_refuses_without_a_database(tmp_path, monkeypatch):
    reviewed = _capture(tmp_path / "reviewed")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_run(_args(reviewed))[1]["manifest"]))
    documents = _documents()
    # The same filing captured again: SEC appends a different script tag.
    documents[PILOT_ROWS[2].accession_number] += b"<!-- another capture -->"
    fresh = _capture(tmp_path / "fresh", documents)
    wrong_digest = tmp_path / "wrong-digest.json"
    wrong_digest.write_text(json.dumps({**json.loads(manifest.read_text()), "fact_digest": "0" * 64}))
    _no_database(monkeypatch)

    for capture, reviewed_manifest, flags in (
        (fresh, manifest, ()), (fresh, manifest, ("--publish",)), (reviewed, wrong_digest, ()),
    ):
        if flags:
            monkeypatch.setenv("DATABASE_URL", "postgresql://never-used.invalid/db")
        code, result = _run(_args(capture, "--manifest", str(reviewed_manifest), *flags))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert code == 1 and result["status"] == "refused", (capture, flags)
        assert any(m.startswith("fact_digest") for m in result["manifest_mismatches"])
    code, result = _run(_args(fresh, "--manifest", str(manifest)))
    assert "documents or their hashes differ from the reviewed manifest" in result["manifest_mismatches"]

    code, result = _run(_args(reviewed, "--manifest", str(manifest)))
    assert code == 0 and result["status"] == "dry_run" and result["manifest_mismatches"] == []


def _published(tmp_path):
    """The pilot capture's facts as a publication would store them."""

    capture = _capture(tmp_path)
    dry_run, downloader, source, calendar = command._saved_capture_dry_run(capture, "18230", PILOT_CUTOFF, BATCH, None)
    manifest = command.build_manifest(dry_run, downloader, source, calendar)
    return dry_run.facts, manifest


def _row_for(facts, batch_id=None):
    sample = facts[0].lineage
    batch_id = batch_id or sample.ingestion_batch_id
    return {batch_id: (batch_id, sample.source_adapter, sample.concept_map_version, sample.fiscal_calendar_version, sample.ingested_at)}


def _verify(facts, manifest, rows, *, batch_id=BATCH + "+sec_filing_document", **kwargs):
    return verify_segment_publication(
        InMemoryFundamentalsRepository(facts), manifest=manifest, batch_id=batch_id, batch_rows=rows,
        read_at=datetime(2030, 1, 1, tzinfo=timezone.utc), expectations=[PILOT_PAIR], **kwargs,
    )


def _failed(result):
    return {check["check"] for check in result["checks"] if not check["passed"]}


def test_verification_checks_batch_identity_both_cutoffs_and_their_boundaries(tmp_path):
    facts, manifest = _published(tmp_path)
    result = _verify(facts, manifest, _row_for(facts), expected_ingested_at=facts[0].lineage.ingested_at)

    assert result["passed"], _failed(result)
    assert result["stored_ingested_at"] == facts[0].lineage.ingested_at.isoformat()
    names = [check["check"] for check in result["checks"]]
    assert sum(name.startswith("acceptance boundary") for name in names) == 3
    assert "every reviewed fact is in the expected batch" in names
    assert "margin pair pilot refuses 1us before the ingestion time" in names

    missing = _verify(facts[1:], manifest, _row_for(facts))
    assert {"stored facts equal the reviewed manifest"} <= _failed(missing)
    wrong_oracle = verify_segment_publication(
        InMemoryFundamentalsRepository(facts), manifest=manifest, batch_id=BATCH + "+sec_filing_document",
        batch_rows=_row_for(facts), read_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        expectations=[replace(PILOT_PAIR, current=Decimal("0.5"))],
    )
    assert _failed(wrong_oracle) == {"margin pair pilot"}


def test_a_fact_stamped_before_the_batch_ingestion_time_fails(tmp_path):
    # Reproduces the review: one fact's ingestion time moved a day earlier
    # used to pass all checks.
    facts, manifest = _published(tmp_path)
    early = replace(facts[0], lineage=replace(facts[0].lineage, ingested_at=facts[0].lineage.ingested_at - timedelta(days=1)))
    result = _verify((early,) + facts[1:], manifest, _row_for(facts))

    assert not result["passed"]
    assert {
        "every reviewed fact has one ingestion time",
        "batch row matches the source, versions, and the facts' ingestion time",
        "ingestion boundary: no reviewed fact 1us before the stored ingestion time, all of them at it",
    } <= _failed(result)


def test_every_fact_stamped_earlier_than_its_batch_row_fails(tmp_path):
    facts, manifest = _published(tmp_path)
    shifted = tuple(
        replace(f, lineage=replace(f.lineage, ingested_at=f.lineage.ingested_at - timedelta(days=1))) for f in facts
    )
    result = _verify(shifted, manifest, _row_for(facts))
    assert "batch row matches the source, versions, and the facts' ingestion time" in _failed(result)
    assert "ingestion boundary: no reviewed fact 1us before the stored ingestion time, all of them at it" in _failed(result)


def test_facts_in_another_batch_fail(tmp_path):
    # Reproduces the review: every fact moved to "wrong-batch" used to pass.
    facts, manifest = _published(tmp_path)
    moved = tuple(replace(f, lineage=replace(f.lineage, ingestion_batch_id="wrong-batch")) for f in facts)

    no_row = _verify(moved, manifest, _row_for(moved))  # only wrong-batch has a row
    assert not no_row["passed"] and no_row["stored_ingested_at"] is None
    assert {"every reviewed fact is in the expected batch",
            "batch row matches the source, versions, and the facts' ingestion time"} <= _failed(no_row)

    with_row = _verify(moved, manifest, {**_row_for(moved), **_row_for(facts)})
    assert _failed(with_row) == {"every reviewed fact is in the expected batch"}


def test_batch_row_metadata_and_the_reported_ingestion_time_must_match(tmp_path):
    facts, manifest = _published(tmp_path)
    batch_id = facts[0].lineage.ingestion_batch_id
    wrong_source = {batch_id: (batch_id, "sec_companyfacts") + _row_for(facts)[batch_id][2:]}
    assert "batch row matches the source, versions, and the facts' ingestion time" in _failed(
        _verify(facts, manifest, wrong_source)
    )
    # A replay reports its own read time; it is not the stored ingestion time.
    replay_time = facts[0].lineage.ingested_at + timedelta(days=3)
    assert _failed(_verify(facts, manifest, _row_for(facts), expected_ingested_at=replay_time)) == {
        "stored ingestion time equals the one the publication reported"
    }


# --------------------------------------------------------------------------
# A successful publish (insert or no-op replay) must never be reported as
# "refused" just because the metadata read-back that follows it fails.
# --------------------------------------------------------------------------


def _receipt(*, inserted=14, reused=0, replayed=0, batch=BATCH + "+sec_filing_document"):
    return IncrementalPublicationReceipt(
        batch_ids=(batch,), written_batch_ids=(batch,) if inserted else (),
        inserted_by_batch=((batch, inserted),), reused_by_batch=(),
        replayed_fact_count=replayed,
    ) if not reused else IncrementalPublicationReceipt(
        batch_ids=(batch,), written_batch_ids=(),
        inserted_by_batch=((batch, 0),), reused_by_batch=((batch, reused),),
        replayed_fact_count=replayed,
    )


def _failing_reader(*a, **k):
    raise FundamentalsRepositoryUnavailable("metadata connection dropped")


def test_metadata_read_failure_after_a_real_insert_is_not_reported_as_refused(tmp_path, monkeypatch):
    capture = _capture(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_run(_args(capture))[1]["manifest"]))
    monkeypatch.setenv("DATABASE_URL", "postgresql://never-used.invalid/db")
    receipt = _receipt(inserted=14, replayed=0)
    monkeypatch.setattr(command, "publish_incremental", lambda *a, **k: receipt)

    code, result = _run(
        _args(capture, "--manifest", str(manifest), "--publish"), batch_row_reader=_failing_reader,
    )

    assert code == 1
    assert result["status"] == "published_unverified"
    assert result["status"] != "refused"
    # The receipt itself must be preserved exactly, counts and batch IDs included.
    assert result["publication"] == {
        "batch_ids": [BATCH + "+sec_filing_document"], "written_batch_ids": [BATCH + "+sec_filing_document"],
        "inserted_fact_count": 14, "reused_fact_count_by_earlier_batch": 0, "replayed_fact_count": 0,
        "no_op": False,
    }
    assert "metadata connection dropped" in result["metadata_read_error"]
    # A read-only recovery path is offered, not another publish.
    assert "--verify" in result["message"]
    assert "publish again" not in result["message"] or "Do not publish again" in result["message"]
    assert "stored_ingestion" not in result


def test_metadata_read_failure_after_a_no_op_replay_is_not_reported_as_refused(tmp_path, monkeypatch):
    capture = _capture(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_run(_args(capture))[1]["manifest"]))
    monkeypatch.setenv("DATABASE_URL", "postgresql://never-used.invalid/db")
    receipt = _receipt(inserted=0, reused=0, replayed=14)
    monkeypatch.setattr(command, "publish_incremental", lambda *a, **k: receipt)

    code, result = _run(
        _args(capture, "--manifest", str(manifest), "--publish"), batch_row_reader=_failing_reader,
    )

    assert code == 1
    assert result["status"] == "published_unverified"
    assert result["publication"]["no_op"] is True
    assert result["publication"]["replayed_fact_count"] == 14
    assert "stored_ingestion" not in result


def test_a_publication_failure_before_any_commit_still_refuses(tmp_path, monkeypatch):
    from src.fundamentals.incremental_publication import IncrementalPublicationError

    capture = _capture(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_run(_args(capture))[1]["manifest"]))
    monkeypatch.setenv("DATABASE_URL", "postgresql://never-used.invalid/db")

    def _raise(*a, **k):
        raise IncrementalPublicationError(["stored facts conflict with the incoming publication"])

    monkeypatch.setattr(command, "publish_incremental", _raise)

    def _must_not_be_read(*a, **k):
        pytest.fail("the metadata reader must never be reached: nothing was published")

    code, result = _run(
        _args(capture, "--manifest", str(manifest), "--publish"), batch_row_reader=_must_not_be_read,
    )

    assert code == 1 and result["status"] == "refused"
    assert "publication_problems" in result and "stored_ingestion" not in result and "publication" not in result


def test_the_default_batch_row_reader_is_only_constructed_when_a_database_is_needed(tmp_path, monkeypatch):
    # A dry run (no --publish, no --verify) never needs DATABASE_URL and must
    # not import or call the real read_batch_rows.
    capture = _capture(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    code, result = _run(_args(capture))
    assert code == 0 and result["status"] == "dry_run"


# --------------------------------------------------------------------------
# Real PostgreSQL: publish, verify, replay
# --------------------------------------------------------------------------

DATABASE_URL = os.getenv("FUNDAMENTALS_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="requires the dedicated loopback PostgreSQL integration database")
def test_publish_verify_and_replay_on_postgres(tmp_path, monkeypatch):
    parsed = urlparse(DATABASE_URL)
    assert parsed.hostname == "127.0.0.1" and parsed.path == "/valuation_engine_test"
    connection = psycopg2.connect(DATABASE_URL)
    try:
        connection.set_session(readonly=False, autocommit=False)
        ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE fundamentals_facts, fundamentals_ingestion_batches RESTART IDENTITY CASCADE")
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    capture = _capture(tmp_path / "capture")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_run(_args(capture))[1]["manifest"]))

    code, published = _run(_args(capture, "--manifest", str(manifest), "--publish"), clock=_Clock(read_start := datetime(2026, 10, 1, tzinfo=timezone.utc)))
    assert code == 0, published
    assert published["publication"]["inserted_fact_count"] == 14
    original = published["stored_ingestion"]
    assert original == [{"batch_id": BATCH + "+sec_filing_document", "fact_count": 14,
                         "ingested_at": (read_start + timedelta(seconds=3)).isoformat()}]
    original_time = original[0]["ingested_at"]
    expectations = tmp_path / "expectations.json"
    expectations.write_text(json.dumps([{
        "name": "pilot", "knowledge_cutoff": CUTOFF, "latest_end": "2024-06-30", "prior_end": "2023-06-30",
        "current": str(PILOT_PAIR.current), "prior": str(PILOT_PAIR.prior),
    }]))
    verify = ["--manifest", str(manifest), "--verify", "--expectations", str(expectations)]
    # The test's clocks run ahead of the real one; verification reads as of after them.
    reader = _Clock(datetime(2026, 10, 10, tzinfo=timezone.utc))
    code, verified = _run(_args(capture, *verify, "--expect-ingested-at", original_time), clock=reader)
    assert code == 0, [c for c in verified["checks"] if not c["passed"]]
    assert verified["stored_ingested_at"] == original_time

    # Replays: the facts stay in the original batch with the original time,
    # and the output says so rather than reporting the replay's read time.
    later = _Clock(datetime(2026, 10, 5, tzinfo=timezone.utc))
    code, replay = _run(_args(capture, "--manifest", str(manifest), "--publish"), clock=later)
    assert code == 0 and replay["publication"]["no_op"] and replay["publication"]["replayed_fact_count"] == 14
    assert replay["stored_ingestion"] == original and replay["this_run_ingested_at"] != original_time
    reuse_args = _args(capture, "--manifest", str(manifest), "--publish")
    reuse_args[reuse_args.index("--batch-id") + 1] = BATCH + "-replay"
    code, reuse = _run(reuse_args, clock=_Clock(datetime(2026, 10, 6, tzinfo=timezone.utc)))
    assert code == 0 and reuse["publication"]["no_op"]
    assert reuse["publication"]["reused_fact_count_by_earlier_batch"] == 14
    assert reuse["stored_ingestion"] == original

    # Verifying the replay's batch ID, or its read time, fails.
    replay_verify = _args(capture, *verify)
    replay_verify[replay_verify.index("--batch-id") + 1] = BATCH + "-replay"
    code, wrong_batch = _run(replay_verify, clock=reader)
    assert code == 1 and wrong_batch["stored_ingested_at"] is None
    code, wrong_time = _run(_args(capture, *verify, "--expect-ingested-at", replay["this_run_ingested_at"]), clock=reader)
    assert code == 1
    assert [c["check"] for c in wrong_time["checks"] if not c["passed"]] == [
        "stored ingestion time equals the one the publication reported"
    ]
