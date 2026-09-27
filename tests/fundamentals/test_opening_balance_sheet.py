"""Opening balance sheet: reported values only, point-in-time, fail-closed."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.fundamentals.adapters.fixture import make_fact, make_lineage, make_period, make_provenance
from src.fundamentals.issuer_manifest import issuer_policy_for
from src.fundamentals.opening_balance_sheet import (
    OPENING_BALANCE_SHEET_POLICY_VERSION,
    OpeningBalanceSheetIssueCode as Code,
    OpeningBalanceSheetRequest,
    build_opening_balance_sheet,
)
from src.fundamentals.repository import InMemoryFundamentalsRepository
from src.fundamentals.types import StatementKind

UTC = timezone.utc
CIK = "0001111111"
PERIOD_END = date(2024, 6, 30)
ACCEPTED = datetime(2024, 8, 1, 20, tzinfo=UTC)
INGESTED = datetime(2024, 8, 2, tzinfo=UTC)
CUTOFF = datetime(2024, 9, 3, 20, tzinfo=UTC)
VINTAGE = datetime(2024, 9, 4, tzinfo=UTC)
LINEAGE = dict(source_adapter="sec_companyfacts", concept_map_version="map-v1", fiscal_calendar_version="cal-v1")

# A small, fully itemized sheet: 10 + 20 + 30 = 60 current assets, 40 PP&E;
# 15 + 5 = 20 current liabilities, 30 long-term debt; equity 50.
BALANCED = {
    "cash_and_cash_equivalents": 10, "accounts_receivable": 20, "inventory": 30, "current_assets": 60,
    "property_plant_and_equipment_net": 40, "total_assets": 100,
    "accounts_payable": 15, "current_debt": 5, "current_liabilities": 20, "long_term_debt": 30,
    "total_liabilities": 50, "total_equity": 50,
}


def _fact(concept, value, *, cik=CIK, period_end=PERIOD_END, accession="0001111111-24-000010", accepted=ACCEPTED,
          form="10-Q", ingested=INGESTED, unit="USD", currency="USD", raw_tag=None, lineage=None):
    return make_fact(
        statement_kind=StatementKind.BALANCE_SHEET,
        concept=concept,
        period=make_period(fiscal_year=2024, fiscal_period="Q2", period_end=period_end, periodicity="quarterly"),
        value=value,
        provenance=make_provenance(
            accession_number=accession, filed_date=accepted.date(), form_type=form,
            is_amendment=form.endswith("/A"), accepted_at=accepted,
        ),
        unit=unit,
        currency=currency,
        entity_cik=cik,
        raw_tag=raw_tag,
        lineage=lineage or make_lineage(ingestion_batch_id="batch-1", ingested_at=ingested, **LINEAGE),
    )


def _facts(values=BALANCED, **kwargs):
    return tuple(_fact(concept, value, **kwargs) for concept, value in values.items())


def _request(**overrides):
    fields = dict(cik=CIK, period_end=PERIOD_END, knowledge_cutoff=CUTOFF, data_vintage_cutoff=VINTAGE,
                  source_adapter="sec_companyfacts", concept_map_version="map-v1", fiscal_calendar_version="cal-v1")
    fields.update(overrides)
    return OpeningBalanceSheetRequest(**fields)


def _build(facts, **overrides):
    return build_opening_balance_sheet(InMemoryFundamentalsRepository(facts), _request(**overrides))


def _refusal(result):
    assert not result.is_complete
    return result.issues[0]


def test_balanced_sheet_carries_every_line_with_its_provenance():
    result = _build(_facts())

    snapshot = result.snapshot
    assert snapshot.policy_version == OPENING_BALANCE_SHEET_POLICY_VERSION
    assert snapshot.equity_concept == "total_equity"
    assert snapshot.value("total_assets") == snapshot.value("total_liabilities") + snapshot.total_equity
    assert snapshot.is_fully_itemized and snapshot.forecast_itemization_gaps == ()
    assert {line.concept for line in snapshot.lines} == set(BALANCED)
    line = snapshot.line("total_assets")
    assert (line.accession_number, line.accepted_at, line.ingestion_batch_id, line.ingested_at) == (
        "0001111111-24-000010", ACCEPTED, "batch-1", INGESTED,
    )
    assert (line.source_adapter, line.concept_map_version, line.fiscal_calendar_version) == (
        "sec_companyfacts", "map-v1", "cal-v1",
    )


def test_independent_example_apple_june_29_2024_balances_on_parent_equity():
    # Apple's Q3 FY2024 10-Q (0000320193-24-000081, accepted 2024-08-01
    # 18:03:34 ET), USD millions. Checked by hand: 125,435 + 206,177 non-current
    # = 331,612 assets; 131,624 + 133,280 non-current = 264,904 liabilities;
    # 264,904 + 66,708 shareholders' equity = 331,612. Only parent equity is
    # supplied here, so the snapshot must balance on it.
    accepted = datetime(2024, 8, 1, 22, 3, 34, tzinfo=UTC)
    apple = {
        "cash_and_cash_equivalents": 25_565, "accounts_receivable": 22_795, "inventory": 6_165,
        "current_assets": 125_435, "property_plant_and_equipment_net": 44_502, "total_assets": 331_612,
        "accounts_payable": 47_574, "current_debt": 12_114, "current_liabilities": 131_624,
        "long_term_debt": 86_196, "total_liabilities": 264_904, "retained_earnings": -4_726,
        "shareholders_equity": 66_708,
    }
    facts = tuple(
        _fact(concept, Decimal(value) * 1_000_000, cik="0000320193", period_end=date(2024, 6, 29),
              accession="0000320193-24-000081", accepted=accepted)
        for concept, value in apple.items()
    )

    snapshot = _build(facts, cik="0000320193", period_end=date(2024, 6, 29)).snapshot

    assert snapshot.equity_concept == "shareholders_equity"
    assert snapshot.total_equity == Decimal(66_708_000_000)
    assert snapshot.value("total_assets") - snapshot.value("total_liabilities") == snapshot.total_equity
    assert snapshot.line("total_assets").accepted_at == accepted
    # Marketable securities, other assets, deferred revenue, commercial paper,
    # and other lines are not mapped: every section has an itemization gap.
    assert snapshot.forecast_itemization_gaps == (
        "current_assets", "noncurrent_assets", "current_liabilities", "noncurrent_liabilities",
    )


@pytest.mark.parametrize(
    "missing", ["total_assets", "current_assets", "cash_and_cash_equivalents", "total_liabilities", "current_liabilities"]
)
def test_a_missing_required_line_refuses_and_names_it(missing):
    issue = _refusal(_build(tuple(f for f in _facts() if f.identity.concept != missing)))
    assert issue.code is Code.MISSING_COMPONENT and issue.concepts == (missing,)


def test_missing_equity_refuses_rather_than_deriving_it():
    issue = _refusal(_build(tuple(f for f in _facts() if f.identity.concept != "total_equity")))
    assert issue.code is Code.MISSING_COMPONENT and issue.concepts == ("total_equity",)


def test_imbalance_refuses_with_the_difference():
    issue = _refusal(_build(_facts({**BALANCED, "total_liabilities": 51})))
    assert issue.code is Code.IMBALANCE and "by -1" in issue.message


def test_parent_equity_without_the_noncontrolling_interest_refuses_instead_of_plugging():
    # Total equity 50 includes a 4 noncontrolling interest the issuer does not
    # report separately; parent equity alone (46) cannot balance the sheet.
    values = {k: v for k, v in BALANCED.items() if k != "total_equity"} | {"shareholders_equity": 46}
    issue = _refusal(_build(_facts(values)))
    assert issue.code is Code.IMBALANCE


def test_reported_total_equity_is_preferred_over_parent_equity():
    snapshot = _build(_facts({**BALANCED, "shareholders_equity": 46})).snapshot
    assert snapshot.equity_concept == "total_equity" and snapshot.total_equity == 50


def test_a_value_in_another_unit_or_currency_refuses():
    facts = _facts() + (_fact("inventory", 30, unit="EUR", currency="EUR", raw_tag="InventoryEur"),)
    assert _refusal(_build(facts)).code is Code.MIXED_UNITS
    with pytest.raises(ValueError, match="Only USD"):
        _request(currency="EUR")


def test_a_line_reported_only_at_an_earlier_date_is_never_carried_forward():
    facts = tuple(f for f in _facts() if f.identity.concept != "current_liabilities") + (
        _fact("current_liabilities", 20, period_end=date(2024, 3, 31)),
    )
    issue = _refusal(_build(facts))
    assert issue.code is Code.MISSING_COMPONENT and issue.concepts == ("current_liabilities",)


def test_stale_or_future_balance_sheet_dates_refuse():
    assert _refusal(_build(_facts(), knowledge_cutoff=datetime(2025, 3, 1, tzinfo=UTC))).code is Code.STALE_PERIOD
    assert _refusal(_build(_facts(), period_end=date(2024, 9, 30))).code is Code.INVALID_REQUEST
    assert _refusal(_build(_facts(), period_end=date(2024, 3, 31))).code is Code.MISSING_PERIOD


def test_subtotals_that_cannot_hold_refuse():
    issue = _refusal(_build(_facts({**BALANCED, "inventory": 31, "current_assets": 60})))
    assert issue.code is Code.INCONSISTENT_SUBTOTAL


def test_contradictory_values_in_one_filing_refuse():
    facts = _facts() + (_fact("total_assets", 101, raw_tag="AssetsDuplicate"),)
    issue = _refusal(_build(facts))
    assert issue.code is Code.CONFLICTING_VALUE and issue.concepts == ("total_assets",)


# --------------------------------------------------------------------------
# Amendments and cutoffs
# --------------------------------------------------------------------------

AMENDED_AT = datetime(2024, 8, 20, 14, tzinfo=UTC)


def _amendment(values):
    return tuple(
        _fact(concept, value, accession="0001111111-24-000020", accepted=AMENDED_AT, form="10-Q/A",
              ingested=AMENDED_AT + timedelta(hours=1))
        for concept, value in values.items()
    )


def test_an_amendment_counts_only_from_its_acceptance():
    # The 10-Q/A restates liabilities and equity consistently (60 / 40).
    facts = _facts() + _amendment({"total_liabilities": 60, "total_equity": 40})

    before = _build(facts, knowledge_cutoff=AMENDED_AT - timedelta(microseconds=1)).snapshot
    assert before.value("total_liabilities") == 50 and before.line("total_liabilities").form_type == "10-Q"

    at = _build(facts, knowledge_cutoff=AMENDED_AT).snapshot
    assert (at.value("total_liabilities"), at.total_equity) == (60, 40)
    assert at.line("total_liabilities").accession_number == "0001111111-24-000020"
    assert at.line("total_assets").accession_number == "0001111111-24-000010"


def test_a_partial_amendment_that_breaks_the_identity_refuses_after_acceptance():
    facts = _facts() + _amendment({"total_liabilities": 60})
    assert _build(facts, knowledge_cutoff=AMENDED_AT - timedelta(microseconds=1)).is_complete
    assert _refusal(_build(facts, knowledge_cutoff=AMENDED_AT)).code is Code.IMBALANCE


def test_facts_ingested_after_the_data_vintage_cutoff_are_invisible():
    facts = _facts() + _amendment({"total_liabilities": 60, "total_equity": 40})
    ingested_at = AMENDED_AT + timedelta(hours=1)
    # Accepted by the cutoff, but not yet ingested at this vintage: the
    # original filing is used, never the later-ingested amendment.
    snapshot = _build(facts, knowledge_cutoff=CUTOFF, data_vintage_cutoff=ingested_at - timedelta(microseconds=1)).snapshot
    assert snapshot.value("total_liabilities") == 50
    assert _build(facts, knowledge_cutoff=CUTOFF, data_vintage_cutoff=ingested_at).snapshot.total_equity == 40
    # Nothing ingested yet: refuse.
    assert _refusal(_build(_facts(), data_vintage_cutoff=INGESTED - timedelta(microseconds=1))).code is Code.MISSING_PERIOD


class _LeakyRepository:
    def __init__(self, facts):
        self._facts = tuple(facts)

    def get_facts(self, query):
        return self._facts


def test_a_repository_that_leaks_a_later_fact_refuses():
    facts = _facts() + _amendment({"total_liabilities": 60, "total_equity": 40})
    result = build_opening_balance_sheet(_LeakyRepository(facts), _request(knowledge_cutoff=AMENDED_AT - timedelta(days=1)))
    assert _refusal(result).code is Code.CUTOFF_VIOLATION
    wrong_source = tuple(replace(f, lineage=replace(f.lineage, source_adapter="yahoo")) for f in _facts())
    assert _refusal(build_opening_balance_sheet(_LeakyRepository(wrong_source), _request())).code is Code.OUT_OF_SCOPE_FACT


def test_request_from_the_issuer_manifest_uses_its_source_policy():
    policy = issuer_policy_for("CAT")
    request = OpeningBalanceSheetRequest.from_policy(
        policy, period_end=date(2024, 6, 30), knowledge_cutoff=CUTOFF, data_vintage_cutoff=VINTAGE
    )
    assert (request.cik, request.concept_map_version, request.fiscal_calendar_version) == (
        "0000018230", policy.concept_map_version, policy.fiscal_calendar_version,
    )
    assert request.supplemental_source_adapters == policy.supplemental_source_adapters


# --------------------------------------------------------------------------
# Itemization: a missing line is missing evidence, never a zero
# --------------------------------------------------------------------------

# Cash plus the required totals; every other section is zero. Absent lines
# summed as zero would make each section "itemized" without evidence.
CASH_ONLY = {
    "cash_and_cash_equivalents": 60, "current_assets": 60, "total_assets": 60,
    "current_liabilities": 0, "total_liabilities": 0, "total_equity": 60,
}
EXPLICIT_ZEROS = {
    "accounts_receivable": 0, "inventory": 0, "property_plant_and_equipment_net": 0,
    "accounts_payable": 0, "current_debt": 0, "long_term_debt": 0,
}


def test_absent_components_are_itemization_gaps_not_zeros():
    snapshot = _build(_facts(CASH_ONLY)).snapshot

    assert not snapshot.is_fully_itemized
    assert snapshot.forecast_itemization_gaps == (
        "current_assets", "noncurrent_assets", "current_liabilities", "noncurrent_liabilities",
    )
    assert snapshot.unreported_itemization_lines == (
        "accounts_receivable", "inventory", "property_plant_and_equipment_net",
        "accounts_payable", "current_debt", "long_term_debt",
    )


def test_explicitly_reported_zeros_itemize_a_section():
    snapshot = _build(_facts(CASH_ONLY | EXPLICIT_ZEROS)).snapshot
    assert snapshot.is_fully_itemized and snapshot.unreported_itemization_lines == ()


def test_one_absent_component_is_a_gap_even_when_the_rest_sum_to_the_section():
    # Current debt is unreported and payables alone equal current liabilities.
    values = {k: v for k, v in BALANCED.items() if k != "current_debt"} | {
        "current_liabilities": 15, "total_liabilities": 45, "total_equity": 55,
    }
    snapshot = _build(_facts(values)).snapshot
    assert snapshot.forecast_itemization_gaps == ("current_liabilities",)
    assert snapshot.unreported_itemization_lines == ("current_debt",)


# --------------------------------------------------------------------------
# Cash, cash equivalents, and restricted cash
# --------------------------------------------------------------------------

# Current assets hold no room for restricted cash (10 + 20 + 30 = 60); the
# noncurrent section has 10 beyond PP&E (100 - 60 - 30).
ROOM_FOR_NONCURRENT_RESTRICTED_CASH = {**BALANCED, "property_plant_and_equipment_net": 30}
ZERO_CASH = {**BALANCED, "cash_and_cash_equivalents": 0, "accounts_receivable": 30}


@pytest.mark.parametrize(
    "values, combined",
    [
        (BALANCED, 10),  # equals cash; cash + receivables + inventory + PP&E = total assets
        (ROOM_FOR_NONCURRENT_RESTRICTED_CASH, 20),  # restricted cash that can only be noncurrent
        (ZERO_CASH, 0),
    ],
)
def test_a_consistent_combined_cash_balance_is_kept(values, combined):
    snapshot = _build(_facts({**values, "cash_and_restricted_cash": combined})).snapshot
    assert snapshot.value("cash_and_restricted_cash") == combined


@pytest.mark.parametrize(
    "values, combined, reason",
    [
        (BALANCED, -5, "negative"),  # the reported review case
        (ZERO_CASH, -1, "negative"),
        (BALANCED, 9, "below cash and cash equivalents"),
        (BALANCED, 11, "exceed total assets"),
        (ROOM_FOR_NONCURRENT_RESTRICTED_CASH, 21, "exceed total assets"),
    ],
)
def test_an_impossible_combined_cash_balance_refuses(values, combined, reason):
    issue = _refusal(_build(_facts({**values, "cash_and_restricted_cash": combined})))
    assert issue.code is Code.INCONSISTENT_SUBTOTAL and reason in issue.message


# --------------------------------------------------------------------------
# Conflicts: superseded by a clean later filing vs unresolved
# --------------------------------------------------------------------------

CORRECTION_INGESTED = AMENDED_AT + timedelta(hours=1)


def _conflicted_original_then_clean_correction():
    # The original 10-Q reports total assets as both 100 and 101; the 10-Q/A
    # reports 100 under one tag.
    return _facts() + (_fact("total_assets", 101, raw_tag="AssetsDuplicate"),) + _amendment({"total_assets": 100})


@pytest.mark.parametrize(
    "knowledge_cutoff, data_vintage_cutoff, corrected",
    [
        (AMENDED_AT - timedelta(microseconds=1), VINTAGE, False),
        (AMENDED_AT, VINTAGE, True),
        (CUTOFF, CORRECTION_INGESTED - timedelta(microseconds=1), False),
        (CUTOFF, CORRECTION_INGESTED, True),
    ],
)
def test_a_conflict_blocks_only_until_a_clean_correction_is_accepted_and_ingested(
    knowledge_cutoff, data_vintage_cutoff, corrected
):
    result = _build(
        _conflicted_original_then_clean_correction(),
        knowledge_cutoff=knowledge_cutoff,
        data_vintage_cutoff=data_vintage_cutoff,
    )

    if not corrected:
        issue = _refusal(result)
        assert issue.code is Code.CONFLICTING_VALUE and issue.concepts == ("total_assets",)
        return
    snapshot = result.snapshot
    assert snapshot.value("total_assets") == 100
    assert snapshot.line("total_assets").accession_number == "0001111111-24-000020"
    (superseded,) = snapshot.superseded_conflicts
    assert (superseded.concept, superseded.accession_number, superseded.superseded_by) == (
        "total_assets", "0001111111-24-000010", "0001111111-24-000020",
    )
    assert sorted(superseded.values) == [Decimal(100), Decimal(101)]


def test_a_conflicted_correction_is_never_replaced_by_the_older_clean_value():
    facts = _facts() + _amendment({"total_assets": 100}) + (
        _fact("total_assets", 101, accession="0001111111-24-000020", accepted=AMENDED_AT, form="10-Q/A",
              ingested=CORRECTION_INGESTED, raw_tag="AssetsDuplicate"),
    )
    before = _build(facts, knowledge_cutoff=AMENDED_AT - timedelta(microseconds=1)).snapshot
    assert before.value("total_assets") == 100 and before.superseded_conflicts == ()

    issue = _refusal(_build(facts, knowledge_cutoff=AMENDED_AT))
    assert issue.code is Code.CONFLICTING_VALUE and issue.concepts == ("total_assets",)
    assert "0001111111-24-000020" in issue.message
