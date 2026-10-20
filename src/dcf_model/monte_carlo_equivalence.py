"""
Numerical equivalence of two Monte Carlo results: the cross-platform replay policy.

Three different claims are kept apart everywhere in this feature:

* integrity verified   - stored content still hashes to the digests recorded with it
                         (snapshot id, bundle digest, provenance digest, stored result digest).
                         Exact hashes over stored bytes; nothing here relaxes them.
* exact replay         - recomputing on this machine reproduces the recorded result digest bit for bit.
* numerically equivalent replay - recomputing reproduces the recorded numbers within the explicit
                         tolerances below, with every count, status, key and configuration value equal.

Floating-point results depend on the CPU, the C math library and the numpy build, so a result
computed on Linux x86-64 and replayed on macOS arm64 differs in the last few bits even though both
are correct. A hash of the floats cannot see that distinction; this module can.

The policy is versioned. Changing a tolerance, a class or a rule means a new version string,
with the reason recorded in docs/model-specifications/monte-carlo-dcf.md.

Observed differences that set the tolerances (3 tickers; Vercel Linux x86-64 numpy 2.0.2, macOS arm64
numpy 2.0.2 and numpy 2.4.6): every integer, string, boolean, key, list length, status, count,
histogram bin count and above-market count was identical. Floats differed by at most 1.4e-13
relative (a tail value that is a near-cancellation: absolute error 2.8e-17) and otherwise by at most
2.9e-15 relative / 21 ULP. The tolerances below leave at least two orders of magnitude of headroom
over that, and are far below any change to a formula or an input that matters.
"""

from __future__ import annotations

import math
import numbers
import re
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

NUMERICAL_POLICY_VERSION = "mc-dcf-numerical-equivalence-v1"

# Top-level keys that are not numerical output (the same keys the result digest leaves out,
# except that baseline_provenance IS compared, field by field, when both sides carry it).
_SKIPPED_TOP_LEVEL = ("meta", "result_digest", "disclosures")


@dataclass(frozen=True)
class Tolerance:
    """|a - b| <= atol + rtol * max(|a|, |b|)."""

    rtol: float
    atol: float
    unit: str


# Why each tolerance:
#   rtol 1e-12  - about 4,500 ULP of headroom; observed worst non-cancelling error is 21 ULP.
#   value_per_share atol 1e-9 USD - a billionth of a dollar, far below a cent and above summation noise.
#   amount atol 1e-6 - a millionth of a unit on balance-sheet scale inputs.
#   rate atol 1e-15 - roughly four ULP at 0.2; this is what absorbs `mean + sd * z` near-cancellation
#     (observed 2.8e-17 absolute on a margin of about 2e-4, which is 1.4e-13 relative).
#   tail_mass atol 0 - probabilities down to 1e-80 occur, so only a relative comparison is meaningful.
TOLERANCES: Dict[str, Tolerance] = {
    "value_per_share": Tolerance(1e-12, 1e-9, "USD per share"),
    "amount": Tolerance(1e-12, 1e-6, "currency units"),
    "rate": Tolerance(1e-12, 1e-15, "fraction"),
    "tail_mass": Tolerance(1e-12, 0.0, "probability"),
    "provenance_numeric": Tolerance(1e-12, 1e-12, "mixed"),
}

# The kernel and the scalar pipeline must agree at the baseline within this relative tolerance. It is the same
# 1e-9 at which the run itself refuses (monte_carlo.KERNEL_MISMATCH_REFUSAL_REL_TOLERANCE; a test keeps the two equal).
KERNEL_SCALAR_AGREEMENT = Tolerance(1e-9, 1e-9, "USD per share")

# Classes with no tolerance. "exact": floats must be bit-identical (copied inputs, configuration, ratios of
# exact integers). "informational": not compared, of any type (a diagnostic that is itself platform-dependent,
# or a hash of floats whose inputs are compared field by field).
EXACT = "exact"
INFORMATIONAL = "informational"

# First match wins. Paths have list indices replaced by "[]".
FLOAT_RULES: List[Tuple[str, str]] = [
    (r"^/baseline_provenance/provenance_digest$", INFORMATIONAL),
    (r"^/baseline_provenance/", "provenance_numeric"),
    (r"^/kernel_baseline_check/(exactly_equal|absolute_difference)$", INFORMATIONAL),
    (r"^/kernel_baseline_check/", "value_per_share"),
    (r"^/(config|config_hash|seed|market_price)(/|$)", EXACT),
    (r"^/histogram_edges\[\]$", "value_per_share"),
    (r"^/baseline/intrinsic_value_per_share$", "value_per_share"),
    (r"^/baseline/(enterprise_value|equity_value|base_revenue|cash_and_equivalents|total_debt|shares_outstanding)$", "amount"),
    (r"^/baseline/", "rate"),
    (r"^/history/revenues\[\]$", "amount"),
    (r"^/history/", "rate"),
    (r"^/experiments/[^/]+/statistics/", "value_per_share"),
    (r"^/experiments/[^/]+/distributions/[^/]+/(untruncated_mass_below_lower|untruncated_mass_above_upper|admissible_mass)$", "tail_mass"),
    (r"^/experiments/[^/]+/distributions/", "rate"),
    (r"^/experiments/[^/]+/fixed_inputs_at_baseline/", "rate"),
    (r"^/experiments/[^/]+/sampled_input_summary/", "rate"),
    (r"^/experiments/[^/]+/above_market/fraction$", EXACT),
]
_COMPILED = [(re.compile(pattern), cls) for pattern, cls in FLOAT_RULES]
_INDEX = re.compile(r"\[\d+\]")

_MAX_REPORTED_VIOLATIONS = 25


def float_class(path: str) -> Optional[str]:
    """The comparison class for a float at `path`, or None when no rule covers it (a violation)."""
    normalized = _INDEX.sub("[]", path)
    for pattern, cls in _COMPILED:
        if pattern.search(normalized):
            return cls
    return None


def ulp_distance(a: float, b: float) -> int:
    """Number of representable doubles between a and b."""
    if a == b:
        return 0

    def ordered(x: float) -> int:
        bits = struct.unpack("<q", struct.pack("<d", x))[0]
        return bits if bits >= 0 else -(bits & 0x7FFFFFFFFFFFFFFF)

    return abs(ordered(a) - ordered(b))


def within(a: float, b: float, tolerance: Tolerance) -> bool:
    return abs(a - b) <= tolerance.atol + tolerance.rtol * max(abs(a), abs(b))


def _kind(value: Any) -> str:
    """A JSON-level type name: numpy scalars count as the Python number they stand for."""
    if value is None:
        return "null"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return "boolean"
    if isinstance(value, numbers.Integral):
        return "integer"
    if isinstance(value, numbers.Real):
        return "float"
    return type(value).__name__


class _Comparison:
    def __init__(self) -> None:
        self.violations: List[Dict[str, Any]] = []
        self.violation_count = 0
        self.compared_exact_values = 0
        self.by_class: Dict[str, Dict[str, Any]] = {}
        self.informational: List[str] = []

    def violate(self, path: str, reason: str, **detail: Any) -> None:
        self.violation_count += 1
        if len(self.violations) < _MAX_REPORTED_VIOLATIONS:
            self.violations.append({"path": path, "reason": reason, **detail})

    def stats(self, cls: str) -> Dict[str, Any]:
        return self.by_class.setdefault(
            cls, {"compared": 0, "different": 0, "max_abs": 0.0, "max_rel": 0.0, "max_ulp": 0, "worst_path": None}
        )

    def walk(self, expected: Any, actual: Any, path: str) -> None:
        if isinstance(expected, Mapping) and isinstance(actual, Mapping):
            if set(expected) != set(actual):
                self.violate(path or "/", "keys differ", only_expected=sorted(set(expected) - set(actual)),
                             only_actual=sorted(set(actual) - set(expected)))
            for key in expected:
                if key in actual:
                    self.walk(expected[key], actual[key], f"{path}/{key}")
            return
        if isinstance(expected, list) and isinstance(actual, list):
            if len(expected) != len(actual):
                self.violate(path, "list length differs", expected=len(expected), actual=len(actual))
                return
            for index, (e, a) in enumerate(zip(expected, actual)):
                self.walk(e, a, f"{path}[{index}]")
            return
        if float_class(path) == INFORMATIONAL:
            self.informational.append(path)
            return
        if _kind(expected) != _kind(actual):
            self.violate(path, "type differs", expected=_kind(expected), actual=_kind(actual))
            return
        if _kind(expected) == "float":
            self._compare_float(float(expected), float(actual), path)
            return
        self.compared_exact_values += 1
        if expected != actual:
            self.violate(path, "value differs (exact comparison)", expected=expected, actual=actual)

    def _compare_float(self, expected: float, actual: float, path: str) -> None:
        cls = float_class(path)
        if cls is None:
            self.violate(path, "unclassified float field (add it to FLOAT_RULES and bump the policy version)")
            return
        if not (math.isfinite(expected) and math.isfinite(actual)):
            if not (expected == actual or (math.isnan(expected) and math.isnan(actual))):
                self.violate(path, "non-finite value differs", expected=repr(expected), actual=repr(actual), **{"class": cls})
            return
        stats = self.stats(cls)
        stats["compared"] += 1
        if expected == actual:
            return
        stats["different"] += 1
        abs_diff = abs(expected - actual)
        scale = max(abs(expected), abs(actual))
        rel_diff = abs_diff / scale if scale else 0.0
        ulps = ulp_distance(expected, actual)
        if abs_diff > stats["max_abs"]:
            stats["max_abs"], stats["worst_path"] = abs_diff, _INDEX.sub("[]", path)
        stats["max_rel"] = max(stats["max_rel"], rel_diff)
        stats["max_ulp"] = max(stats["max_ulp"], ulps)
        if cls == EXACT:
            self.violate(path, "float differs (class requires bit-identical)", expected=expected, actual=actual, **{"class": cls})
        elif not within(expected, actual, TOLERANCES[cls]):
            self.violate(path, "float outside tolerance", expected=expected, actual=actual, abs_diff=abs_diff,
                         rel_diff=rel_diff, ulp=ulps, **{"class": cls})


def numerical_view(result: Mapping) -> dict:
    """What the policy compares: the whole result except meta, the digest and the disclosure text."""
    return {k: v for k, v in result.items() if k not in _SKIPPED_TOP_LEVEL}


def compare_results(expected: Mapping, actual: Mapping) -> dict:
    """Compare two results under the policy. Structure, counts, statuses and configuration must match exactly."""
    run = _Comparison()
    run.walk(numerical_view(expected), numerical_view(actual), "")
    # `exactly_equal` is platform-dependent and not compared, but each result must still show its own kernel and
    # scalar baselines agreeing within the policy; a real divergence between the two code paths cannot hide here.
    check = actual.get("kernel_baseline_check") if isinstance(actual, Mapping) else None
    if isinstance(check, Mapping) and isinstance(check.get("kernel_value_per_share"), float):
        if not within(check["kernel_value_per_share"], check["scalar_value_per_share"], KERNEL_SCALAR_AGREEMENT):
            run.violate("/kernel_baseline_check", "kernel and scalar baselines disagree beyond the policy tolerance",
                        kernel=check["kernel_value_per_share"], scalar=check["scalar_value_per_share"])
    differing = sum(entry["different"] for entry in run.by_class.values())
    return {
        "policy_version": NUMERICAL_POLICY_VERSION,
        "numerically_equivalent": run.violation_count == 0,
        "bit_identical": run.violation_count == 0 and differing == 0,
        "violation_count": run.violation_count,
        "violations": run.violations,
        "compared_exact_values": run.compared_exact_values,
        "float_differences": differing,
        "by_class": {cls: dict(entry) for cls, entry in sorted(run.by_class.items())},
        "informational_paths_not_compared": sorted(set(_INDEX.sub("[]", p) for p in run.informational)),
        "tolerances": {cls: {"rtol": t.rtol, "atol": t.atol, "unit": t.unit} for cls, t in TOLERANCES.items()},
    }
