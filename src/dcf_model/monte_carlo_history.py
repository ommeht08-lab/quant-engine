"""
Historical centers and spreads for the Monte Carlo DCF.

Alignment (all from the one income statement already in the snapshot):

* Growth center = the uncapped Revenue CAGR from
  `calculate_historical_revenue_cagr`. Spread = the sample standard deviation
  (ddof=1) of consecutive year-over-year revenue growth rates, taken over the
  SAME revenue periods that CAGR uses (`Total Revenue` after dropping missing
  values, sorted by date).
* Margin center = `calculate_historical_average_operating_margin`, spread = the
  sample standard deviation of the same per-period margins it averages.

A spread is never invented: too little history, irregular fiscal spacing or a
misaligned period set is a typed refusal. With only a handful of annual
observations the sample standard deviation is a noisy estimate; callers
disclose that.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from src.dcf_model.dcf import (
    _get_row_value,  # same-package helper: reused so margin selection cannot drift
    calculate_historical_average_operating_margin,
    calculate_historical_revenue_cagr,
)
from src.dcf_model.monte_carlo_config import MonteCarloConfig, MonteCarloRefusal

_REVENUE_LABELS = ("Total Revenue", "TotalRevenue")


@dataclass(frozen=True)
class HistoricalSpreads:
    revenue_periods: List[str]
    revenues: List[float]
    yoy_growths: List[float]
    growth_center_cagr: float
    growth_sd: float
    margin_periods: List[str]
    margins: List[float]
    margin_center: float
    margin_sd: float
    n_growth: int
    n_margin: int

    def to_dict(self) -> dict:
        return {
            "revenue_periods": list(self.revenue_periods),
            "revenues": list(self.revenues),
            "yoy_growths": list(self.yoy_growths),
            "growth_center_cagr": self.growth_center_cagr,
            "growth_sd": self.growth_sd,
            "margin_periods": list(self.margin_periods),
            "margins": list(self.margins),
            "margin_center": self.margin_center,
            "margin_sd": self.margin_sd,
            "n_growth": self.n_growth,
            "n_margin": self.n_margin,
            "sd_convention": "sample_ddof1",
        }


def _period_label(period) -> str:
    return pd.Timestamp(period).date().isoformat()


def _sample_sd(values: List[float]) -> float:
    return float(np.std(np.asarray(values, dtype=np.float64), ddof=1))


def extract_historical_spreads(
    income_stmt: Optional[pd.DataFrame], config: MonteCarloConfig
) -> HistoricalSpreads:
    """Return centers and sample spreads, or raise `MonteCarloRefusal`."""
    if income_stmt is None or income_stmt.empty:
        raise MonteCarloRefusal("input_incomplete", "No income statement is available.")

    revenue_label = next((name for name in _REVENUE_LABELS if name in income_stmt.index), None)
    if revenue_label is None:
        raise MonteCarloRefusal("input_incomplete", "The income statement has no Total Revenue row.")

    revenue_by_period = income_stmt.loc[revenue_label].dropna()
    try:
        ordered = sorted(revenue_by_period.index, key=pd.Timestamp)
    except (TypeError, ValueError):
        raise MonteCarloRefusal(
            "history_alignment_mismatch", "Statement period labels are not valid dates."
        ) from None

    min_periods = config.min_growth_observations + 1
    if len(ordered) < min_periods:
        raise MonteCarloRefusal(
            "insufficient_history",
            f"{len(ordered)} revenue period(s) available; at least {min_periods} are needed "
            f"for {config.min_growth_observations} year-over-year growth observations.",
        )

    revenues = [float(revenue_by_period[period]) for period in ordered]
    if any(not math.isfinite(value) or value <= 0 for value in revenues):
        raise MonteCarloRefusal(
            "nonpositive_revenue", "Growth is undefined because a historical revenue is not positive."
        )

    timestamps = [pd.Timestamp(period) for period in ordered]
    for earlier, later in zip(timestamps, timestamps[1:]):
        gap_days = (later - earlier).days
        if not config.min_fiscal_gap_days <= gap_days <= config.max_fiscal_gap_days:
            raise MonteCarloRefusal(
                "irregular_fiscal_spacing",
                f"Consecutive revenue periods are {gap_days} days apart; annual spacing of "
                f"{config.min_fiscal_gap_days}-{config.max_fiscal_gap_days} days is required.",
            )

    yoy_growths = [later / earlier - 1.0 for earlier, later in zip(revenues, revenues[1:])]
    growth_center = calculate_historical_revenue_cagr(income_stmt)
    if growth_center is None:
        raise MonteCarloRefusal(
            "history_alignment_mismatch", "The deterministic model could not derive a revenue CAGR."
        )

    # Margins: exactly the columns the deterministic average uses, in column order.
    margin_periods: List[str] = []
    margins: List[float] = []
    for column in income_stmt.columns:
        revenue = _get_row_value(income_stmt, list(_REVENUE_LABELS), column=column)
        ebit = _get_row_value(income_stmt, ["EBIT", "Ebit"], column=column)
        if ebit is None:
            ebit = _get_row_value(income_stmt, ["Operating Income", "OperatingIncome"], column=column)
        if revenue is None or revenue <= 0 or ebit is None:
            continue
        margins.append(ebit / revenue)
        margin_periods.append(_period_label(column))

    if len(margins) < config.min_margin_observations:
        raise MonteCarloRefusal(
            "insufficient_history",
            f"{len(margins)} operating-margin observation(s) available; at least "
            f"{config.min_margin_observations} are needed.",
        )

    revenue_period_labels = [_period_label(period) for period in ordered]
    if not set(margin_periods) <= set(revenue_period_labels):
        raise MonteCarloRefusal(
            "history_alignment_mismatch",
            "Operating-margin periods are not a subset of the revenue periods used for growth.",
        )

    margin_center = calculate_historical_average_operating_margin(income_stmt)
    if margin_center is None or margin_center != sum(margins) / len(margins):
        raise MonteCarloRefusal(
            "history_alignment_mismatch",
            "Margin observations do not reproduce the deterministic model's historical average.",
        )

    return HistoricalSpreads(
        revenue_periods=revenue_period_labels,
        revenues=revenues,
        yoy_growths=yoy_growths,
        growth_center_cagr=float(growth_center),
        growth_sd=_sample_sd(yoy_growths),
        margin_periods=margin_periods,
        margins=margins,
        margin_center=float(margin_center),
        margin_sd=_sample_sd(margins),
        n_growth=len(yoy_growths),
        n_margin=len(margins),
    )
