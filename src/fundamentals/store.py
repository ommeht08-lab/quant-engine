"""
Append-only PostgreSQL persistence for normalized point-in-time facts.

The live read path is bounded, explicitly read-only, and never creates
schema. The offline publish path owns schema creation and inserts complete
ingestion batches atomically. Database exceptions are converted to fixed,
sanitized domain errors so a DSN can never leak through an API response or log.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Iterable, Optional, Tuple

from .repository import FundamentalsQuery, FundamentalsRepositoryUnavailable
from .types import (
    FactContext,
    FactIdentity,
    FactLineage,
    FilingProvenance,
    FinancialFact,
    StatementKind,
    StatementPeriod,
)

logger = logging.getLogger(__name__)

READ_APPLICATION_NAME = "valuation-engine-fundamentals-read"
READ_CONNECT_TIMEOUT_SECONDS = 3
READ_STATEMENT_TIMEOUT_MS = 3000

PUBLISH_APPLICATION_NAME = "valuation-engine-fundamentals-publish"
PUBLISH_CONNECT_TIMEOUT_SECONDS = 10
PUBLISH_STATEMENT_TIMEOUT_MS = 15000

CREATE_INGESTION_BATCH_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fundamentals_ingestion_batches (
    ingestion_batch_id TEXT PRIMARY KEY,
    source_adapter TEXT NOT NULL,
    concept_map_version TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    UNIQUE (ingestion_batch_id, source_adapter, concept_map_version, ingested_at)
);
"""

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fundamentals_facts (
    id BIGSERIAL PRIMARY KEY,
    cik TEXT NOT NULL CHECK (cik ~ '^[0-9]{10}$'),
    statement_kind TEXT NOT NULL CHECK (
        statement_kind IN ('income_statement', 'balance_sheet', 'cash_flow', 'cover')
    ),
    canonical_concept TEXT NOT NULL,
    raw_tag TEXT NOT NULL,
    taxonomy TEXT NOT NULL,
    unit TEXT NOT NULL,
    currency TEXT,
    dimensions JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(dimensions) = 'array'),
    value NUMERIC NOT NULL,
    period_start DATE,
    period_end DATE NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_period TEXT NOT NULL,
    periodicity TEXT,
    accession_number TEXT NOT NULL,
    form_type TEXT NOT NULL,
    is_amendment BOOLEAN NOT NULL,
    filed_date DATE NOT NULL,
    accepted_at TIMESTAMPTZ,
    eligible_at TIMESTAMPTZ NOT NULL,
    source_adapter TEXT NOT NULL,
    source_document_url TEXT NOT NULL,
    concept_map_version TEXT NOT NULL,
    ingestion_batch_id TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (
        ingestion_batch_id, source_adapter, concept_map_version, ingested_at
    ) REFERENCES fundamentals_ingestion_batches (
        ingestion_batch_id, source_adapter, concept_map_version, ingested_at
    ),
    CHECK (
        (
            statement_kind = 'cover'
            AND fiscal_period = 'COVER'
            AND period_start IS NULL
            AND periodicity IS NULL
        ) OR (
            statement_kind = 'balance_sheet'
            AND fiscal_period <> 'COVER'
            AND period_start IS NULL
            AND periodicity IS NOT NULL
        ) OR (
            statement_kind IN ('income_statement', 'cash_flow')
            AND fiscal_period <> 'COVER'
            AND period_start IS NOT NULL
            AND periodicity IS NOT NULL
        )
    ),
    UNIQUE NULLS NOT DISTINCT (
        cik, source_adapter, concept_map_version, statement_kind,
        canonical_concept, raw_tag, taxonomy, period_start, period_end,
        unit, currency, dimensions, accession_number
    )
);
"""

CREATE_PIT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS fundamentals_facts_pit_idx
ON fundamentals_facts (
    cik, source_adapter, concept_map_version, eligible_at, ingested_at, period_end DESC
);
"""

CREATE_BATCH_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS fundamentals_facts_batch_idx
ON fundamentals_facts (ingestion_batch_id, ingested_at);
"""

CREATE_APPEND_ONLY_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION reject_fundamentals_fact_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'fundamentals_facts is append-only; publish a new concept-map version';
END;
$function$;
"""

CREATE_APPEND_ONLY_TRIGGER_SQL = """
DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'fundamentals_facts_append_only'
          AND tgrelid = 'fundamentals_facts'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER fundamentals_facts_append_only
        BEFORE UPDATE OR DELETE ON fundamentals_facts
        FOR EACH ROW EXECUTE FUNCTION reject_fundamentals_fact_mutation();
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'fundamentals_ingestion_batches_append_only'
          AND tgrelid = 'fundamentals_ingestion_batches'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER fundamentals_ingestion_batches_append_only
        BEFORE UPDATE OR DELETE ON fundamentals_ingestion_batches
        FOR EACH ROW EXECUTE FUNCTION reject_fundamentals_fact_mutation();
    END IF;
END;
$block$;
"""

INSERT_BATCH_SQL = """
INSERT INTO fundamentals_ingestion_batches (
    ingestion_batch_id, source_adapter, concept_map_version, ingested_at
)
VALUES (%s, %s, %s, %s)
ON CONFLICT DO NOTHING
RETURNING ingestion_batch_id;
"""

SELECT_BATCH_SQL = """
SELECT ingestion_batch_id, source_adapter, concept_map_version, ingested_at
FROM fundamentals_ingestion_batches
WHERE ingestion_batch_id = %s;
"""

_INSERT_COLUMNS = (
    "cik",
    "statement_kind",
    "canonical_concept",
    "raw_tag",
    "taxonomy",
    "unit",
    "currency",
    "dimensions",
    "value",
    "period_start",
    "period_end",
    "fiscal_year",
    "fiscal_period",
    "periodicity",
    "accession_number",
    "form_type",
    "is_amendment",
    "filed_date",
    "accepted_at",
    "eligible_at",
    "source_adapter",
    "source_document_url",
    "concept_map_version",
    "ingestion_batch_id",
    "ingested_at",
)

INSERT_FACT_SQL = f"""
INSERT INTO fundamentals_facts ({', '.join(_INSERT_COLUMNS)})
VALUES ({', '.join(['%s'] * len(_INSERT_COLUMNS))})
ON CONFLICT DO NOTHING
RETURNING id;
"""

SELECT_EXISTING_FACT_SQL = f"""
SELECT {', '.join(_INSERT_COLUMNS)}
FROM fundamentals_facts
WHERE cik = %s
  AND source_adapter = %s
  AND concept_map_version = %s
  AND statement_kind = %s
  AND canonical_concept = %s
  AND raw_tag = %s
  AND taxonomy = %s
  AND period_start IS NOT DISTINCT FROM %s
  AND period_end = %s
  AND unit = %s
  AND currency IS NOT DISTINCT FROM %s
  AND dimensions = %s
  AND accession_number = %s;
"""

SELECT_FACTS_SQL = f"""
WITH eligible AS (
    SELECT {', '.join(_INSERT_COLUMNS)},
           DENSE_RANK() OVER (
               PARTITION BY statement_kind
               ORDER BY period_end DESC,
                        period_start DESC NULLS LAST,
                        periodicity DESC NULLS LAST,
                        fiscal_year DESC,
                        fiscal_period DESC
           ) AS statement_period_rank
    FROM fundamentals_facts
    WHERE cik = %s
      AND canonical_concept = ANY(%s)
      AND source_adapter = %s
      AND concept_map_version = %s
      AND eligible_at <= %s
      AND ingested_at <= %s
), bounded AS (
    SELECT *
    FROM eligible
    WHERE statement_period_rank <= %s
)
SELECT {', '.join(_INSERT_COLUMNS)}
FROM bounded
ORDER BY statement_kind,
         period_end,
         period_start NULLS FIRST,
         periodicity NULLS FIRST,
         fiscal_year,
         fiscal_period,
         canonical_concept,
         unit,
         currency NULLS FIRST,
         dimensions,
         eligible_at,
         accession_number,
         raw_tag,
         taxonomy;
"""


class FundamentalsPublishError(RuntimeError):
    """A sanitized, atomic publish failure."""


def _get_database_url() -> Optional[str]:
    from dotenv import load_dotenv

    load_dotenv()
    return os.getenv("DATABASE_URL") or None


def _connect(
    database_url: str,
    *,
    application_name: str,
    connect_timeout_seconds: int,
    statement_timeout_ms: int,
):
    import psycopg2

    return psycopg2.connect(
        database_url,
        application_name=application_name,
        connect_timeout=connect_timeout_seconds,
        options=f"-c statement_timeout={statement_timeout_ms}",
    )


def _json_dimensions(dimensions):
    from psycopg2.extras import Json

    return Json([list(dimension) for dimension in dimensions])


def _rollback_quietly(connection) -> None:
    try:
        connection.rollback()
    except Exception as error:
        logger.error("point-in-time fundamentals rollback failed (%s)", type(error).__name__)


def _close_quietly(connection) -> None:
    try:
        connection.close()
    except Exception as error:
        logger.error("point-in-time fundamentals connection close failed (%s)", type(error).__name__)


def _fact_to_row(fact: FinancialFact) -> tuple:
    return (
        fact.identity.context.entity_cik,
        fact.statement_kind.value,
        fact.identity.concept,
        fact.raw_tag,
        fact.taxonomy,
        fact.identity.unit,
        fact.identity.currency,
        _json_dimensions(fact.identity.context.dimensions),
        fact.value,
        fact.period.period_start,
        fact.period.period_end,
        fact.period.fiscal_year,
        fact.period.fiscal_period,
        fact.period.periodicity,
        fact.provenance.accession_number,
        fact.provenance.form_type,
        fact.provenance.is_amendment,
        fact.provenance.filed_date,
        fact.provenance.accepted_at,
        fact.provenance.eligible_at,
        fact.lineage.source_adapter,
        fact.lineage.source_document_url,
        fact.lineage.concept_map_version,
        fact.lineage.ingestion_batch_id,
        fact.lineage.ingested_at,
    )


def _row_to_fact(row: tuple) -> FinancialFact:
    if len(row) != len(_INSERT_COLUMNS):
        raise ValueError("fundamentals store returned an unexpected row shape.")

    (
        cik,
        statement_kind,
        canonical_concept,
        raw_tag,
        taxonomy,
        unit,
        currency,
        dimensions,
        value,
        period_start,
        period_end,
        fiscal_year,
        fiscal_period,
        periodicity,
        accession_number,
        form_type,
        is_amendment,
        filed_date,
        accepted_at,
        stored_eligible_at,
        source_adapter,
        source_document_url,
        concept_map_version,
        ingestion_batch_id,
        ingested_at,
    ) = row

    period = StatementPeriod(
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_start=period_start,
        period_end=period_end,
        periodicity=periodicity,
    )
    provenance = FilingProvenance(
        accession_number=accession_number,
        form_type=form_type,
        is_amendment=is_amendment,
        filed_date=filed_date,
        accepted_at=accepted_at,
    )
    if provenance.eligible_at != stored_eligible_at:
        raise ValueError("stored fact eligibility contradicts its filing provenance.")

    identity = FactIdentity(
        concept=canonical_concept,
        period_start=period_start,
        period_end=period_end,
        unit=unit,
        currency=currency,
        context=FactContext(
            entity_cik=cik,
            dimensions=tuple((str(axis), str(member)) for axis, member in dimensions),
        ),
    )
    return FinancialFact(
        statement_kind=StatementKind(statement_kind),
        period=period,
        identity=identity,
        value=value if isinstance(value, Decimal) else Decimal(str(value)),
        raw_tag=raw_tag,
        taxonomy=taxonomy,
        provenance=provenance,
        lineage=FactLineage(
            source_adapter=source_adapter,
            source_document_url=source_document_url,
            concept_map_version=concept_map_version,
            ingestion_batch_id=ingestion_batch_id,
            ingested_at=ingested_at,
        ),
    )


def _existing_lookup_params(fact: FinancialFact) -> tuple:
    return (
        fact.identity.context.entity_cik,
        fact.lineage.source_adapter,
        fact.lineage.concept_map_version,
        fact.statement_kind.value,
        fact.identity.concept,
        fact.raw_tag,
        fact.taxonomy,
        fact.period.period_start,
        fact.period.period_end,
        fact.identity.unit,
        fact.identity.currency,
        _json_dimensions(fact.identity.context.dimensions),
        fact.provenance.accession_number,
    )


def _same_source_fact(existing: FinancialFact, incoming: FinancialFact) -> bool:
    """Ignore only ingestion batch/time when checking an idempotent replay."""
    return (
        existing.statement_kind == incoming.statement_kind
        and existing.period == incoming.period
        and existing.identity == incoming.identity
        and existing.value == incoming.value
        and existing.raw_tag == incoming.raw_tag
        and existing.taxonomy == incoming.taxonomy
        and existing.provenance == incoming.provenance
        and existing.lineage.source_adapter == incoming.lineage.source_adapter
        and existing.lineage.source_document_url == incoming.lineage.source_document_url
        and existing.lineage.concept_map_version == incoming.lineage.concept_map_version
    )


def ensure_schema(conn) -> None:
    """Create the append-only schema from an offline publish connection."""
    with conn.cursor() as cursor:
        cursor.execute(CREATE_INGESTION_BATCH_TABLE_SQL)
        cursor.execute(CREATE_TABLE_SQL)
        cursor.execute(CREATE_PIT_INDEX_SQL)
        cursor.execute(CREATE_BATCH_INDEX_SQL)
        cursor.execute(CREATE_APPEND_ONLY_FUNCTION_SQL)
        cursor.execute(CREATE_APPEND_ONLY_TRIGGER_SQL)


def append_facts(
    facts: Iterable[FinancialFact],
    *,
    database_url: Optional[str] = None,
    conn=None,
) -> int:
    """Atomically append one internally-consistent ingestion batch."""
    facts = tuple(facts)
    if not facts:
        raise ValueError("append_facts requires at least one fact.")

    batch_keys = {
        (
            fact.lineage.ingestion_batch_id,
            fact.lineage.source_adapter,
            fact.lineage.concept_map_version,
            fact.lineage.ingested_at,
        )
        for fact in facts
    }
    if len(batch_keys) != 1:
        raise ValueError("append_facts requires one source, mapping version, batch ID, and ingestion time.")

    owns_connection = conn is None
    connection = conn
    try:
        if connection is None:
            resolved_url = database_url or _get_database_url()
            if not resolved_url:
                raise FundamentalsPublishError("point-in-time fundamentals database is not configured.")
            connection = _connect(
                resolved_url,
                application_name=PUBLISH_APPLICATION_NAME,
                connect_timeout_seconds=PUBLISH_CONNECT_TIMEOUT_SECONDS,
                statement_timeout_ms=PUBLISH_STATEMENT_TIMEOUT_MS,
            )

        connection.set_session(readonly=False, autocommit=False)
        ensure_schema(connection)
        inserted = 0
        with connection.cursor() as cursor:
            batch_key = next(iter(batch_keys))
            cursor.execute(INSERT_BATCH_SQL, batch_key)
            if cursor.fetchone() is None:
                cursor.execute(SELECT_BATCH_SQL, (batch_key[0],))
                if cursor.fetchone() != batch_key:
                    raise FundamentalsPublishError(
                        "an ingestion batch ID already exists with different immutable metadata."
                    )
            for fact in facts:
                cursor.execute(INSERT_FACT_SQL, _fact_to_row(fact))
                if cursor.fetchone() is not None:
                    inserted += 1
                    continue

                cursor.execute(SELECT_EXISTING_FACT_SQL, _existing_lookup_params(fact))
                existing_row = cursor.fetchone()
                if existing_row is None or not _same_source_fact(_row_to_fact(existing_row), fact):
                    raise FundamentalsPublishError(
                        "an existing point-in-time fact conflicts with the incoming batch."
                    )
        connection.commit()
        return inserted
    except FundamentalsPublishError:
        if connection is not None:
            _rollback_quietly(connection)
        raise
    except Exception as error:
        logger.error("point-in-time fundamentals publish failed (%s)", type(error).__name__)
        if connection is not None:
            _rollback_quietly(connection)
        raise FundamentalsPublishError("point-in-time fundamentals publish failed.") from None
    finally:
        if owns_connection and connection is not None:
            _close_quietly(connection)


class PostgresFundamentalsRepository:
    """Production read adapter for FundamentalsRepository."""

    def __init__(self, database_url: Optional[str] = None):
        self._database_url = database_url

    def get_facts(self, query: FundamentalsQuery) -> Tuple[FinancialFact, ...]:
        database_url = self._database_url or _get_database_url()
        if not database_url:
            raise FundamentalsRepositoryUnavailable(
                "point-in-time fundamentals store is not configured."
            )

        connection = None
        try:
            connection = _connect(
                database_url,
                application_name=READ_APPLICATION_NAME,
                connect_timeout_seconds=READ_CONNECT_TIMEOUT_SECONDS,
                statement_timeout_ms=READ_STATEMENT_TIMEOUT_MS,
            )
            # Must precede cursor creation and the first SQL statement so the
            # database enforces read-only behavior for this transaction.
            connection.set_session(readonly=True, autocommit=False)
            with connection.cursor() as cursor:
                cursor.execute(
                    SELECT_FACTS_SQL,
                    (
                        query.cik,
                        list(query.concepts),
                        query.source_adapter,
                        query.concept_map_version,
                        query.knowledge_cutoff,
                        query.data_vintage_cutoff,
                        query.max_periods_per_statement,
                    ),
                )
                rows = cursor.fetchall()
            connection.rollback()
            return tuple(_row_to_fact(tuple(row)) for row in rows)
        except FundamentalsRepositoryUnavailable:
            raise
        except Exception as error:
            logger.error("point-in-time fundamentals read failed (%s)", type(error).__name__)
            if connection is not None:
                _rollback_quietly(connection)
            raise FundamentalsRepositoryUnavailable(
                "point-in-time fundamentals store is unavailable."
            ) from None
        finally:
            if connection is not None:
                _close_quietly(connection)
