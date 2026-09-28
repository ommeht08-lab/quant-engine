"""Linked forecast invariants and revolver financing from a filed opening."""

from dataclasses import replace
from decimal import Decimal
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.dcf_model.dcf import ForecastYearAssumptions, project_free_cash_flows_from_path
from src.fundamentals import msft_opening_balance as source
from src.fundamentals.linked_forecast import (
    ForecastRefusal, ForecastYear, forecast_one_year, validate_cash_linkage, zero_movements,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/fundamentals/msft-2024-opening-balance.xhtml"
D = lambda value: Decimal(str(value))


def _opening(monkeypatch):
    body = FIXTURE.read_bytes()
    monkeypatch.setattr(source, "DOCUMENT_SHA256", hashlib.sha256(body).hexdigest())
    filed = source.read_microsoft_opening(body)
    stored_lines = {
        concept: SimpleNamespace(value=filed.value(tag) * D(1_000_000))
        for concept, tag in source.STORED_CONCEPTS.items()
    }
    stored = SimpleNamespace(
        request=SimpleNamespace(cik=source.MSFT_CIK, period_end=source.PERIOD_END),
        line=lambda concept: stored_lines.get(concept),
    )
    return source.read_microsoft_opening(body, stored_snapshot=stored)


def _assumptions(**overrides):
    values = dict(
        base_revenue=D(1000), revenue_growth=D(0), operating_margin=D(0), tax_rate=D("0.25"),
        capex=D(0), depreciation=D(0), dividends=D(0), equity_issuance=D(0), share_repurchases=D(0),
        debt_interest_rate=D(0), revolver_interest_rate=D(0), revolver_limit=D(0), minimum_cash=D(0),
        commitment_fee_rate=D(0), draw_fee_rate=D(0), movements=zero_movements(),
    )
    values.update(overrides)
    return ForecastYear(**values)


def test_linked_statements_and_existing_unlevered_dcf_agree_under_equivalent_assumptions(monkeypatch):
    opening = _opening(monkeypatch)
    movements = zero_movements()
    movements["AccountsReceivableNetCurrent"] = D(10)
    assumptions = _assumptions(
        revenue_growth=D("0.10"), operating_margin=D("0.20"),
        capex=D(77), depreciation=D(55), movements=movements,
    )
    result = forecast_one_year(opening, assumptions)
    assert result.income["revenue"] == D(1100)
    assert result.income["ebit"] == D(220)
    assert result.income["taxes"] == D(55)
    assert result.income["net_income"] == D(165)
    assert result.cash_flow["cfo"] == D(210)
    assert result.cash_flow["cfi"] == D(-77)
    assert result.cash_flow["cff"] == D(0)
    assert result.cash_flow["ending_cash"] == opening.value("CashAndCashEquivalentsAtCarryingValue") + D(133)
    assert result.balance["total_assets"] == result.balance["total_liabilities"] + result.balance["total_equity"]
    assert result.debt["opening_total_debt"] == result.debt["ending_total_debt"]
    assert result.unlevered_fcf == D(133)
    dcf = project_free_cash_flows_from_path(
        1000.0, (ForecastYearAssumptions(1, "test", 0.1, 0.2),), tax_rate=0.25,
        da_pct_revenue=0.05, capex_pct_revenue=0.07, nwc_pct_revenue_change=0.10,
    )
    assert float(result.unlevered_fcf) == pytest.approx(dcf.loc[1, "fcf"])


def test_revolver_draw_fees_interest_and_next_year_repayment(monkeypatch):
    opening = _opening(monkeypatch)
    first = forecast_one_year(opening, _assumptions(
        revolver_limit=D(5000), minimum_cash=D(20000),
        commitment_fee_rate=D("0.01"), draw_fee_rate=D("0.02"),
    ))
    assert first.debt["draw"] > 0 and first.debt["repayment"] == 0
    assert first.cash_flow["ending_cash"] == pytest.approx(D(20000))
    assert first.income["interest"] == 0  # draws accrue next year
    assert first.income["commitment_fee"] == D(50)
    assert first.income["draw_fee"] == first.debt["draw"] * D("0.02")
    assert first.balance["total_assets"] == pytest.approx(first.balance["total_liabilities"] + first.balance["total_equity"])
    second = forecast_one_year(first, _assumptions(
        operating_margin=D("0.5"), revolver_limit=D(5000), minimum_cash=D(20000),
        revolver_interest_rate=D("0.01"), commitment_fee_rate=D("0.01"),
    ))
    assert second.income["interest"] == first.debt["ending_revolver"] * D("0.01")
    assert second.debt["draw"] == 0 and second.debt["repayment"] > 0
    assert second.debt["ending_revolver"] == first.debt["ending_revolver"] - second.debt["repayment"]
    assert second.debt["ending_total_debt"] == second.debt["ending_existing_debt"] + second.debt["ending_revolver"]
    assert second.cash_flow["ending_cash"] == pytest.approx(D(20000))
    assert second.balance["total_assets"] == pytest.approx(second.balance["total_liabilities"] + second.balance["total_equity"])


def test_funding_shortfall_refuses_without_a_cash_plug(monkeypatch):
    with pytest.raises(ForecastRefusal, match="Funding shortfall"):
        forecast_one_year(_opening(monkeypatch), _assumptions(revolver_limit=D(100), minimum_cash=D(20000)))


def test_every_movement_must_be_explicit_and_unmodeled_oci_refuses():
    movements = zero_movements()
    movements.pop("Goodwill")
    with pytest.raises(ForecastRefusal, match="explicitly name every movable line"):
        _assumptions(movements=movements)
    movements = zero_movements()
    movements["AccumulatedOtherComprehensiveIncomeLossNetOfTax"] = D(1)
    with pytest.raises(ForecastRefusal, match="reviewed schedule"):
        _assumptions(movements=movements)


@pytest.mark.parametrize("tag", (
    "Goodwill", "OperatingLeaseRightOfUseAsset", "OperatingLeaseLiabilityNoncurrent",
    "DeferredIncomeTaxLiabilitiesNet", "AccruedIncomeTaxesCurrent",
    "AccruedIncomeTaxesNoncurrent", "FiniteLivedIntangibleAssetsNet",
    "ShortTermInvestments", "LongTermInvestments", "OtherAssetsCurrent",
    "OtherAssetsNoncurrent", "OtherLiabilitiesCurrent", "OtherLiabilitiesNoncurrent",
))
def test_non_cash_or_tax_timing_movement_refuses_before_it_can_change_cash(tag):
    movements = zero_movements()
    movements[tag] = D(100)
    with pytest.raises(ForecastRefusal, match="reviewed schedule"):
        _assumptions(movements=movements)


def test_forecast_assumptions_and_linked_outputs_cannot_change_after_validation(monkeypatch):
    movements = zero_movements()
    assumptions = _assumptions(movements=movements)
    movements.pop("Goodwill")
    movements["AccountsReceivableNetCurrent"] = float("nan")
    assert assumptions.movements["AccountsReceivableNetCurrent"] == D(0)
    with pytest.raises(TypeError):
        assumptions.movements["Goodwill"] = D(100)
    result = forecast_one_year(_opening(monkeypatch), assumptions)
    with pytest.raises(TypeError):
        result.balance["revolver"] = D(999)


def test_existing_debt_rolls_forward_and_interest_uses_opening_balance(monkeypatch):
    opening = _opening(monkeypatch)
    movements = zero_movements()
    movements["LongTermDebtCurrent"] = D(-100)
    movements["LongTermDebtNoncurrent"] = D(150)
    period = forecast_one_year(opening, _assumptions(
        movements=movements, debt_interest_rate=D("0.05"),
    ))
    opening_debt = sum(opening.value(tag) for tag in (
        "CommercialPaper", "LongTermDebtCurrent", "LongTermDebtNoncurrent",
    ))
    assert period.debt["opening_existing_debt"] == opening_debt
    assert period.debt["existing_debt_movement"] == D(50)
    assert period.debt["ending_existing_debt"] == opening_debt + D(50)
    assert period.income["interest"] == opening_debt * D("0.05")
    assert period.cash_flow["cff"] == D(50)
    assert period.balance["total_assets"] == period.balance["total_liabilities"] + period.balance["total_equity"]


def test_source_only_opening_cannot_start_a_forecast(monkeypatch):
    body = FIXTURE.read_bytes()
    monkeypatch.setattr(source, "DOCUMENT_SHA256", hashlib.sha256(body).hexdigest())
    filed = source.read_microsoft_opening(body)
    with pytest.raises(ForecastRefusal, match="historical SEC store comparison"):
        forecast_one_year(filed, _assumptions())


def test_depreciation_cannot_imply_negative_cash_operating_expense(monkeypatch):
    with pytest.raises(ForecastRefusal, match="Depreciation exceeds total operating expense"):
        forecast_one_year(_opening(monkeypatch), _assumptions(
            operating_margin=D("0.9"), depreciation=D(500),
        ))


def test_depreciation_diagnostic_precedes_negative_ppe_refusal(monkeypatch):
    opening = _opening(monkeypatch)
    with pytest.raises(ForecastRefusal, match="Depreciation exceeds total operating expense"):
        forecast_one_year(opening, _assumptions(
            depreciation=opening.value("PropertyPlantAndEquipmentNet") + D(1),
        ))


def test_smaller_next_year_limit_refuses_existing_revolver_even_if_cash_can_repay(monkeypatch):
    first = forecast_one_year(_opening(monkeypatch), _assumptions(
        revolver_limit=D(5000), minimum_cash=D(20000),
    ))
    assert first.debt["ending_revolver"] > 0
    with pytest.raises(ForecastRefusal, match="Opening revolver exceeds its authorized limit"):
        forecast_one_year(first, _assumptions(
            operating_margin=D("0.9"), minimum_cash=D(20000),
            revolver_limit=first.debt["ending_revolver"] - D(1),
        ))


@pytest.mark.parametrize("field", ("cfo", "cff", "ending_cash"))
def test_cash_linkage_rejects_corrupt_cash_flow_fields(monkeypatch, field):
    opening = _opening(monkeypatch)
    result = forecast_one_year(opening, _assumptions())
    corrupted = replace(result, cash_flow={**result.cash_flow, field: result.cash_flow[field] + D(1)})
    with pytest.raises(ForecastRefusal, match="Cash flow does not reconcile"):
        validate_cash_linkage(corrupted, expected_opening_cash=opening.value("CashAndCashEquivalentsAtCarryingValue"))


def test_cash_linkage_rejects_balance_sheet_cash_and_wrong_opening_cash(monkeypatch):
    opening = _opening(monkeypatch)
    result = forecast_one_year(opening, _assumptions())
    cash_tag = "CashAndCashEquivalentsAtCarryingValue"
    corrupted = replace(result, balance={**result.balance, cash_tag: result.balance[cash_tag] + D(1)})
    with pytest.raises(ForecastRefusal, match="Cash flow does not reconcile"):
        validate_cash_linkage(corrupted)
    with pytest.raises(ForecastRefusal, match="opening cash differs"):
        validate_cash_linkage(result, expected_opening_cash=opening.value(cash_tag) + D(1))
    with pytest.raises(ForecastRefusal, match="Cash flow does not reconcile"):
        forecast_one_year(corrupted, _assumptions())


def test_repurchases_refuse_unsupported_negative_paid_in_capital(monkeypatch):
    opening = _opening(monkeypatch)
    paid = opening.value("CommonStocksIncludingAdditionalPaidInCapital")
    with pytest.raises(ForecastRefusal, match="equity allocation schedule"):
        forecast_one_year(opening, _assumptions(share_repurchases=paid + D(1)))
    boundary = forecast_one_year(opening, _assumptions(
        base_revenue=paid * D(4), operating_margin=D(1), share_repurchases=paid,
    ))
    assert boundary.balance["CommonStocksIncludingAdditionalPaidInCapital"] == 0
    assert boundary.balance["total_assets"] == boundary.balance["total_liabilities"] + boundary.balance["total_equity"]
