"""Pure, offline reconciliation of one historical three-statement period.

This module does not extract, select, publish, forecast, or value anything. It
accepts already-normalized quarterly values on one currency basis and either
returns a fully linked period or one deterministic refusal.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Dict, Iterable, List, Optional, Tuple

from .quarterly import QuarterlyFundamentals, StandaloneQuarterValue
from .time_policy import is_aware
from .types import FinancialFact, FundamentalHistory, normalize_cik


LINKED_DURATION_CONCEPTS = (
    "capital_expenditures",
    "depreciation_and_amortization",
    "financing_cash_flow",
    "investing_cash_flow",
    "net_change_in_cash",
    "net_income",
    "operating_cash_flow",
    "operating_income",
    "revenue",
)
_REQUIRED_BALANCE_CONCEPTS = (
    "cash_and_restricted_cash",
    "total_assets",
    "total_liabilities",
)


@dataclass(frozen=True)
class ThreeStatementPeriodInput:
    """Aggregate historical inputs for one standalone fiscal quarter.

    ``capital_expenditures`` is a positive amount spent. Cash-flow statement
    aggregates retain their reported signs. Beginning and ending cash use the
    same cash-and-restricted-cash definition as ``net_change_in_cash``.
    """

    cik: str
    fiscal_year: int
    fiscal_quarter: int
    period_start: date
    period_end: date
    currency: str
    revenue: Decimal
    operating_income: Decimal
    net_income: Decimal
    depreciation_and_amortization: Decimal
    operating_cash_flow: Decimal
    capital_expenditures: Decimal
    investing_cash_flow: Decimal
    financing_cash_flow: Decimal
    exchange_rate_effect: Decimal
    net_change_in_cash: Decimal
    beginning_cash: Decimal
    ending_cash: Decimal
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if isinstance(self.fiscal_year, bool) or not isinstance(self.fiscal_year, int):
            raise ValueError("fiscal_year must be an integer.")
        if self.fiscal_year <= 0:
            raise ValueError("fiscal_year must be positive.")
        if (
            isinstance(self.fiscal_quarter, bool)
            or not isinstance(self.fiscal_quarter, int)
            or self.fiscal_quarter not in (1, 2, 3, 4)
        ):
            raise ValueError("fiscal_quarter must be from 1 through 4.")
        if (
            not isinstance(self.period_start, date)
            or not isinstance(self.period_end, date)
            or isinstance(self.period_start, datetime)
            or isinstance(self.period_end, datetime)
            or self.period_start > self.period_end
        ):
            raise ValueError("The period must have valid, ordered date boundaries.")
        if (
            not isinstance(self.currency, str)
            or not self.currency.strip()
            or self.currency != self.currency.strip()
        ):
            raise ValueError("currency must be non-empty text.")
        decimal_fields = (
            item
            for item in fields(self)
            if item.name
            not in {
                "cik",
                "fiscal_year",
                "fiscal_quarter",
                "period_start",
                "period_end",
                "currency",
            }
        )
        for item in decimal_fields:
            value = getattr(self, item.name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{item.name} must be a finite Decimal.")
        if self.revenue < 0:
            raise ValueError("revenue cannot be negative.")
        if self.capital_expenditures < 0:
            raise ValueError("capital_expenditures must use a positive-spend convention.")
        if min(
            self.beginning_cash,
            self.ending_cash,
            self.total_assets,
            self.total_liabilities,
        ) < 0:
            raise ValueError("Cash, asset, and liability balances cannot be negative.")


class ThreeStatementIssueCode(str, Enum):
    BALANCE_SHEET_DOES_NOT_BALANCE = "balance_sheet_does_not_balance"
    CASH_FLOW_DOES_NOT_SUM = "cash_flow_does_not_sum"
    CASH_ROLLFORWARD_DOES_NOT_BALANCE = "cash_rollforward_does_not_balance"


@dataclass(frozen=True)
class ThreeStatementIssue:
    code: ThreeStatementIssueCode
    message: str
    difference: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.code, ThreeStatementIssueCode):
            raise ValueError("code must be a ThreeStatementIssueCode.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be non-empty text.")
        if not isinstance(self.difference, Decimal) or not self.difference.is_finite():
            raise ValueError("difference must be a finite Decimal.")


@dataclass(frozen=True)
class LinkedThreeStatementPeriod:
    source: ThreeStatementPeriodInput
    free_cash_flow: Decimal
    operating_cash_conversion_adjustments: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.source, ThreeStatementPeriodInput):
            raise ValueError("source must be a ThreeStatementPeriodInput.")
        for field_name in ("free_cash_flow", "operating_cash_conversion_adjustments"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be a finite Decimal.")


@dataclass(frozen=True)
class ThreeStatementLinkage:
    source: ThreeStatementPeriodInput
    linked_period: Optional[LinkedThreeStatementPeriod] = None
    issues: Tuple[ThreeStatementIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.source, ThreeStatementPeriodInput):
            raise ValueError("source must be a ThreeStatementPeriodInput.")
        object.__setattr__(self, "issues", tuple(self.issues))
        if any(not isinstance(issue, ThreeStatementIssue) for issue in self.issues):
            raise ValueError("issues must contain only ThreeStatementIssue values.")
        if self.linked_period is not None and not isinstance(
            self.linked_period, LinkedThreeStatementPeriod
        ):
            raise ValueError("linked_period must be a LinkedThreeStatementPeriod.")
        if (self.linked_period is None) == (not self.issues):
            raise ValueError("Linkage must contain complete output or refusal issues.")
        if self.linked_period is not None and self.linked_period.source != self.source:
            raise ValueError("linked_period must preserve the exact source input.")

    @property
    def is_complete(self) -> bool:
        return self.linked_period is not None


def link_three_statements(source: ThreeStatementPeriodInput) -> ThreeStatementLinkage:
    """Reconcile one exact historical quarter without tolerances or partial output."""

    if not isinstance(source, ThreeStatementPeriodInput):
        raise ValueError("source must be a ThreeStatementPeriodInput.")

    balance_difference = (
        source.total_assets - source.total_liabilities - source.total_equity
    )
    cash_flow_difference = (
        source.operating_cash_flow
        + source.investing_cash_flow
        + source.financing_cash_flow
        + source.exchange_rate_effect
        - source.net_change_in_cash
    )
    cash_rollforward_difference = (
        source.beginning_cash + source.net_change_in_cash - source.ending_cash
    )
    checks = (
        (
            ThreeStatementIssueCode.BALANCE_SHEET_DOES_NOT_BALANCE,
            "Total assets do not equal total liabilities plus total equity.",
            balance_difference,
        ),
        (
            ThreeStatementIssueCode.CASH_FLOW_DOES_NOT_SUM,
            "Cash-flow statement sections and exchange effect do not equal net cash change.",
            cash_flow_difference,
        ),
        (
            ThreeStatementIssueCode.CASH_ROLLFORWARD_DOES_NOT_BALANCE,
            "Beginning cash plus net cash change does not equal ending cash.",
            cash_rollforward_difference,
        ),
    )
    issues = tuple(
        ThreeStatementIssue(code, message, difference)
        for code, message, difference in checks
        if difference != 0
    )
    if issues:
        return ThreeStatementLinkage(source=source, issues=issues)

    return ThreeStatementLinkage(
        source=source,
        linked_period=LinkedThreeStatementPeriod(
            source=source,
            free_cash_flow=source.operating_cash_flow - source.capital_expenditures,
            operating_cash_conversion_adjustments=(
                source.operating_cash_flow
                - source.net_income
                - source.depreciation_and_amortization
            ),
        ),
    )


class ThreeStatementAssemblyIssueCode(str, Enum):
    UPSTREAM_REFUSAL = "upstream_refusal"
    HISTORY_MISMATCH = "history_mismatch"
    MISSING_QUARTER_VALUE = "missing_quarter_value"
    AMBIGUOUS_QUARTER_VALUE = "ambiguous_quarter_value"
    INCOMPATIBLE_QUARTER = "incompatible_quarter"
    MISSING_BALANCE = "missing_balance"
    AMBIGUOUS_BALANCE = "ambiguous_balance"
    INCOMPATIBLE_LINEAGE = "incompatible_lineage"
    ACCOUNTING_LINK_FAILURE = "accounting_link_failure"
    NO_LINKABLE_PERIODS = "no_linkable_periods"


@dataclass(frozen=True)
class ThreeStatementAssemblyIssue:
    code: ThreeStatementAssemblyIssueCode
    message: str
    fiscal_year: Optional[int] = None
    fiscal_quarter: Optional[int] = None
    concept: Optional[str] = None
    linkage_issues: Tuple[ThreeStatementIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.code, ThreeStatementAssemblyIssueCode):
            raise ValueError("code must be a ThreeStatementAssemblyIssueCode.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be non-empty text.")
        object.__setattr__(self, "linkage_issues", tuple(self.linkage_issues))
        if any(not isinstance(issue, ThreeStatementIssue) for issue in self.linkage_issues):
            raise ValueError("linkage_issues must contain only ThreeStatementIssue values.")


@dataclass(frozen=True)
class LinkedThreeStatementHistory:
    cik: str
    knowledge_cutoff: datetime
    periods: Tuple[LinkedThreeStatementPeriod, ...] = field(default_factory=tuple)
    issues: Tuple[ThreeStatementAssemblyIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if not isinstance(self.knowledge_cutoff, datetime) or not is_aware(
            self.knowledge_cutoff
        ):
            raise ValueError("knowledge_cutoff must be timezone-aware.")
        object.__setattr__(self, "periods", tuple(self.periods))
        object.__setattr__(self, "issues", tuple(self.issues))
        if any(not isinstance(period, LinkedThreeStatementPeriod) for period in self.periods):
            raise ValueError("periods must contain only LinkedThreeStatementPeriod values.")
        if any(not isinstance(issue, ThreeStatementAssemblyIssue) for issue in self.issues):
            raise ValueError("issues must contain only ThreeStatementAssemblyIssue values.")
        if bool(self.periods) == bool(self.issues):
            raise ValueError("Linked history must contain complete periods or refusal issues.")

    @property
    def is_complete(self) -> bool:
        return bool(self.periods)


def _assembly_refusal(
    history: FundamentalHistory,
    code: ThreeStatementAssemblyIssueCode,
    message: str,
    *,
    fiscal_year: Optional[int] = None,
    fiscal_quarter: Optional[int] = None,
    concept: Optional[str] = None,
    linkage_issues: Tuple[ThreeStatementIssue, ...] = (),
) -> LinkedThreeStatementHistory:
    return LinkedThreeStatementHistory(
        cik=history.cik,
        knowledge_cutoff=history.knowledge_cutoff,
        issues=(
            ThreeStatementAssemblyIssue(
                code=code,
                message=message,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                concept=concept,
                linkage_issues=linkage_issues,
            ),
        ),
    )


def _quarter_index(
    quarterly: QuarterlyFundamentals,
) -> Dict[Tuple[int, int, str], List[StandaloneQuarterValue]]:
    result: Dict[Tuple[int, int, str], List[StandaloneQuarterValue]] = {}
    for value in quarterly.quarters:
        result.setdefault(
            (value.fiscal_year, value.fiscal_quarter, value.key.concept), []
        ).append(value)
    return result


def _balance_index(
    history: FundamentalHistory,
) -> Dict[Tuple[date, str], List[FinancialFact]]:
    result: Dict[Tuple[date, str], List[FinancialFact]] = {}
    for period in history.balance_sheet_periods:
        for fact in period.facts:
            if not fact.identity.context.dimensions:
                result.setdefault(
                    (fact.period.period_end, fact.identity.concept), []
                ).append(fact)
    return result


def _single_balance(
    index: Dict[Tuple[date, str], List[FinancialFact]],
    period_end: date,
    concept: str,
) -> Tuple[Optional[FinancialFact], bool]:
    matches = index.get((period_end, concept), [])
    return (matches[0] if len(matches) == 1 else None, len(matches) > 1)


def _lineage_policy(facts: Iterable[FinancialFact]) -> Tuple[Tuple[str, str, str], ...]:
    return tuple(
        sorted(
            {
                (
                    fact.lineage.source_adapter,
                    fact.lineage.concept_map_version,
                    fact.lineage.fiscal_calendar_version or "",
                )
                for fact in facts
            }
        )
    )


def assemble_linked_three_statement_history(
    history: FundamentalHistory,
    quarterly: QuarterlyFundamentals,
) -> LinkedThreeStatementHistory:
    """Build every exactly linkable historical quarter or refuse all output."""

    if not isinstance(history, FundamentalHistory):
        raise ValueError("history must be a FundamentalHistory.")
    if not isinstance(quarterly, QuarterlyFundamentals):
        raise ValueError("quarterly must be QuarterlyFundamentals.")
    if not quarterly.is_complete:
        return _assembly_refusal(
            history,
            ThreeStatementAssemblyIssueCode.UPSTREAM_REFUSAL,
            "Quarterly fundamentals must be complete before three-statement assembly.",
        )
    if (
        history.cik != quarterly.cik
        or history.knowledge_cutoff != quarterly.knowledge_cutoff
    ):
        return _assembly_refusal(
            history,
            ThreeStatementAssemblyIssueCode.HISTORY_MISMATCH,
            "History and quarterly fundamentals must describe the same issuer and cutoff.",
        )

    quarters = _quarter_index(quarterly)
    balances = _balance_index(history)
    revenue_periods = sorted(
        (
            values[0]
            for (fiscal_year, fiscal_quarter, concept), values in quarters.items()
            if concept == "revenue" and len(values) == 1
        ),
        key=lambda value: (value.period_end, value.fiscal_year, value.fiscal_quarter),
    )
    if any(
        len(values) > 1
        for (_fiscal_year, _fiscal_quarter, concept), values in quarters.items()
        if concept == "revenue"
    ):
        return _assembly_refusal(
            history,
            ThreeStatementAssemblyIssueCode.AMBIGUOUS_QUARTER_VALUE,
            "Revenue has more than one quarterly series for the same fiscal period.",
            concept="revenue",
        )
    if not revenue_periods:
        return _assembly_refusal(
            history,
            ThreeStatementAssemblyIssueCode.MISSING_QUARTER_VALUE,
            "No unambiguous revenue quarter is available as the statement-period anchor.",
            concept="revenue",
        )

    linked = []
    for previous, anchor in zip(revenue_periods, revenue_periods[1:]):
        expected_fiscal_period = (
            (previous.fiscal_year, previous.fiscal_quarter + 1)
            if previous.fiscal_quarter < 4
            else (previous.fiscal_year + 1, 1)
        )
        if (
            anchor.period_start != previous.period_end + timedelta(days=1)
            or (anchor.fiscal_year, anchor.fiscal_quarter) != expected_fiscal_period
        ):
            continue
        fiscal_year = anchor.fiscal_year
        fiscal_quarter = anchor.fiscal_quarter
        selected_quarters: Dict[str, StandaloneQuarterValue] = {}
        for concept in LINKED_DURATION_CONCEPTS:
            matches = quarters.get((fiscal_year, fiscal_quarter, concept), [])
            if not matches:
                return _assembly_refusal(
                    history,
                    ThreeStatementAssemblyIssueCode.MISSING_QUARTER_VALUE,
                    "A required duration concept is absent from the quarter.",
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    concept=concept,
                )
            if len(matches) != 1:
                return _assembly_refusal(
                    history,
                    ThreeStatementAssemblyIssueCode.AMBIGUOUS_QUARTER_VALUE,
                    "A required duration concept has more than one quarterly series.",
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    concept=concept,
                )
            selected_quarters[concept] = matches[0]

        if any(
            value.period_start != anchor.period_start
            or value.period_end != anchor.period_end
            or value.key.unit != anchor.key.unit
            or value.key.currency != anchor.key.currency
            or value.key.context != anchor.key.context
            for value in selected_quarters.values()
        ):
            return _assembly_refusal(
                history,
                ThreeStatementAssemblyIssueCode.INCOMPATIBLE_QUARTER,
                "Required duration concepts do not share one period, unit, currency, and context.",
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
            )
        if (
            anchor.key.unit != "USD"
            or anchor.key.currency != "USD"
            or anchor.key.context.dimensions
        ):
            return _assembly_refusal(
                history,
                ThreeStatementAssemblyIssueCode.INCOMPATIBLE_QUARTER,
                "Linked three-statement assembly currently requires consolidated USD facts.",
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
            )

        selected_balances: Dict[str, FinancialFact] = {}
        for concept in _REQUIRED_BALANCE_CONCEPTS:
            fact, ambiguous = _single_balance(balances, anchor.period_end, concept)
            if ambiguous:
                return _assembly_refusal(
                    history,
                    ThreeStatementAssemblyIssueCode.AMBIGUOUS_BALANCE,
                    "A required ending balance has more than one consolidated value.",
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    concept=concept,
                )
            if fact is None:
                return _assembly_refusal(
                    history,
                    ThreeStatementAssemblyIssueCode.MISSING_BALANCE,
                    "A required ending balance is absent.",
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    concept=concept,
                )
            selected_balances[concept] = fact

        beginning_cash, ambiguous = _single_balance(
            balances, previous.period_end, "cash_and_restricted_cash"
        )
        if ambiguous or beginning_cash is None:
            return _assembly_refusal(
                history,
                ThreeStatementAssemblyIssueCode.AMBIGUOUS_BALANCE
                if ambiguous
                else ThreeStatementAssemblyIssueCode.MISSING_BALANCE,
                "The prior-period cash balance is not uniquely available.",
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                concept="cash_and_restricted_cash",
            )

        total_equity, ambiguous = _single_balance(
            balances, anchor.period_end, "total_equity"
        )
        if ambiguous:
            return _assembly_refusal(
                history,
                ThreeStatementAssemblyIssueCode.AMBIGUOUS_BALANCE,
                "Total equity has more than one consolidated value.",
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                concept="total_equity",
            )
        if total_equity is None:
            total_equity, ambiguous = _single_balance(
                balances, anchor.period_end, "shareholders_equity"
            )
            if ambiguous or total_equity is None:
                return _assembly_refusal(
                    history,
                    ThreeStatementAssemblyIssueCode.AMBIGUOUS_BALANCE
                    if ambiguous
                    else ThreeStatementAssemblyIssueCode.MISSING_BALANCE,
                    "Neither total equity nor one unambiguous shareholders' equity fallback is available.",
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    concept="total_equity",
                )

        optional_exchange = quarters.get(
            (fiscal_year, fiscal_quarter, "exchange_rate_effect"), []
        )
        if len(optional_exchange) > 1:
            return _assembly_refusal(
                history,
                ThreeStatementAssemblyIssueCode.AMBIGUOUS_QUARTER_VALUE,
                "Exchange-rate effect has more than one quarterly series.",
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                concept="exchange_rate_effect",
            )
        exchange_rate_effect = optional_exchange[0].value if optional_exchange else Decimal("0")

        source_facts = tuple(
            fact
            for value in selected_quarters.values()
            for fact in value.source_facts
        ) + tuple(selected_balances.values()) + (beginning_cash, total_equity)
        if optional_exchange:
            source_facts += optional_exchange[0].source_facts
        balance_facts = tuple(selected_balances.values()) + (beginning_cash, total_equity)
        if any(
            fact.identity.unit != anchor.key.unit
            or fact.identity.currency != anchor.key.currency
            or fact.identity.context != anchor.key.context
            for fact in balance_facts
        ):
            return _assembly_refusal(
                history,
                ThreeStatementAssemblyIssueCode.INCOMPATIBLE_QUARTER,
                "Duration values and balance snapshots do not share one unit, currency, and context.",
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
            )
        if optional_exchange and (
            optional_exchange[0].period_start != anchor.period_start
            or optional_exchange[0].period_end != anchor.period_end
            or optional_exchange[0].key.unit != anchor.key.unit
            or optional_exchange[0].key.currency != anchor.key.currency
            or optional_exchange[0].key.context != anchor.key.context
        ):
            return _assembly_refusal(
                history,
                ThreeStatementAssemblyIssueCode.INCOMPATIBLE_QUARTER,
                "Exchange-rate effect does not share the linked quarter's identity.",
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                concept="exchange_rate_effect",
            )
        if len(_lineage_policy(source_facts)) != 1:
            return _assembly_refusal(
                history,
                ThreeStatementAssemblyIssueCode.INCOMPATIBLE_LINEAGE,
                "Linked statement facts do not share one source, mapping, and calendar policy.",
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
            )

        linkage = link_three_statements(
            ThreeStatementPeriodInput(
                cik=history.cik,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                period_start=anchor.period_start,
                period_end=anchor.period_end,
                currency="USD",
                revenue=selected_quarters["revenue"].value,
                operating_income=selected_quarters["operating_income"].value,
                net_income=selected_quarters["net_income"].value,
                depreciation_and_amortization=selected_quarters[
                    "depreciation_and_amortization"
                ].value,
                operating_cash_flow=selected_quarters["operating_cash_flow"].value,
                capital_expenditures=selected_quarters["capital_expenditures"].value,
                investing_cash_flow=selected_quarters["investing_cash_flow"].value,
                financing_cash_flow=selected_quarters["financing_cash_flow"].value,
                exchange_rate_effect=exchange_rate_effect,
                net_change_in_cash=selected_quarters["net_change_in_cash"].value,
                beginning_cash=beginning_cash.value,
                ending_cash=selected_balances["cash_and_restricted_cash"].value,
                total_assets=selected_balances["total_assets"].value,
                total_liabilities=selected_balances["total_liabilities"].value,
                total_equity=total_equity.value,
            )
        )
        if not linkage.is_complete:
            return _assembly_refusal(
                history,
                ThreeStatementAssemblyIssueCode.ACCOUNTING_LINK_FAILURE,
                "The selected quarter fails one or more three-statement accounting links.",
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                linkage_issues=linkage.issues,
            )
        linked.append(linkage.linked_period)

    if not linked:
        return _assembly_refusal(
            history,
            ThreeStatementAssemblyIssueCode.NO_LINKABLE_PERIODS,
            "No quarter has both a consecutive predecessor and complete statement inputs.",
        )
    return LinkedThreeStatementHistory(
        cik=history.cik,
        knowledge_cutoff=history.knowledge_cutoff,
        periods=tuple(linked),
    )
