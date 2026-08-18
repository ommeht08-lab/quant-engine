"""
Phase 5 tolerance math -- pure functions, independently testable.

Two tolerance families, per docs/independent-validation-plan.md:
  - "absolute_pp": rate-like metrics (Revenue CAGR, Operating Margin, WACC)
    compared by RAW percentage-point difference, never relative percentage
    (a 0.01 vs 0.02 WACC is a 1pp difference, not a "100% relative" one).
  - "relative_pct": monetary metrics, compared by
    relative_difference = |codebase - workbook| / |workbook|, with a
    documented near-zero carve-out: if the workbook value is EXACTLY zero,
    relative difference is undefined (division by zero), so this requires
    an exact match up to a small, explicitly stated absolute tolerance for
    floating-point noise instead of a relative one.
"""
from dataclasses import dataclass
from typing import Optional, Union

from validation.dcf_reconciliation.cell_map import TOLERANCES

# Absolute-dollar tolerance used ONLY when the workbook's comparison value
# is exactly zero (a relative/percentage tolerance is mathematically
# undefined at zero) -- covers floating-point noise, not a real
# discrepancy.
ZERO_VALUE_ABS_TOLERANCE = 1e-6


@dataclass
class ComparisonResult:
    metric: str
    codebase_value: float
    workbook_value: float
    absolute_difference: float
    difference_kind: str  # "percentage_points" | "relative_fraction" | "absolute_zero_carveout"
    difference_value: float
    tolerance_kind: str
    tolerance_value: float
    passed: bool
    notes: str = ""

    def to_dict(self):
        return {
            "metric": self.metric,
            "codebase_value": self.codebase_value,
            "workbook_value": self.workbook_value,
            "absolute_difference": self.absolute_difference,
            "difference_kind": self.difference_kind,
            "difference_value": self.difference_value,
            "tolerance_kind": self.tolerance_kind,
            "tolerance_value": self.tolerance_value,
            "passed": self.passed,
            "notes": self.notes,
        }


def compare_rate(metric: str, codebase_value: float, workbook_value: float, tolerance_pp_decimal: float) -> ComparisonResult:
    """Rate-like metric: compare RAW (codebase - workbook) difference, in
    percentage points, against a percentage-point tolerance. Correct for
    negative values by construction (plain subtraction, then abs)."""
    abs_diff = abs(codebase_value - workbook_value)
    passed = abs_diff <= tolerance_pp_decimal
    return ComparisonResult(
        metric=metric,
        codebase_value=codebase_value,
        workbook_value=workbook_value,
        absolute_difference=abs_diff,
        difference_kind="percentage_points",
        difference_value=abs_diff * 100.0,  # expressed in pp for readability
        tolerance_kind="percentage_points",
        tolerance_value=tolerance_pp_decimal * 100.0,
        passed=passed,
    )


def compare_monetary(metric: str, codebase_value: float, workbook_value: float, tolerance_relative: float) -> ComparisonResult:
    """Monetary metric: relative difference = |codebase - workbook| / |workbook|,
    with a documented absolute-tolerance carve-out when workbook_value == 0
    exactly (relative difference is undefined at zero; a zero denominator
    must never be silently treated as a pass or as an unconditional fail)."""
    abs_diff = abs(codebase_value - workbook_value)
    if workbook_value == 0:
        passed = abs_diff <= ZERO_VALUE_ABS_TOLERANCE
        return ComparisonResult(
            metric=metric,
            codebase_value=codebase_value,
            workbook_value=workbook_value,
            absolute_difference=abs_diff,
            difference_kind="absolute_zero_carveout",
            difference_value=abs_diff,
            tolerance_kind="absolute_zero_carveout",
            tolerance_value=ZERO_VALUE_ABS_TOLERANCE,
            passed=passed,
            notes="Workbook value is exactly zero; relative tolerance is undefined here, "
                  "so an absolute floating-point-noise tolerance was applied instead.",
        )
    relative_diff = abs_diff / abs(workbook_value)
    passed = relative_diff <= tolerance_relative
    return ComparisonResult(
        metric=metric,
        codebase_value=codebase_value,
        workbook_value=workbook_value,
        absolute_difference=abs_diff,
        difference_kind="relative_fraction",
        difference_value=relative_diff,
        tolerance_kind="relative_fraction",
        tolerance_value=tolerance_relative,
        passed=passed,
    )


def compare_metric(metric: str, codebase_value: float, workbook_value: float) -> ComparisonResult:
    kind, tol = TOLERANCES[metric]
    if kind == "absolute_pp":
        return compare_rate(metric, codebase_value, workbook_value, tol)
    elif kind == "relative_pct":
        return compare_monetary(metric, codebase_value, workbook_value, tol)
    raise ValueError(f"Unknown tolerance kind {kind!r} for metric {metric!r}.")
