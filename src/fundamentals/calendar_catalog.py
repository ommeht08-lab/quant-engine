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


# The SEC pilot universe: one 52/53-week issuer (Apple), two non-calendar
# issuers (Microsoft, Walmart), and one calendar-year issuer (Caterpillar).
# Dates come from the cited 10-Q/10-K filing periods, not heuristics; every
# boundary was cross-checked against the durations tagged in the same filing.
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
            version="sec-filing-calendar-v2-fy2020-fy2026",
            fiscal_years=(
                FiscalYearDefinition(
                    fiscal_year=2020,
                    period_start=date(2019, 7, 1),
                    quarter_ends=(
                        date(2019, 9, 30),
                        date(2019, 12, 31),
                        date(2020, 3, 31),
                        date(2020, 6, 30),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2021,
                    period_start=date(2020, 7, 1),
                    quarter_ends=(
                        date(2020, 9, 30),
                        date(2020, 12, 31),
                        date(2021, 3, 31),
                        date(2021, 6, 30),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2022,
                    period_start=date(2021, 7, 1),
                    quarter_ends=(
                        date(2021, 9, 30),
                        date(2021, 12, 31),
                        date(2022, 3, 31),
                        date(2022, 6, 30),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2023,
                    period_start=date(2022, 7, 1),
                    quarter_ends=(
                        date(2022, 9, 30),
                        date(2022, 12, 31),
                        date(2023, 3, 31),
                        date(2023, 6, 30),
                    ),
                ),
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
                FiscalYearDefinition(
                    fiscal_year=2025,
                    period_start=date(2024, 7, 1),
                    quarter_ends=(
                        date(2024, 9, 30),
                        date(2024, 12, 31),
                        date(2025, 3, 31),
                        date(2025, 6, 30),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2026,
                    period_start=date(2025, 7, 1),
                    quarter_ends=(
                        date(2025, 9, 30),
                        date(2025, 12, 31),
                        date(2026, 3, 31),
                        date(2026, 6, 30),
                    ),
                ),
            ),
            evidence_urls=(
                "https://www.sec.gov/Archives/edgar/data/789019/000156459019037549/msft-10q_20190930.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459020002450/msft-10q_20191231.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459020019706/msft-10q_20200331.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459020034944/msft-10k_20200630.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459020047996/msft-10q_20200930.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459021002316/msft-10q_20201231.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459021020891/msft-10q_20210331.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459021039151/msft-10k_20210630.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459021051992/msft-10q_20210930.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459022002324/msft-10q_20211231.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459022015675/msft-10q_20220331.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459022026876/msft-10k_20220630.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459022035087/msft-10q_20220930.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000156459023000733/msft-10q_20221231.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017023014423/msft-20230331.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017023035122/msft-20230630.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017023054855/msft-20230930.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017024008814/msft-20231231.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017024048288/msft-20240331.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017024118967/msft-20240930.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017025010491/msft-20241231.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017025061046/msft-20250331.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000095017025100235/msft-20250630.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000119312525256321/msft-20250930.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000119312526027207/msft-20251231.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000119312526191507/msft-20260331.htm",
                "https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm",
            ),
        ),
        _entry(
            cik="0000104169",
            version="sec-filing-calendar-v2-fy2020-open-fy2027-q2",
            fiscal_years=(
                FiscalYearDefinition(
                    fiscal_year=2020,
                    period_start=date(2019, 2, 1),
                    quarter_ends=(
                        date(2019, 4, 30),
                        date(2019, 7, 31),
                        date(2019, 10, 31),
                        date(2020, 1, 31),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2021,
                    period_start=date(2020, 2, 1),
                    quarter_ends=(
                        date(2020, 4, 30),
                        date(2020, 7, 31),
                        date(2020, 10, 31),
                        date(2021, 1, 31),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2022,
                    period_start=date(2021, 2, 1),
                    quarter_ends=(
                        date(2021, 4, 30),
                        date(2021, 7, 31),
                        date(2021, 10, 31),
                        date(2022, 1, 31),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2023,
                    period_start=date(2022, 2, 1),
                    quarter_ends=(
                        date(2022, 4, 30),
                        date(2022, 7, 31),
                        date(2022, 10, 31),
                        date(2023, 1, 31),
                    ),
                ),
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
                FiscalYearDefinition(
                    fiscal_year=2025,
                    period_start=date(2024, 2, 1),
                    quarter_ends=(
                        date(2024, 4, 30),
                        date(2024, 7, 31),
                        date(2024, 10, 31),
                        date(2025, 1, 31),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2026,
                    period_start=date(2025, 2, 1),
                    quarter_ends=(
                        date(2025, 4, 30),
                        date(2025, 7, 31),
                        date(2025, 10, 31),
                        date(2026, 1, 31),
                    ),
                ),
            ),
            open_fiscal_years=(
                OpenFiscalYearDefinition(
                    fiscal_year=2027,
                    period_start=date(2026, 2, 1),
                    quarter_ends=(
                        date(2026, 4, 30),
                        date(2026, 7, 31),
                    ),
                ),
            ),
            evidence_urls=(
                "https://www.sec.gov/Archives/edgar/data/104169/000010416919000024/wmtform10-qx4302019.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416919000064/wmtform10-qx7312019.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416919000088/wmtform10-qx103119.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416920000011/wmtform10-kx1312020.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416920000020/wmtform10-qx4302020.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416920000042/wmtform10-qx7312020.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416920000076/wmt-20201031.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416921000033/wmt-20210131.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416921000042/wmt-20210430.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416921000058/wmt-20210731.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416921000072/wmt-20211031.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416922000012/wmt-20220131.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416922000029/wmt-20220430.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416922000072/wmt-20220731.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416922000083/wmt-20221031.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416923000020/wmt-20230131.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416923000052/wmt-20230430.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416923000097/wmt-20230731.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416923000132/wmt-20231031.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416924000056/wmt-20240131.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416924000105/wmt-20240430.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416924000141/wmt-20240731.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416924000178/wmt-20241031.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416925000021/wmt-20250131.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416925000090/wmt-20250430.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416925000137/wmt-20250731.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416925000191/wmt-20251031.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416926000055/wmt-20260131.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416926000102/wmt-20260430.htm",
                "https://www.sec.gov/Archives/edgar/data/104169/000010416926000154/wmt-20260731.htm",
            ),
        ),
        _entry(
            cik="0000018230",
            version="sec-filing-calendar-v1-fy2020-open-fy2026-q2",
            fiscal_years=(
                FiscalYearDefinition(
                    fiscal_year=2020,
                    period_start=date(2020, 1, 1),
                    quarter_ends=(
                        date(2020, 3, 31),
                        date(2020, 6, 30),
                        date(2020, 9, 30),
                        date(2020, 12, 31),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2021,
                    period_start=date(2021, 1, 1),
                    quarter_ends=(
                        date(2021, 3, 31),
                        date(2021, 6, 30),
                        date(2021, 9, 30),
                        date(2021, 12, 31),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2022,
                    period_start=date(2022, 1, 1),
                    quarter_ends=(
                        date(2022, 3, 31),
                        date(2022, 6, 30),
                        date(2022, 9, 30),
                        date(2022, 12, 31),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2023,
                    period_start=date(2023, 1, 1),
                    quarter_ends=(
                        date(2023, 3, 31),
                        date(2023, 6, 30),
                        date(2023, 9, 30),
                        date(2023, 12, 31),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2024,
                    period_start=date(2024, 1, 1),
                    quarter_ends=(
                        date(2024, 3, 31),
                        date(2024, 6, 30),
                        date(2024, 9, 30),
                        date(2024, 12, 31),
                    ),
                ),
                FiscalYearDefinition(
                    fiscal_year=2025,
                    period_start=date(2025, 1, 1),
                    quarter_ends=(
                        date(2025, 3, 31),
                        date(2025, 6, 30),
                        date(2025, 9, 30),
                        date(2025, 12, 31),
                    ),
                ),
            ),
            open_fiscal_years=(
                OpenFiscalYearDefinition(
                    fiscal_year=2026,
                    period_start=date(2026, 1, 1),
                    quarter_ends=(
                        date(2026, 3, 31),
                        date(2026, 6, 30),
                    ),
                ),
            ),
            evidence_urls=(
                "https://www.sec.gov/Archives/edgar/data/18230/000001823020000155/cat10q3312020.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823020000214/cat10q6302020.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823020000297/cat-20200930.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823021000063/cat-20201231.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823021000151/cat-20210331.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823021000221/cat-20210630.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823021000260/cat-20210930.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823022000050/cat-20211231.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823022000112/cat-20220331.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823022000183/cat-20220630.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823022000223/cat-20220930.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823023000011/cat-20221231.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823023000022/cat-20230331.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823023000047/cat-20230630.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823023000056/cat-20230930.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823024000009/cat-20231231.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823024000020/cat-20240331.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823024000045/cat-20240630.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823024000053/cat-20240930.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823025000008/cat-20241231.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823025000016/cat-20250331.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823025000040/cat-20250630.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823025000048/cat-20250930.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823026000008/cat-20251231.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823026000021/cat-20260331.htm",
                "https://www.sec.gov/Archives/edgar/data/18230/000001823026000046/cat-20260630.htm",
            ),
        ),
    ),
)
