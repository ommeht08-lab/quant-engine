"""Versioned policy for translating SEC taxonomy tags into canonical concepts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, Optional, Tuple

from .types import StatementKind, normalize_cik


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


def _require_text(owner: str, field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{owner}.{field_name} must be a non-empty string.")


@dataclass(frozen=True)
class IssuerTagExclusion:
    """One issuer's raw tag that must not feed a canonical concept.

    Used only where the issuer's filings prove that the excluded tag reports a
    different economic quantity from the concept's other synonyms. Every other
    synonym disagreement still refuses extraction.
    """

    cik: str
    taxonomy: str
    raw_tag: str
    canonical_concept: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        for field_name in ("taxonomy", "raw_tag", "canonical_concept", "reason"):
            _require_text("IssuerTagExclusion", field_name, getattr(self, field_name))


@dataclass(frozen=True)
class OpeningBalanceExclusion:
    """One issuer's instant tag whose fiscal-year-start values are opening balances.

    Such facts are dated on the first day of a fiscal year rather than on the
    prior period end. They keep their source lineage but are never relabeled
    to a period end or offered to canonical balance selection.
    """

    cik: str
    taxonomy: str
    raw_tag: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        for field_name in ("taxonomy", "raw_tag", "reason"):
            _require_text("OpeningBalanceExclusion", field_name, getattr(self, field_name))


@dataclass(frozen=True)
class ConceptMap:
    version: str
    rules: Tuple[ConceptRule, ...]
    issuer_tag_exclusions: Tuple[IssuerTagExclusion, ...] = ()
    opening_balance_exclusions: Tuple[OpeningBalanceExclusion, ...] = ()

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

        rules_by_key = {raw_keys_for(rule): rule for rule in rules}
        tags_by_concept: Dict[str, set] = {}
        for rule in rules:
            tags_by_concept.setdefault(rule.canonical_concept, set()).add(raw_keys_for(rule))

        try:
            tag_exclusions = tuple(self.issuer_tag_exclusions)
            opening_exclusions = tuple(self.opening_balance_exclusions)
        except TypeError:
            raise ValueError("ConceptMap exclusions must be collections.") from None
        if any(not isinstance(item, IssuerTagExclusion) for item in tag_exclusions):
            raise ValueError("issuer_tag_exclusions must contain IssuerTagExclusion values.")
        if any(not isinstance(item, OpeningBalanceExclusion) for item in opening_exclusions):
            raise ValueError(
                "opening_balance_exclusions must contain OpeningBalanceExclusion values."
            )

        excluded_by_issuer_concept: Dict[Tuple[str, str], set] = {}
        for exclusion in tag_exclusions:
            key = (exclusion.taxonomy, exclusion.raw_tag)
            rule = rules_by_key.get(key)
            if rule is None or rule.canonical_concept != exclusion.canonical_concept:
                raise ValueError(
                    "An issuer tag exclusion must name an existing rule and its concept: "
                    f"{exclusion.raw_tag!r}."
                )
            excluded = excluded_by_issuer_concept.setdefault(
                (exclusion.cik, exclusion.canonical_concept), set()
            )
            if key in excluded:
                raise ValueError("Issuer tag exclusions must not contain duplicates.")
            excluded.add(key)
        for (_cik, concept), excluded in excluded_by_issuer_concept.items():
            if not tags_by_concept[concept] - excluded:
                raise ValueError(
                    f"Issuer tag exclusions must leave at least one tag for {concept!r}."
                )

        opening_keys = set()
        for exclusion in opening_exclusions:
            key = (exclusion.taxonomy, exclusion.raw_tag)
            rule = rules_by_key.get(key)
            if rule is None or rule.period_type is not FactPeriodType.INSTANT:
                raise ValueError(
                    "An opening-balance exclusion must name an existing instant rule: "
                    f"{exclusion.raw_tag!r}."
                )
            if (exclusion.cik, key) in opening_keys:
                raise ValueError("Opening-balance exclusions must not contain duplicates.")
            opening_keys.add((exclusion.cik, key))

        object.__setattr__(self, "issuer_tag_exclusions", tag_exclusions)
        object.__setattr__(self, "opening_balance_exclusions", opening_exclusions)

    def rule_for(self, taxonomy: str, raw_tag: str) -> Optional[ConceptRule]:
        for rule in self.rules:
            if rule.taxonomy == taxonomy and rule.raw_tag == raw_tag:
                return rule
        return None

    def rules_for_issuer(self, cik: str) -> Tuple[ConceptRule, ...]:
        """Rules that apply to one issuer after its explicit tag exclusions."""

        normalized_cik = normalize_cik(cik)
        excluded = {
            (exclusion.taxonomy, exclusion.raw_tag)
            for exclusion in self.issuer_tag_exclusions
            if exclusion.cik == normalized_cik
        }
        return tuple(rule for rule in self.rules if raw_keys_for(rule) not in excluded)

    def opening_balance_tags_for(self, cik: str) -> FrozenSet[Tuple[str, str]]:
        """Instant tags whose fiscal-year-start values are this issuer's opening balances."""

        normalized_cik = normalize_cik(cik)
        return frozenset(
            (exclusion.taxonomy, exclusion.raw_tag)
            for exclusion in self.opening_balance_exclusions
            if exclusion.cik == normalized_cik
        )


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


# Version 2 extends the immutable first policy with high-confidence aggregate
# lines needed to reconcile the three primary financial statements. Version 1
# remains available so facts already published under it keep exact lineage.
SEC_CONCEPT_MAP_V2 = ConceptMap(
    version="sec-companyfacts-v2",
    rules=SEC_CONCEPT_MAP_V1.rules
    + (
        _rule(
            "GrossProfit",
            "gross_profit",
            StatementKind.INCOME_STATEMENT,
            FactPeriodType.DURATION,
        ),
        _rule(
            "CostOfRevenue",
            "cost_of_revenue",
            StatementKind.INCOME_STATEMENT,
            FactPeriodType.DURATION,
        ),
        _rule(
            "CostOfGoodsAndServicesSold",
            "cost_of_revenue",
            StatementKind.INCOME_STATEMENT,
            FactPeriodType.DURATION,
        ),
        _rule(
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "pretax_income",
            StatementKind.INCOME_STATEMENT,
            FactPeriodType.DURATION,
        ),
        _rule(
            "Assets",
            "total_assets",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule(
            "AssetsCurrent",
            "current_assets",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule(
            "Liabilities",
            "total_liabilities",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule(
            "LiabilitiesCurrent",
            "current_liabilities",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule(
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "total_equity",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule(
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "cash_and_restricted_cash",
            StatementKind.BALANCE_SHEET,
            FactPeriodType.INSTANT,
        ),
        _rule(
            "NetCashProvidedByUsedInInvestingActivities",
            "investing_cash_flow",
            StatementKind.CASH_FLOW,
            FactPeriodType.DURATION,
        ),
        _rule(
            "NetCashProvidedByUsedInFinancingActivities",
            "financing_cash_flow",
            StatementKind.CASH_FLOW,
            FactPeriodType.DURATION,
        ),
        _rule(
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
            "net_change_in_cash",
            StatementKind.CASH_FLOW,
            FactPeriodType.DURATION,
        ),
        _rule(
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "exchange_rate_effect",
            StatementKind.CASH_FLOW,
            FactPeriodType.DURATION,
        ),
    ),
)


_WALMART_CIK = "0000104169"
_CATERPILLAR_CIK = "0000018230"

# Version 3 keeps every Version 2 rule and adds three narrowly scoped issuer
# policies. Each is justified by the issuer's own filings; any other synonym
# disagreement or unrecognized fact period still refuses ingestion.
SEC_CONCEPT_MAP_V3 = ConceptMap(
    version="sec-companyfacts-v3",
    rules=SEC_CONCEPT_MAP_V2.rules,
    issuer_tag_exclusions=(
        IssuerTagExclusion(
            cik=_WALMART_CIK,
            taxonomy="us-gaap",
            raw_tag="RevenueFromContractWithCustomerExcludingAssessedTax",
            canonical_concept="revenue",
            reason=(
                "Walmart tags net sales here; its reported total revenues, which add "
                "membership and other income, are tagged Revenues."
            ),
        ),
        IssuerTagExclusion(
            cik=_CATERPILLAR_CIK,
            taxonomy="us-gaap",
            raw_tag="CostOfGoodsAndServicesSold",
            canonical_concept="cost_of_revenue",
            reason=(
                "Caterpillar tags its total cost of goods sold as CostOfRevenue; this "
                "tag carries a separately disclosed component, not the total."
            ),
        ),
    ),
    opening_balance_exclusions=(
        OpeningBalanceExclusion(
            cik=_CATERPILLAR_CIK,
            taxonomy="us-gaap",
            raw_tag="StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            reason=(
                "Caterpillar's first-quarter equity statements date the opening "
                "balance January 1 rather than the prior December 31 period end."
            ),
        ),
    ),
)

SEC_CONCEPT_MAPS_BY_VERSION: Dict[str, ConceptMap] = {
    concept_map.version: concept_map
    for concept_map in (SEC_CONCEPT_MAP_V1, SEC_CONCEPT_MAP_V2, SEC_CONCEPT_MAP_V3)
}

# The mapping policy each issuer is ingested and read under. Apple stays on
# Version 2 because its published facts carry Version 2 lineage; moving it is a
# deliberate republication, not a side effect of adding another issuer.
SEC_ISSUER_CONCEPT_MAP_VERSIONS: Dict[str, str] = {
    "0000320193": SEC_CONCEPT_MAP_V2.version,
    "0000789019": SEC_CONCEPT_MAP_V3.version,
    _WALMART_CIK: SEC_CONCEPT_MAP_V3.version,
    _CATERPILLAR_CIK: SEC_CONCEPT_MAP_V3.version,
}


class ConceptMapUnavailable(LookupError):
    """No mapping policy has been assigned to the requested issuer."""


def concept_map_for_issuer(cik: str) -> ConceptMap:
    normalized_cik = normalize_cik(cik)
    version = SEC_ISSUER_CONCEPT_MAP_VERSIONS.get(normalized_cik)
    if version is None:
        raise ConceptMapUnavailable(
            f"No SEC concept-map policy is assigned to CIK {normalized_cik}."
        )
    return SEC_CONCEPT_MAPS_BY_VERSION[version]
