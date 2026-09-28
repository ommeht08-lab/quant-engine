"""One-period linked three-statement forecast from a fully itemized opening.

All money is USD millions. Noncash opening lines move only by explicit
assumptions. Cash is derived from the cash-flow statement, never plugged to
balance assets and liabilities. An unreconciled forecast refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Tuple

from .msft_opening_balance import MicrosoftOpening, SECTION_TAGS, STORED_CONCEPTS


class ForecastRefusal(ValueError):
    pass


_ASSETS = SECTION_TAGS["current_assets"] + SECTION_TAGS["noncurrent_assets"]
_LIABILITIES = SECTION_TAGS["current_liabilities"] + SECTION_TAGS["noncurrent_liabilities"]
_EQUITY = SECTION_TAGS["equity"]
_CASH = "CashAndCashEquivalentsAtCarryingValue"
_PPE = "PropertyPlantAndEquipmentNet"
_RETAINED = "RetainedEarningsAccumulatedDeficit"
_PAID = "CommonStocksIncludingAdditionalPaidInCapital"
_AOCI = "AccumulatedOtherComprehensiveIncomeLossNetOfTax"
_FINANCING_DEBT = ("CommercialPaper", "LongTermDebtCurrent", "LongTermDebtNoncurrent")
_INVESTING_ASSETS = (
    "ShortTermInvestments", "LongTermInvestments", "Goodwill", "FiniteLivedIntangibleAssetsNet",
    "OtherAssetsNoncurrent",
)
_OPERATING_ASSETS = tuple(tag for tag in _ASSETS if tag not in (_CASH, _PPE) + _INVESTING_ASSETS)
_OPERATING_LIABILITIES = tuple(tag for tag in _LIABILITIES if tag not in _FINANCING_DEBT)
_MOVABLE = tuple(tag for tag in _ASSETS + _LIABILITIES if tag not in (_CASH, _PPE)) + (_AOCI,)
# These balances can change through noncash recognition, amortization,
# remeasurement, or tax timing. A cash-flow schedule is required before a
# nonzero movement can be forecast without inventing a cash flow.
_REQUIRES_SCHEDULE = (
    "OtherAssetsCurrent", "OperatingLeaseRightOfUseAsset", "ShortTermInvestments",
    "LongTermInvestments", "Goodwill", "FiniteLivedIntangibleAssetsNet",
    "OtherAssetsNoncurrent", "AccruedIncomeTaxesCurrent", "AccruedIncomeTaxesNoncurrent",
    "DeferredIncomeTaxLiabilitiesNet", "OperatingLeaseLiabilityNoncurrent",
    "OtherLiabilitiesCurrent", "OtherLiabilitiesNoncurrent", _AOCI,
)


def _money(value, name):
    if isinstance(value, bool) or not isinstance(value, Decimal) or not value.is_finite():
        raise ForecastRefusal(f"{name} must be a finite Decimal in USD millions.")
    return value


@dataclass(frozen=True)
class ForecastYear:
    base_revenue: Decimal
    revenue_growth: Decimal
    operating_margin: Decimal
    tax_rate: Decimal
    capex: Decimal
    depreciation: Decimal
    dividends: Decimal
    equity_issuance: Decimal
    share_repurchases: Decimal
    debt_interest_rate: Decimal
    revolver_interest_rate: Decimal
    revolver_limit: Decimal
    minimum_cash: Decimal
    commitment_fee_rate: Decimal
    draw_fee_rate: Decimal
    movements: Mapping[str, Decimal]  # every noncash, non-PPE line, including debt and OCI

    def __post_init__(self):
        for name in (
            "base_revenue", "revenue_growth", "operating_margin", "tax_rate", "capex",
            "depreciation", "dividends", "equity_issuance", "share_repurchases",
            "debt_interest_rate", "revolver_interest_rate", "revolver_limit",
            "minimum_cash", "commitment_fee_rate", "draw_fee_rate",
        ):
            _money(getattr(self, name), name)
        if set(self.movements) != set(_MOVABLE):
            raise ForecastRefusal(f"Forecast must explicitly name every movable line; missing {sorted(set(_MOVABLE)-set(self.movements))}, extra {sorted(set(self.movements)-set(_MOVABLE))}.")
        for tag, value in self.movements.items():
            _money(value, tag)
        unsupported = [tag for tag in _REQUIRES_SCHEDULE if self.movements[tag] != 0]
        if unsupported:
            raise ForecastRefusal(f"Noncash or tax-timing movements need a reviewed schedule: {unsupported}.")
        if self.base_revenue <= 0 or self.revenue_growth < -1 or not 0 <= self.operating_margin <= 1:
            raise ForecastRefusal("Invalid revenue or operating assumptions.")
        for name in ("tax_rate", "debt_interest_rate", "revolver_interest_rate", "commitment_fee_rate", "draw_fee_rate"):
            if not 0 <= getattr(self, name) < 1:
                raise ForecastRefusal(f"{name} must be between zero and one.")
        for name in ("capex", "depreciation", "dividends", "equity_issuance", "share_repurchases", "revolver_limit", "minimum_cash"):
            if getattr(self, name) < 0:
                raise ForecastRefusal(f"{name} cannot be negative.")
        object.__setattr__(self, "movements", MappingProxyType(dict(self.movements)))


@dataclass(frozen=True)
class LinkedPeriod:
    income: Mapping[str, Decimal]
    balance: Mapping[str, Decimal]
    cash_flow: Mapping[str, Decimal]
    debt: Mapping[str, Decimal]
    unlevered_fcf: Decimal
    dcfsame_assumptions: Mapping[str, Decimal]

    def __post_init__(self):
        for name in ("income", "balance", "cash_flow", "debt", "dcfsame_assumptions"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


def validate_cash_linkage(period: LinkedPeriod, *, expected_opening_cash: Decimal | None = None) -> None:
    """Check the finished statement fields, including after reconstruction.

    This detects disagreement between the returned cash-flow statement and
    balance sheet. It does not prove that an assumed transaction is cash.
    """
    try:
        cf = period.cash_flow
        for name in ("opening_cash", "cfo", "cfi", "cff", "ending_cash"):
            _money(cf[name], f"cash_flow.{name}")
        cash = _money(period.balance[_CASH], "balance.cash")
    except KeyError as error:
        raise ForecastRefusal("Cash linkage requires all cash-flow and balance-sheet cash fields.") from error
    if expected_opening_cash is not None:
        _money(expected_opening_cash, "expected_opening_cash")
        if cf["opening_cash"] != expected_opening_cash:
            raise ForecastRefusal("Cash-flow opening cash differs from the preceding balance sheet.")
    rolled_cash = cf["opening_cash"] + cf["cfo"] + cf["cfi"] + cf["cff"]
    if rolled_cash != cf["ending_cash"] or cf["ending_cash"] != cash:
        raise ForecastRefusal("Cash flow does not reconcile to balance-sheet cash.")


def forecast_one_year(
    opening: MicrosoftOpening | LinkedPeriod, assumptions: ForecastYear
) -> LinkedPeriod:
    """Forecast one year with beginning-balance interest and fail-closed funding.

    Existing term debt and revolver interest use opening balances, so a draw
    this year affects next year's interest. Commitment fee uses opening unused
    capacity. Draw fees reduce taxable income and are paid in the draw year.
    Cash taxes are the tax expense, with a zero floor and no loss carryforward.
    """
    a = assumptions
    if isinstance(opening, MicrosoftOpening):
        if set(opening.stored_matches) != set(STORED_CONCEPTS) or not all(opening.stored_matches.values()):
            raise ForecastRefusal("The opening must pass its historical SEC store comparison first.")
        before = {tag: opening.value(tag) for tags in SECTION_TAGS.values() for tag in tags}
        opening_revolver = Decimal(0)
    elif isinstance(opening, LinkedPeriod):
        validate_cash_linkage(opening)
        before = {tag: opening.balance[tag] for tags in SECTION_TAGS.values() for tag in tags}
        opening_revolver = opening.balance["revolver"]
        if a.base_revenue != opening.income["revenue"]:
            raise ForecastRefusal("Next year base revenue must equal the preceding forecast revenue.")
    else:
        raise ForecastRefusal("Opening must be a verified filing or linked prior forecast.")
    _money(opening_revolver, "opening_revolver")
    if opening_revolver < 0 or opening_revolver > a.revolver_limit:
        raise ForecastRefusal("Opening revolver exceeds its authorized limit.")
    revenue = a.base_revenue * (1 + a.revenue_growth)
    ebit = revenue * a.operating_margin
    op_expenses = revenue - ebit
    if a.depreciation > op_expenses:
        raise ForecastRefusal("Depreciation exceeds total operating expense implied by the EBIT margin.")
    equity_cash = a.equity_issuance - a.share_repurchases
    ending_paid = before[_PAID] + equity_cash
    if ending_paid < 0:
        raise ForecastRefusal("Repurchases exceed available paid-in capital; an equity allocation schedule is required.")
    balance = dict(before)
    for tag, delta in a.movements.items():
        balance[tag] = before[tag] + delta
    balance[_PPE] = before[_PPE] + a.capex - a.depreciation
    if any(balance[tag] < 0 for tag in _ASSETS + _LIABILITIES):
        raise ForecastRefusal("A forecast asset or liability became negative.")
    existing_debt = sum((before[tag] for tag in _FINANCING_DEBT), Decimal(0))
    interest = existing_debt * a.debt_interest_rate + opening_revolver * a.revolver_interest_rate
    commitment_fee = (a.revolver_limit - opening_revolver) * a.commitment_fee_rate
    change_op_assets = sum((a.movements[tag] for tag in _OPERATING_ASSETS), Decimal(0))
    change_op_liab = sum((a.movements[tag] for tag in _OPERATING_LIABILITIES), Decimal(0))
    change_nwc = change_op_assets - change_op_liab
    investing = -a.capex - sum((a.movements[tag] for tag in _INVESTING_ASSETS), Decimal(0))
    debt_movement = sum((a.movements[tag] for tag in _FINANCING_DEBT), Decimal(0))
    ending_existing_debt = sum((balance[tag] for tag in _FINANCING_DEBT), Decimal(0))
    def at_draw(draw: Decimal) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        fee = draw * a.draw_fee_rate
        pretax = ebit - interest - commitment_fee - fee
        taxes = max(pretax, Decimal(0)) * a.tax_rate
        net_income = pretax - taxes
        cfo = net_income + a.depreciation - change_nwc
        cff = debt_movement + equity_cash - a.dividends + draw
        ending_cash = before[_CASH] + cfo + investing + cff
        return ending_cash, taxes, net_income, fee

    no_draw_cash, _, _, _ = at_draw(Decimal(0))
    available = a.revolver_limit - opening_revolver
    draw = Decimal(0)
    repay = Decimal(0)
    if no_draw_cash < a.minimum_cash:
        max_cash, _, _, _ = at_draw(available)
        if max_cash < a.minimum_cash:
            raise ForecastRefusal(f"Funding shortfall: maximum cash {max_cash} is below minimum {a.minimum_cash}.")
        lower, upper = Decimal(0), available
        for _ in range(180):
            middle = (lower + upper) / 2
            if at_draw(middle)[0] >= a.minimum_cash:
                upper = middle
            else:
                lower = middle
        draw = upper
    elif opening_revolver > 0:
        repay = min(opening_revolver, no_draw_cash - a.minimum_cash)
    ending_cash, taxes, net_income, draw_fee = at_draw(draw)
    ending_cash -= repay
    balance[_CASH] = ending_cash
    balance[_PAID] = ending_paid
    balance[_RETAINED] = before[_RETAINED] + net_income - a.dividends
    if ending_cash < a.minimum_cash - Decimal("0.000000001"):
        raise ForecastRefusal("Revolver failed to preserve minimum cash.")
    assets = sum((balance[tag] for tag in _ASSETS), Decimal(0))
    liabilities = sum((balance[tag] for tag in _LIABILITIES), Decimal(0)) + opening_revolver + draw - repay
    equity = sum((balance[tag] for tag in _EQUITY), Decimal(0))
    if abs(assets - liabilities - equity) > Decimal("0.000000001"):
        raise ForecastRefusal(f"Linked balance sheet does not reconcile: assets {assets}, liabilities {liabilities}, equity {equity}.")
    cfo = net_income + a.depreciation - change_nwc
    cff = debt_movement + equity_cash - a.dividends + draw - repay
    unlevered = ebit * (1 - a.tax_rate) + a.depreciation - a.capex - change_nwc
    result = LinkedPeriod(
        income={"revenue": revenue, "operating_expense": op_expenses, "ebit": ebit, "interest": interest,
                "commitment_fee": commitment_fee, "draw_fee": draw_fee, "taxes": taxes, "net_income": net_income},
        balance={**balance, "revolver": opening_revolver + draw - repay,
                 "total_assets": assets, "total_liabilities": liabilities, "total_equity": equity},
        cash_flow={"cfo": cfo, "cfi": investing, "cff": cff, "opening_cash": before[_CASH], "ending_cash": ending_cash,
                   "depreciation_addback": a.depreciation, "change_in_operating_nwc": change_nwc,
                   "capex": a.capex, "dividends": a.dividends},
        debt={"opening_existing_debt": existing_debt, "existing_debt_movement": debt_movement,
              "ending_existing_debt": ending_existing_debt,
              "opening_total_debt": existing_debt + opening_revolver,
              "ending_total_debt": ending_existing_debt + opening_revolver + draw - repay,
              "opening_revolver": opening_revolver, "draw": draw, "repayment": repay,
              "ending_revolver": opening_revolver + draw - repay, "capacity": a.revolver_limit,
              "interest_on_opening_balances": interest, "commitment_fee": commitment_fee, "draw_fee": draw_fee},
        unlevered_fcf=unlevered,
        dcfsame_assumptions={"revenue": revenue, "ebit": ebit, "tax_rate": a.tax_rate,
                             "da": a.depreciation, "capex": a.capex, "change_in_nwc": change_nwc},
    )
    validate_cash_linkage(result, expected_opening_cash=before[_CASH])
    return result


def zero_movements() -> dict[str, Decimal]:
    """Explicit zero forecast assumption for each noncash movable line."""
    return {tag: Decimal(0) for tag in _MOVABLE}
