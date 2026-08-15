"""
Security regression tests for `src.utils.cache`'s JSON-envelope codec,
which replaced `pickle.loads(base64.b64decode(...))` — a compromised or
corrupted Redis value could previously execute arbitrary Python on
decode. These tests prove:

  - No production `pickle`/executable-deserializer usage remains.
  - Every real cached return type (float, point-in-time price tuple,
    DataFrame — including tz-aware datetime indexes/columns and NaN)
    round-trips faithfully.
  - Legacy pickle bytes, and a wide range of malformed/oversized/
    malicious JSON payloads, are rejected as `CacheDecodeError` rather
    than decoded — and the `@cached` decorator turns that rejection into
    a safe cache-miss passthrough, never a crash or code execution.
"""

import base64
import inspect
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.utils import cache as cache_module
from src.utils.cache import CacheDecodeError, cached, decode_cache_value, encode_cache_value


class TestNoExecutableDeserialization:
    def test_no_pickle_import_in_cache_module(self):
        assert "pickle" not in cache_module.__dict__
        source = Path(inspect.getfile(cache_module)).read_text()
        assert "import pickle" not in source
        assert "eval(" not in source
        assert "exec(" not in source

    def test_legacy_pickle_payload_is_rejected_not_executed(self):
        """A value written by the OLD pickle/base64 format — including a
        completely benign one — must never be accepted by the new JSON
        codec. Pickle bytes are not valid JSON, so this also structurally
        proves `pickle.loads` is never reached: decoding fails before any
        execution-capable deserialization step could run."""
        legacy_payload = base64.b64encode(pickle.dumps("a perfectly benign legacy value")).decode("ascii")
        with pytest.raises(CacheDecodeError):
            decode_cache_value(legacy_payload)


class TestRoundTripFidelity:
    def test_float_round_trips(self):
        assert decode_cache_value(encode_cache_value(0.041237)) == pytest.approx(0.041237)

    def test_negative_and_zero_float_round_trip(self):
        assert decode_cache_value(encode_cache_value(-1.5)) == pytest.approx(-1.5)
        assert decode_cache_value(encode_cache_value(0.0)) == pytest.approx(0.0)

    def test_point_in_time_price_tz_aware_round_trips(self):
        ts = pd.Timestamp("2024-06-15 09:30:00", tz="America/New_York")
        price, decoded_ts = decode_cache_value(encode_cache_value((187.32, ts)))
        assert price == pytest.approx(187.32)
        assert decoded_ts == ts
        assert str(decoded_ts.tz) == str(ts.tz)

    def test_point_in_time_price_naive_round_trips(self):
        ts = pd.Timestamp("2023-01-03")
        price, decoded_ts = decode_cache_value(encode_cache_value((42.0, ts)))
        assert price == pytest.approx(42.0)
        assert decoded_ts == ts
        assert decoded_ts.tzinfo is None

    def test_price_history_dataframe_round_trips(self):
        """Shape mirrors `_get_daily_close_history`'s real return: tz-aware
        DatetimeIndex, float/int columns, and a NaN cell."""
        idx = pd.date_range("2024-01-01", periods=5, freq="D", tz="America/New_York")
        df = pd.DataFrame(
            {
                "Open": [1.0, 2.0, 3.0, 4.0, 5.0],
                "Close": [1.1, 2.2, np.nan, 4.4, 5.5],
                "Volume": [100, 200, 300, 400, 500],
            },
            index=idx,
        )

        decoded = decode_cache_value(encode_cache_value(df))

        assert list(decoded.columns) == list(df.columns)
        assert str(decoded.index.tz) == str(df.index.tz)
        assert decoded.dtypes.to_dict() == df.dtypes.to_dict()
        # check_freq=False: `date_range`'s inferred `freq` metadata isn't
        # part of the cached values themselves, and real yfinance-returned
        # indexes don't carry it either — only the data need round-trip.
        pd.testing.assert_frame_equal(decoded, df, check_freq=False)

    def test_financial_statement_dataframe_round_trips(self):
        """Shape mirrors `get_income_statement`/etc.: string row index
        (line items), Timestamp columns (fiscal period ends)."""
        columns = pd.to_datetime(["2023-12-31", "2022-12-31"])
        df = pd.DataFrame(
            {columns[0]: [1000.0, 200.0], columns[1]: [900.0, 180.0]},
            index=["Total Revenue", "EBIT"],
        )

        decoded = decode_cache_value(encode_cache_value(df))

        assert list(decoded.index) == list(df.index)
        assert list(decoded.columns) == list(df.columns)
        pd.testing.assert_frame_equal(decoded, df)

    def test_empty_dataframe_round_trips(self):
        df = pd.DataFrame({"Close": pd.Series(dtype="float64")}, index=pd.DatetimeIndex([]))
        decoded = decode_cache_value(encode_cache_value(df))
        assert decoded.empty
        assert list(decoded.columns) == list(df.columns)

    def test_index_and_columns_names_round_trip(self):
        """`.index.name`/`.columns.name` are real pandas metadata, not
        decoration — must survive a round trip like any other field."""
        idx = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC", name="Date")
        columns = pd.Index(["Open", "Close"], name="Field")
        df = pd.DataFrame({"Open": [1.0, 2.0, 3.0], "Close": [1.5, 2.5, 3.5]}, index=idx)
        df.columns = columns

        decoded = decode_cache_value(encode_cache_value(df))

        assert decoded.index.name == "Date"
        assert decoded.columns.name == "Field"

    def test_unnamed_index_and_columns_round_trip_as_none(self):
        # An explicit string index — a bare `pd.DataFrame({...})` would
        # default to an integer RangeIndex, which is intentionally NOT a
        # supported index kind (see TestEncodeRejectsUnsupportedValues).
        df = pd.DataFrame({"Close": [1.0, 2.0]}, index=["a", "b"])
        decoded = decode_cache_value(encode_cache_value(df))
        assert decoded.index.name is None
        assert decoded.columns.name is None

    def test_distinct_column_dtypes_round_trip_correctly_by_position(self):
        """Regression test for a positional (not label-based) dtype
        assignment: two DIFFERENT, adjacent, non-duplicate-labeled
        columns must each keep their OWN dtype after decode — proves
        `_decode_dataframe`'s `isetitem`-based reconstruction doesn't
        cross-contaminate dtypes between columns the way a label-based
        `df[column] = ...` loop risks doing whenever labels aren't
        guaranteed unique."""
        df = pd.DataFrame(
            {"Volume": [100, 200, 300], "Close": [1.1, 2.2, 3.3]}, index=["a", "b", "c"]
        )
        df["Volume"] = df["Volume"].astype("int32")
        df["Close"] = df["Close"].astype("float32")

        decoded = decode_cache_value(encode_cache_value(df))

        assert str(decoded["Volume"].dtype) == "int32"
        assert str(decoded["Close"].dtype) == "float32"
        assert list(decoded["Volume"]) == [100, 200, 300]
        assert decoded["Close"].tolist() == pytest.approx([1.1, 2.2, 3.3], abs=1e-6)


class TestEncodeRejectsUnsupportedValues:
    def test_bool_is_rejected(self):
        with pytest.raises(TypeError):
            encode_cache_value(True)

    def test_non_finite_float_is_rejected(self):
        with pytest.raises(ValueError):
            encode_cache_value(float("nan"))
        with pytest.raises(ValueError):
            encode_cache_value(float("inf"))

    def test_arbitrary_object_is_rejected(self):
        with pytest.raises(TypeError):
            encode_cache_value({"not": "a supported type"})

    def test_dataframe_with_unsupported_dtype_is_rejected(self):
        df = pd.DataFrame({"label": ["a", "b", "c"]})  # object dtype, not in the allow-list
        with pytest.raises(ValueError):
            encode_cache_value(df)

    def test_dataframe_with_default_integer_rangeindex_is_rejected(self):
        """A bare `pd.DataFrame({...})` gets a default integer `RangeIndex`
        — NOT one of the two documented supported index kinds
        (DatetimeIndex or all-string Index). Must be rejected at encode
        time, never silently `str()`-coerced into looking like a string
        index it never was."""
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})  # default RangeIndex
        assert isinstance(df.index, pd.RangeIndex)
        with pytest.raises(ValueError):
            encode_cache_value(df)

    def test_dataframe_with_integer_columns_is_rejected(self):
        df = pd.DataFrame([[1.0, 2.0]], columns=[0, 1], index=["r"])
        with pytest.raises(ValueError):
            encode_cache_value(df)

    def test_dataframe_with_non_string_index_name_is_rejected(self):
        df = pd.DataFrame({"Close": [1.0, 2.0]}, index=["a", "b"])
        df.index.name = 123  # not None, not a str
        with pytest.raises(ValueError):
            encode_cache_value(df)

    def test_dataframe_with_non_string_columns_name_is_rejected(self):
        df = pd.DataFrame({"Close": [1.0, 2.0]}, index=["a", "b"])
        df.columns.name = 123
        with pytest.raises(ValueError):
            encode_cache_value(df)

    def test_dataframe_with_duplicate_column_labels_is_rejected(self):
        """Reconstruction assigns dtypes by position but the OLD
        implementation assigned by label — a duplicate label made
        `df[column] = ...` select and overwrite BOTH duplicate columns
        with the same dtype. Rejected outright rather than risk that."""
        df = pd.DataFrame([[1.0, 2]], columns=["A", "A"], index=["r"])
        with pytest.raises(ValueError):
            encode_cache_value(df)


class TestDecodeRejectsMalformedOrMaliciousPayloads:
    @pytest.mark.parametrize(
        "raw",
        [
            "not json at all",
            "{}",
            "null",
            "[]",
            '{"schema": 1, "type": "float", "value": 1.0}',
            '{"schema": 2, "type": "evil", "value": 1.0}',
            '{"schema": 2, "type": "float", "value": NaN}',
            '{"schema": 2, "type": "float", "value": Infinity}',
            '{"schema": 2, "type": "float", "value": "not a number"}',
            '{"schema": 2, "type": "float", "value": true}',
            '{"schema": 2, "type": "point_in_time_price", "value": {"price": "x", "timestamp": "2024-01-01"}}',
            '{"schema": 2, "type": "point_in_time_price", "value": {"price": 1.0, "timestamp": "not-a-date"}}',
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["object"], "data": [], "index": {"kind": "string", "values": []}, "columns": {"kind": "string", "values": ["a"]}}}',
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float64"], "data": [[[1, 2]]], "index": {"kind": "string", "values": ["r"]}, "columns": {"kind": "string", "values": ["c"]}}}',
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float64"], "data": [[1.0], [2.0]], "index": {"kind": "string", "values": ["only_one"]}, "columns": {"kind": "string", "values": ["c"]}}}',
        ],
    )
    def test_rejected_payload_raises_cache_decode_error(self, raw):
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)

    def test_oversized_payload_is_rejected(self):
        huge = '{"schema": 2, "type": "float", "value": ' + "1" * 3_000_000 + "}"
        with pytest.raises(CacheDecodeError):
            decode_cache_value(huge)

    def test_non_string_payload_is_rejected(self):
        with pytest.raises(CacheDecodeError):
            decode_cache_value(12345)
        with pytest.raises(CacheDecodeError):
            decode_cache_value(None)

    def test_oversized_dataframe_cell_count_is_rejected(self):
        num_cols = 3
        num_rows = (cache_module.MAX_DATAFRAME_CELLS // num_cols) + 10
        payload = {
            "schema": 2,
            "type": "dataframe",
            "value": {
                "dtypes": ["float64"] * num_cols,
                "data": [[0.0] * num_cols for _ in range(num_rows)],
                "index": {"kind": "string", "values": [str(i) for i in range(num_rows)], "tz": None},
                "columns": {"kind": "string", "values": ["a", "b", "c"], "tz": None},
            },
        }
        import json

        with pytest.raises(CacheDecodeError):
            decode_cache_value(json.dumps(payload))

    def test_lone_utf16_surrogate_raises_cache_decode_error_not_unicode_error(self):
        """A Python `str` CAN legally contain an unpaired UTF-16 surrogate
        (e.g. from a driver that decoded bytes with `errors=
        "surrogateescape"`) that cannot itself be encoded back to UTF-8 —
        `raw.encode("utf-8")` raises `UnicodeEncodeError` for this, which
        decode_cache_value must convert to `CacheDecodeError` rather than
        let escape as an unrelated exception type the `cached` decorator's
        `except CacheDecodeError` cache-miss fallback wouldn't catch."""
        lone_surrogate = "\ud800"
        with pytest.raises(CacheDecodeError):
            decode_cache_value(lone_surrogate)
        # Also somewhere inside an otherwise well-formed-looking payload.
        with pytest.raises(CacheDecodeError):
            decode_cache_value('{"schema": 2, "type": "float", "value": 1.0, "junk": "\ud800"}')

    @pytest.mark.parametrize(
        "raw",
        [
            # Extra top-level envelope field.
            '{"schema": 2, "type": "float", "value": 1.0, "extra": "field"}',
            # Missing a required envelope field.
            '{"schema": 2, "type": "float"}',
            # Extra field inside a point_in_time_price payload.
            '{"schema": 2, "type": "point_in_time_price", "value": {"price": 1.0, "timestamp": "2024-01-01T00:00:00", "tz": null, "extra": 1}}',
            # Extra field inside a dataframe payload.
            (
                '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float64"], "data": [[1.0]], '
                '"index": {"kind": "string", "values": ["r"], "tz": null, "name": null}, '
                '"columns": {"kind": "string", "values": ["c"], "tz": null, "name": null}, "extra": 1}}'
            ),
            # Extra field inside an index/columns wire dict.
            (
                '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float64"], "data": [[1.0]], '
                '"index": {"kind": "string", "values": ["r"], "tz": null, "name": null, "extra": 1}, '
                '"columns": {"kind": "string", "values": ["c"], "tz": null, "name": null}}}'
            ),
            # Missing the (now required) "name" key on an index wire dict.
            (
                '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float64"], "data": [[1.0]], '
                '"index": {"kind": "string", "values": ["r"], "tz": null}, '
                '"columns": {"kind": "string", "values": ["c"], "tz": null, "name": null}}}'
            ),
            # Wrong type for an index "name" value.
            (
                '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float64"], "data": [[1.0]], '
                '"index": {"kind": "string", "values": ["r"], "tz": null, "name": 123}, '
                '"columns": {"kind": "string", "values": ["c"], "tz": null, "name": null}}}'
            ),
        ],
    )
    def test_extra_or_missing_envelope_fields_are_rejected(self, raw):
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)

    def test_dataframe_payload_with_duplicate_column_labels_is_rejected(self):
        """A crafted payload can claim duplicate column labels even though
        this module's own encoder refuses to ever produce one — rejected
        as defense in depth, not just relying on the encoder's own
        refusal."""
        raw = (
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float64", "int64"], '
            '"data": [[1.0, 2]], "index": {"kind": "string", "values": ["r"], "tz": null, "name": null}, '
            '"columns": {"kind": "string", "values": ["A", "A"], "tz": null, "name": null}}}'
        )
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)


# -- adversarial: numbers/values that could otherwise leak OverflowError
# -- (or another raw exception) past the CacheDecodeError boundary -------


HUGE_INTEGER = 10**400  # far beyond any float/numpy dtype's representable range


class TestDecodeNeverLeaksOverflowOrOtherRawExceptions:
    """
    A read-only adversarial probe demonstrated that `decode_cache_value`
    previously let `OverflowError: int too large to convert to float`
    escape for a schema-VALID JSON payload containing an enormous
    integer — contradicting the "malformed cache values always become
    CacheDecodeError, therefore a safe cache miss" guarantee. These
    tests cover all three payload families, plus the general safety-net
    boundary (`decode_cache_value` never raises anything but
    `CacheDecodeError`, and never swallows `KeyboardInterrupt`/
    `SystemExit`).
    """

    def test_huge_integer_in_float_payload_is_rejected_not_overflow_error(self):
        raw = f'{{"schema": 2, "type": "float", "value": {HUGE_INTEGER}}}'
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)

    def test_huge_integer_in_point_in_time_price_is_rejected_not_overflow_error(self):
        raw = (
            f'{{"schema": 2, "type": "point_in_time_price", '
            f'"value": {{"price": {HUGE_INTEGER}, "timestamp": "2024-01-01T00:00:00", "tz": null}}}}'
        )
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)

    def test_huge_integer_dataframe_cell_targeting_float64_is_rejected(self):
        raw = (
            f'{{"schema": 2, "type": "dataframe", "value": {{"dtypes": ["float64"], '
            f'"data": [[{HUGE_INTEGER}]], "index": {{"kind": "string", "values": ["r"], "tz": null, "name": null}}, '
            f'"columns": {{"kind": "string", "values": ["c"], "tz": null, "name": null}}}}}}'
        )
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)

    def test_huge_integer_dataframe_cell_targeting_int64_is_rejected(self):
        raw = (
            f'{{"schema": 2, "type": "dataframe", "value": {{"dtypes": ["int64"], '
            f'"data": [[{HUGE_INTEGER}]], "index": {{"kind": "string", "values": ["r"], "tz": null, "name": null}}, '
            f'"columns": {{"kind": "string", "values": ["c"], "tz": null, "name": null}}}}}}'
        )
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)

    def test_out_of_range_int32_cell_is_rejected(self):
        out_of_range = 2**40  # comfortably within int64 but outside int32
        raw = (
            f'{{"schema": 2, "type": "dataframe", "value": {{"dtypes": ["int32"], '
            f'"data": [[{out_of_range}]], "index": {{"kind": "string", "values": ["r"], "tz": null, "name": null}}, '
            f'"columns": {{"kind": "string", "values": ["c"], "tz": null, "name": null}}}}}}'
        )
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)

    def test_in_range_int32_cell_is_accepted(self):
        """Sanity check alongside the out-of-range test above: a
        genuinely in-range int32 value must still decode successfully —
        the fix must reject only what's actually out of range."""
        raw = (
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["int32"], '
            '"data": [[12345]], "index": {"kind": "string", "values": ["r"], "tz": null, "name": null}, '
            '"columns": {"kind": "string", "values": ["c"], "tz": null, "name": null}}}'
        )
        decoded = decode_cache_value(raw)
        assert int(decoded.iloc[0, 0]) == 12345
        assert str(decoded.dtypes.iloc[0]) == "int32"

    def test_out_of_range_magnitude_float32_cell_is_rejected(self):
        """Beyond float32's max magnitude (~3.4e38) but well within
        float64's (~1.8e308) — a naive numpy cast to float32 wouldn't
        raise, it would silently produce +/-inf, which this codec must
        reject rather than let through."""
        raw = (
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float32"], '
            '"data": [[1e300]], "index": {"kind": "string", "values": ["r"], "tz": null, "name": null}, '
            '"columns": {"kind": "string", "values": ["c"], "tz": null, "name": null}}}'
        )
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)

    def test_extreme_out_of_range_timestamp_in_point_in_time_price_is_rejected(self):
        raw = (
            '{"schema": 2, "type": "point_in_time_price", '
            '"value": {"price": 1.0, "timestamp": "99999-01-01T00:00:00", "tz": null}}'
        )
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)

    def test_extreme_out_of_range_timestamp_in_dataframe_datetime_index_is_rejected(self):
        raw = (
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float64"], "data": [[1.0]], '
            '"index": {"kind": "datetime", "values": ["99999-01-01T00:00:00"], "tz": null, "name": null}, '
            '"columns": {"kind": "string", "values": ["c"], "tz": null, "name": null}}}'
        )
        with pytest.raises(CacheDecodeError):
            decode_cache_value(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            f'{{"schema": 2, "type": "float", "value": {HUGE_INTEGER}}}',
            f'{{"schema": 2, "type": "float", "value": -{HUGE_INTEGER}}}',
            f'{{"schema": 2, "type": "point_in_time_price", "value": {{"price": {HUGE_INTEGER}, "timestamp": "2024-01-01T00:00:00", "tz": null}}}}',
            (
                f'{{"schema": 2, "type": "dataframe", "value": {{"dtypes": ["bool"], '
                f'"data": [[{HUGE_INTEGER}]], "index": {{"kind": "string", "values": ["r"], "tz": null, "name": null}}, '
                f'"columns": {{"kind": "string", "values": ["c"], "tz": null, "name": null}}}}}}'
            ),
        ],
    )
    def test_only_cache_decode_error_ever_escapes_for_adversarial_numeric_payloads(self, raw):
        """Broad sweep: for every adversarial numeric payload above, the
        ONLY exception type `decode_cache_value` may ever raise is
        `CacheDecodeError` — explicitly proving no `OverflowError` (or
        any other raw exception) reaches the caller."""
        try:
            decode_cache_value(raw)
        except CacheDecodeError:
            pass  # expected
        except Exception as exc:  # noqa: BLE001 - this IS the assertion under test
            pytest.fail(f"Expected only CacheDecodeError, but {type(exc).__name__} escaped: {exc}")

    def test_keyboard_interrupt_is_never_caught_by_the_decode_boundary(self, monkeypatch):
        """The safety-net `except Exception` in `decode_cache_value` must
        NOT catch `KeyboardInterrupt`/`SystemExit` — they are not
        `Exception` subclasses, but this proves the implementation
        doesn't accidentally widen its catch to `BaseException`."""

        def _boom(*args, **kwargs):
            raise KeyboardInterrupt("simulated interrupt during decode")

        monkeypatch.setattr(cache_module, "_finite_float_from_json_number", _boom)

        with pytest.raises(KeyboardInterrupt):
            decode_cache_value('{"schema": 2, "type": "float", "value": 1.0}')

    def test_system_exit_is_never_caught_by_the_decode_boundary(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise SystemExit(1)

        monkeypatch.setattr(cache_module, "_finite_float_from_json_number", _boom)

        with pytest.raises(SystemExit):
            decode_cache_value('{"schema": 2, "type": "float", "value": 1.0}')


def _dataframe_cell_payload(dtype: str, value) -> str:
    """Build a one-cell DataFrame envelope with `value` (a raw Python
    value, JSON-encoded as-is — `True`/`False` become JSON booleans,
    `int`/`float` become the matching JSON number shape) in a single
    column of dtype `dtype`."""
    import json

    return json.dumps(
        {
            "schema": 2,
            "type": "dataframe",
            "value": {
                "dtypes": [dtype],
                "data": [[value]],
                "index": {"kind": "string", "values": ["r"], "tz": None, "name": None},
                "columns": {"kind": "string", "values": ["c"], "tz": None, "name": None},
            },
        }
    )


class TestDataFrameCellsRejectNonCanonicalTypes:
    """
    Confirmed adversarial-review examples: the decoder previously
    accepted a JSON scalar type incompatible with a column's declared
    dtype, relying on NumPy/pandas's own permissive coercion (a JSON
    number truthily cast to a NumPy bool, a JSON boolean numerically
    cast to an int/float, a JSON integer losing precision when cast to
    float64) instead of enforcing the EXACT canonical shape this
    module's own encoder writes. `_assert_canonical_dataframe_cell` now
    rejects all of these as `CacheDecodeError` before `pd.DataFrame(...)`/
    `.astype()` ever runs.
    """

    # -- the five confirmed examples, verbatim -----------------------------

    def test_bool_dtype_rejects_json_number_two(self):
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("bool", 2))

    def test_bool_dtype_rejects_json_number_zero_point_five(self):
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("bool", 0.5))

    def test_int64_dtype_rejects_json_true(self):
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("int64", True))

    def test_float64_dtype_rejects_json_true(self):
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("float64", True))

    def test_float64_dtype_rejects_precision_losing_large_integer(self):
        """9007199254740993 (2**53 + 1) cannot be represented exactly as a
        float64 — casting it silently produces the DIFFERENT value
        9007199254740992.0. Must be rejected, not silently rounded."""
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("float64", 9007199254740993))

    # -- broader coverage of the same principle, both directions -----------

    def test_bool_dtype_rejects_json_number_zero_and_one_too(self):
        """Even the "looks equivalent" 0/1 must be rejected for a bool
        column — only a literal JSON boolean is canonical."""
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("bool", 0))
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("bool", 1))

    def test_int32_dtype_rejects_json_false(self):
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("int32", False))

    def test_float32_dtype_rejects_json_false(self):
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("float32", False))

    def test_int64_dtype_rejects_json_float_even_when_whole_valued(self):
        """A JSON float token (e.g. "5.0") must not be accepted for an int
        column just because it happens to have no fractional part —
        this encoder never writes a float-shaped token for an int column."""
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("int64", 5.0))

    def test_int64_dtype_rejects_null(self):
        """A non-nullable pandas int64 column can never hold NaN, so this
        encoder can never have written `null` for one — null must be
        rejected here, not silently accepted as some other 'valid' shape."""
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("int64", None))

    def test_bool_dtype_rejects_null(self):
        with pytest.raises(CacheDecodeError):
            decode_cache_value(_dataframe_cell_payload("bool", None))

    # -- valid boundary values must still be accepted -----------------------

    def test_bool_dtype_accepts_true_and_false(self):
        decoded_true = decode_cache_value(_dataframe_cell_payload("bool", True))
        decoded_false = decode_cache_value(_dataframe_cell_payload("bool", False))
        assert bool(decoded_true.iloc[0, 0]) is True
        assert bool(decoded_false.iloc[0, 0]) is False

    def test_int64_dtype_accepts_a_genuine_json_integer(self):
        decoded = decode_cache_value(_dataframe_cell_payload("int64", 42))
        assert int(decoded.iloc[0, 0]) == 42

    def test_float64_dtype_accepts_a_genuine_json_float(self):
        decoded = decode_cache_value(_dataframe_cell_payload("float64", 3.5))
        assert float(decoded.iloc[0, 0]) == pytest.approx(3.5)

    def test_float64_dtype_accepts_a_whole_valued_json_float(self):
        """The encoder's own canonical form for a whole-valued float64 cell
        (e.g. 5.0) is the JSON float token "5.0", not the integer "5" —
        this must still decode successfully."""
        decoded = decode_cache_value(_dataframe_cell_payload("float64", 5.0))
        assert float(decoded.iloc[0, 0]) == pytest.approx(5.0)
        assert str(decoded.dtypes.iloc[0]) == "float64"

    def test_float64_dtype_accepts_null_as_nan(self):
        decoded = decode_cache_value(_dataframe_cell_payload("float64", None))
        assert pd.isna(decoded.iloc[0, 0])

    def test_float64_dtype_accepts_the_largest_exactly_representable_integer(self):
        """2**53 (9007199254740992.0) — the largest integer float64 can
        represent exactly — encoded in its canonical float-token form,
        must round-trip exactly."""
        exact = float(2**53)
        decoded = decode_cache_value(_dataframe_cell_payload("float64", exact))
        assert float(decoded.iloc[0, 0]) == exact


class _FakeRedisClient:
    """Minimal in-memory stand-in for the Upstash Redis client's get/set surface."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):  # noqa: ARG002 - ttl not modeled
        self.store[key] = value


class TestCachedDecoratorSafePassthrough:
    def test_legacy_pickle_entry_is_a_miss_not_executed(self, monkeypatch):
        """A key holding an old-format pickle payload must be treated as a
        miss: the wrapped function still runs and its result is returned,
        never an exception, never a deserialized/executed pickle object."""
        client = _FakeRedisClient()
        monkeypatch.setattr(cache_module, "_get_redis_client", lambda: client)

        calls = []

        @cached(ttl_seconds=60, prefix="test_legacy")
        def compute(x):
            calls.append(x)
            return float(x) * 2

        cache_key = cache_module._build_cache_key("test_legacy", compute.__wrapped__, (5,), {})
        client.store[cache_key] = base64.b64encode(pickle.dumps("legacy-poison")).decode("ascii")

        result = compute(5)

        assert result == 10.0
        assert calls == [5]  # the wrapped function WAS called (cache miss), not a decoded pickle

    def test_corrupted_json_entry_is_a_miss(self, monkeypatch):
        client = _FakeRedisClient()
        monkeypatch.setattr(cache_module, "_get_redis_client", lambda: client)

        @cached(ttl_seconds=60, prefix="test_corrupt")
        def compute():
            return 3.0

        cache_key = cache_module._build_cache_key("test_corrupt", compute.__wrapped__, (), {})
        client.store[cache_key] = "{not valid json"

        assert compute() == 3.0

    def test_valid_entry_is_served_from_cache_without_calling_wrapped_function(self, monkeypatch):
        client = _FakeRedisClient()
        monkeypatch.setattr(cache_module, "_get_redis_client", lambda: client)

        calls = []

        @cached(ttl_seconds=60, prefix="test_hit")
        def compute():
            calls.append(1)
            return 9.5

        assert compute() == 9.5
        assert compute() == 9.5
        assert calls == [1]  # second call was a genuine cache hit

    def test_cache_keys_are_namespaced_with_current_version(self):
        assert cache_module.CACHE_KEY_VERSION == "v2"
        key = cache_module._build_cache_key("prefix", lambda: None, (), {})
        assert key.startswith("v2:prefix:")

    def test_none_result_is_never_cached(self, monkeypatch):
        client = _FakeRedisClient()
        monkeypatch.setattr(cache_module, "_get_redis_client", lambda: client)

        @cached(ttl_seconds=60, prefix="test_none")
        def compute():
            return None

        compute()
        assert client.store == {}

    def test_huge_integer_float_entry_is_a_miss_not_an_overflow_error(self, monkeypatch):
        """Decorator-level regression test (not just a direct
        `decode_cache_value` call): a stored entry containing a JSON
        integer far too large to convert to a float must be treated as a
        cache miss — the wrapped function still runs and its result is
        returned — never let `OverflowError` propagate out of a
        `@cached`-wrapped call."""
        client = _FakeRedisClient()
        monkeypatch.setattr(cache_module, "_get_redis_client", lambda: client)

        calls = []

        @cached(ttl_seconds=60, prefix="test_overflow_float")
        def compute(x):
            calls.append(x)
            return float(x) * 2.5

        cache_key = cache_module._build_cache_key("test_overflow_float", compute.__wrapped__, (4,), {})
        client.store[cache_key] = f'{{"schema": 2, "type": "float", "value": {10**400}}}'

        result = compute(4)

        assert result == 10.0
        assert calls == [4]  # wrapped function WAS called; no exception escaped

    def test_huge_integer_dataframe_cell_entry_is_a_miss_not_an_overflow_error(self, monkeypatch):
        client = _FakeRedisClient()
        monkeypatch.setattr(cache_module, "_get_redis_client", lambda: client)

        calls = []

        @cached(ttl_seconds=60, prefix="test_overflow_df")
        def compute():
            calls.append(1)
            return pd.DataFrame({"Close": [1.0, 2.0]}, index=["a", "b"])

        cache_key = cache_module._build_cache_key("test_overflow_df", compute.__wrapped__, (), {})
        client.store[cache_key] = (
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float64"], '
            f'"data": [[{10**400}], [2.0]], '
            '"index": {"kind": "string", "values": ["a", "b"], "tz": null, "name": null}, '
            '"columns": {"kind": "string", "values": ["Close"], "tz": null, "name": null}}}'
        )

        result = compute()

        assert calls == [1]  # wrapped function WAS called; no exception escaped
        assert list(result["Close"]) == [1.0, 2.0]

    # -- decorator-level cache-miss tests for the confirmed non-canonical-
    # -- type examples (item 2 of the correction pass) ----------------------

    def test_bool_dtype_with_json_number_entry_is_a_miss(self, monkeypatch):
        """A stored entry with a JSON number `2` in a declared `bool`
        column (previously silently accepted as `True` via NumPy's own
        truthy cast) must be treated as a cache miss."""
        client = _FakeRedisClient()
        monkeypatch.setattr(cache_module, "_get_redis_client", lambda: client)

        calls = []

        @cached(ttl_seconds=60, prefix="test_bool_noncanonical")
        def compute():
            calls.append(1)
            return pd.DataFrame({"IsUp": [True, False]}, index=["a", "b"])

        cache_key = cache_module._build_cache_key("test_bool_noncanonical", compute.__wrapped__, (), {})
        client.store[cache_key] = (
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["bool"], '
            '"data": [[2], [0]], '
            '"index": {"kind": "string", "values": ["a", "b"], "tz": null, "name": null}, '
            '"columns": {"kind": "string", "values": ["IsUp"], "tz": null, "name": null}}}'
        )

        result = compute()

        assert calls == [1]  # cache miss -> wrapped function ran
        assert list(result["IsUp"]) == [True, False]

    def test_int_dtype_with_json_boolean_entry_is_a_miss(self, monkeypatch):
        """A stored entry with a JSON boolean `true` in a declared `int64`
        column (previously silently accepted as `1` via NumPy's own
        numeric cast) must be treated as a cache miss."""
        client = _FakeRedisClient()
        monkeypatch.setattr(cache_module, "_get_redis_client", lambda: client)

        calls = []

        @cached(ttl_seconds=60, prefix="test_int_noncanonical")
        def compute():
            calls.append(1)
            df = pd.DataFrame({"Volume": [100, 200]}, index=["a", "b"])
            df["Volume"] = df["Volume"].astype("int64")
            return df

        cache_key = cache_module._build_cache_key("test_int_noncanonical", compute.__wrapped__, (), {})
        client.store[cache_key] = (
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["int64"], '
            '"data": [[true], [200]], '
            '"index": {"kind": "string", "values": ["a", "b"], "tz": null, "name": null}, '
            '"columns": {"kind": "string", "values": ["Volume"], "tz": null, "name": null}}}'
        )

        result = compute()

        assert calls == [1]  # cache miss -> wrapped function ran
        assert list(result["Volume"]) == [100, 200]

    def test_precision_losing_large_integer_in_float64_entry_is_a_miss(self, monkeypatch):
        """A stored entry with the JSON integer 9007199254740993 in a
        declared `float64` column (previously silently rounded to the
        DIFFERENT value 9007199254740992.0) must be treated as a cache
        miss, not a silently-wrong cache hit."""
        client = _FakeRedisClient()
        monkeypatch.setattr(cache_module, "_get_redis_client", lambda: client)

        calls = []

        @cached(ttl_seconds=60, prefix="test_precision_noncanonical")
        def compute():
            calls.append(1)
            return pd.DataFrame({"Value": [1.5]}, index=["a"])

        cache_key = cache_module._build_cache_key("test_precision_noncanonical", compute.__wrapped__, (), {})
        client.store[cache_key] = (
            '{"schema": 2, "type": "dataframe", "value": {"dtypes": ["float64"], '
            '"data": [[9007199254740993]], '
            '"index": {"kind": "string", "values": ["a"], "tz": null, "name": null}, '
            '"columns": {"kind": "string", "values": ["Value"], "tz": null, "name": null}}}'
        )

        result = compute()

        assert calls == [1]  # cache miss -> wrapped function ran (never the silently-wrong cached value)
        assert list(result["Value"]) == [1.5]
