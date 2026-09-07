"""Versioned policy for translating SEC taxonomy tags into canonical concepts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

from .types import StatementKind


class FactPeriodType(str, Enum):
    DURATION = "duration"
    INSTANT = "instant"
    COVER = "cover"


@dataclass(frozen=True)
class ConceptRule:
    taxonomy: str
    raw_tag: str
    canonical_concept: str
    statement_kind: StatementKind
    period_type: FactPeriodType
    allowed_units: Tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in ("taxonomy", "raw_tag", "canonical_concept"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ConceptRule.{field_name} must be a non-empty string.")
        if not isinstance(self.statement_kind, StatementKind):
            raise ValueError("ConceptRule.statement_kind must be a StatementKind.")
        if not isinstance(self.period_type, FactPeriodType):
            raise ValueError("ConceptRule.period_type must be a FactPeriodType.")
        if isinstance(self.allowed_units, (str, bytes)):
            raise ValueError("ConceptRule.allowed_units must be a collection of unit names.")
        try:
            units = tuple(self.allowed_units)
        except TypeError:
            raise ValueError(
                "ConceptRule.allowed_units must be a collection of unit names."
            ) from None
        if not units or any(not isinstance(unit, str) or not unit.strip() for unit in units):
            raise ValueError("ConceptRule.allowed_units must contain non-empty unit names.")
        if len(units) != len(set(units)):
            raise ValueError("ConceptRule.allowed_units must not contain duplicates.")
        object.__setattr__(self, "allowed_units", tuple(sorted(units)))

        if self.statement_kind is StatementKind.COVER:
            if self.period_type is not FactPeriodType.COVER:
                raise ValueError("COVER concepts must use the COVER period type.")
        elif self.period_type is FactPeriodType.COVER:
            raise ValueError("Only COVER concepts may use the COVER period type.")
        elif (
            self.statement_kind is StatementKind.BALANCE_SHEET
            and self.period_type is not FactPeriodType.INSTANT
        ):
            raise ValueError("Balance-sheet concepts must use the instant period type.")
        elif (
            self.statement_kind
            in (StatementKind.INCOME_STATEMENT, StatementKind.CASH_FLOW)
            and self.period_type is not FactPeriodType.DURATION
        ):
            raise ValueError("Income-statement and cash-flow concepts must use the duration period type.")


@dataclass(frozen=True)
class ConceptMap:
    version: str
    rules: Tuple[ConceptRule, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("ConceptMap.version must be a non-empty string.")
        try:
            rules = tuple(self.rules)
        except TypeError:
            raise ValueError("ConceptMap.rules must be a collection of ConceptRule values.") from None
        if not rules:
            raise ValueError("ConceptMap.rules must not be empty.")
        if any(not isinstance(rule, ConceptRule) for rule in rules):
            raise ValueError("ConceptMap.rules must contain only ConceptRule values.")

        raw_keys = [(rule.taxonomy, rule.raw_tag) for rule in rules]
        if len(raw_keys) != len(set(raw_keys)):
            raise ValueError("ConceptMap rules must have unique taxonomy/raw-tag pairs.")

        policies: Dict[str, Tuple[StatementKind, FactPeriodType, Tuple[str, ...]]] = {}
        for rule in rules:
            policy = (rule.statement_kind, rule.period_type, rule.allowed_units)
            existing = policies.setdefault(rule.canonical_concept, policy)
            if existing != policy:
                raise ValueError(
                    "All raw tags for one canonical concept must share statement, period, "
                    f"and unit policy: {rule.canonical_concept!r}."
                )

        object.__setattr__(self, "rules", tuple(sorted(rules, key=lambda rule: raw_keys_for(rule))))

    def rule_for(self, taxonomy: str, raw_tag: str) -> Optional[ConceptRule]:
        for rule in self.rules:
            if rule.taxonomy == taxonomy and rule.raw_tag == raw_tag:
                return rule
        return None


def raw_keys_for(rule: ConceptRule) -> Tuple[str, str]:
    return rule.taxonomy, rule.raw_tag


def _rule(
    raw_tag: str,
    canonical_concept: str,
    statement_kind: StatementKind,
    period_type: FactPeriodType,
    allowed_units: Tuple[str, ...] = ("USD",),
    taxonomy: str = "us-gaap",
) -> ConceptRule:
    return ConceptRule(
        taxonomy=taxonomy,
        raw_tag=raw_tag,
        canonical_concept=canonical_concept,
        statement_kind=statement_kind,
        period_type=period_type,
        allowed_units=allowed_units,
    )


# Deliberately conservative: tags are mapped only when their accounting meaning
# and period geometry are suitable for the first linked-model concept set.
SEC_CONCEPT_MAP_V1 = ConceptMap(
    version="sec-companyfacts-v1",
    rules=(
        _rule(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "revenue",
            StatementKind.INCOME_STATEMENT,
            FactPeriodType.DURATION,
        ),
        _rule("Revenues", "revenue", StatementKind.INCOME_STATEMENT, FactPeriodType.DURATION),
        _rule(
            "SalesRevenueNet",
            "revenue",
            StatementKind.INCOME_STATEMENT,
            FactPeriodType.DURATION,
        ),
        _rule(
            "OperatingIncomeLoss",
            "operating_income",
            StatementKind.INCOME_STATEMENT,
            FactPeriodType.DURATION,
        ),
        _rule("NetIncomeLoss", "net_income", StatementKind.INCOME_STATEMENT, FactPeriodType.DURATION),
        _rule(
            "InterestExpenseNonOperating",
            "interest_expense",
            StatementKind.INCOME_STATEMENT,
            FactPeriodType.DURATION,
        ),
        _rule(
            "IncomeTaxExpenseBenefit",
            "income_tax_expense",
            StatementKind.INCOME_STATEMENT,
            FactPeriodType.DURATION,
        ),
        _rule(
            "CashAndCashEquivalentsAtCarryingValue",
            "cash_and_cash_equivalents",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule(
            "AccountsReceivableNetCurrent",
            "accounts_receivable",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule("InventoryNet", "inventory", StatementKind.BALANCE_SHEET, FactPeriodType.INSTANT),
        _rule(
            "AccountsPayableCurrent",
            "accounts_payable",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule(
            "PropertyPlantAndEquipmentNet",
            "property_plant_and_equipment_net",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule("LongTermDebtCurrent", "current_debt", StatementKind.BALANCE_SHEET, FactPeriodType.INSTANT),
        _rule(
            "LongTermDebtNoncurrent",
            "long_term_debt",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule("StockholdersEquity", "shareholders_equity", StatementKind.BALANCE_SHEET, FactPeriodType.INSTANT),
        _rule(
            "RetainedEarningsAccumulatedDeficit",
            "retained_earnings",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule(
            "DepreciationDepletionAndAmortization",
            "depreciation_and_amortization",
            StatementKind.CASH_FLOW,
            FactPeriodType.DURATION,
        ),
        _rule(
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "capital_expenditures",
            StatementKind.CASH_FLOW,
            FactPeriodType.DURATION,
        ),
        _rule(
            "NetCashProvidedByUsedInOperatingActivities",
            "operating_cash_flow",
            StatementKind.CASH_FLOW,
            FactPeriodType.DURATION,
        ),
        _rule("PaymentsOfDividends", "dividends_paid", StatementKind.CASH_FLOW, FactPeriodType.DURATION),
        _rule(
            "PaymentsForRepurchaseOfCommonStock",
            "share_repurchases",
            StatementKind.CASH_FLOW,
            FactPeriodType.DURATION,
        ),
        _rule(
            "EntityCommonStockSharesOutstanding",
            "common_shares_outstanding",
            StatementKind.COVER,
            FactPeriodType.COVER,
            allowed_units=("shares",),
            taxonomy="dei",
        ),
    ),
)
