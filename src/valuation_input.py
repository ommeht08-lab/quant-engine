"""One deep input seam for live valuations and historical research.

Callers ask for a source policy; adapters own provider-specific statement
construction. A result is either one complete DCF input with provenance or a
typed refusal. Automatic selection never falls back after choosing SEC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Callable, Mapping, Optional, Protocol, Tuple

import pandas as pd

from src.data_ingestion.fetch_financials import fetch_company_financials
from src.dcf_model.dcf import DCFAssumptions
from src.fundamentals.issuer_manifest import (
    ISSUER_MANIFEST_VERSION,
    IssuerValuationPolicy,
    issuer_policy_for,
)
from src.fundamentals.repository import FundamentalsRepository
from src.fundamentals.time_policy import is_aware
from src.fundamentals.valuation_integration import (
    SecDCFPolicy,
    ValuationMarketObservations,
    build_sec_dcf_assumptions,
    build_sec_dcf_financial_data,
    prepare_sec_dcf_inputs,
)
from src.fundamentals.valuation_snapshot import (
    ValuationFundamentalsRequest,
    load_valuation_fundamentals_snapshot,
)


YAHOO_INPUT_POLICY_VERSION = "yahoo-live-statements-v1"


class ValuationInputSource(str, Enum):
    AUTO = "auto"
    SEC = "sec"
    YAHOO = "yahoo"


class ValuationInputIssueCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_ISSUER = "unsupported_issuer"
    SEC_HISTORY_NOT_READY = "sec_history_not_ready"
    SEC_SNAPSHOT_INCOMPLETE = "sec_snapshot_incomplete"
    SEC_COMPOSITION_FAILED = "sec_composition_failed"
    MARKET_OBSERVATIONS_UNAVAILABLE = "market_observations_unavailable"
    YAHOO_INPUT_INCOMPLETE = "yahoo_input_incomplete"


@dataclass(frozen=True)
class ValuationInputIssue:
    code: ValuationInputIssueCode
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, ValuationInputIssueCode):
            raise ValueError("code must be ValuationInputIssueCode.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be non-empty text.")


@dataclass(frozen=True)
class ValuationInputProvenance:
    source: ValuationInputSource
    source_selection_reason: str
    knowledge_cutoff: datetime
    data_vintage_cutoff: datetime
    statement_period_start: Optional[date]
    statement_period_end: date
    policy_version: str
    source_adapter: str
    issuer_manifest_version: str = ISSUER_MANIFEST_VERSION
    concept_map_version: Optional[str] = None
    fiscal_calendar_version: Optional[str] = None
    ingestion_batch_ids: Tuple[str, ...] = field(default_factory=tuple)
    filing_accessions: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.source is ValuationInputSource.AUTO:
            raise ValueError("Provenance must identify the resolved source.")
        for value in (self.knowledge_cutoff, self.data_vintage_cutoff):
            if not isinstance(value, datetime) or not is_aware(value):
                raise ValueError("Valuation input cutoffs must be timezone-aware.")
        if self.statement_period_start and self.statement_period_start > self.statement_period_end:
            raise ValueError("Statement period boundaries are invalid.")
        for name in ("source_selection_reason", "policy_version", "source_adapter"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text.")
        object.__setattr__(
            self,
            "ingestion_batch_ids",
            tuple(sorted(set(self.ingestion_batch_ids))),
        )
        object.__setattr__(self, "filing_accessions", tuple(sorted(set(self.filing_accessions))))


@dataclass(frozen=True)
class ValuationInput:
    ticker: str
    financial_data: Mapping[str, object]
    provenance: ValuationInputProvenance
    default_assumptions: Optional[DCFAssumptions] = None

    def __post_init__(self) -> None:
        ticker = self.ticker.strip().upper() if isinstance(self.ticker, str) else ""
        if not ticker:
            raise ValueError("ticker must be non-empty text.")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "financial_data", dict(self.financial_data))
        if not isinstance(self.provenance, ValuationInputProvenance):
            raise ValueError("provenance must be ValuationInputProvenance.")
        if self.default_assumptions is not None and not isinstance(
            self.default_assumptions, DCFAssumptions
        ):
            raise ValueError("default_assumptions must be DCFAssumptions when provided.")


@dataclass(frozen=True)
class ValuationInputResult:
    requested_source: ValuationInputSource
    valuation_input: Optional[ValuationInput] = None
    issues: Tuple[ValuationInputIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.requested_source, ValuationInputSource):
            raise ValueError("requested_source must be ValuationInputSource.")
        object.__setattr__(self, "issues", tuple(self.issues))
        if any(not isinstance(issue, ValuationInputIssue) for issue in self.issues):
            raise ValueError("issues must contain ValuationInputIssue values.")
        if (self.valuation_input is None) == (not self.issues):
            raise ValueError("A result must contain one complete input or refusal issues.")

    @property
    def is_complete(self) -> bool:
        return self.valuation_input is not None


class ValuationInputAdapter(Protocol):
    def load(
        self,
        ticker: str,
        knowledge_cutoff: datetime,
        data_vintage_cutoff: datetime,
        policy: Optional[IssuerValuationPolicy],
        source_selection_reason: str,
    ) -> ValuationInputResult: ...


def _refusal(
    requested_source: ValuationInputSource,
    code: ValuationInputIssueCode,
    message: str,
) -> ValuationInputResult:
    return ValuationInputResult(
        requested_source=requested_source,
        issues=(ValuationInputIssue(code=code, message=message),),
    )


def _as_requested_source(
    result: ValuationInputResult,
    requested_source: ValuationInputSource,
) -> ValuationInputResult:
    """Preserve the caller's policy while provenance carries the resolved source."""

    return ValuationInputResult(
        requested_source=requested_source,
        valuation_input=result.valuation_input,
        issues=result.issues,
    )


def _latest_statement_period(financial_data: Mapping[str, object]) -> Tuple[Optional[date], date]:
    ends = []
    statement = financial_data.get("income_statement")
    if isinstance(statement, pd.DataFrame) and not statement.empty:
        for column in statement.columns:
            try:
                ends.append(pd.Timestamp(column).date())
            except (TypeError, ValueError):
                continue
    if not ends:
        raise ValueError("No dated financial statement period is available.")
    return None, max(ends)


class YahooValuationInputAdapter:
    """Current Yahoo statement adapter; it makes no point-in-time history claim."""

    def __init__(self, fetcher: Callable[[str], dict] = fetch_company_financials):
        self._fetcher = fetcher

    def load(
        self,
        ticker: str,
        knowledge_cutoff: datetime,
        data_vintage_cutoff: datetime,
        policy: Optional[IssuerValuationPolicy],
        source_selection_reason: str,
    ) -> ValuationInputResult:
        try:
            financial_data = self._fetcher(ticker)
            period_start, period_end = _latest_statement_period(financial_data)
        except (ValueError, TypeError):
            return _refusal(
                ValuationInputSource.YAHOO,
                ValuationInputIssueCode.YAHOO_INPUT_INCOMPLETE,
                "Yahoo did not provide a complete dated statement input.",
            )
        missing = [
            key
            for key in (
                "income_statement",
                "current_price",
                "shares_outstanding",
                "beta",
            )
            if financial_data.get(key) is None
        ]
        if missing:
            return _refusal(
                ValuationInputSource.YAHOO,
                ValuationInputIssueCode.YAHOO_INPUT_INCOMPLETE,
                "Yahoo valuation input is incomplete: " + ", ".join(missing) + ".",
            )
        return ValuationInputResult(
            requested_source=ValuationInputSource.YAHOO,
            valuation_input=ValuationInput(
                ticker=ticker,
                financial_data=financial_data,
                provenance=ValuationInputProvenance(
                    source=ValuationInputSource.YAHOO,
                    source_selection_reason=source_selection_reason,
                    knowledge_cutoff=knowledge_cutoff,
                    data_vintage_cutoff=data_vintage_cutoff,
                    statement_period_start=period_start,
                    statement_period_end=period_end,
                    policy_version=YAHOO_INPUT_POLICY_VERSION,
                    source_adapter="yahoo_finance",
                ),
            ),
        )


class SecValuationInputAdapter:
    """Point-in-time SEC statements plus separately supplied market observations."""

    def __init__(
        self,
        repository: FundamentalsRepository,
        market_observation_loader: Callable[
            [str, IssuerValuationPolicy, datetime], ValuationMarketObservations
        ],
        composition_policy: Optional[SecDCFPolicy] = None,
    ):
        self._repository = repository
        self._market_observation_loader = market_observation_loader
        self._composition_policy = composition_policy or SecDCFPolicy()

    def load(
        self,
        ticker: str,
        knowledge_cutoff: datetime,
        data_vintage_cutoff: datetime,
        policy: Optional[IssuerValuationPolicy],
        source_selection_reason: str,
    ) -> ValuationInputResult:
        if policy is None:
            return _refusal(
                ValuationInputSource.SEC,
                ValuationInputIssueCode.UNSUPPORTED_ISSUER,
                f"{ticker.upper()} is not present in the audited SEC issuer manifest.",
            )
        if not policy.sec_history_ready or policy.fiscal_calendar_version is None:
            return _refusal(
                ValuationInputSource.SEC,
                ValuationInputIssueCode.SEC_HISTORY_NOT_READY,
                policy.readiness_reason,
            )
        request = ValuationFundamentalsRequest(
            cik=policy.cik,
            knowledge_cutoff=knowledge_cutoff,
            data_vintage_cutoff=data_vintage_cutoff,
            source_adapter=policy.source_adapter,
            concept_map_version=policy.concept_map_version,
            fiscal_calendar_version=policy.fiscal_calendar_version,
        )
        snapshot_result = load_valuation_fundamentals_snapshot(self._repository, request)
        if not snapshot_result.is_complete:
            detail = "; ".join(issue.message for issue in snapshot_result.issues)
            return _refusal(
                ValuationInputSource.SEC,
                ValuationInputIssueCode.SEC_SNAPSHOT_INCOMPLETE,
                detail,
            )
        try:
            market = self._market_observation_loader(ticker, policy, knowledge_cutoff)
        except Exception:  # noqa: BLE001 - provider failures become a typed refusal
            return _refusal(
                ValuationInputSource.SEC,
                ValuationInputIssueCode.MARKET_OBSERVATIONS_UNAVAILABLE,
                "Market-only observations are unavailable at the requested cutoff.",
            )
        prepared_result = prepare_sec_dcf_inputs(
            snapshot_result.snapshot, market, self._composition_policy
        )
        if not prepared_result.is_complete:
            return _refusal(
                ValuationInputSource.SEC,
                ValuationInputIssueCode.SEC_COMPOSITION_FAILED,
                "; ".join(issue.message for issue in prepared_result.issues),
            )
        prepared = prepared_result.prepared
        facts = prepared.snapshot.source_facts
        return ValuationInputResult(
            requested_source=ValuationInputSource.SEC,
            valuation_input=ValuationInput(
                ticker=ticker,
                financial_data=build_sec_dcf_financial_data(prepared),
                default_assumptions=build_sec_dcf_assumptions(prepared),
                provenance=ValuationInputProvenance(
                    source=ValuationInputSource.SEC,
                    source_selection_reason=source_selection_reason,
                    knowledge_cutoff=knowledge_cutoff,
                    data_vintage_cutoff=data_vintage_cutoff,
                    statement_period_start=prepared.snapshot.latest.period_start,
                    statement_period_end=prepared.snapshot.latest.period_end,
                    policy_version=prepared.policy.version,
                    source_adapter=policy.source_adapter,
                    concept_map_version=policy.concept_map_version,
                    fiscal_calendar_version=policy.fiscal_calendar_version,
                    ingestion_batch_ids=tuple(fact.lineage.ingestion_batch_id for fact in facts),
                    filing_accessions=tuple(fact.provenance.accession_number for fact in facts),
                ),
            ),
        )


class ValuationInputLoader:
    """Select a source once, then delegate without cross-provider fallback."""

    def __init__(
        self,
        yahoo_adapter: ValuationInputAdapter,
        sec_adapter: Optional[ValuationInputAdapter] = None,
    ):
        self._yahoo_adapter = yahoo_adapter
        self._sec_adapter = sec_adapter

    def load(
        self,
        ticker: str,
        knowledge_cutoff: datetime,
        data_vintage_cutoff: datetime,
        source: ValuationInputSource | str = ValuationInputSource.AUTO,
    ) -> ValuationInputResult:
        try:
            resolved_request = ValuationInputSource(source)
        except ValueError:
            return _refusal(
                ValuationInputSource.AUTO,
                ValuationInputIssueCode.INVALID_REQUEST,
                "source must be auto, sec, or yahoo.",
            )
        if not isinstance(ticker, str) or not ticker.strip():
            return _refusal(
                resolved_request,
                ValuationInputIssueCode.INVALID_REQUEST,
                "ticker is required.",
            )
        if any(
            not isinstance(value, datetime) or not is_aware(value)
            for value in (knowledge_cutoff, data_vintage_cutoff)
        ):
            return _refusal(
                resolved_request,
                ValuationInputIssueCode.INVALID_REQUEST,
                "knowledge and data-vintage cutoffs must be timezone-aware.",
            )
        ticker = ticker.strip().upper()
        policy = issuer_policy_for(ticker)
        if resolved_request is ValuationInputSource.SEC:
            if self._sec_adapter is None:
                return _refusal(
                    resolved_request,
                    ValuationInputIssueCode.SEC_SNAPSHOT_INCOMPLETE,
                    "The SEC fundamentals store is not configured.",
                )
            return _as_requested_source(
                self._sec_adapter.load(
                    ticker,
                    knowledge_cutoff,
                    data_vintage_cutoff,
                    policy,
                    "SEC was explicitly required; Yahoo fallback is prohibited.",
                ),
                resolved_request,
            )
        if resolved_request is ValuationInputSource.YAHOO:
            return _as_requested_source(
                self._yahoo_adapter.load(
                    ticker,
                    knowledge_cutoff,
                    data_vintage_cutoff,
                    policy,
                    "Yahoo was explicitly requested.",
                ),
                resolved_request,
            )
        if policy is not None and policy.sec_live_approved:
            if self._sec_adapter is None:
                return _refusal(
                    resolved_request,
                    ValuationInputIssueCode.SEC_SNAPSHOT_INCOMPLETE,
                    "SEC is approved for this issuer, but the fundamentals store "
                    "is not configured.",
                )
            return _as_requested_source(
                self._sec_adapter.load(
                    ticker,
                    knowledge_cutoff,
                    data_vintage_cutoff,
                    policy,
                    "The issuer's reviewed policy approves SEC for automatic live use.",
                ),
                resolved_request,
            )
        reason = (
            policy.readiness_reason
            if policy is not None
            else "The issuer is not present in the audited SEC issuer manifest."
        )
        return _as_requested_source(
            self._yahoo_adapter.load(
                ticker,
                knowledge_cutoff,
                data_vintage_cutoff,
                policy,
                "Yahoo was selected because SEC automatic use is not approved: " + reason,
            ),
            resolved_request,
        )


def load(
    ticker: str,
    knowledge_cutoff: datetime,
    data_vintage_cutoff: datetime,
    source: ValuationInputSource | str,
    *,
    loader: ValuationInputLoader,
) -> ValuationInputResult:
    """Functional form of the shared valuation-input interface."""

    return loader.load(ticker, knowledge_cutoff, data_vintage_cutoff, source)
