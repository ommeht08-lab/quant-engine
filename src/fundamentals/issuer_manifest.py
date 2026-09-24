"""Auditable issuer readiness for SEC ingestion and valuation cutover.

Presence in this manifest is not approval. Historical SEC readiness and live
automatic cutover are deliberately separate decisions so incomplete coverage
cannot become a silent production source change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from .adapters.sec_companyfacts import SOURCE_ADAPTER
from .adapters.sec_filing_xbrl import SOURCE_ADAPTER as FILING_XBRL_SOURCE_ADAPTER
from .calendar_catalog import SEC_FISCAL_CALENDAR_CATALOG_V1
from .concept_map import SEC_CONCEPT_MAP_V2, concept_map_for_issuer
from .types import normalize_cik
from .valuation_integration import SEC_DCF_POLICY_VERSION


ISSUER_MANIFEST_VERSION = "sec-issuer-manifest-v1"


@dataclass(frozen=True)
class IssuerValuationPolicy:
    ticker: str
    cik: str
    fiscal_calendar_version: str | None
    sec_history_ready: bool
    sec_live_approved: bool
    readiness_reason: str
    source_adapter: str = SOURCE_ADAPTER
    concept_map_version: str = SEC_CONCEPT_MAP_V2.version
    composition_policy_version: str = SEC_DCF_POLICY_VERSION
    # Additional SEC sources this issuer's concept map requires (for example
    # consolidated balances composed from filing XBRL).
    supplemental_source_adapters: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ticker = self.ticker.strip().upper() if isinstance(self.ticker, str) else ""
        if not ticker:
            raise ValueError("ticker must be non-empty text.")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if self.sec_history_ready and not self.fiscal_calendar_version:
            raise ValueError("SEC history readiness requires an exact fiscal calendar.")
        if self.sec_live_approved and not self.sec_history_ready:
            raise ValueError("Live SEC approval requires SEC history readiness.")
        if not isinstance(self.readiness_reason, str) or not self.readiness_reason.strip():
            raise ValueError("readiness_reason must be non-empty text.")


def _calendar_version(cik: str) -> str:
    return SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for(cik).version


def _concept_map_version(cik: str) -> str:
    return concept_map_for_issuer(cik).version


def _supplemental_source_adapters(cik: str) -> Tuple[str, ...]:
    return (FILING_XBRL_SOURCE_ADAPTER,) if concept_map_for_issuer(cik).balance_compositions_for(cik) else ()


SEC_ISSUER_MANIFEST_V1: Tuple[IssuerValuationPolicy, ...] = (
    IssuerValuationPolicy(
        ticker="AAPL",
        cik="0000320193",
        fiscal_calendar_version=_calendar_version("0000320193"),
        concept_map_version=_concept_map_version("0000320193"),
        sec_history_ready=True,
        sec_live_approved=False,
        readiness_reason=(
            "SEC history is available, but the repeated shadow-cutover evidence gate "
            "has not approved automatic live use."
        ),
    ),
    IssuerValuationPolicy(
        ticker="MSFT",
        cik="0000789019",
        fiscal_calendar_version=_calendar_version("0000789019"),
        concept_map_version=_concept_map_version("0000789019"),
        sec_history_ready=True,
        sec_live_approved=False,
        readiness_reason=(
            "SEC history is published in batch backfill-789019-35777629947-1 and verified at the "
            "2024-09-03 and publish cutoffs (Actions run 35778450234), but the repeated "
            "shadow-cutover evidence gate has not approved automatic live use."
        ),
    ),
    IssuerValuationPolicy(
        ticker="WMT",
        cik="0000104169",
        fiscal_calendar_version=_calendar_version("0000104169"),
        concept_map_version=_concept_map_version("0000104169"),
        sec_history_ready=True,
        sec_live_approved=False,
        readiness_reason=(
            "SEC history is published in batch backfill-104169-35778664157-1 and verified at the "
            "2024-09-03 and publish cutoffs (Actions run 35778664157), but the repeated "
            "shadow-cutover evidence gate has not approved automatic live use."
        ),
    ),
    IssuerValuationPolicy(
        ticker="CAT",
        cik="0000018230",
        fiscal_calendar_version=_calendar_version("0000018230"),
        concept_map_version=_concept_map_version("0000018230"),
        supplemental_source_adapters=_supplemental_source_adapters("0000018230"),
        # Concept map v5 composes term debt from filing XBRL and derives net
        # income by the ASC 810 identity, so its history spans two sources: the
        # Company Facts batch and its "+sec_filing_xbrl" companion.
        sec_history_ready=True,
        sec_live_approved=False,
        readiness_reason=(
            "SEC history is published in batch backfill-18230-35940788245-1 and its companion "
            "backfill-18230-35940788245-1+sec_filing_xbrl (concept map sec-companyfacts-v5) and "
            "verified at the 2024-09-03 cutoff (1,071 facts) and the 2026-09-24T00:58:34Z publish "
            "cutoff (1,629 facts) in Actions run 35940788245, but the repeated shadow-cutover "
            "evidence gate has not approved automatic live use."
        ),
    ),
)

_POLICY_BY_TICKER: Dict[str, IssuerValuationPolicy] = {
    policy.ticker: policy for policy in SEC_ISSUER_MANIFEST_V1
}


def issuer_policy_for(ticker: str) -> IssuerValuationPolicy | None:
    if not isinstance(ticker, str):
        return None
    return _POLICY_BY_TICKER.get(ticker.strip().upper())
