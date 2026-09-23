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
class BalanceCompositionRule:
    """One issuer's consolidated balance line composed from axis members."""

    cik: str
    taxonomy: str
    raw_tag: str
    canonical_concept: str
    axis: str
    member_sets: Tuple[FrozenSet[str], ...]
    reason: str
    # (duplicate member, counterpart it must exactly equal); never summed.
    equivalent_members: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        for name in ("taxonomy", "raw_tag", "canonical_concept", "axis", "reason"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"BalanceCompositionRule.{name} must be non-empty text.")
        sets = tuple(frozenset(members) for members in self.member_sets)
        if not sets or any(len(members) < 2 for members in sets):
            raise ValueError("Each declared member set must list at least two members.")
        if len(set(sets)) != len(sets):
            raise ValueError("Declared member sets must be distinct.")
        object.__setattr__(self, "member_sets", sets)
        equivalents = tuple(tuple(pair) for pair in self.equivalent_members)
        members = frozenset().union(*sets)
        for duplicate, counterpart in equivalents:
            if duplicate in members or counterpart not in members:
                raise ValueError("An equivalent member must mirror a declared member and not be one.")
        object.__setattr__(self, "equivalent_members", equivalents)



@dataclass(frozen=True)
class DerivedFlowRule:
    """One issuer's flow concept derived by an exact accounting identity.

    ``value = minuend - subtrahend`` using both raw facts from the same
    filing, period, and unit. Every ``cross_checks`` tag reported for that
    filing and period must equal the derived value, or extraction refuses.
    """

    cik: str
    canonical_concept: str
    minuend: Tuple[str, str]
    subtrahend: Tuple[str, str]
    cross_checks: Tuple[Tuple[str, str], ...]
    identity: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        for name in ("canonical_concept", "identity", "reason"):
            _require_text("DerivedFlowRule", name, getattr(self, name))
        tags = [tuple(self.minuend), tuple(self.subtrahend)] + [tuple(tag) for tag in self.cross_checks]
        if any(len(tag) != 2 or not all(isinstance(part, str) and part.strip() for part in tag) for tag in tags):
            raise ValueError("DerivedFlowRule tags must be (taxonomy, raw_tag) pairs.")
        if len(set(tags)) != len(tags):
            raise ValueError("DerivedFlowRule tags must be distinct.")
        if not self.cross_checks:
            raise ValueError("A derived flow requires at least one reported cross-check.")
        object.__setattr__(self, "minuend", tuple(self.minuend))
        object.__setattr__(self, "subtrahend", tuple(self.subtrahend))
        object.__setattr__(self, "cross_checks", tuple(tuple(tag) for tag in self.cross_checks))

    @property
    def raw_tag(self) -> str:
        return f"{self.minuend[1]}-{self.subtrahend[1]}"


@dataclass(frozen=True)
class ConceptMap:
    version: str
    rules: Tuple[ConceptRule, ...]
    issuer_tag_exclusions: Tuple[IssuerTagExclusion, ...] = ()
    opening_balance_exclusions: Tuple[OpeningBalanceExclusion, ...] = ()
    # Consolidated balances composed from a filing's own XBRL instance (see
    # adapters.sec_filing_xbrl); Company Facts omits dimensional facts.
    balance_compositions: Tuple["BalanceCompositionRule", ...] = ()
    derived_flows: Tuple["DerivedFlowRule", ...] = ()

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

        try:
            compositions = tuple(self.balance_compositions)
        except TypeError:
            raise ValueError("balance_compositions must be a collection.") from None
        concepts_by_name = {rule.canonical_concept: rule for rule in rules}
        seen_compositions = set()
        for composition in compositions:
            if not isinstance(composition, BalanceCompositionRule):
                raise ValueError("balance_compositions must contain BalanceCompositionRule values.")
            target = concepts_by_name.get(composition.canonical_concept)
            if target is None or target.statement_kind is not StatementKind.BALANCE_SHEET:
                raise ValueError(
                    "A balance composition must target an existing balance-sheet concept: "
                    f"{composition.canonical_concept!r}."
                )
            key = (composition.cik, composition.canonical_concept)
            if key in seen_compositions:
                raise ValueError("Only one composition per issuer and concept is allowed.")
            seen_compositions.add(key)
        object.__setattr__(self, "balance_compositions", compositions)

        try:
            derived = tuple(self.derived_flows)
        except TypeError:
            raise ValueError("derived_flows must be a collection.") from None
        seen_derived = set()
        for flow in derived:
            if not isinstance(flow, DerivedFlowRule):
                raise ValueError("derived_flows must contain DerivedFlowRule values.")
            target = concepts_by_name.get(flow.canonical_concept)
            if target is None or target.period_type is not FactPeriodType.DURATION:
                raise ValueError(
                    f"A derived flow must target an existing duration concept: {flow.canonical_concept!r}."
                )
            if (flow.cik, flow.canonical_concept) in seen_derived:
                raise ValueError("Only one derived flow per issuer and concept is allowed.")
            seen_derived.add((flow.cik, flow.canonical_concept))
            if flow.minuend in rules_by_key or flow.subtrahend in rules_by_key:
                raise ValueError("Derived-flow components must not also be mapped tags.")
        object.__setattr__(self, "derived_flows", derived)

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

    def derived_flows_for(self, cik: str) -> Tuple["DerivedFlowRule", ...]:
        normalized_cik = normalize_cik(cik)
        return tuple(flow for flow in self.derived_flows if flow.cik == normalized_cik)

    def balance_compositions_for(self, cik: str) -> Tuple["BalanceCompositionRule", ...]:
        normalized_cik = normalize_cik(cik)
        return tuple(rule for rule in self.balance_compositions if rule.cik == normalized_cik)

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

_CATERPILLAR_PRODUCT_MEMBER_SETS = (
    # Machinery, Energy & Transportation + Financial Products (through FY2025 Q3).
    frozenset({"cat:MachineryEnergyTransportationMember", "cat:FinancialProductsMember"}),
    # The same two lines after the FY2025 10-K renamed ME&T.
    frozenset({"cat:MachineryPowerEnergyMember", "cat:FinancialProductsMember"}),
)
_CATERPILLAR_EQUIVALENT_MEMBERS = (
    # 10-K segment-note facts mirror Financial Products on the same axis.
    ("cat:FinancialProductsSegmentMember", "cat:FinancialProductsMember"),
)

# Version 4 keeps every Version 3 policy and adds Caterpillar's term debt,
# which its filings tag only as Machinery, Energy & Transportation plus
# Financial Products members of srt:ProductOrServiceAxis. The two members are
# the complete consolidated line: they reconcile exactly to reported total
# current liabilities and total liabilities, and at every year-end to the
# filing's own consolidated LongTermDebtNoncurrent.
SEC_CONCEPT_MAP_V4 = ConceptMap(
    version="sec-companyfacts-v4",
    rules=SEC_CONCEPT_MAP_V3.rules,
    issuer_tag_exclusions=SEC_CONCEPT_MAP_V3.issuer_tag_exclusions,
    opening_balance_exclusions=SEC_CONCEPT_MAP_V3.opening_balance_exclusions,
    balance_compositions=(
        BalanceCompositionRule(
            cik=_CATERPILLAR_CIK,
            taxonomy="us-gaap",
            raw_tag="LongTermDebtAndCapitalLeaseObligationsCurrent",
            canonical_concept="current_debt",
            axis="srt:ProductOrServiceAxis",
            member_sets=_CATERPILLAR_PRODUCT_MEMBER_SETS,
            equivalent_members=_CATERPILLAR_EQUIVALENT_MEMBERS,
            reason="Consolidated 'Long-term debt due within one year' is tagged only by line of business.",
        ),
        BalanceCompositionRule(
            cik=_CATERPILLAR_CIK,
            taxonomy="us-gaap",
            raw_tag="LongTermDebtAndCapitalLeaseObligations",
            canonical_concept="long_term_debt",
            axis="srt:ProductOrServiceAxis",
            member_sets=_CATERPILLAR_PRODUCT_MEMBER_SETS,
            equivalent_members=_CATERPILLAR_EQUIVALENT_MEMBERS,
            reason="Consolidated 'Long-term debt due after one year' is tagged only by line of business.",
        ),
    ),
)

# Version 5 keeps every Version 4 policy and derives Caterpillar's net income
# attributable to the parent (the meaning of us-gaap:NetIncomeLoss, which CAT
# never tags in its 10-K/10-Q filings) by the ASC 810 identity: consolidated
# profit less the noncontrolling interest's share. Profit attributable to
# common shareholders must equal it wherever reported, which also refuses if
# preferred dividends or participating-security adjustments ever appear.
SEC_CONCEPT_MAP_V5 = ConceptMap(
    version="sec-companyfacts-v5",
    rules=SEC_CONCEPT_MAP_V4.rules,
    issuer_tag_exclusions=SEC_CONCEPT_MAP_V4.issuer_tag_exclusions,
    opening_balance_exclusions=SEC_CONCEPT_MAP_V4.opening_balance_exclusions,
    balance_compositions=SEC_CONCEPT_MAP_V4.balance_compositions,
    derived_flows=(
        DerivedFlowRule(
            cik=_CATERPILLAR_CIK,
            canonical_concept="net_income",
            minuend=("us-gaap", "ProfitLoss"),
            subtrahend=("us-gaap", "NetIncomeLossAttributableToNoncontrollingInterest"),
            cross_checks=(("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic"),),
            identity="NetIncomeLoss = ProfitLoss - NetIncomeLossAttributableToNoncontrollingInterest",
            reason="Caterpillar reports consolidated profit and the NCI share but not NetIncomeLoss.",
        ),
    ),
)

SEC_CONCEPT_MAPS_BY_VERSION: Dict[str, ConceptMap] = {
    concept_map.version: concept_map
    for concept_map in (
        SEC_CONCEPT_MAP_V1,
        SEC_CONCEPT_MAP_V2,
        SEC_CONCEPT_MAP_V3,
        SEC_CONCEPT_MAP_V4,
        SEC_CONCEPT_MAP_V5,
    )
}

# The mapping policy each issuer is ingested and read under. Apple stays on
# Version 2 because its published facts carry Version 2 lineage; moving it is a
# deliberate republication, not a side effect of adding another issuer.
SEC_ISSUER_CONCEPT_MAP_VERSIONS: Dict[str, str] = {
    "0000320193": SEC_CONCEPT_MAP_V2.version,
    "0000789019": SEC_CONCEPT_MAP_V3.version,
    _WALMART_CIK: SEC_CONCEPT_MAP_V3.version,
    # Version 5 requires a re-backfill before Caterpillar is SEC-history ready.
    _CATERPILLAR_CIK: SEC_CONCEPT_MAP_V5.version,
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
