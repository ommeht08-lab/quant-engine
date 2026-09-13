"""Interpretation checks for an already-computed DCF, never a second valuation.

The caller retains every numeric result. This module only decides whether
market-relative presentation is responsible for the evidence at hand.
"""

from dataclasses import dataclass
from typing import Literal, Optional, Tuple

from src.dcf_model.scenarios import ScenarioSet

QualityCode = Literal[
    "nonpositive_terminal_fcf", "nonpositive_enterprise_value",
    "reversed_scenario_values", "extreme_observed_tax_rate",
    "high_terminal_value_concentration",
]
QualityLevel = Literal["ordinary", "caution", "diagnostic_only"]

# Review triggers, not corrections to any financial input or DCF formula.
EXTREME_OBSERVED_TAX_RATE = 0.60
HIGH_TERMINAL_VALUE_SHARE = 0.80


@dataclass(frozen=True)
class ValuationQuality:
    level: QualityLevel
    codes: Tuple[QualityCode, ...]
    allows_market_comparison: bool
    terminal_value_share_of_enterprise_value: Optional[float]
    observed_effective_tax_rate: Optional[float]


def assess_valuation_quality(
    *, terminal_fcf: float, enterprise_value: float, pv_terminal_value: float,
    tax_rate: float, tax_rate_source: str, scenarios: ScenarioSet,
) -> ValuationQuality:
    """Classify interpretation using completed values, without changing them.

    A terminal-value share is meaningful only for positive EV and nonnegative
    PV terminal value. Uncomputable scenarios do not imply reversed ordering.
    """
    codes: list[QualityCode] = []
    if terminal_fcf <= 0:
        codes.append("nonpositive_terminal_fcf")
    if enterprise_value <= 0:
        codes.append("nonpositive_enterprise_value")

    values = tuple(case.intrinsic_value_per_share for case in (
        scenarios.bear, scenarios.base, scenarios.bull
    ))
    if all(value is not None for value in values) and not (values[0] <= values[1] <= values[2]):
        codes.append("reversed_scenario_values")

    observed_tax = tax_rate if tax_rate_source == "historical" else None
    if observed_tax is not None and observed_tax >= EXTREME_OBSERVED_TAX_RATE:
        codes.append("extreme_observed_tax_rate")

    terminal_share = (
        pv_terminal_value / enterprise_value
        if enterprise_value > 0 and pv_terminal_value >= 0 else None
    )
    if terminal_share is not None and terminal_share >= HIGH_TERMINAL_VALUE_SHARE:
        codes.append("high_terminal_value_concentration")

    serious = {"nonpositive_terminal_fcf", "nonpositive_enterprise_value", "reversed_scenario_values"}
    level: QualityLevel = (
        "diagnostic_only" if any(code in serious for code in codes)
        else "caution" if codes else "ordinary"
    )
    return ValuationQuality(
        level=level, codes=tuple(codes), allows_market_comparison=not codes,
        terminal_value_share_of_enterprise_value=terminal_share,
        observed_effective_tax_rate=observed_tax,
    )
