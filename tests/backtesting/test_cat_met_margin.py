"""CAT pilot segment-margin evidence and fail-closed cutoff tests."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.backtesting.cat_met_margin import (
    CAT_PILOT_MET_EVIDENCE,
    EVIDENCE_CAPTURED_AT,
    CatMetMarginRefusal,
    pilot_margin_pair,
)


DECISION_CUTOFF = datetime(2024, 9, 3, 20, tzinfo=timezone.utc)
PILOT_VINTAGE = EVIDENCE_CAPTURED_AT + timedelta(days=1)


def _pair(**kwargs):
    return pilot_margin_pair(
        kwargs.pop("knowledge_cutoff", DECISION_CUTOFF),
        kwargs.pop("data_vintage_cutoff", PILOT_VINTAGE),
        kwargs.pop("latest_end", date(2024, 6, 30)),
        kwargs.pop("prior_end", date(2023, 6, 30)),
        **kwargs,
    )


def test_exact_segment_ttm_margins_and_filing_lineage():
    result = _pair()
    assert result.current == Decimal(21605) / Decimal(63025)
    assert result.prior == Decimal(18803) / Decimal(61793)
    assert result.current > result.prior
    assert result.filing_accessions == (
        "0000018230-23-000044", "0000018230-24-000009", "0000018230-24-000042"
    )
    assert len(result.exhibit_urls) == 3


def test_filing_not_public_before_acceptance_is_refused():
    latest_acceptance = max(item.accepted_at for item in CAT_PILOT_MET_EVIDENCE)
    with pytest.raises(CatMetMarginRefusal, match="not public"):
        _pair(knowledge_cutoff=latest_acceptance - timedelta(microseconds=1))
    assert _pair(knowledge_cutoff=latest_acceptance).current > 0


def test_evidence_not_available_before_recorded_capture_is_refused():
    with pytest.raises(CatMetMarginRefusal, match="data-vintage cutoff"):
        _pair(data_vintage_cutoff=EVIDENCE_CAPTURED_AT - timedelta(microseconds=1))


def test_other_formation_period_refuses_instead_of_reusing_pilot_values():
    with pytest.raises(CatMetMarginRefusal, match="does not cover"):
        _pair(latest_end=date(2024, 9, 30), prior_end=date(2023, 9, 30))


def test_missing_or_duplicate_required_period_refuses():
    with pytest.raises(CatMetMarginRefusal, match="missing"):
        _pair(evidence=CAT_PILOT_MET_EVIDENCE[:-1])
    with pytest.raises(CatMetMarginRefusal, match="Duplicate"):
        _pair(evidence=CAT_PILOT_MET_EVIDENCE + (CAT_PILOT_MET_EVIDENCE[0],))


def test_invalid_period_values_and_naive_cutoffs_refuse():
    bad = (replace(CAT_PILOT_MET_EVIDENCE[0], sales_millions=Decimal(0)),) + CAT_PILOT_MET_EVIDENCE[1:]
    with pytest.raises(CatMetMarginRefusal, match="differs"):
        _pair(evidence=bad)
    with pytest.raises(CatMetMarginRefusal, match="timezone-aware"):
        _pair(knowledge_cutoff=datetime(2024, 9, 3, 20))


def test_changed_filing_identity_or_value_cannot_silently_replace_frozen_evidence():
    changed = replace(CAT_PILOT_MET_EVIDENCE[0], accession="0000000000-00-000000")
    with pytest.raises(CatMetMarginRefusal, match="differs"):
        _pair(evidence=(changed,) + CAT_PILOT_MET_EVIDENCE[1:])
