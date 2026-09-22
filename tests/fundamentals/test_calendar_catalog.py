import datetime as dt

import pytest

from src.fundamentals.calendar_catalog import (
    SEC_FISCAL_CALENDAR_CATALOG_V1,
    FiscalCalendarCatalog,
    FiscalCalendarCatalogEntry,
    FiscalCalendarUnavailable,
)
from src.fundamentals.fiscal_calendar import FiscalYearDefinition, IssuerFiscalCalendarPolicy


def test_bounded_catalog_contains_exact_verified_issuer_calendars():
    catalog = SEC_FISCAL_CALENDAR_CATALOG_V1

    assert catalog.supported_ciks == ("0000018230", "0000104169", "0000320193", "0000789019")
    apple = catalog.entry_for("320193")
    assert apple.policy.version == "sec-filing-calendar-v3-fy2020-open-fy2026-q3"
    assert tuple(definition.fiscal_year for definition in apple.policy.fiscal_years) == (
        2020,
        2021,
        2022,
        2023,
        2024,
        2025,
    )
    assert apple.policy.fiscal_years[0].period_start == dt.date(2019, 9, 29)
    assert apple.policy.fiscal_years[0].quarter_ends == (
        dt.date(2019, 12, 28),
        dt.date(2020, 3, 28),
        dt.date(2020, 6, 27),
        dt.date(2020, 9, 26),
    )
    assert apple.policy.fiscal_years[3].quarter_ends == (
        dt.date(2022, 12, 31),
        dt.date(2023, 4, 1),
        dt.date(2023, 7, 1),
        dt.date(2023, 9, 30),
    )
    assert apple.policy.fiscal_years[-1].quarter_ends == (
        dt.date(2024, 12, 28),
        dt.date(2025, 3, 29),
        dt.date(2025, 6, 28),
        dt.date(2025, 9, 27),
    )
    assert len(apple.policy.open_fiscal_years) == 1
    assert apple.policy.open_fiscal_years[0].fiscal_year == 2026
    assert apple.policy.open_fiscal_years[0].quarter_ends == (
        dt.date(2025, 12, 27),
        dt.date(2026, 3, 28),
        dt.date(2026, 6, 27),
    )
    assert len(apple.evidence_urls) == 27
    assert all(url.startswith("https://www.sec.gov/Archives/") for url in apple.evidence_urls)



def test_microsoft_calendar_covers_closed_fiscal_years_2020_through_2026():
    microsoft = SEC_FISCAL_CALENDAR_CATALOG_V1.entry_for("789019")

    assert microsoft.policy.version == "sec-filing-calendar-v2-fy2020-fy2026"
    assert tuple(item.fiscal_year for item in microsoft.policy.fiscal_years) == tuple(
        range(2020, 2027)
    )
    assert microsoft.policy.fiscal_years[0].period_start == dt.date(2019, 7, 1)
    assert microsoft.policy.fiscal_years[0].quarter_ends == (
        dt.date(2019, 9, 30),
        dt.date(2019, 12, 31),
        dt.date(2020, 3, 31),
        dt.date(2020, 6, 30),
    )
    assert microsoft.policy.fiscal_years[-1].period_start == dt.date(2025, 7, 1)
    assert microsoft.policy.fiscal_years[-1].period_end == dt.date(2026, 6, 30)
    # FY2027 Q1 (ending September 30, 2026) has not been filed, so no open year.
    assert microsoft.policy.open_fiscal_years == ()
    assert len(microsoft.evidence_urls) == 28


def test_walmart_calendar_covers_fiscal_years_2020_through_2026_and_open_2027():
    walmart = SEC_FISCAL_CALENDAR_CATALOG_V1.entry_for("104169")

    assert walmart.policy.version == "sec-filing-calendar-v2-fy2020-open-fy2027-q2"
    assert tuple(item.fiscal_year for item in walmart.policy.fiscal_years) == tuple(
        range(2020, 2027)
    )
    # Walmart's fiscal year is named for the calendar year in which it ends.
    assert walmart.policy.fiscal_years[0].period_start == dt.date(2019, 2, 1)
    assert walmart.policy.fiscal_years[0].quarter_ends == (
        dt.date(2019, 4, 30),
        dt.date(2019, 7, 31),
        dt.date(2019, 10, 31),
        dt.date(2020, 1, 31),
    )
    assert walmart.policy.fiscal_years[-1].period_end == dt.date(2026, 1, 31)
    assert len(walmart.policy.open_fiscal_years) == 1
    assert walmart.policy.open_fiscal_years[0].fiscal_year == 2027
    assert walmart.policy.open_fiscal_years[0].period_start == dt.date(2026, 2, 1)
    assert walmart.policy.open_fiscal_years[0].quarter_ends == (
        dt.date(2026, 4, 30),
        dt.date(2026, 7, 31),
    )
    assert len(walmart.evidence_urls) == 30


def test_caterpillar_calendar_covers_fiscal_years_2020_through_2025_and_open_2026():
    caterpillar = SEC_FISCAL_CALENDAR_CATALOG_V1.entry_for("18230")

    assert caterpillar.policy.version == "sec-filing-calendar-v1-fy2020-open-fy2026-q2"
    assert tuple(item.fiscal_year for item in caterpillar.policy.fiscal_years) == tuple(
        range(2020, 2026)
    )
    for definition in caterpillar.policy.fiscal_years:
        year = definition.fiscal_year
        assert definition.period_start == dt.date(year, 1, 1)
        assert definition.quarter_ends == (
            dt.date(year, 3, 31),
            dt.date(year, 6, 30),
            dt.date(year, 9, 30),
            dt.date(year, 12, 31),
        )
    assert len(caterpillar.policy.open_fiscal_years) == 1
    assert caterpillar.policy.open_fiscal_years[0].fiscal_year == 2026
    assert caterpillar.policy.open_fiscal_years[0].quarter_ends == (
        dt.date(2026, 3, 31),
        dt.date(2026, 6, 30),
    )
    assert len(caterpillar.evidence_urls) == 26


def test_extended_calendars_preserve_previously_curated_fiscal_2024_dates():
    catalog = SEC_FISCAL_CALENDAR_CATALOG_V1
    microsoft_2024 = next(
        item for item in catalog.policy_for("789019").fiscal_years if item.fiscal_year == 2024
    )
    walmart_2024 = next(
        item for item in catalog.policy_for("104169").fiscal_years if item.fiscal_year == 2024
    )

    assert microsoft_2024 == FiscalYearDefinition(
        fiscal_year=2024,
        period_start=dt.date(2023, 7, 1),
        quarter_ends=(
            dt.date(2023, 9, 30),
            dt.date(2023, 12, 31),
            dt.date(2024, 3, 31),
            dt.date(2024, 6, 30),
        ),
    )
    assert walmart_2024 == FiscalYearDefinition(
        fiscal_year=2024,
        period_start=dt.date(2023, 2, 1),
        quarter_ends=(
            dt.date(2023, 4, 30),
            dt.date(2023, 7, 31),
            dt.date(2023, 10, 31),
            dt.date(2024, 1, 31),
        ),
    )


@pytest.mark.parametrize("cik", SEC_FISCAL_CALENDAR_CATALOG_V1.supported_ciks)
def test_every_pilot_calendar_is_contiguous_and_has_one_filing_per_quarter(cik):
    entry = SEC_FISCAL_CALENDAR_CATALOG_V1.entry_for(cik)
    definitions = entry.policy.fiscal_years + entry.policy.open_fiscal_years

    for previous, current in zip(definitions, definitions[1:]):
        assert current.period_start == previous.period_end + dt.timedelta(days=1)
    assert definitions[0].fiscal_year == 2020
    assert len(entry.evidence_urls) == sum(len(item.quarter_ends) for item in definitions)
    assert all(
        url.startswith(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/")
        for url in entry.evidence_urls
    )

def test_unknown_issuer_fails_closed_instead_of_guessing_dates():
    with pytest.raises(FiscalCalendarUnavailable, match="0000000001"):
        SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for("1")


def test_catalog_refuses_duplicate_issuer_policies():
    policy = IssuerFiscalCalendarPolicy(
        cik="1",
        version="calendar-v1",
        fiscal_years=(
            FiscalYearDefinition(
                fiscal_year=2024,
                period_start=dt.date(2024, 1, 1),
                quarter_ends=(
                    dt.date(2024, 3, 31),
                    dt.date(2024, 6, 30),
                    dt.date(2024, 9, 30),
                    dt.date(2024, 12, 31),
                ),
            ),
        ),
    )
    entry = FiscalCalendarCatalogEntry(
        policy=policy,
        evidence_urls=("https://www.sec.gov/Archives/edgar/data/1/example.htm",),
    )

    with pytest.raises(ValueError, match="duplicate issuer"):
        FiscalCalendarCatalog(version="catalog-v1", entries=(entry, entry))


def test_evidence_must_be_an_official_sec_archive_url():
    policy = SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for("320193")

    with pytest.raises(ValueError, match="official SEC"):
        FiscalCalendarCatalogEntry(
            policy=policy,
            evidence_urls=("https://example.com/not-sec",),
        )
