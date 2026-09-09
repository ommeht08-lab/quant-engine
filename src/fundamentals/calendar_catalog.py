"""Versioned, evidence-backed catalog of exact issuer fiscal calendars.

The catalog is deliberately curated. It never guesses quarter boundaries from
month names, durations, SEC filing labels, or a generic fiscal-year-end field.
Adding coverage means adding exact dates and the public filings that prove them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Tuple

from .fiscal_calendar import (
    FiscalYearDefinition,
    IssuerFiscalCalendarPolicy,
    OpenFiscalYearDefinition,
)
from .types import normalize_cik


class FiscalCalendarUnavailable(LookupError):
    """The curated catalog does not cover the requested issuer."""


@dataclass(frozen=True)
class FiscalCalendarCatalogEntry:
    policy: IssuerFiscalCalendarPolicy
    evidence_urls: Tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            evidence_urls = tuple(self.evidence_urls)
        except TypeError:
            raise ValueError("evidence_urls must be a collection of SEC filing URLs.") from None
        if not evidence_urls or any(
            not isinstance(url, str)
            or not url.startswith("https://www.sec.gov/Archives/edgar/data/")
            for url in evidence_urls
        ):
            raise ValueError("evidence_urls must contain official SEC filing URLs.")
        if len(evidence_urls) != len(set(evidence_urls)):
            raise ValueError("evidence_urls must not contain duplicates.")
        object.__setattr__(self, "evidence_urls", evidence_urls)


@dataclass(frozen=True)
class FiscalCalendarCatalog:
    version: str
    entries: Tuple[FiscalCalendarCatalogEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("FiscalCalendarCatalog.version must be non-empty text.")
        try:
            entries = tuple(self.entries)
        except TypeError:
            raise ValueError("FiscalCalendarCatalog.entries must be a collection.") from None
        if not entries or any(not isinstance(entry, FiscalCalendarCatalogEntry) for entry in entries):
            raise ValueError("FiscalCalendarCatalog.entries must contain catalog entries.")
        ciks = [entry.policy.cik for entry in entries]
        if len(ciks) != len(set(ciks)):
            raise ValueError("A fiscal-calendar catalog cannot contain duplicate issuer CIKs.")
        object.__setattr__(self, "entries", tuple(sorted(entries, key=lambda item: item.policy.cik)))

    @property
    def supported_ciks(self) -> Tuple[str, ...]:
        return tuple(entry.policy.cik for entry in self.entries)

    def entry_for(self, cik: str) -> FiscalCalendarCatalogEntry:
        normalized_cik = normalize_cik(cik)
        for entry in self.entries:
            if entry.policy.cik == normalized_cik:
                return entry
        raise FiscalCalendarUnavailable(
            f"No exact issuer fiscal calendar is available for CIK {normalized_cik}."
        )

    def policy_for(self, cik: str) -> IssuerFiscalCalendarPolicy:
        return self.entry_for(cik).policy


def _entry(
    *,
    cik: str,
    version: str,
    fiscal_years: Iterable[FiscalYearDefinition],
    open_fiscal_years: Iterable[OpenFiscalYearDefinition] = (),
    evidence_urls: Iterable[str],
) -> FiscalCalendarCatalogEntry:
    return FiscalCalendarCatalogEntry(
        policy=IssuerFiscalCalendarPolicy(
            cik=cik,
            version=version,
            fiscal_years=tuple(fiscal_years),
            open_fiscal_years=tuple(open_fiscal_years),
        ),
        evidence_urls=tuple(evidence_urls),
    )


# The first bounded validation set: one 52/53-week issuer and two non-calendar
# issuers. Dates come from the cited 10-Q/10-K filing periods, not heuristics.
SEC_FISCAL_CALENDAR_CATALOG_V1 = FiscalCalendarCatalog(
    version="sec-fiscal-calendar-catalog-v1",
    entries=(
        _entry(
            cik="0000320193",
            version="sec-filing-calendar-v3-fy2020-open-fy2026-q3",
            fiscal_years=(
                FiscalYearDefinition(
                    fiscal_year=2020,
                    period_start=date(2019, 9, 29),
                    quarter_ends=(
                        date(2019, 12, 28),
                        date(2020, 3, 28),
                        date(2020, 6, 27),
                        date(2020, 9, 26),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2021,
                    period_start=date(2020, 9, 27),
                    quarter_ends=(
                        date(2020, 12, 26),
                        date(2021, 3, 27),
                        date(2021, 6, 26),
                        date(2021, 9, 25),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2022,
                    period_start=date(2021, 9, 26),
                    quarter_ends=(
                        date(2021, 12, 25),
                        date(2022, 3, 26),
                        date(2022, 6, 25),
                        date(2022, 9, 24),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2023,
                    period_start=date(2022, 9, 25),
                    quarter_ends=(
                        date(2022, 12, 31),
                        date(2023, 4, 1),
                        date(2023, 7, 1),
                        date(2023, 9, 30),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2024,
                    period_start=date(2023, 10, 1),
                    quarter_ends=(
                        date(2023, 12, 30),
                        date(2024, 3, 30),
                        date(2024, 6, 29),
                        date(2024, 9, 28),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2025,
                    period_start=date(2024, 9, 29),
                    quarter_ends=(
                        date(2024, 12, 28),
                        date(2025, 3, 29),
                        date(2025, 6, 28),
                        date(2025, 9, 27),
                    ),
                ),
            ),
            open_fiscal_years=(
                OpenFiscalYearDefinition(
                    fiscal_year=2026,
                    period_start=date(2025, 9, 28),
                    quarter_ends=(
                        date(2025, 12, 27),
                        date(2026, 3, 28),
                        date(2026, 6, 27),
                    ),
                ),
            ),
            evidence_urls=(
                "https://www.sec.gov/Archives/edgar/data/320193/000032019320000010/a10-qq1202012282019.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019320000052/a10-qq220203282020.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019320000062/aapl-20200627.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019320000096/aapl-20200926.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019321000010/aapl-20201226.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019321000056/aapl-20210327.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019321000065/aapl-20210626.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019321000105/aapl-20210925.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019322000007/aapl-20211225.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019322000059/aapl-20220326.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019322000070/aapl-20220625.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019322000108/aapl-20220924.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019323000006/aapl-20221231.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019323000064/aapl-20230401.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019323000077/aapl-20230701.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019324000006/aapl-20231230.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019324000069/aapl-20240330.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019324000081/aapl-20240629.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019325000008/aapl-20241228.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019325000057/aapl-20250329.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/aapl-20250628.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/aapl-20251227.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm",
            ),
        ),
        _entry(
            cik="0000789019",
            version="sec-filing-calendar-v1-fy2024",
            fiscal_years=(
                FiscalYearDefinition(
                    fiscal_year=2024,
                    period_start=date(2023, 7, 1),
                    quarter_ends=(
                        date(2023, 9, 30),
                        date(2023, 12, 31),
                        date(2024, 3, 31),
                        date(2024, 6, 30),
                    ),
                ),
            ),
            evidence_urls=(
                "https://www.sec.gov/Archives/edgar/data/789019/000095017023054855/msft-20230930.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017024008814/msft-20231231.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017024048288/msft-20240331.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm",
            ),
        ),
        _entry(
            cik="0000104169",
            version="sec-filing-calendar-v1-fy2024",
            fiscal_years=(
                FiscalYearDefinition(
                    fiscal_year=2024,
                    period_start=date(2023, 2, 1),
                    quarter_ends=(
                        date(2023, 4, 30),
                        date(2023, 7, 31),
                        date(2023, 10, 31),
                        date(2024, 1, 31),
                    ),
                ),
            ),
            evidence_urls=(
                "https://www.sec.gov/Archives/edgar/data/104169/000010416923000052/wmt-20230430.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416923000097/wmt-20230731.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416923000132/wmt-20231031.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416924000056/wmt-20240131.htm",
            ),
        ),
    ),
)
