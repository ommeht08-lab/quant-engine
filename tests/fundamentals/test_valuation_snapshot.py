import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from src.fundamentals.adapters.fixture import make_fact, make_lineage, make_period, make_provenance
from src.fundamentals.repository import (
    FundamentalsRepositoryUnavailable,
    InMemoryFundamentalsRepository,
)
from src.fundamentals.types import StatementKind
from src.fundamentals.valuation_snapshot import (
    VALUATION_FUNDAMENTALS_CONCEPTS,
    ValuationFundamentalsRequest,
    ValuationSnapshotIssueCode,
    load_valuation_fundamentals_snapshot,
)


CIK = "0001111111"
KNOWLEDGE_CUTOFF = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
DATA_VINTAGE_CUTOFF = dt.datetime(2025, 1, 2, tzinfo=dt.timezone.utc)
LINEAGE = make_lineage(
    source_adapter="sec_edgar_companyfacts",
    concept_map_version="sec-companyfacts-v2",
    fiscal_calendar_version="calendar-fy2023-fy2024",
    ingested_at=dt.datetime(2024, 11, 1, tzinfo=dt.timezone.utc),
)
FY2023 = (
    dt.date(2022, 10, 2),
    dt.date(2022, 12, 31),
    dt.date(2023, 4, 1),
    dt.date(2023, 7, 1),
    dt.date(2023, 9, 30),
)
FY2024 = (
    dt.date(2023, 10, 1),
    dt.date(2023, 12, 30),
    dt.date(2024, 3, 30),
    dt.date(2024, 6, 29),
    dt.date(2024, 9, 28),
)


def _request(**overrides):
    values = {
        "cik": CIK,
        "knowledge_cutoff": KNOWLEDGE_CUTOFF,
        "data_vintage_cutoff": DATA_VINTAGE_CUTOFF,
        "source_adapter": "sec_edgar_companyfacts",
        "concept_map_version": "sec-companyfacts-v2",
        "fiscal_calendar_version": "calendar-fy2023-fy2024",
    }
    values.update(overrides)
    return ValuationFundamentalsRequest(**values)


def _provenance(fiscal_year, quarter=None):
    sequence = quarter or 9
    return make_provenance(
        accession_number=f"0001111111-{str(fiscal_year)[-2:]}-{sequence:06d}",
        filed_date=dt.date(fiscal_year, min(sequence + 1, 12), 1),
        accepted_at=dt.datetime(
            fiscal_year, min(sequence + 1, 12), 1, tzinfo=dt.timezone.utc
        ),
        form_type="10-Q" if quarter else "10-K",
    )


def _duration_year(fiscal_year, dates, values, *, concept, statement_kind):
    start, q1_end, q2_end, q3_end, fy_end = dates
    starts = (start, q1_end + dt.timedelta(days=1), q2_end + dt.timedelta(days=1))
    facts = []
    for quarter, (period_start, period_end, value) in enumerate(
        zip(starts, (q1_end, q2_end, q3_end), values[:3]), start=1
    ):
        facts.append(
            make_fact(
                statement_kind=statement_kind,
                concept=concept,
                period=make_period(
                    fiscal_year=fiscal_year,
                    fiscal_period=f"Q{quarter}",
                    period_start=period_start,
                    period_end=period_end,
                    periodicity="quarterly",
                ),
                value=value,
                provenance=_provenance(fiscal_year, quarter),
                entity_cik=CIK,
                lineage=LINEAGE,
            )
        )
    facts.append(
        make_fact(
            statement_kind=statement_kind,
            concept=concept,
            period=make_period(
                fiscal_year=fiscal_year,
                fiscal_period="FY",
                period_start=start,
                period_end=fy_end,
                periodicity="annual",
            ),
            value=values[3],
            provenance=_provenance(fiscal_year),
            entity_cik=CIK,
            lineage=LINEAGE,
        )
    )
    return facts


def _balance_fact(concept, value):
    return make_fact(
        statement_kind=StatementKind.BALANCE_SHEET,
        concept=concept,
        period=make_period(
            fiscal_year=2024,
            fiscal_period="FY",
            period_end=FY2024[-1],
            periodicity="annual",
        ),
        value=value,
        provenance=_provenance(2024),
        entity_cik=CIK,
        lineage=LINEAGE,
    )


def _complete_facts():
    facts = []
    series = (
        ("revenue", StatementKind.INCOME_STATEMENT, (100, 100, 100, 400), (100, 100, 100, 500)),
        (
            "operating_income",
            StatementKind.INCOME_STATEMENT,
            (20, 20, 20, 80),
            (20, 20, 20, 100),
        ),
        (
            "operating_cash_flow",
            StatementKind.CASH_FLOW,
            (30, 30, 30, 120),
            (30, 30, 30, 150),
        ),
        (
            "capital_expenditures",
            StatementKind.CASH_FLOW,
            (10, 10, 10, 40),
            (10, 10, 10, 50),
        ),
    )
    for concept, statement_kind, fy2023_values, fy2024_values in series:
        facts.extend(
            _duration_year(
                2023,
                FY2023,
                fy2023_values,
                concept=concept,
                statement_kind=statement_kind,
            )
        )
        facts.extend(
            _duration_year(
                2024,
                FY2024,
                fy2024_values,
                concept=concept,
                statement_kind=statement_kind,
            )
        )
    facts.extend(
        (
            _balance_fact("cash_and_cash_equivalents", 50),
            _balance_fact("current_debt", 10),
            _balance_fact("long_term_debt", 90),
        )
    )
    return tuple(facts)


def test_loads_typed_snapshot_and_uses_year_over_year_ttm_growth():
    result = load_valuation_fundamentals_snapshot(
        InMemoryFundamentalsRepository(_complete_facts()),
        _request(),
    )

    assert result.is_complete
    snapshot = result.snapshot
    assert len(snapshot.trailing_periods) == 5
    assert snapshot.latest.revenue == 500
    assert snapshot.latest.free_cash_flow == 100
    assert snapshot.latest.operating_margin == Decimal("0.2")
    # The isolated Q4 increase is part of a full four-quarter window. It
    # produces 25% comparable TTM growth, not a fabricated 100% annual rate.
    assert snapshot.comparable_revenue_growth[-1].rate == Decimal("0.25")
    assert {value.key.concept for value in snapshot.latest.source_values} == {
        "capital_expenditures",
        "operating_cash_flow",
        "operating_income",
        "revenue",
    }
    assert snapshot.latest_balance.cash_and_cash_equivalents == 50
    assert snapshot.latest_balance.reported_term_debt == 100
    assert {fact.identity.concept for fact in snapshot.latest_balance.source_facts} == {
        "cash_and_cash_equivalents",
        "current_debt",
        "long_term_debt",
    }


def test_repository_query_is_owned_by_the_snapshot_module():
    captured = []

    class CapturingRepository:
        def get_facts(self, query):
            captured.append(query)
            return _complete_facts()

    result = load_valuation_fundamentals_snapshot(CapturingRepository(), _request())

    assert result.is_complete
    assert captured[0].concepts == VALUATION_FUNDAMENTALS_CONCEPTS
    assert captured[0].max_periods_per_statement == 64


def test_missing_latest_balance_refuses_instead_of_mixing_another_source():
    facts = tuple(
        fact for fact in _complete_facts() if fact.identity.concept != "current_debt"
    )

    result = load_valuation_fundamentals_snapshot(
        InMemoryFundamentalsRepository(facts),
        _request(),
    )

    assert not result.is_complete
    assert result.issues[0].code is ValuationSnapshotIssueCode.MISSING_BALANCE_POSITION


def test_one_fiscal_year_refuses_as_noncomparable_history():
    facts = tuple(
        fact
        for fact in _complete_facts()
        if fact.statement_kind is StatementKind.BALANCE_SHEET or fact.period.fiscal_year == 2024
    )

    result = load_valuation_fundamentals_snapshot(
        InMemoryFundamentalsRepository(facts),
        _request(),
    )

    assert not result.is_complete
    assert result.issues[0].code is ValuationSnapshotIssueCode.INSUFFICIENT_COMPARABLE_HISTORY


def test_disconnected_ttm_histories_return_typed_refusal_instead_of_raising():
    disconnected_year = (
        dt.date(2021, 9, 26),
        dt.date(2021, 12, 25),
        dt.date(2022, 3, 26),
        dt.date(2022, 6, 25),
        dt.date(2022, 9, 24),
    )
    facts = list(_complete_facts())
    for concept, statement_kind, values in (
        ("revenue", StatementKind.INCOME_STATEMENT, (100, 100, 100, 400)),
        ("operating_income", StatementKind.INCOME_STATEMENT, (20, 20, 20, 80)),
        ("operating_cash_flow", StatementKind.CASH_FLOW, (30, 30, 30, 120)),
        ("capital_expenditures", StatementKind.CASH_FLOW, (10, 10, 10, 40)),
    ):
        facts.extend(
            _duration_year(
                2022,
                disconnected_year,
                values,
                concept=concept,
                statement_kind=statement_kind,
            )
        )

    result = load_valuation_fundamentals_snapshot(
        InMemoryFundamentalsRepository(facts),
        _request(),
    )

    assert not result.is_complete
    assert result.issues[0].code is ValuationSnapshotIssueCode.INCOMPATIBLE_TRAILING_PERIODS


def test_store_failure_is_sanitized_and_typed():
    class UnavailableRepository:
        def get_facts(self, _query):
            raise FundamentalsRepositoryUnavailable("postgresql://secret@host/database")

    result = load_valuation_fundamentals_snapshot(UnavailableRepository(), _request())

    assert not result.is_complete
    assert result.issues[0].code is ValuationSnapshotIssueCode.STORE_UNAVAILABLE
    assert "secret" not in result.issues[0].message


def test_request_rejects_naive_cutoffs_and_whitespace_padded_lineage():
    with pytest.raises(ValueError, match="timezone-aware"):
        _request(knowledge_cutoff=dt.datetime(2024, 1, 1))
    with pytest.raises(ValueError, match="source_adapter"):
        _request(source_adapter=" sec_edgar_companyfacts")


def test_trailing_period_rejects_an_invented_free_cash_flow():
    result = load_valuation_fundamentals_snapshot(
        InMemoryFundamentalsRepository(_complete_facts()),
        _request(),
    )
    with pytest.raises(ValueError, match="must equal"):
        replace(result.snapshot.latest, free_cash_flow=Decimal("101"))
