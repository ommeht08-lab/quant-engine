"""
Digest compatibility with results produced before the digest scope changed.

`result_digest` originally covered the disclosure text. Disclosure copy is
presentation, not numerics, and has since been revised (and is expected to
keep being refined), so the current digest excludes it. Bundles exported
earlier still carry the original digest; `legacy_v1_digest` recomputes it by
substituting the frozen original disclosures, so an archived replay can still
be verified bit-for-bit instead of being reported as a mismatch.

LEGACY_V1_DISCLOSURES must never be edited: it is the text that was hashed.
"""

from __future__ import annotations

from typing import List, Mapping

from src.dcf_model.monte_carlo_config import canonical_json, sha256_hex

LEGACY_V1_DISCLOSURES: List[dict] = [
    {"id": "independence", "text": "Growth, operating margin and WACC are sampled independently; real-world correlations between them are ignored."},
    {"id": "persistent_rates", "text": "Each draw applies one growth rate (the starting rate under the maturation forecast) and one margin to the whole forecast. The growth spread is the volatility of year-to-year growth, which likely overstates uncertainty in a single persistent rate."},
    {"id": "noisy_spreads", "text": "Spreads are sample standard deviations (ddof=1) of a few annual observations. With three growth observations the 90% interval for the true spread is roughly 0.58x to 4.4x the estimate."},
    {"id": "truncation", "text": "Growth and margin are truncated normals sampled by inverse CDF (no clipping, no rejected proposals). The reported excluded mass is the probability of the untruncated normal outside the bounds, not a count of draws; a truncated distribution's mean can differ from its center."},
    {"id": "centers_distinct", "text": "The growth distribution is centered on the uncapped historical revenue CAGR; the deterministic baseline applies the 25% ceiling to that CAGR; and the sampled values come from the truncated distribution. These three are reported separately."},
    {"id": "wacc", "text": "Sampled WACC replaces the CAPM-derived rate in both the terminal value and discounting. It is uniform within the applied baseline +/-1 percentage point, intersected with the model's 5%-20% bounds and kept at least 1 point above terminal growth. Any clamping of the baseline and the resulting interval are reported."},
    {"id": "fixed_inputs", "text": "Terminal growth, tax rate, D&A, capex, working capital, debt, cash and share count are held fixed at the deterministic model's resolved values."},
    {"id": "failed_draw_resampling", "text": "A draw whose valuation fails is redrawn within a bounded retry budget. This conditions the simulated distribution on success; the counts of failures by cause are reported."},
    {"id": "market_price", "text": "The market price is the quote captured with the snapshot; the data provider does not supply a quote time. Comparisons count simulated values strictly above that price."},
    {"id": "not_prediction", "text": "Single-input experiments show sensitivity under the selected ranges. They are not a formal variance attribution, a probability of mispricing, or evidence of predictive accuracy."},
    {"id": "data_scope", "text": "History is limited to the annual statement periods the provider returns."},
]


def legacy_v1_digest(canonical_body: Mapping) -> str:
    """The pre-revision digest of a result's numerical payload (everything but meta/provenance)."""
    return sha256_hex(canonical_json({**canonical_body, "disclosures": LEGACY_V1_DISCLOSURES}))
