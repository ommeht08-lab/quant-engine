"""Frozen SEC-filing evidence for CAT's ME&T gross-margin pilot factor.

This is a research calculation, not a production quality-data adapter. The
three cited filings contain the five fiscal-period observations required for
the 2024-09-03 pilot's two trailing-year margins. Nothing here is read by the
pilot until an audited source/integration seam is approved separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Tuple


CAT_MET_MARGIN_POLICY_VERSION = "cat-met-gross-margin-pilot-v1"
EVIDENCE_CAPTURED_AT = datetime(2026, 9, 25, 2, 55, 7, tzinfo=timezone.utc)
CAT_PILOT_LATEST_END = date(2024, 6, 30)
CAT_PILOT_PRIOR_END = date(2023, 6, 30)


class CatMetMarginRefusal(ValueError):
    """Required filing evidence is unavailable or internally inconsistent."""


@dataclass(frozen=True)
class CatMetPeriod:
    kind: str  # fiscal_year or first_half
    period_end: date
    sales_millions: Decimal
    cost_of_goods_sold_millions: Decimal
    accession: str
    accepted_at: datetime
    exhibit_url: str


@dataclass(frozen=True)
class CatMetMarginPair:
    current: Decimal
    prior: Decimal
    policy_version: str
    evidence_captured_at: datetime
    filing_accessions: Tuple[str, ...]
    exhibit_urls: Tuple[str, ...]


# The FY2023 10-K reports both FY2023 and FY2022 ME&T supplemental columns.
_FY_2023_10K = "https://www.sec.gov/Archives/edgar/data/18230/000001823024000009/cat-20231231.htm"
_H1_2023_8K = "https://www.sec.gov/Archives/edgar/data/18230/000001823023000044/ex991toformcat2q2023earnin.htm"
_H1_2024_8K = "https://www.sec.gov/Archives/edgar/data/18230/000001823024000042/ex991toformcat2q2024earnin.htm"

# SEC filing-index acceptance times, converted from Eastern time to UTC.
CAT_PILOT_MET_EVIDENCE = (
    CatMetPeriod("fiscal_year", date(2022, 12, 31), Decimal(56574), Decimal(41356),
                 "0000018230-24-000009", datetime(2024, 2, 16, 15, 5, 13, tzinfo=timezone.utc), _FY_2023_10K),
    CatMetPeriod("fiscal_year", date(2023, 12, 31), Decimal(63869), Decimal(42776),
                 "0000018230-24-000009", datetime(2024, 2, 16, 15, 5, 13, tzinfo=timezone.utc), _FY_2023_10K),
    CatMetPeriod("first_half", date(2022, 6, 30), Decimal(26425), Decimal(19538),
                 "0000018230-23-000044", datetime(2023, 8, 1, 10, 31, 57, tzinfo=timezone.utc), _H1_2023_8K),
    CatMetPeriod("first_half", date(2023, 6, 30), Decimal(31644), Decimal(21172),
                 "0000018230-24-000042", datetime(2024, 8, 6, 10, 32, 5, tzinfo=timezone.utc), _H1_2024_8K),
    CatMetPeriod("first_half", date(2024, 6, 30), Decimal(30800), Decimal(19816),
                 "0000018230-24-000042", datetime(2024, 8, 6, 10, 32, 5, tzinfo=timezone.utc), _H1_2024_8K),
)


def pilot_margin_pair(
    knowledge_cutoff: datetime,
    data_vintage_cutoff: datetime,
    latest_end: date,
    prior_end: date,
    *,
    evidence: Iterable[CatMetPeriod] = CAT_PILOT_MET_EVIDENCE,
) -> CatMetMarginPair:
    """Calculate the two CAT ME&T TTM margins, refusing any unsupported case.

    Every observation must come from an SEC filing accepted by the knowledge
    cutoff. This frozen manual extraction is visible only after its recorded
    capture time. It deliberately supports one pilot comparison, not arbitrary
    historical decisions or the live valuation route.
    """

    if knowledge_cutoff.utcoffset() is None or data_vintage_cutoff.utcoffset() is None:
        raise CatMetMarginRefusal("Both cutoffs must be timezone-aware.")
    if (latest_end, prior_end) != (CAT_PILOT_LATEST_END, CAT_PILOT_PRIOR_END):
        raise CatMetMarginRefusal("CAT ME&T evidence does not cover this trailing-year comparison.")
    if data_vintage_cutoff < EVIDENCE_CAPTURED_AT:
        raise CatMetMarginRefusal("CAT ME&T evidence was not captured by the data-vintage cutoff.")

    required = {
        ("fiscal_year", date(2022, 12, 31)),
        ("fiscal_year", date(2023, 12, 31)),
        ("first_half", date(2022, 6, 30)),
        ("first_half", date(2023, 6, 30)),
        ("first_half", date(2024, 6, 30)),
    }
    frozen = {(item.kind, item.period_end): item for item in CAT_PILOT_MET_EVIDENCE}
    selected = {}
    for item in evidence:
        key = (item.kind, item.period_end)
        if key not in required:
            continue
        if key in selected:
            raise CatMetMarginRefusal(f"Duplicate CAT ME&T evidence for {key}.")
        if item != frozen[key]:
            raise CatMetMarginRefusal(f"CAT ME&T evidence differs from the audited filing excerpt for {key}.")
        if item.accepted_at.utcoffset() is None or item.accepted_at > knowledge_cutoff:
            raise CatMetMarginRefusal(f"CAT ME&T evidence for {key} was not public at the cutoff.")
        if item.sales_millions <= 0 or item.cost_of_goods_sold_millions < 0:
            raise CatMetMarginRefusal(f"Invalid CAT ME&T sales or cost for {key}.")
        selected[key] = item
    if set(selected) != required:
        raise CatMetMarginRefusal("CAT ME&T evidence is missing a required fiscal period.")

    def ttm(annual_year: int, half_year: int):
        annual = selected[("fiscal_year", date(annual_year, 12, 31))]
        newer = selected[("first_half", date(half_year, 6, 30))]
        older = selected[("first_half", date(half_year - 1, 6, 30))]
        sales = annual.sales_millions + newer.sales_millions - older.sales_millions
        cost = (annual.cost_of_goods_sold_millions + newer.cost_of_goods_sold_millions
                - older.cost_of_goods_sold_millions)
        if sales <= 0 or cost < 0:
            raise CatMetMarginRefusal("CAT ME&T trailing-year sales or cost is invalid.")
        return (sales - cost) / sales

    return CatMetMarginPair(
        current=ttm(2023, 2024),
        prior=ttm(2022, 2023),
        policy_version=CAT_MET_MARGIN_POLICY_VERSION,
        evidence_captured_at=EVIDENCE_CAPTURED_AT,
        filing_accessions=tuple(sorted({item.accession for item in selected.values()})),
        exhibit_urls=tuple(sorted({item.exhibit_url for item in selected.values()})),
    )
