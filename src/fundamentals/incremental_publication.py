"""Incremental SEC publication: prove exactly what one transaction inserted.

A recurring refresh republishes an issuer's complete classified history under
a new ingestion batch ID. Facts that are already stored keep their original
batch, because fact inserts are ``ON CONFLICT DO NOTHING``, so a refreshed
point-in-time result legitimately spans the backfill batch and earlier refresh
batches. The backfill verifier's rule that every fact sits in the supplied
batch therefore cannot verify a refresh.

``publish_incremental`` runs the publication in one transaction and, before
commit, requires:

1. the identities returned by ``INSERT ... RETURNING`` and the identities
   stored before the insert are disjoint and together equal the incoming
   identities exactly;
2. every inserted row is in this publication's batch for its source and
   matches its incoming fact, and every pre-existing fact matches its
   incoming fact (an idempotent replay);
3. the issuer's stored facts visible at the knowledge cutoff, read across the
   primary source and every supplement it declares (even one absent from
   this publication), equal the incoming facts exactly: nothing missing,
   nothing unexpected;
4. every batch holding those facts is lineage-valid. The foreign key ties a
   fact to a batch row, but it does not pair a supplemental batch with its
   primary, so each supplemental batch ``X+source`` must have a primary batch
   row ``X`` of a source that declares it, with the same mapping version,
   calendar version, and ingestion time;
5. point-in-time selection over the stored facts equals selection over the
   incoming facts;
6. when the caller freezes history before a cutoff (the recurring refresh
   freezes 2024-09-03), no fact this transaction inserted is eligible at or
   before it. A concept-map change or a late SEC addition to an old filing
   therefore needs a reviewed backfill instead of slipping into a refresh.

The batch table has no issuer column, so a batch row can be tied to an issuer
only through facts, and each fact carries its issuer. The transaction therefore
decides what to write from the pre-insert state before writing anything: it
writes a batch row only for a source that inserts at least one fact, plus the
primary row a written supplemental batch must pair with. A no-op writes
nothing at all. Every committed batch holds a fact of this issuer, or is the
primary of a paired supplemental batch that does.

Publications are serialized by one transaction-scoped advisory lock taken
before any schema statement, so overlapping publishes wait for each other
instead of blocking in schema DDL or making an identity look neither inserted
nor pre-existing.

Any failure rolls the whole transaction back, so no batch row or fact from
that transaction survives. Post-publication verification (see
``sec_backfill_verification``) can only detect problems, not undo them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .selection import select_point_in_time
from .store import (
    PUBLISH_STATEMENT_TIMEOUT_MS,
    SUPPLEMENTAL_SOURCES_BY_PRIMARY,
    FundamentalsPublishError,
    _INSERT_COLUMNS,
    _SUPPLEMENTAL_SOURCES,
    _fact_to_row,
    _publication_batch_keys,
    _row_to_fact,
    _same_source_fact,
    _source_identity_key,
    insert_publication_batches,
    run_publish_transaction,
    run_read_transaction,
    _execute_values,
    source_qualified_batch_id,
)
from .time_policy import is_aware
from .types import FinancialFact, FundamentalHistory

_NEUTRAL_INGESTED_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)

INSERT_FACTS_RETURNING_SQL = f"""
INSERT INTO fundamentals_facts ({', '.join(_INSERT_COLUMNS)})
VALUES %s
ON CONFLICT DO NOTHING
RETURNING {', '.join(_INSERT_COLUMNS)};
"""

SELECT_ISSUER_FACTS_SQL = f"""
SELECT {', '.join(_INSERT_COLUMNS)}
FROM fundamentals_facts
WHERE cik = %s
  AND source_adapter = ANY(%s)
  AND concept_map_version = %s
  AND fiscal_calendar_version = %s
  AND eligible_at <= %s;
"""

# The lock wait gets its own bound; the publish statement timeout resumes after it.
PUBLISH_LOCK_WAIT_MS = 300_000
LOCK_PUBLICATIONS_SQL = (
    "SELECT pg_advisory_xact_lock(hashtextextended('valuation-engine:fundamentals-publish', 0));"
)
SET_LOCAL_STATEMENT_TIMEOUT_SQL = "SELECT set_config('statement_timeout', %s, true);"

SELECT_BATCH_FACT_CIKS_SQL = """
SELECT ingestion_batch_id, cik, COUNT(*)
FROM fundamentals_facts
WHERE ingestion_batch_id = ANY(%s)
GROUP BY ingestion_batch_id, cik;
"""

SELECT_BATCH_ROWS_SQL = """
SELECT ingestion_batch_id, source_adapter, concept_map_version, fiscal_calendar_version, ingested_at
FROM fundamentals_ingestion_batches
WHERE ingestion_batch_id = ANY(%s);
"""


class IncrementalPublicationError(FundamentalsPublishError):
    """The transaction could not prove an exact incremental publication; it was rolled back."""

    def __init__(self, problems: Sequence[str]):
        self.problems = tuple(problems)
        super().__init__("incremental publication refused: " + "; ".join(self.problems))


@dataclass(frozen=True)
class IncrementalPublicationReceipt:
    """What one committed publication inserted, and what it reused.

    ``inserted_by_batch`` counts facts this transaction inserted, per batch of
    this publication. ``reused_by_batch`` counts incoming facts that were
    already stored in an earlier batch, per the batch that still holds them;
    those facts are not in this publication's batches. ``replayed_fact_count``
    counts facts a previous attempt with the same batch ID already stored.
    """

    batch_ids: Tuple[str, ...]
    written_batch_ids: Tuple[str, ...]
    inserted_by_batch: Tuple[Tuple[str, int], ...]
    reused_by_batch: Tuple[Tuple[str, int], ...]
    replayed_fact_count: int = 0

    @property
    def inserted_fact_count(self) -> int:
        return sum(count for _, count in self.inserted_by_batch)

    @property
    def reused_fact_count(self) -> int:
        return sum(count for _, count in self.reused_by_batch)

    @property
    def is_no_op(self) -> bool:
        return self.inserted_fact_count == 0

    @property
    def batch_written(self) -> bool:
        """Whether this transaction wrote batch rows; a no-op writes none."""

        return bool(self.written_batch_ids)


def source_key(fact: FinancialFact) -> FinancialFact:
    """The fact without ingestion bookkeeping, which differs between batches."""

    return replace(
        fact,
        lineage=replace(fact.lineage, ingestion_batch_id="-", ingested_at=_NEUTRAL_INGESTED_AT),
    )


def selected_source_keys(history: FundamentalHistory) -> frozenset:
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
    return frozenset(source_key(fact) for fact in facts)


def point_in_time_selection(facts: Iterable[FinancialFact], knowledge_cutoff: datetime, cik: str) -> frozenset:
    """Selected facts, independent of input order.

    Selection keeps one representative of agreeing duplicates by raw tag, a
    stable sort, so the input is put in one canonical order first; stored rows
    come back from Postgres in no particular order.
    """

    ordered = sorted(facts, key=lambda fact: repr(source_key(fact)))
    return selected_source_keys(select_point_in_time(ordered, knowledge_cutoff, cik=cik))


def _batch_key(fact: FinancialFact) -> tuple:
    lineage = fact.lineage
    return (
        lineage.ingestion_batch_id,
        lineage.source_adapter,
        lineage.concept_map_version,
        lineage.fiscal_calendar_version,
        lineage.ingested_at,
    )


def paired_primary_batch_id(ingestion_batch_id: str, source_adapter: str) -> Optional[str]:
    """The primary batch ID a supplemental batch ID names, or None if it names none."""

    suffix = source_qualified_batch_id("", source_adapter)
    if not ingestion_batch_id.endswith(suffix) or len(ingestion_batch_id) == len(suffix):
        return None
    return ingestion_batch_id[: -len(suffix)]


def _pairs_with_primary(row: tuple, batch_rows: Mapping[str, tuple]) -> bool:
    """A supplemental batch row names a primary batch row that declares it, with equal metadata."""

    batch_id, source = row[0], row[1]
    primary_id = paired_primary_batch_id(batch_id, source)
    primary = batch_rows.get(primary_id) if primary_id else None
    return (
        primary is not None
        and source in SUPPLEMENTAL_SOURCES_BY_PRIMARY.get(primary[1], frozenset())
        and tuple(primary[2:]) == tuple(row[2:])
    )


def batch_lineage_problems(
    facts: Iterable[FinancialFact], batch_rows: Mapping[str, tuple]
) -> Tuple[str, ...]:
    """Every fact's batch row matches it, and every supplemental batch pairs with its primary."""

    problems = []
    fact_batches = {_batch_key(fact) for fact in facts}
    unmatched = sorted(key[0] for key in fact_batches if batch_rows.get(key[0]) != key)
    if unmatched:
        problems.append(f"Facts do not match their batch rows: {', '.join(unmatched)}.")
    unpaired = sorted(
        key[0] for key in fact_batches if key[1] in _SUPPLEMENTAL_SOURCES and not _pairs_with_primary(key, batch_rows)
    )
    if unpaired:
        problems.append(
            "Supplemental batches lack a matching primary batch "
            f"(same mapping, calendar, and ingestion time): {', '.join(unpaired)}."
        )
    return tuple(problems)


def publication_batch_problems(
    batch_ids: Sequence[str], batch_rows: Mapping[str, tuple]
) -> Tuple[str, ...]:
    """This publication's batch rows exist, even on a no-op, and its supplements pair with its primary."""

    problems = []
    missing = [batch_id for batch_id in batch_ids if batch_id not in batch_rows]
    if missing:
        problems.append(f"The publication's batch rows are missing: {', '.join(missing)}.")
    unpaired = [
        batch_id
        for batch_id in batch_ids
        if batch_id in batch_rows
        and batch_rows[batch_id][1] in _SUPPLEMENTAL_SOURCES
        and not _pairs_with_primary(batch_rows[batch_id], batch_rows)
    ]
    if unpaired:
        problems.append(f"The publication's supplemental batches lack a matching primary: {', '.join(unpaired)}.")
    return tuple(problems)


def batch_ids_to_read(facts: Iterable[FinancialFact]) -> Tuple[str, ...]:
    """Each fact's batch ID plus, for supplemental batches, the primary ID it names."""

    ids = set()
    for fact in facts:
        ids.add(fact.lineage.ingestion_batch_id)
        primary_id = paired_primary_batch_id(fact.lineage.ingestion_batch_id, fact.lineage.source_adapter)
        if fact.lineage.source_adapter in _SUPPLEMENTAL_SOURCES and primary_id:
            ids.add(primary_id)
    return tuple(sorted(ids))


def prove_incremental_publication(
    *,
    incoming: Sequence[FinancialFact],
    pre_insert: Mapping[tuple, FinancialFact],
    returned: Sequence[FinancialFact],
    stored: Sequence[FinancialFact],
    batch_rows: Mapping[str, tuple],
    batch_keys: Sequence[tuple],
    written_batch_keys: Sequence[tuple],
    knowledge_cutoff: datetime,
    cik: str,
    history_frozen_through: Optional[datetime] = None,
) -> IncrementalPublicationReceipt:
    """Prove the transaction's effect exactly, or raise IncrementalPublicationError."""

    problems = []
    incoming_by_identity: Dict[tuple, FinancialFact] = {}
    for fact in incoming:
        if incoming_by_identity.setdefault(_source_identity_key(fact), fact) is not fact:
            problems.append("The incoming publication repeats a fact identity.")
            break
    incoming_ids = frozenset(incoming_by_identity)
    before_ids = frozenset(pre_insert)
    returned_ids = [_source_identity_key(fact) for fact in returned]
    inserted_ids = frozenset(returned_ids)

    if len(inserted_ids) != len(returned_ids):
        problems.append("The insert returned a fact identity more than once.")
    if inserted_ids & before_ids:
        problems.append(
            f"{len(inserted_ids & before_ids)} inserted facts were already stored before the insert."
        )
    if before_ids - incoming_ids:
        problems.append("The pre-insert lookup returned facts outside the publication.")
    unaccounted = incoming_ids - inserted_ids - before_ids
    if unaccounted:
        problems.append(
            f"{len(unaccounted)} incoming facts were neither inserted nor already stored."
        )
    if inserted_ids - incoming_ids:
        problems.append(f"{len(inserted_ids - incoming_ids)} inserted facts are not in the publication.")

    batch_by_source = {key[1]: key for key in batch_keys}
    misfiled = sum(1 for fact in returned if batch_by_source.get(fact.lineage.source_adapter) != _batch_key(fact))
    if misfiled:
        problems.append(f"{misfiled} inserted facts are not in this publication's batch for their source.")
    changed = sum(
        1
        for fact in returned
        if _source_identity_key(fact) in incoming_by_identity
        and not _same_source_fact(fact, incoming_by_identity[_source_identity_key(fact)])
    )
    if changed:
        problems.append(f"{changed} inserted facts differ from their incoming facts.")
    conflicting = sum(
        1
        for identity, fact in pre_insert.items()
        if identity in incoming_by_identity and not _same_source_fact(fact, incoming_by_identity[identity])
    )
    if conflicting:
        problems.append(f"{conflicting} stored facts conflict with the incoming publication.")

    if history_frozen_through is not None:
        rewritten = sum(1 for fact in returned if fact.provenance.eligible_at <= history_frozen_through)
        if rewritten:
            problems.append(
                f"{rewritten} inserted facts are eligible at or before the frozen historical cutoff "
                f"{history_frozen_through.isoformat()}."
            )
    late = sum(1 for fact in incoming if fact.provenance.eligible_at > knowledge_cutoff)
    if late:
        problems.append(f"{late} incoming facts became public after the knowledge cutoff.")
    stored_ids = frozenset(_source_identity_key(fact) for fact in stored)
    missing, unexpected = len(incoming_ids - stored_ids), len(stored_ids - incoming_ids)
    if missing or unexpected:
        problems.append(
            f"Stored facts at the cutoff differ from the publication: {missing} missing, {unexpected} unexpected."
        )

    problems.extend(batch_lineage_problems(stored, batch_rows))
    written_ids = [key[0] for key in written_batch_keys]
    problems.extend(publication_batch_problems(written_ids, batch_rows))
    inserted_ciks: Dict[str, Counter] = {}
    for fact in returned:
        inserted_ciks.setdefault(fact.lineage.ingestion_batch_id, Counter())[fact.identity.context.entity_cik] += 1
    problems.extend(
        publication_issuer_problems(
            cik, {batch_id: batch_rows[batch_id] for batch_id in written_ids if batch_id in batch_rows}, inserted_ciks
        )
    )

    if not problems:
        try:
            stored_selection = point_in_time_selection(stored, knowledge_cutoff, cik)
            incoming_selection = point_in_time_selection(incoming, knowledge_cutoff, cik)
        except ValueError:
            problems.append("Point-in-time selection refused the stored facts.")
        else:
            if stored_selection != incoming_selection:
                problems.append("Point-in-time selection over the stored facts differs from the publication's.")

    if problems:
        raise IncrementalPublicationError(problems)

    own_batches = {key[0] for key in batch_keys}
    inserted_counts = Counter(fact.lineage.ingestion_batch_id for fact in returned)
    reused_counts = Counter(
        fact.lineage.ingestion_batch_id
        for fact in pre_insert.values()
        if fact.lineage.ingestion_batch_id not in own_batches
    )
    return IncrementalPublicationReceipt(
        batch_ids=tuple(key[0] for key in batch_keys),
        written_batch_ids=tuple(written_ids),
        inserted_by_batch=tuple((batch_id, inserted_counts.get(batch_id, 0)) for batch_id in written_ids),
        reused_by_batch=tuple(sorted(reused_counts.items())),
        replayed_fact_count=sum(
            1 for fact in pre_insert.values() if fact.lineage.ingestion_batch_id in own_batches
        ),
    )


def _insert_returning(cursor, rows) -> list:
    return _execute_values(cursor, rows, sql=INSERT_FACTS_RETURNING_SQL)


def _issuer_sources(batch_keys: Sequence[tuple]) -> list:
    """The primary source and every supplement it declares, published this time or not.

    A supplemental source absent from this publication may still hold stored
    facts; reading it keeps those facts in the stale-fact check.
    """

    primary_source = batch_keys[0][1]  # _publication_batch_keys puts the primary first
    return sorted(SUPPLEMENTAL_SOURCES_BY_PRIMARY.get(primary_source, frozenset()) | {key[1] for key in batch_keys})


def _read_issuer_facts(cursor, cik, batch_keys, knowledge_cutoff) -> list:
    cursor.execute(
        SELECT_ISSUER_FACTS_SQL,
        (cik, _issuer_sources(batch_keys), batch_keys[0][2], batch_keys[0][3], knowledge_cutoff),
    )
    return [_row_to_fact(tuple(row)) for row in cursor.fetchall()]


def _written_batch_keys(batch_keys: Sequence[tuple], to_insert: Sequence[FinancialFact]) -> Tuple[tuple, ...]:
    """Batch rows to write: each source with new facts, plus the primary a written supplement pairs with."""

    sources = {fact.lineage.source_adapter for fact in to_insert}
    if sources & _SUPPLEMENTAL_SOURCES:
        sources.add(batch_keys[0][1])  # _publication_batch_keys puts the primary first
    return tuple(key for key in batch_keys if key[1] in sources)


def _read_batch_rows(cursor, batch_ids: Sequence[str]) -> Dict[str, tuple]:
    cursor.execute(SELECT_BATCH_ROWS_SQL, (list(batch_ids),))
    return {row[0]: tuple(row) for row in cursor.fetchall()}


def publish_incremental(
    facts: Iterable[FinancialFact],
    *,
    knowledge_cutoff: datetime,
    history_frozen_through: Optional[datetime] = None,
    database_url: Optional[str] = None,
    conn=None,
    prove: Callable[..., IncrementalPublicationReceipt] = prove_incremental_publication,
) -> IncrementalPublicationReceipt:
    """Atomically publish one issuer's complete history, proving the increment before commit."""

    facts = tuple(facts)
    if not facts:
        raise ValueError("publish_incremental requires at least one fact.")
    for value in (knowledge_cutoff, history_frozen_through):
        if value is not None and (not isinstance(value, datetime) or not is_aware(value)):
            raise ValueError("cutoffs must be timezone-aware datetimes.")
    if history_frozen_through is not None and history_frozen_through >= knowledge_cutoff:
        raise ValueError("history_frozen_through must precede the knowledge cutoff.")
    ciks = {fact.identity.context.entity_cik for fact in facts}
    if len(ciks) != 1:
        raise FundamentalsPublishError("an incremental publication must cover exactly one issuer.")
    (cik,) = ciks
    batch_keys = _publication_batch_keys(facts)

    def serialize(cursor) -> None:
        # Before ensure_schema's DDL, and with its own wait bound.
        cursor.execute(SET_LOCAL_STATEMENT_TIMEOUT_SQL, (str(PUBLISH_LOCK_WAIT_MS),))
        cursor.execute(LOCK_PUBLICATIONS_SQL)
        cursor.execute(SET_LOCAL_STATEMENT_TIMEOUT_SQL, (str(PUBLISH_STATEMENT_TIMEOUT_MS),))

    def work(cursor) -> IncrementalPublicationReceipt:
        # The pre-insert state decides what to write before anything is written.
        incoming_ids = {_source_identity_key(fact) for fact in facts}
        pre_insert = {
            _source_identity_key(fact): fact
            for fact in _read_issuer_facts(cursor, cik, batch_keys, knowledge_cutoff)
            if _source_identity_key(fact) in incoming_ids
        }
        to_insert = [fact for fact in facts if _source_identity_key(fact) not in pre_insert]
        written_batch_keys = _written_batch_keys(batch_keys, to_insert)
        returned = []
        if written_batch_keys:
            insert_publication_batches(cursor, written_batch_keys)
            returned = [_row_to_fact(tuple(row)) for row in _insert_returning(cursor, [_fact_to_row(fact) for fact in to_insert])]
        stored = _read_issuer_facts(cursor, cik, batch_keys, knowledge_cutoff)
        batch_rows = _read_batch_rows(cursor, sorted(set(batch_ids_to_read(stored)) | {key[0] for key in batch_keys}))
        return prove(
            incoming=facts,
            pre_insert=pre_insert,
            returned=returned,
            stored=stored,
            batch_rows=batch_rows,
            batch_keys=batch_keys,
            written_batch_keys=written_batch_keys,
            knowledge_cutoff=knowledge_cutoff,
            cik=cik,
            history_frozen_through=history_frozen_through,
        )

    return run_publish_transaction(work, database_url=database_url, conn=conn, before_schema=serialize)


def publication_issuer_problems(
    cik: str, batch_rows: Mapping[str, tuple], fact_ciks: Mapping[str, Mapping[str, int]]
) -> Tuple[str, ...]:
    """Bind each present batch to one issuer through facts, the only stored issuer provenance.

    ``batch_rows`` are one publication's batch rows that exist; ``fact_ciks``
    maps each batch ID to the issuers of every fact it holds, across all
    issuers. Each batch must hold a fact of ``cik``, except a primary batch
    whose paired supplemental batch does. Any other issuer's fact refuses.
    """

    problems = []
    foreign = sorted({other for batch_id in batch_rows for other in fact_ciks.get(batch_id, {}) if other != cik})
    if foreign:
        problems.append(f"The publication's batches hold facts for other issuers: {', '.join(foreign)}.")
    holds_facts = {batch_id for batch_id in batch_rows if fact_ciks.get(batch_id)}
    unbound = []
    for batch_id, row in sorted(batch_rows.items()):
        if batch_id in holds_facts:
            continue
        paired = row[1] not in _SUPPLEMENTAL_SOURCES and any(
            paired_primary_batch_id(other, other_row[1]) == batch_id and other in holds_facts
            for other, other_row in batch_rows.items()
            if other_row[1] in _SUPPLEMENTAL_SOURCES
        )
        if not paired:
            unbound.append(batch_id)
    if unbound:
        problems.append(
            f"Batches {', '.join(unbound)} hold no facts and no paired batch does, so stored provenance "
            "cannot establish their issuer; a zero-fact batch cannot be verified."
        )
    return tuple(problems)


def read_batch_rows(batch_ids: Sequence[str], *, database_url: Optional[str] = None) -> Dict[str, tuple]:
    """Read-only lookup of ingestion batch rows, for post-publication verification."""

    return run_read_transaction(lambda cursor: _read_batch_rows(cursor, batch_ids), database_url=database_url)


def read_batch_fact_ciks(
    batch_ids: Sequence[str], *, database_url: Optional[str] = None
) -> Dict[str, Dict[str, int]]:
    """Read-only: the issuers of every fact in each batch, across all issuers."""

    def read(cursor):
        cursor.execute(SELECT_BATCH_FACT_CIKS_SQL, (list(batch_ids),))
        result: Dict[str, Dict[str, int]] = {}
        for batch_id, cik, count in cursor.fetchall():
            result.setdefault(batch_id, {})[cik] = int(count)
        return result

    return run_read_transaction(read, database_url=database_url)
