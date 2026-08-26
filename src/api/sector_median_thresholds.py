"""
Shared numeric thresholds and validators for sector-median snapshots.

Extracted from `src.api.sector_medians` so both that module (the
file-based cache, still used for local generation and by
`tests/api/test_sector_medians.py`) and `src.api.sector_median_store`
(the Supabase-backed live store) can apply exactly the same
coverage/sample-size floors and the same non-raising numeric validation
-- without either module importing the other, which would form an
import cycle (`sector_medians` needs the live store to serve
`/api/evaluate`; the store needs these thresholds to decide whether a
run is publishable). This module has no dependency on either.
"""

import datetime
import math
from enum import Enum

# A comparison against a cache older than this is refused rather than
# silently trusted, even if the requested sector/assumptions otherwise match.
CACHE_MAX_STALENESS = datetime.timedelta(hours=48)

# A sector median backed by fewer valid samples than this is refused —
# a "median" of 1-2 tickers isn't a meaningful peer comparison.
MIN_SECTOR_SAMPLE_SIZE = 3

# Overall generation-health guard, independent of any single sector's
# sample size: if an excessive share of the whole universe failed to
# value (e.g. a broad data-provider outage, a bug that broke every
# ticker's statement parsing), that's a systemic problem with the RUN,
# not sector-specific noise — a sector with exactly MIN_SECTOR_SAMPLE_SIZE
# survivors can still look individually "healthy" while the run that
# produced it was actually badly broken. Refuse the whole cache in that
# case rather than trust a technically-sufficient sector sample drawn
# from an unhealthy run.
MIN_OVERALL_COVERAGE_FRACTION = 0.5

# A comparison is refused if the caller's actual risk-free rate (the same
# rate it fed into `calculate_wacc` for the ticker it's about to compare)
# differs from the cache's own generation-time risk-free rate by more
# than this — otherwise the numerator (freshly computed P/IV) and the
# denominator (a cached peer median) would have been discounted under
# materially different macro regimes, making the ratio comparison
# meaningless even though every other assumption matches. 5 basis points
# comfortably covers the normal drift `get_risk_free_rate`'s own 1-hour
# cache TTL can introduce between two calls made close together, without
# being wide enough to paper over a genuine days-old rate move.
RISK_FREE_RATE_COMPARISON_TOLERANCE = 0.0005

# How far into the future a snapshot's `generated_at` may be before it's
# refused outright — a strictly-in-the-future timestamp can only be
# clock skew, a bug, or tampered data. A few minutes of tolerance
# absorbs ordinary clock drift between the machine that generated the
# snapshot and whichever check (publish-time validation, in
# `src.api.sector_median_store._passes_publish_thresholds`, or read-time
# validation, in `src.api.sector_medians._evaluate_sector_median_cache_full`
# / `_build_snapshot_provenance`) runs slightly later, without letting a
# genuinely bogus far-future date through. A single shared constant here
# is the one source of truth for this policy — both readers and writers
# import it rather than each pinning their own copy of "5 minutes".
MAX_FUTURE_SKEW = datetime.timedelta(minutes=5)


def _is_valid_finite_number(value) -> bool:
    """
    Genuinely non-raising: True only for a genuine finite `int`/`float`
    -- not a `bool` (a `bool` is technically an `int` subclass in
    Python), not a non-numeric type, and not NaN/+-infinity.

    Deliberately does NOT call `math.isfinite(value)` on an `int` -- a
    Python `int` is arbitrary-precision and always finite by definition,
    but `math.isfinite` still converts its argument to a C `double`
    first, and that conversion itself raises `OverflowError` for an
    `int` too large to fit in a float (e.g. a malformed cache JSON field
    like `10**10000`). Calling `math.isfinite` on an `int` this large
    would make this "non-raising" cache-validation check itself raise.
    `float` values are always safe to pass to `math.isfinite` directly.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _is_valid_nonneg_int(value) -> bool:
    """
    True only for a genuine `int` (never a `bool`, never a `float` that
    merely happens to be whole, e.g. `95.0`) that is non-negative. Used
    for `universe_size`/`tickers_used`/per-sector sample counts at BOTH
    publish time (`src.api.sector_median_store._passes_publish_thresholds`)
    and read time (`src.api.sector_medians._evaluate_sector_median_cache_full`
    / `_build_snapshot_provenance`) — these must be genuine integer
    counts, not just finite numbers, at every boundary: a float or
    boolean surviving into either a published or a validated-for-read
    snapshot would indicate the generator's own counting logic was
    tampered with or bypassed. Callers may safely `int()`-cast a value
    that has already passed this check — it is already a genuine `int`,
    so the cast cannot truncate or otherwise change it.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class SectorMedianUnavailableCode(str, Enum):
    """
    A small, stable vocabulary the frontend (and any other API consumer)
    can switch on, instead of pattern-matching the free-text internal
    diagnostic reason string (which is allowed to reword itself over
    time and is never meant to be shown to an end user verbatim).

    - INCOMPATIBLE_ASSUMPTIONS: a snapshot exists and is otherwise
      healthy, but was generated under different DCF assumptions or a
      materially different risk-free rate than this specific request —
      the comparison is only valid under the snapshot's own baseline.
    - INSUFFICIENT_PEERS: a snapshot exists and this request's
      assumptions match it, but too few peer companies in this sector
      were successfully valued to trust a median.
    - SNAPSHOT_UNAVAILABLE: everything else — no snapshot has ever been
      published, the database is unreachable, `DATABASE_URL` is unset,
      the snapshot is stale, or it's internally malformed/unhealthy.
      Deliberately one bucket: none of these are actionable or
      meaningfully different from the caller's (or a user's) point of
      view — "peer data isn't available right now" covers all of them
      without promising a specific fix or timeline.
    """

    INCOMPATIBLE_ASSUMPTIONS = "incompatible_assumptions"
    INSUFFICIENT_PEERS = "insufficient_peers"
    SNAPSHOT_UNAVAILABLE = "snapshot_unavailable"
