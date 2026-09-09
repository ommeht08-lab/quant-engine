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

    assert catalog.supported_ciks == ("0000104169", "0000320193", "0000789019")
    apple = catalog.entry_for("320193")
    assert apple.policy.version == "sec-filing-calendar-v2-fy2020-fy2024"
    assert tuple(definition.fiscal_year for definition in apple.policy.fiscal_years) == (
        2020,
        2021,
        2022,
        2023,
        2024,
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
        dt.date(2023, 12, 30),
        dt.date(2024, 3, 30),
        dt.date(2024, 6, 29),
        dt.date(2024, 9, 28),
    )
    assert len(apple.evidence_urls) == 20
    assert all(url.startswith("https://www.sec.gov/Archives/") for url in apple.evidence_urls)


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
