from datetime import date, datetime, timezone
from types import SimpleNamespace

import pandas as pd

import src.valuation_input as valuation_input_module
from src.dcf_model.dcf import DCFAssumptions
from src.fundamentals.issuer_manifest import (
    ISSUER_MANIFEST_VERSION,
    SEC_ISSUER_MANIFEST_V1,
    issuer_policy_for,
)
from src.valuation_input import (
    ValuationInput,
    ValuationInputIssue,
    ValuationInputIssueCode,
    ValuationInputLoader,
    ValuationInputProvenance,
    ValuationInputResult,
    ValuationInputSource,
    SecValuationInputAdapter,
    YahooValuationInputAdapter,
)


UTC = timezone.utc
KNOWLEDGE_CUTOFF = datetime(2024, 9, 3, 20, 0, tzinfo=UTC)
DATA_VINTAGE_CUTOFF = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _complete(source: ValuationInputSource, reason: str) -> ValuationInputResult:
    return ValuationInputResult(
        requested_source=source,
        valuation_input=ValuationInput(
            ticker="AAPL",
            financial_data={"ticker": "AAPL"},
            provenance=ValuationInputProvenance(
                source=source,
                source_selection_reason=reason,
                knowledge_cutoff=KNOWLEDGE_CUTOFF,
                data_vintage_cutoff=DATA_VINTAGE_CUTOFF,
                statement_period_start=None,
                statement_period_end=date(2024, 6, 30),
                policy_version="test-policy-v1",
                source_adapter=f"{source.value}_test",
            ),
        ),
    )


class RecordingAdapter:
    def __init__(self, source: ValuationInputSource, *, refuse: bool = False):
        self.source = source
        self.refuse = refuse
        self.calls = []

    def load(self, ticker, knowledge_cutoff, data_vintage_cutoff, policy, reason):
        self.calls.append((ticker, knowledge_cutoff, data_vintage_cutoff, policy, reason))
        if self.refuse:
            return ValuationInputResult(
                requested_source=self.source,
                issues=(
                    ValuationInputIssue(
                        code=ValuationInputIssueCode.SEC_SNAPSHOT_INCOMPLETE,
                        message="SEC snapshot is incomplete.",
                    ),
                ),
            )
        return _complete(self.source, reason)


# Issuers whose SEC backfill passed read-only verification at both cutoffs.
VERIFIED_BACKFILL_BATCHES = {
    "MSFT": "backfill-789019-35777629947-1",
    "WMT": "backfill-104169-35778664157-1",
}


def test_manifest_contains_the_exact_pilot_universe_without_implying_cutover():
    assert ISSUER_MANIFEST_VERSION == "sec-issuer-manifest-v1"
    assert {policy.ticker for policy in SEC_ISSUER_MANIFEST_V1} == {
        "AAPL",
        "MSFT",
        "WMT",
        "CAT",
    }
    assert all(not policy.sec_live_approved for policy in SEC_ISSUER_MANIFEST_V1)
    assert issuer_policy_for(" aapl ").sec_history_ready is True
    assert issuer_policy_for("AAPL").concept_map_version == "sec-companyfacts-v2"
    for ticker in ("MSFT", "WMT"):
        policy = issuer_policy_for(ticker)
        assert policy.fiscal_calendar_version is not None
        assert policy.concept_map_version == "sec-companyfacts-v3"
        assert policy.supplemental_source_adapters == ()
        assert policy.sec_history_ready is True
        assert VERIFIED_BACKFILL_BATCHES[ticker] in policy.readiness_reason
    # Caterpillar's v5 policy (filing-XBRL term debt, derived net income)
    # needs its own verified backfill.
    cat = issuer_policy_for("CAT")
    assert cat.concept_map_version == "sec-companyfacts-v5"
    assert cat.supplemental_source_adapters == ("sec_filing_xbrl",)
    assert cat.sec_history_ready is False
    assert "v5 backfill" in cat.readiness_reason


def test_auto_reports_yahoo_and_the_reason_sec_was_not_used():
    yahoo = RecordingAdapter(ValuationInputSource.YAHOO)
    sec = RecordingAdapter(ValuationInputSource.SEC)
    result = ValuationInputLoader(yahoo, sec).load(
        "AAPL", KNOWLEDGE_CUTOFF, DATA_VINTAGE_CUTOFF, "auto"
    )

    assert result.is_complete
    assert result.requested_source is ValuationInputSource.AUTO
    assert result.valuation_input.provenance.source is ValuationInputSource.YAHOO
    assert (
        "shadow-cutover evidence gate"
        in result.valuation_input.provenance.source_selection_reason
    )
    assert len(yahoo.calls) == 1
    assert sec.calls == []


def test_explicit_sec_refusal_never_calls_yahoo_fallback():
    yahoo = RecordingAdapter(ValuationInputSource.YAHOO)
    sec = RecordingAdapter(ValuationInputSource.SEC, refuse=True)
    result = ValuationInputLoader(yahoo, sec).load(
        "AAPL", KNOWLEDGE_CUTOFF, DATA_VINTAGE_CUTOFF, "sec"
    )

    assert not result.is_complete
    assert result.requested_source is ValuationInputSource.SEC
    assert result.issues[0].code is ValuationInputIssueCode.SEC_SNAPSHOT_INCOMPLETE
    assert len(sec.calls) == 1
    assert yahoo.calls == []


def test_auto_does_not_try_sec_for_an_unapproved_issuer():
    yahoo = RecordingAdapter(ValuationInputSource.YAHOO)
    sec = RecordingAdapter(ValuationInputSource.SEC)
    result = ValuationInputLoader(yahoo, sec).load(
        "MSFT", KNOWLEDGE_CUTOFF, DATA_VINTAGE_CUTOFF, "auto"
    )

    assert result.is_complete
    assert result.valuation_input.provenance.source is ValuationInputSource.YAHOO
    assert (
        issuer_policy_for("MSFT").readiness_reason
        in result.valuation_input.provenance.source_selection_reason
    )
    assert sec.calls == []


def test_auto_never_falls_back_after_an_approved_sec_policy_is_selected(monkeypatch):
    approved = SimpleNamespace(
        sec_live_approved=True,
        readiness_reason="Approved for the test.",
    )
    monkeypatch.setattr(
        valuation_input_module,
        "issuer_policy_for",
        lambda _ticker: approved,
    )
    yahoo = RecordingAdapter(ValuationInputSource.YAHOO)
    sec = RecordingAdapter(ValuationInputSource.SEC, refuse=True)

    result = ValuationInputLoader(yahoo, sec).load(
        "AAPL", KNOWLEDGE_CUTOFF, DATA_VINTAGE_CUTOFF, "auto"
    )

    assert not result.is_complete
    assert result.requested_source is ValuationInputSource.AUTO
    assert len(sec.calls) == 1
    assert yahoo.calls == []


def test_yahoo_adapter_records_latest_statement_period_and_policy_reason():
    columns = [pd.Timestamp("2023-12-31"), pd.Timestamp("2024-06-30")]
    statement = pd.DataFrame({column: {"Total Revenue": 1.0} for column in columns})
    adapter = YahooValuationInputAdapter(
        lambda ticker: {
            "ticker": ticker,
            "income_statement": statement,
            "balance_sheet": statement,
            "cash_flow": statement,
            "current_price": 100.0,
            "shares_outstanding": 10.0,
            "beta": 1.0,
            "sector": "Technology",
        }
    )

    result = adapter.load(
        "AAPL",
        KNOWLEDGE_CUTOFF,
        DATA_VINTAGE_CUTOFF,
        issuer_policy_for("AAPL"),
        "Yahoo was selected for a test.",
    )

    assert result.is_complete
    provenance = result.valuation_input.provenance
    assert provenance.statement_period_end == date(2024, 6, 30)
    assert provenance.source_selection_reason == "Yahoo was selected for a test."
    assert provenance.ingestion_batch_ids == ()


def test_data_vintage_may_precede_knowledge_cutoff():
    provenance = ValuationInputProvenance(
        source=ValuationInputSource.SEC,
        source_selection_reason="Reproducing the stored dataset available at the vintage cutoff.",
        knowledge_cutoff=DATA_VINTAGE_CUTOFF,
        data_vintage_cutoff=KNOWLEDGE_CUTOFF,
        statement_period_start=date(2023, 7, 1),
        statement_period_end=date(2024, 6, 30),
        policy_version="sec-policy-v1",
        source_adapter="sec_companyfacts",
    )

    assert provenance.data_vintage_cutoff < provenance.knowledge_cutoff


def test_sec_adapter_emits_statement_policy_batch_and_filing_provenance(monkeypatch):
    captured_requests = []
    facts = (
        SimpleNamespace(
            lineage=SimpleNamespace(ingestion_batch_id="batch-b"),
            provenance=SimpleNamespace(accession_number="accession-b"),
        ),
        SimpleNamespace(
            lineage=SimpleNamespace(ingestion_batch_id="batch-a"),
            provenance=SimpleNamespace(accession_number="accession-a"),
        ),
        SimpleNamespace(
            lineage=SimpleNamespace(ingestion_batch_id="batch-a"),
            provenance=SimpleNamespace(accession_number="accession-a"),
        ),
    )
    snapshot = SimpleNamespace(
        source_facts=facts,
        latest=SimpleNamespace(
            period_start=date(2023, 7, 1),
            period_end=date(2024, 6, 30),
        ),
    )
    prepared = SimpleNamespace(
        snapshot=snapshot,
        policy=SimpleNamespace(version="sec-dcf-composition-v1"),
    )
    monkeypatch.setattr(
        valuation_input_module,
        "load_valuation_fundamentals_snapshot",
        lambda _repository, request: captured_requests.append(request)
        or SimpleNamespace(is_complete=True, snapshot=snapshot),
    )
    monkeypatch.setattr(
        valuation_input_module,
        "prepare_sec_dcf_inputs",
        lambda *_args: SimpleNamespace(is_complete=True, prepared=prepared),
    )
    monkeypatch.setattr(
        valuation_input_module,
        "build_sec_dcf_financial_data",
        lambda _prepared: {"ticker": "AAPL"},
    )
    monkeypatch.setattr(
        valuation_input_module,
        "build_sec_dcf_assumptions",
        lambda _prepared: DCFAssumptions(),
    )

    result = SecValuationInputAdapter(
        repository=SimpleNamespace(),
        market_observation_loader=lambda *_args: SimpleNamespace(),
    ).load(
        "AAPL",
        KNOWLEDGE_CUTOFF,
        DATA_VINTAGE_CUTOFF,
        issuer_policy_for("AAPL"),
        "SEC was explicitly required.",
    )

    assert result.is_complete
    assert captured_requests[0].knowledge_cutoff == KNOWLEDGE_CUTOFF
    provenance = result.valuation_input.provenance
    assert provenance.source is ValuationInputSource.SEC
    assert provenance.policy_version == "sec-dcf-composition-v1"
    assert provenance.statement_period_end == date(2024, 6, 30)
    assert provenance.ingestion_batch_ids == ("batch-a", "batch-b")
    assert provenance.filing_accessions == ("accession-a", "accession-b")
