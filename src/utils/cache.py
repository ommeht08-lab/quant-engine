"""
Redis caching layer (Upstash) for expensive external calls — primarily
`yfinance` fetches, which are both slow and subject to Yahoo Finance rate
limiting. Read-through cache: on a hit, the wrapped function is never
called at all.

Configured via the `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`
environment variables (from `.env` — never hardcoded here, same as every
other credential in this project). If either is missing, or Redis is
unreachable for any reason, the cache degrades to a transparent
passthrough: callers still get a working (just uncached) function rather
than a hard failure, since caching here is a performance optimization,
not a correctness requirement.

Serialization: values are encoded as a versioned, schema-validated JSON
envelope (`{"schema": ..., "type": ..., "value": ...}`) — never pickled.
JSON cannot execute code on decode, unlike `pickle.loads`, so a
compromised or corrupted Redis value can never do more than fail to
decode. Only the three return shapes this codebase's `@cached` call
sites actually produce are supported: `float`, `Tuple[float,
pd.Timestamp]` ("point in time price"), and `pd.DataFrame` limited to a
`pd.DatetimeIndex` (tz-aware or naive) or an ALL-STRING `pd.Index` (every
value a plain `str` — an integer/`RangeIndex` or any other kind is
rejected at ENCODE time, never silently `str()`-coerced into looking
like a string index it never was) for BOTH its row index and its
columns, with `float64`/`float32`/`int64`/`int32`/`bool` column dtypes —
this exact set, not pandas dtypes/index kinds in general — and no
duplicate column labels (also rejected at encode time; see
`_encode_dataframe`'s docstring for why reconstruction can't safely
support them). Within that supported shape, round-tripping is faithful:
index/column values, an index/column's own `.name` (pandas metadata,
not decoration — must itself be `None` or a plain `str`, also enforced
at encode time), per-column dtype (assigned by POSITION on decode, not
by label), and (for a `DatetimeIndex`) the original tz label are all
preserved, not just the cell values.

Decoding enforces the expected schema/shape EXACTLY at every level, not
approximately — the envelope and each type tag's own payload must have
precisely the key set this codec itself writes, no unlisted extra field
tolerated. This extends to each DataFrame cell's own JSON scalar TYPE,
not just the envelope's keys: a cell must be the exact canonical shape
`_encode_dataframe` itself would have written for its column's declared
dtype (`_assert_canonical_dataframe_cell`) — a JSON boolean for a `bool`
column, a JSON integer within range for an `int32`/`int64` column, a
JSON float-literal token (or `null` for NaN) within range for a
`float32`/`float64` column — never accepted merely because NumPy/pandas
COULD cast it (a JSON number in a `bool` column, a JSON boolean in an
`int`/`float` column, or a JSON integer that would silently lose
precision being cast to `float64`, are all rejected rather than
coerced). `decode_cache_value` is the single untrusted-input boundary:
all of the above, plus unknown type tags, malformed structure, an extra
envelope/payload field, oversized payloads, non-finite numbers, a
numeric value too large to represent as a float or too large for its
target DataFrame column dtype (rejected explicitly, before ever reaching
a numpy/pandas conversion that would otherwise raise a raw, uncaught
`OverflowError`), duplicate column labels, and text that cannot itself
be encoded back to UTF-8 (e.g. an unpaired Unicode surrogate) all fall
back to a cache miss (the wrapped function is called directly) rather
than ever raising into the caller or silently coercing bad data.
`decode_cache_value` additionally converts ANY other ordinary
`Exception` it wasn't explicitly written to anticipate into the same
safe outcome — see its own docstring — so a malformed-input case this
description doesn't enumerate still can't propagate past this boundary;
only `BaseException` subtypes that are NOT `Exception` subtypes
(`KeyboardInterrupt`, `SystemExit`) are ever allowed through uncaught,
as they must be.

Cache keys are namespaced with `CACHE_KEY_VERSION` below. Bumping it
(as this module does, moving off the old pickle-based format) makes
every previously-written entry a guaranteed miss under the new key
scheme — old pickled values are never read, let alone deserialized.
"""

import functools
import hashlib
import json
import logging
import math
import os
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_redis_client = None
_client_initialized = False

# Bumping this segment orphans every cache entry written by the old
# pickle/base64 format — they simply become unreachable keys (and expire
# on their original TTL) rather than ever being read back and decoded.
CACHE_KEY_VERSION = "v2"

# Envelope schema version for the JSON codec below. Any stored value
# whose "schema" field doesn't match exactly is rejected as unknown
# rather than guessed at — a future format change bumps this again
# rather than mutating the meaning of an existing version in place.
CACHE_SCHEMA_VERSION = 2

# Hard ceiling on a cache entry's raw encoded size, checked before any
# JSON parsing is attempted — bounds the memory/CPU cost of decoding a
# maliciously large stored value.
MAX_CACHE_PAYLOAD_BYTES = 2_000_000

# Hard ceiling on a decoded DataFrame's cell count (rows * columns),
# checked after parsing but before per-cell reconstruction — the
# DataFrames actually cached here (≤1y of daily OHLCV, or a handful of
# annual statement line items) are at most a few thousand cells.
MAX_DATAFRAME_CELLS = 200_000

# Only column dtypes this codebase's cached DataFrames actually use are
# accepted on decode. Each maps to a plain numpy dtype constructor call
# (never anything dynamic/evaluated) used to restore the original column
# dtype after reconstruction from JSON-safe scalars.
_ALLOWED_DATAFRAME_DTYPES = {
    "float64": np.float64,
    "float32": np.float32,
    "int64": np.int64,
    "int32": np.int32,
    "bool": np.bool_,
}

# Exact representable range for each integer dtype, and the largest
# finite magnitude each float dtype can hold without a cast silently
# producing +/-inf. Checked against every DataFrame cell BEFORE
# `pd.DataFrame(...)`/`.astype(...)` ever runs — a JSON integer with
# hundreds of digits (or one merely outside a narrower int32/float32
# column's range) is rejected here with a clear message, rather than
# reaching numpy/pandas and raising a raw `OverflowError` that would
# otherwise escape this module's `CacheDecodeError` contract.
_INTEGER_DTYPE_BOUNDS = {
    "int64": (int(np.iinfo(np.int64).min), int(np.iinfo(np.int64).max)),
    "int32": (int(np.iinfo(np.int32).min), int(np.iinfo(np.int32).max)),
}
_FLOAT_DTYPE_MAX_MAGNITUDE = {
    "float64": float(np.finfo(np.float64).max),
    "float32": float(np.finfo(np.float32).max),
}

_VALID_CACHE_TYPE_TAGS = frozenset({"float", "point_in_time_price", "dataframe"})


class CacheDecodeError(Exception):
    """A stored cache value did not decode to a valid, expected shape."""


def _reject_json_constant(name: str) -> None:
    """
    `json.loads`'s `parse_constant` hook — passed a token name
    ("NaN", "Infinity", "-Infinity") whenever the JSON text contains one
    of these non-standard literal extensions. Refusing them here means a
    stored payload can never smuggle a non-finite float value in
    directly as a raw token; the codec's own encoder never emits them
    either (finite values only, `null` for NaN/NaT), so any occurrence
    on decode is necessarily a legacy/malformed/malicious entry.
    """
    raise CacheDecodeError(f"Rejected non-finite JSON constant: {name}")


def _finite_float_from_json_number(value: Any, context: str) -> float:
    """
    Convert a JSON-decoded `int`/`float` to a finite Python `float`,
    raising `CacheDecodeError` — never letting `OverflowError` escape —
    if `value` is outside the range a `float` can represent at all (a
    JSON integer with hundreds of digits converts to a Python `int` of
    unbounded size at parse time; `float(value)` on one that large
    raises `OverflowError`, not `ValueError`) or is non-finite.
    """
    try:
        fv = float(value)
    except OverflowError as exc:
        raise CacheDecodeError(f"{context} is too large to represent as a float: {exc}") from exc
    if not math.isfinite(fv):
        raise CacheDecodeError(f"{context} must be finite")
    return fv


def _assert_canonical_dataframe_cell(cell: Any, dtype_str: str) -> None:
    """
    Raise `CacheDecodeError` unless `cell` is EXACTLY the JSON scalar
    shape `_encode_dataframe` itself would have written for a column of
    this dtype — never accepted merely because NumPy/pandas COULD cast
    it to that dtype. This is what makes the codec's "exact schema"
    claim true for cell VALUES, not just for the envelope's own key set:
    without it, `dtype="bool"` + JSON `2` decodes (via NumPy's own
    truthy cast) to `True`, `dtype="bool"` + JSON `0.5` also to `True`,
    `dtype="int64"` + JSON `true` to `1`, `dtype="float64"` + JSON `true`
    to `1.0`, and — the precision-losing case — `dtype="float64"` +
    the JSON INTEGER `9007199254740993` silently becomes the DIFFERENT
    float64 value `9007199254740992.0`. None of those are values this
    module's own encoder could ever have written for that dtype, so
    none of them should decode successfully at all.

    Per dtype:
      - `bool`: a JSON boolean ONLY. A non-nullable pandas `bool` column
        can never hold NaN, so `null` is not valid here either.
      - `int32`/`int64`: a JSON integer ONLY, explicitly excluding
        `bool` (a subtype of `int` in Python, but JSON `true`/`false`
        must never be accepted as `1`/`0`), within the dtype's exact
        representable range. `null` is not valid here either — a
        non-nullable pandas int column can never hold NaN.
      - `float32`/`float64`: a JSON number ONLY where the token itself
        was a float literal — `_encode_dataframe` always writes
        `float(value)`, and Python's `json.dumps` always serializes a
        `float` with a decimal point/exponent, so `json.loads` always
        gives back a Python `float` for a value this encoder wrote,
        NEVER an `int`. A bare JSON integer literal in a float-dtype
        cell (however large, however well it might otherwise fit) is
        therefore never something this codec's own encoder could have
        produced, and is rejected outright rather than silently
        accepted as a lossy/non-canonical conversion. `null` (NaN) IS
        valid here — the one dtype whose encoder can genuinely produce
        it. `bool` is explicitly excluded here too.

    `cell` has already been confirmed by the caller to be one of
    `None`/`bool`/`int`/`float` (never e.g. a `str`/`list`/`dict`) and,
    if a `float`, finite — this adds the dtype-specific SHAPE and RANGE
    checks on top of that.
    """
    if dtype_str == "bool":
        if not isinstance(cell, bool):
            raise CacheDecodeError(f"Cell {cell!r} must be a JSON boolean for dtype {dtype_str!r}")
        return

    if dtype_str in _INTEGER_DTYPE_BOUNDS:
        if cell is None:
            raise CacheDecodeError(f"Cell must not be null for non-nullable dtype {dtype_str!r}")
        if isinstance(cell, bool) or not isinstance(cell, int):
            raise CacheDecodeError(f"Cell {cell!r} must be a JSON integer for dtype {dtype_str!r}")
        low, high = _INTEGER_DTYPE_BOUNDS[dtype_str]
        if not (low <= cell <= high):
            raise CacheDecodeError(f"Integer cell is out of range for dtype {dtype_str!r}")
        return

    if dtype_str in _FLOAT_DTYPE_MAX_MAGNITUDE:
        if cell is None:
            return  # NaN — the one legitimate missing-value marker.
        if isinstance(cell, bool) or not isinstance(cell, float):
            raise CacheDecodeError(
                f"Cell {cell!r} must be a JSON floating-point number for dtype {dtype_str!r}"
            )
        if abs(cell) > _FLOAT_DTYPE_MAX_MAGNITUDE[dtype_str]:
            raise CacheDecodeError(f"Numeric cell magnitude is out of range for dtype {dtype_str!r}")
        return

    raise CacheDecodeError(f"Unknown dtype during cell validation: {dtype_str!r}")


def _get_redis_client():
    """Lazily construct (once) the Upstash Redis client, or None if unconfigured/unavailable."""
    global _redis_client, _client_initialized
    if _client_initialized:
        return _redis_client
    _client_initialized = True

    load_dotenv()
    url = os.getenv("UPSTASH_REDIS_REST_URL")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        logger.warning(
            "UPSTASH_REDIS_REST_URL/UPSTASH_REDIS_REST_TOKEN not set; caching is disabled (passthrough)."
        )
        return None

    try:
        from upstash_redis import Redis

        _redis_client = Redis(url=url, token=token)
    except Exception as exc:  # noqa: BLE001 - caching must never block the caller
        logger.warning("Failed to initialize Upstash Redis client; caching is disabled: %s", exc)
        _redis_client = None
    return _redis_client


def _build_cache_key(prefix: str, func: Callable, args: tuple, kwargs: dict) -> str:
    """Deterministic cache key: version + prefix + function name + a hash of its arguments."""
    raw = repr((args, sorted(kwargs.items())))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{CACHE_KEY_VERSION}:{prefix}:{func.__name__}:{digest}"


# -- timestamp <-> wire helpers (shared by the point-in-time-price and
# -- dataframe index/column codecs) -----------------------------------


def _timestamp_to_wire(ts: pd.Timestamp) -> tuple:
    """A single `pd.Timestamp` -> (iso_string, tz_name_or_None), normalizing
    a tz-aware timestamp to a naive UTC ISO string plus its original zone
    name, so the exact instant AND the original tz label both survive."""
    if ts.tzinfo is not None:
        naive_utc = ts.tz_convert("UTC").tz_localize(None)
        return naive_utc.isoformat(), str(ts.tzinfo)
    return ts.isoformat(), None


def _timestamp_from_wire(iso_string: str, tz: Optional[str]) -> pd.Timestamp:
    if not isinstance(iso_string, str):
        raise CacheDecodeError("Timestamp wire value must be a string")
    try:
        ts = pd.Timestamp(iso_string)
    except (ValueError, TypeError) as exc:
        raise CacheDecodeError(f"Unparseable timestamp: {iso_string!r}") from exc
    if tz is not None:
        if not isinstance(tz, str):
            raise CacheDecodeError("Timestamp tz wire value must be a string or null")
        try:
            ts = ts.tz_localize("UTC").tz_convert(tz)
        except Exception as exc:  # noqa: BLE001 - any tz-localization failure is a decode error
            raise CacheDecodeError(f"Unusable timestamp tz: {tz!r}") from exc
    return ts


_INDEX_WIRE_KEYS = frozenset({"kind", "values", "tz", "name"})


def _assert_supported_index_name(name: Any) -> None:
    """
    `_index_from_wire` only ever accepts `None` or a `str` for an
    index/column `.name` (see `_INDEX_WIRE_KEYS` validation below) — this
    is the matching ENCODE-time check, so a DataFrame with e.g. an
    integer `.name` fails fast at `encode_cache_value` (never cached,
    caller unaffected — see `cached()`'s write-path try/except) rather
    than being silently written to Redis as a cache entry that can only
    ever come back as a decode failure.
    """
    if name is not None and not isinstance(name, str):
        raise ValueError(
            f"Unsupported index/column name for cache encoding: {name!r} "
            f"(only None or a string is supported)"
        )


def _index_to_wire(index: pd.Index) -> dict:
    """
    `pd.Index` -> wire dict, preserving everything needed for a faithful
    round-trip: the values themselves (as ISO timestamps or strings), an
    original tz label for a tz-aware `DatetimeIndex`, and the index's own
    `.name` (e.g. a financial statement's row index is unnamed, but a
    caller-supplied DataFrame could have one — `df.index.name`/
    `df.columns.name` are real pandas metadata, not decoration, and code
    downstream of a cache hit may depend on them being present).

    Only a `DatetimeIndex` or an index whose values are ALL plain `str`
    is supported — matching `_index_from_wire`'s decode-side requirement
    exactly. Any other index kind (an integer `RangeIndex`, a `MultiIndex`,
    a mix of types, ...) is rejected here with `ValueError` rather than
    silently `str()`-coerced into looking like a string index it never
    was — a caller relying on those original (e.g. integer) values after
    a cache hit would otherwise get back strings instead, with no
    indication anything had changed.
    """
    _assert_supported_index_name(index.name)

    if isinstance(index, pd.DatetimeIndex):
        if index.tz is not None:
            tz_name = str(index.tz)
            naive = index.tz_convert("UTC").tz_localize(None)
        else:
            tz_name = None
            naive = index
        return {
            "kind": "datetime",
            "values": [ts.isoformat() for ts in naive],
            "tz": tz_name,
            "name": index.name,
        }

    if not all(isinstance(v, str) for v in index):
        offending_types = sorted({type(v).__name__ for v in index if not isinstance(v, str)})
        raise ValueError(
            "Unsupported index/column kind for cache encoding: only a DatetimeIndex or an "
            f"all-string Index is supported, got value type(s) {offending_types!r}"
        )
    return {"kind": "string", "values": list(index), "tz": None, "name": index.name}


def _index_from_wire(wire: Any) -> pd.Index:
    if not isinstance(wire, dict):
        raise CacheDecodeError("Index wire value must be an object")
    if set(wire.keys()) != _INDEX_WIRE_KEYS:
        raise CacheDecodeError(f"Index wire value has unexpected keys: {sorted(wire.keys())!r}")

    kind = wire.get("kind")
    values = wire.get("values")
    tz = wire.get("tz")
    name = wire.get("name")
    if not isinstance(values, list):
        raise CacheDecodeError("Index wire values must be a list")
    if name is not None and not isinstance(name, str):
        raise CacheDecodeError("Index wire name must be a string or null")

    if kind == "datetime":
        timestamps = [_timestamp_from_wire(v, tz) for v in values]
        index = pd.DatetimeIndex(timestamps)
        index.name = name
        return index
    if kind == "string":
        if tz is not None:
            raise CacheDecodeError("String index wire value must not carry a tz")
        if not all(isinstance(v, str) for v in values):
            raise CacheDecodeError("String index values must all be strings")
        index = pd.Index(values)
        index.name = name
        return index
    raise CacheDecodeError(f"Unknown index kind: {kind!r}")


# -- dataframe <-> wire ---------------------------------------------------


def _encode_dataframe(df: pd.DataFrame) -> dict:
    num_rows, num_cols = df.shape
    if num_rows * max(num_cols, 1) > MAX_DATAFRAME_CELLS:
        raise ValueError("DataFrame too large to cache")

    if df.columns.duplicated().any():
        # Duplicate column labels are rejected even though reconstruction
        # now assigns dtypes by POSITION (`df.isetitem(position, ...)`,
        # see `_decode_dataframe` below), not by label — position-based
        # assignment is no longer vulnerable to the cross-contamination
        # bug a duplicate label caused under the OLD label-based
        # `df[column] = ...` approach (where `df[column]` would select
        # EVERY column sharing that label at once and cast them all to
        # whichever dtype the loop reached last for it). Still refused
        # here for simplicity: no cached DataFrame in this codebase
        # (OHLCV price history, financial statements) has duplicate
        # column labels in practice, and rejecting them keeps column
        # identity unambiguous end-to-end.
        duplicate_labels = sorted(set(df.columns[df.columns.duplicated()]))
        raise ValueError(f"Duplicate column labels are not supported for cache encoding: {duplicate_labels!r}")

    dtypes = []
    for dtype in df.dtypes:
        dtype_str = str(dtype)
        if dtype_str not in _ALLOWED_DATAFRAME_DTYPES:
            raise ValueError(f"Unsupported column dtype for cache encoding: {dtype_str}")
        dtypes.append(dtype_str)

    data = []
    for row in df.itertuples(index=False, name=None):
        wire_row = []
        for value in row:
            if pd.isna(value):
                wire_row.append(None)
            elif isinstance(value, (bool, np.bool_)):
                wire_row.append(bool(value))
            elif isinstance(value, (int, np.integer)):
                wire_row.append(int(value))
            elif isinstance(value, (float, np.floating)):
                fv = float(value)
                wire_row.append(fv if math.isfinite(fv) else None)
            else:
                raise ValueError(f"Unsupported cell type for cache encoding: {type(value)!r}")
        data.append(wire_row)

    return {
        "index": _index_to_wire(df.index),
        "columns": _index_to_wire(df.columns),
        "dtypes": dtypes,
        "data": data,
    }


_DATAFRAME_PAYLOAD_KEYS = frozenset({"index", "columns", "dtypes", "data"})


def _decode_dataframe(payload: Any) -> pd.DataFrame:
    if not isinstance(payload, dict):
        raise CacheDecodeError("DataFrame payload must be an object")
    if set(payload.keys()) != _DATAFRAME_PAYLOAD_KEYS:
        raise CacheDecodeError(f"DataFrame payload has unexpected keys: {sorted(payload.keys())!r}")

    dtypes = payload.get("dtypes")
    data = payload.get("data")
    if not isinstance(dtypes, list) or not all(isinstance(d, str) for d in dtypes):
        raise CacheDecodeError("DataFrame dtypes must be a list of strings")
    if not all(d in _ALLOWED_DATAFRAME_DTYPES for d in dtypes):
        raise CacheDecodeError("DataFrame contains an unsupported column dtype")
    if not isinstance(data, list) or not all(isinstance(row, list) for row in data):
        raise CacheDecodeError("DataFrame data must be a list of lists")

    num_cols = len(dtypes)
    if len(data) * max(num_cols, 1) > MAX_DATAFRAME_CELLS:
        raise CacheDecodeError("DataFrame payload exceeds the maximum supported cell count")
    for row in data:
        if len(row) != num_cols:
            raise CacheDecodeError("DataFrame row length does not match column count")
        for cell, dtype_str in zip(row, dtypes):
            if cell is not None and not isinstance(cell, (bool, int, float)):
                raise CacheDecodeError(f"Unsupported DataFrame cell type: {type(cell)!r}")
            if isinstance(cell, float) and not math.isfinite(cell):
                raise CacheDecodeError("DataFrame cell contains a non-finite float")
            # Enforces the EXACT canonical JSON scalar shape this
            # codec's own encoder writes for this dtype (rejecting e.g.
            # a JSON boolean in an int column, or vice versa) and the
            # dtype-specific RANGE — never accepting a value merely
            # because NumPy/pandas COULD cast it, and never letting a
            # raw OverflowError escape from `.astype()` below either
            # (e.g. for a JSON integer with hundreds of digits).
            _assert_canonical_dataframe_cell(cell, dtype_str)

    index = _index_from_wire(payload.get("index"))
    columns = _index_from_wire(payload.get("columns"))
    if len(index) != len(data):
        raise CacheDecodeError("DataFrame index length does not match row count")
    if len(columns) != num_cols:
        raise CacheDecodeError("DataFrame columns length does not match column count")
    if columns.duplicated().any():
        # Symmetric with `_encode_dataframe`'s own refusal to encode
        # duplicate column labels — reject here too as defense in depth
        # against a crafted payload that never went through this
        # module's own encoder at all.
        raise CacheDecodeError("DataFrame payload has duplicate column labels")

    try:
        df = pd.DataFrame(data, index=index, columns=columns)
        # Assigns each column's dtype by POSITION (`isetitem`), not by
        # label (`df[column] = ...`) — correct regardless of whether
        # column labels happen to collide, and avoids ever needing to
        # rely on the duplicate-label rejection above as the ONLY thing
        # preventing one column's dtype cast from silently overwriting
        # another's.
        for position, dtype_str in enumerate(dtypes):
            column_dtype = _ALLOWED_DATAFRAME_DTYPES[dtype_str]
            df.isetitem(position, df.iloc[:, position].astype(column_dtype))
    except (ValueError, TypeError, OverflowError, ArithmeticError) as exc:
        raise CacheDecodeError(f"Failed to reconstruct DataFrame: {exc}") from exc

    return df


# -- top-level envelope codec ----------------------------------------------


def encode_cache_value(value: Any) -> str:
    """
    Encode a value into this cache's versioned JSON envelope. Raises
    `TypeError`/`ValueError` for anything not among this codebase's
    actual cached return types (`float`, `(float, pd.Timestamp)`,
    `pd.DataFrame`) or containing a non-finite number where one isn't
    valid — callers (the `cached` decorator) must treat any such failure
    as "don't cache this," never propagate it.
    """
    if isinstance(value, bool):
        raise TypeError("bool is not a supported top-level cache value type")

    if isinstance(value, (int, float)):
        fv = float(value)
        if not math.isfinite(fv):
            raise ValueError("Refusing to cache a non-finite float")
        envelope: dict = {"schema": CACHE_SCHEMA_VERSION, "type": "float", "value": fv}
    elif (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], (int, float))
        and not isinstance(value[0], bool)
        and isinstance(value[1], pd.Timestamp)
    ):
        price = float(value[0])
        if not math.isfinite(price):
            raise ValueError("Refusing to cache a non-finite price")
        iso_string, tz = _timestamp_to_wire(value[1])
        envelope = {
            "schema": CACHE_SCHEMA_VERSION,
            "type": "point_in_time_price",
            "value": {"price": price, "timestamp": iso_string, "tz": tz},
        }
    elif isinstance(value, pd.DataFrame):
        envelope = {
            "schema": CACHE_SCHEMA_VERSION,
            "type": "dataframe",
            "value": _encode_dataframe(value),
        }
    else:
        raise TypeError(f"Unsupported type for cache encoding: {type(value)!r}")

    return json.dumps(envelope, allow_nan=False)


_ENVELOPE_KEYS = frozenset({"schema", "type", "value"})
_POINT_IN_TIME_PRICE_PAYLOAD_KEYS = frozenset({"price", "timestamp", "tz"})


def decode_cache_value(raw: Any) -> Any:
    """
    Decode a stored cache entry back into its original Python value.
    Raises `CacheDecodeError` for absolutely anything that doesn't
    EXACTLY match the expected envelope/schema/shape at every level —
    the top-level envelope, and each type tag's own payload, must have
    exactly the keys this codec itself writes, no more and no fewer. An
    oversized payload, invalid/non-UTF-8-encodable text, a non-finite
    numeric literal, an unknown schema version or type tag, an unlisted
    extra field, a JSON integer too large to represent as a float or too
    large for its target DataFrame column dtype, or any other malformed
    nested structure are all rejected the same way. Callers must treat
    this as a cache miss (fall through to calling the wrapped function),
    never propagate it and never attempt a best-effort partial decode.

    `CacheDecodeError` is the ONLY exception type this function ever
    raises. Every specific malformed-input case above is converted
    explicitly by `_decode_cache_value_inner` (with a precise, useful
    message); this wrapper additionally converts ANY OTHER `Exception`
    that reaches it — a safety net for a malformed-input case not
    explicitly anticipated, e.g. a raw `OverflowError` from converting
    an astronomically large JSON integer — into a generic
    `CacheDecodeError` too, so nothing below this boundary can ever
    escape it uncaught. `BaseException` subtypes that are NOT
    `Exception` subtypes (`KeyboardInterrupt`, `SystemExit`,
    `GeneratorExit`) are never caught here and always propagate, as they
    must — this function only ever suppresses ordinary data-shape
    errors, never signals used to interrupt or terminate the process.
    """
    try:
        return _decode_cache_value_inner(raw)
    except CacheDecodeError:
        raise
    except Exception as exc:  # noqa: BLE001 - the untrusted-decode boundary: see docstring above.
        raise CacheDecodeError(f"Unexpected error decoding cache value: {exc}") from exc


def _decode_cache_value_inner(raw: Any) -> Any:
    if not isinstance(raw, str):
        raise CacheDecodeError(f"Cache value must be a string, got {type(raw)!r}")
    try:
        # A Python `str` CAN contain an unpaired UTF-16 surrogate (e.g.
        # from a driver that decoded raw bytes with `errors="surrogate
        # escape"`) that is a legal `str` value but cannot itself be
        # encoded back to UTF-8 — `.encode("utf-8")` raises
        # `UnicodeEncodeError` for that, which must be caught here rather
        # than escaping this function as an unhandled exception distinct
        # from `CacheDecodeError`, or the `cached` decorator's `except
        # CacheDecodeError` cache-miss fallback would never catch it.
        encoded_length = len(raw.encode("utf-8"))
    except UnicodeError as exc:
        raise CacheDecodeError(f"Cache value contains invalid/unencodable Unicode: {exc}") from exc
    if encoded_length > MAX_CACHE_PAYLOAD_BYTES:
        raise CacheDecodeError("Cache value exceeds the maximum supported payload size")

    try:
        envelope = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise CacheDecodeError(f"Invalid JSON: {exc}") from exc

    if not isinstance(envelope, dict):
        raise CacheDecodeError("Cache envelope must be a JSON object")
    if set(envelope.keys()) != _ENVELOPE_KEYS:
        raise CacheDecodeError(f"Cache envelope has unexpected keys: {sorted(envelope.keys())!r}")
    if envelope.get("schema") != CACHE_SCHEMA_VERSION:
        raise CacheDecodeError(f"Unsupported/unknown cache schema: {envelope.get('schema')!r}")

    type_tag = envelope.get("type")
    if type_tag not in _VALID_CACHE_TYPE_TAGS:
        raise CacheDecodeError(f"Unknown cache value type tag: {type_tag!r}")

    payload = envelope.get("value")

    if type_tag == "float":
        if not isinstance(payload, (int, float)) or isinstance(payload, bool):
            raise CacheDecodeError("float payload must be a JSON number")
        return _finite_float_from_json_number(payload, "float payload")

    if type_tag == "point_in_time_price":
        if not isinstance(payload, dict):
            raise CacheDecodeError("point_in_time_price payload must be an object")
        if set(payload.keys()) != _POINT_IN_TIME_PRICE_PAYLOAD_KEYS:
            raise CacheDecodeError(
                f"point_in_time_price payload has unexpected keys: {sorted(payload.keys())!r}"
            )
        price = payload.get("price")
        if not isinstance(price, (int, float)) or isinstance(price, bool):
            raise CacheDecodeError("point_in_time_price price must be a JSON number")
        price = _finite_float_from_json_number(price, "point_in_time_price price")
        timestamp = _timestamp_from_wire(payload.get("timestamp"), payload.get("tz"))
        return price, timestamp

    return _decode_dataframe(payload)


def cached(ttl_seconds: int, prefix: str) -> Callable:
    """
    Decorator: cache a function's return value in Upstash Redis for
    `ttl_seconds`, keyed by `prefix` + function name + a hash of its
    positional/keyword arguments (so e.g. `get_balance_sheet(aapl_ticker)`
    and `get_balance_sheet(msft_ticker)` never collide).

    On any Redis failure (unreachable, misconfigured), an encode/decode
    failure, or a stored value that doesn't match the expected schema,
    the wrapped function is simply called directly — caching must never
    become a source of failures for the caller, and an invalid/legacy
    entry is treated as a plain cache miss. A `None` result is never
    cached, so a transient fetch failure doesn't get "frozen" as a cache
    hit for the full TTL.

    Args:
        ttl_seconds: How long a cached result stays valid, e.g. 86400
            for a 24-hour TTL on relatively static financial statements.
        prefix: Cache key namespace, e.g. "balance_sheet".

    Returns:
        A decorator to apply to the function whose result should be cached.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            client = _get_redis_client()
            if client is None:
                return func(*args, **kwargs)

            cache_key = _build_cache_key(prefix, func, args, kwargs)

            try:
                cached_value = client.get(cache_key)
            except Exception as exc:  # noqa: BLE001 - a cache-read failure must fall through
                logger.warning(
                    "Cache read failed for %s; calling %s directly: %s", cache_key, func.__name__, exc
                )
                cached_value = None

            if cached_value is not None:
                try:
                    decoded = decode_cache_value(cached_value)
                except CacheDecodeError as exc:
                    logger.warning("Rejecting invalid/legacy cache entry for %s: %s", cache_key, exc)
                else:
                    logger.info("Cache hit: %s", cache_key)
                    return decoded

            result = func(*args, **kwargs)

            if result is not None:
                try:
                    encoded = encode_cache_value(result)
                    client.set(cache_key, encoded, ex=ttl_seconds)
                except Exception as exc:  # noqa: BLE001 - a cache-write failure must not propagate
                    logger.warning("Cache write failed for %s: %s", cache_key, exc)

            return result

        return wrapper

    return decorator
