import datetime as dt

import pytest

from src.fundamentals.adapters.fixture import (
    DEFAULT_TEST_CIK,
    make_fact,
    make_lineage,
    make_period,
    make_provenance,
)
from src.fundamentals.repository import (
    FundamentalIssue,
    FundamentalIssueCode,
    FundamentalsLoadResult,
    FundamentalsQuery,
    InMemoryFundamentalsRepository,
)
from src.fundamentals.selection import select_point_in_time
from src.fundamentals.time_policy import US_EASTERN
from src.fundamentals.types import FactLineage, StatementKind

CIK = DEFAULT_TEST_CIK
OTHER_CIK = "0002222222"
KNOWLEDGE_CUTOFF = dt.datetime(2025, 3, 1, 16, 0, tzinfo=US_EASTERN)
DATA_VINTAGE_CUTOFF = dt.datetime(2025, 3, 2, tzinfo=dt.timezone.utc)


def _query(**overrides):
    values = {
        "cik": CIK,
        "knowledge_cutoff": KNOWLEDGE_CUTOFF,
        "data_vintage_cutoff": DATA_VINTAGE_CUTOFF,
        "concepts": ("revenue", "total_assets", "operating_cash_flow", "shares_outstanding"),
        "source_adapter": "sec_edgar",
        "concept_map_version": "sec-v1",
        "max_periods_per_statement": 8,
    }
    values.update(overrides)
    return FundamentalsQuery(**values)


def _provenance(year: int, sequence: int = 1):
    filed = dt.date(year + 1, 2, 1)
    return make_provenance(
        accession_number=f"0001111111-{str(year + 1)[-2:]}-{sequence:06d}",
        filed_date=filed,
        accepted_at=dt.datetime(year + 1, 2, 1, 10, 0, tzinfo=US_EASTERN),
    )


def _lineage(
    *,
    source_adapter="sec_edgar",
    concept_map_version="sec-v1",
    ingestion_batch_id="batch-001",
    ingested_at=dt.datetime(2025, 2, 20, tzinfo=dt.timezone.utc),
):
    return make_lineage(
        source_adapter=source_adapter,
        source_document_url="https://www.sec.gov/Archives/fixture.json",
        concept_map_version=concept_map_version,
        ingestion_batch_id=ingestion_batch_id,
        ingested_at=ingested_at,
    )


def _period(statement_kind: StatementKind, year: int):
    if statement_kind in (StatementKind.BALANCE_SHEET, StatementKind.COVER):
        return make_period(
            fiscal_year=year,
            fiscal_period="COVER" if statement_kind is StatementKind.COVER else "FY",
            period_end=dt.date(year, 12, 31),
            periodicity=None if statement_kind is StatementKind.COVER else "annual",
        )
    return make_period(
        fiscal_year=year,
        fiscal_period="FY",
        period_start=dt.date(year, 1, 1),
        period_end=dt.date(year, 12, 31),
        periodicity="annual",
    )


def _fact(
    statement_kind: StatementKind,
    concept: str,
    year: int,
    *,
    value="100",
    provenance=None,
    lineage=None,
    entity_cik=CIK,
):
    return make_fact(
        statement_kind=statement_kind,
        concept=concept,
        period=_period(statement_kind, year),
        value=value,
        provenance=provenance or _provenance(year),
        lineage=lineage or _lineage(),
        entity_cik=entity_cik,
        unit="shares" if statement_kind is StatementKind.COVER else "USD",
        currency=None if statement_kind is StatementKind.COVER else "USD",
    )


class TestFactLineage:
    @pytest.mark.parametrize(
        "field_name",
        ("source_adapter", "source_document_url", "concept_map_version", "ingestion_batch_id"),
    )
    def test_required_text_fields_reject_empty_values(self, field_name):
        values = {
            "source_adapter": "sec_edgar",
            "source_document_url": "https://www.sec.gov/fixture",
            "concept_map_version": "sec-v1",
            "ingestion_batch_id": "batch-001",
            "ingested_at": DATA_VINTAGE_CUTOFF,
        }
        values[field_name] = ""
        with pytest.raises(ValueError, match=field_name):
            FactLineage(**values)

    def test_ingested_at_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            make_lineage(ingested_at=dt.datetime(2025, 1, 1))


class TestFundamentalsQuery:
    def test_normalizes_cik_and_sorts_concepts(self):
        query = _query(cik="1111111", concepts=("total_assets", "revenue"))
        assert query.cik == CIK
        assert query.concepts == ("revenue", "total_assets")

    @pytest.mark.parametrize("field_name", ("knowledge_cutoff", "data_vintage_cutoff"))
    def test_cutoffs_must_be_timezone_aware(self, field_name):
        with pytest.raises(ValueError, match="timezone-aware"):
            _query(**{field_name: dt.datetime(2025, 1, 1)})

    @pytest.mark.parametrize("concepts", ((), ("revenue", ""), ("revenue", "revenue")))
    def test_concepts_must_be_nonempty_unique_names(self, concepts):
        with pytest.raises(ValueError):
            _query(concepts=concepts)

    @pytest.mark.parametrize("value", (0, -1, True, 1.5))
    def test_max_periods_must_be_a_positive_genuine_integer(self, value):
        with pytest.raises(ValueError):
            _query(max_periods_per_statement=value)


class TestInMemoryFundamentalsRepository:
    def test_filters_by_every_time_and_lineage_dimension(self):
        included = _fact(StatementKind.INCOME_STATEMENT, "revenue", 2023)
        wrong_issuer = _fact(
            StatementKind.INCOME_STATEMENT, "revenue", 2023, entity_cik=OTHER_CIK
        )
        future_filing = _fact(StatementKind.INCOME_STATEMENT, "revenue", 2025)
        future_ingestion = _fact(
            StatementKind.INCOME_STATEMENT,
            "revenue",
            2023,
            lineage=_lineage(
                ingestion_batch_id="batch-future",
                ingested_at=dt.datetime(2025, 3, 3, tzinfo=dt.timezone.utc),
            ),
        )
        wrong_source = _fact(
            StatementKind.INCOME_STATEMENT,
            "revenue",
            2023,
            lineage=_lineage(source_adapter="other_source"),
        )
        wrong_mapping = _fact(
            StatementKind.INCOME_STATEMENT,
            "revenue",
            2023,
            lineage=_lineage(concept_map_version="sec-v2"),
        )
        unrequested = _fact(StatementKind.INCOME_STATEMENT, "net_income", 2023)

        repository = InMemoryFundamentalsRepository(
            (
                included,
                wrong_issuer,
                future_filing,
                future_ingestion,
                wrong_source,
                wrong_mapping,
                unrequested,
            )
        )

        assert repository.get_facts(_query(concepts=("revenue",))) == (included,)

    def test_bounds_each_statement_and_cover_independently(self):
        facts = []
        definitions = (
            (StatementKind.INCOME_STATEMENT, "revenue"),
            (StatementKind.BALANCE_SHEET, "total_assets"),
            (StatementKind.CASH_FLOW, "operating_cash_flow"),
            (StatementKind.COVER, "shares_outstanding"),
        )
        for statement_kind, concept in definitions:
            for year in (2021, 2022, 2023):
                facts.append(_fact(statement_kind, concept, year))

        result = InMemoryFundamentalsRepository(facts).get_facts(
            _query(max_periods_per_statement=1)
        )

        assert len(result) == 4
        assert {fact.statement_kind for fact in result} == set(StatementKind)
        assert {fact.period.fiscal_year for fact in result} == {2023}

    def test_keeps_all_accessions_for_a_bounded_period_for_central_selection(self):
        original = _fact(
            StatementKind.INCOME_STATEMENT,
            "revenue",
            2023,
            value="100",
            provenance=_provenance(2023, sequence=1),
        )
        amendment = _fact(
            StatementKind.INCOME_STATEMENT,
            "revenue",
            2023,
            value="110",
            provenance=make_provenance(
                accession_number="0001111111-24-000099",
                filed_date=dt.date(2024, 3, 1),
                accepted_at=dt.datetime(2024, 3, 1, 10, 0, tzinfo=US_EASTERN),
                form_type="10-K/A",
                is_amendment=True,
            ),
        )
        candidates = InMemoryFundamentalsRepository((amendment, original)).get_facts(
            _query(concepts=("revenue",), max_periods_per_statement=1)
        )

        assert set(candidates) == {original, amendment}
        history = select_point_in_time(candidates, KNOWLEDGE_CUTOFF, cik=CIK)
        assert history.income_statement_periods[0].facts[0].value == amendment.value


class TestFundamentalsLoadResult:
    def test_complete_result_requires_matching_history(self):
        fact = _fact(StatementKind.INCOME_STATEMENT, "revenue", 2023)
        query = _query(concepts=("revenue",))
        history = select_point_in_time((fact,), query.knowledge_cutoff, cik=query.cik)
        result = FundamentalsLoadResult(query=query, history=history)
        assert result.is_complete is True
        assert result.issues == ()

    def test_refusal_result_requires_at_least_one_typed_issue(self):
        query = _query()
        issue = FundamentalIssue(
            code=FundamentalIssueCode.MISSING_REQUIRED_CONCEPT,
            message="Revenue was not reported for the required annual period.",
            concepts=("revenue",),
        )
        result = FundamentalsLoadResult(query=query, issues=(issue,))
        assert result.is_complete is False
        assert result.history is None

    @pytest.mark.parametrize(
        "history,issues",
        ((None, ()), ("history-placeholder", (FundamentalIssue(FundamentalIssueCode.STORE_UNAVAILABLE, "x"),))),
    )
    def test_ambiguous_or_empty_results_are_rejected(self, history, issues):
        with pytest.raises(ValueError, match="either one complete history or refusal issues"):
            FundamentalsLoadResult(query=_query(), history=history, issues=issues)
