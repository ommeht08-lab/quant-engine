"""Filed Microsoft balance table: complete itemization and refusal cases."""

from decimal import Decimal
import gzip
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.fundamentals import msft_opening_balance as opening

FIXTURE = Path(__file__).parents[1] / "fixtures/fundamentals/msft-2024-opening-balance.xhtml"


def _read(monkeypatch, document):
    monkeypatch.setattr(opening, "DOCUMENT_SHA256", hashlib.sha256(document).hexdigest())
    return opening.read_microsoft_opening(document)


def test_every_reported_section_reconciles_without_a_plug(monkeypatch):
    opening_sheet = _read(monkeypatch, FIXTURE.read_bytes())
    assert len(opening_sheet.lines) == 33
    assert opening_sheet.value("ShortTermInvestments") == Decimal(57228)
    assert opening_sheet.value("CommercialPaper") == Decimal(6693)
    assert opening_sheet.value("AccumulatedOtherComprehensiveIncomeLossNetOfTax") == Decimal(-5590)
    assert opening_sheet.totals["assets"] == Decimal(512163)
    assert opening_sheet.totals["liabilities"] + opening_sheet.totals["equity"] == Decimal(512163)
    assert all(item.source_url == opening.SOURCE_URL and item.accession == opening.ACCESSION for item in opening_sheet.lines.values())


def test_changed_filing_hash_refuses(monkeypatch):
    original = FIXTURE.read_bytes()
    monkeypatch.setattr(opening, "DOCUMENT_SHA256", hashlib.sha256(original).hexdigest())
    with pytest.raises(opening.OpeningBalanceRefusal, match="hash differs"):
        opening.read_microsoft_opening(original.replace(b"57,228", b"57,229", 1))


def test_changed_reported_value_cannot_be_plugged(monkeypatch):
    document = FIXTURE.read_bytes().replace(b"57,228", b"57,229", 1)
    with pytest.raises(opening.OpeningBalanceRefusal, match="current_assets does not reconcile"):
        _read(monkeypatch, document)


def test_missing_line_refuses_even_when_subtotals_remain(monkeypatch):
    document = FIXTURE.read_bytes().replace(b"us-gaap:Goodwill", b"us-gaap:Unreviewed", 1)
    with pytest.raises(opening.OpeningBalanceRefusal, match="Unreported opening lines"):
        _read(monkeypatch, document)


def test_stored_fact_comparison_survives_partial_map_improvement(monkeypatch):
    document = FIXTURE.read_bytes()
    source = _read(monkeypatch, document)
    lines = {
        concept: SimpleNamespace(value=source.value(tag) * Decimal(1_000_000))
        for concept, tag in opening.STORED_CONCEPTS.items()
    }
    stored = SimpleNamespace(
        request=SimpleNamespace(cik=opening.MSFT_CIK, period_end=opening.PERIOD_END),
        forecast_itemization_gaps=("noncurrent_assets", "current_liabilities"),
        line=lambda concept: lines.get(concept),
    )
    assert len(opening.read_microsoft_opening(document, stored_snapshot=stored).stored_matches) == 13
    lines["total_assets"] = SimpleNamespace(value=Decimal(1))
    with pytest.raises(opening.OpeningBalanceRefusal, match="does not match"):
        opening.read_microsoft_opening(document, stored_snapshot=stored)


def test_verified_sec_fetch_decodes_gzip_and_refuses_changed_document(monkeypatch):
    document = FIXTURE.read_bytes()
    monkeypatch.setattr(opening, "DOCUMENT_SHA256", hashlib.sha256(document).hexdigest())

    class Response:
        headers = {"Content-Encoding": "gzip"}

        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.body

    def fake_open(request, *, timeout):
        assert request.full_url == opening.SOURCE_URL
        assert request.get_header("User-agent") == "Valuation Engine reviewer@example.com"
        assert timeout == 30
        return Response(gzip.compress(document))

    monkeypatch.setattr(opening, "urlopen", fake_open)
    assert opening.fetch_microsoft_opening_document("Valuation Engine reviewer@example.com") == document

    monkeypatch.setattr(opening, "urlopen", lambda *_args, **_kwargs: Response(gzip.compress(document + b"changed")))
    with pytest.raises(opening.OpeningBalanceRefusal, match="hash differs"):
        opening.fetch_microsoft_opening_document("Valuation Engine reviewer@example.com")


def test_verified_opening_evidence_is_immutable(monkeypatch):
    filed = _read(monkeypatch, FIXTURE.read_bytes())
    with pytest.raises(TypeError):
        filed.lines["Goodwill"] = filed.lines["Goodwill"]
