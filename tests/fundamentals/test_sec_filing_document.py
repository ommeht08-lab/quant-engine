"""Reading CAT's ME&T supplemental tables from filing HTML, fail-closed."""

from datetime import date
from decimal import Decimal

import pytest

from src.fundamentals.adapters.sec_filing_document import (
    SOURCE_ADAPTER,
    FilingDocumentError,
    FilingDocumentIssueCode,
    parse_supplemental_tables,
)
from src.fundamentals.segment_gross_margin import (
    CAT_MET_SUPPLEMENTAL_RULE,
    SEGMENT_COST_OF_GOODS_SOLD,
    SEGMENT_SALES,
)
from tests.fundamentals.segment_margin_fixtures import (
    FY2023_10K,
    Q2_2023_10Q,
    Q2_2024_10Q,
    document,
    facts_for,
    reference,
)

MILLION = Decimal(1_000_000)
RULE = CAT_MET_SUPPLEMENTAL_RULE


def _values(observations):
    return {(item.concept, item.period_start, item.period_end): item.value / MILLION for item in observations}


def test_real_10k_excerpt_yields_three_fiscal_years_of_met_sales_and_cost():
    values = _values(parse_supplemental_tables(document(FY2023_10K), RULE))

    assert values == {
        (SEGMENT_SALES, date(2021, 1, 1), date(2021, 12, 31)): 48188,
        (SEGMENT_SALES, date(2022, 1, 1), date(2022, 12, 31)): 56574,
        (SEGMENT_SALES, date(2023, 1, 1), date(2023, 12, 31)): 63869,
        # ME&T column, not the consolidated 42,767 / 41,350 / 35,513.
        (SEGMENT_COST_OF_GOODS_SOLD, date(2021, 1, 1), date(2021, 12, 31)): 35521,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2022, 1, 1), date(2022, 12, 31)): 41356,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2023, 1, 1), date(2023, 12, 31)): 42776,
    }


def test_real_10q_excerpts_yield_six_month_values_despite_footnote_cells():
    q2_2023 = _values(parse_supplemental_tables(document(Q2_2023_10Q), RULE))
    q2_2024 = _values(parse_supplemental_tables(document(Q2_2024_10Q), RULE))

    assert q2_2023 == {
        (SEGMENT_SALES, date(2022, 1, 1), date(2022, 6, 30)): 26425,
        (SEGMENT_SALES, date(2023, 1, 1), date(2023, 6, 30)): 31644,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2022, 1, 1), date(2022, 6, 30)): 19538,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2023, 1, 1), date(2023, 6, 30)): 21172,
    }
    assert q2_2024 == {
        (SEGMENT_SALES, date(2023, 1, 1), date(2023, 6, 30)): 31644,
        (SEGMENT_SALES, date(2024, 1, 1), date(2024, 6, 30)): 30800,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2023, 1, 1), date(2023, 6, 30)): 21172,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30)): 19816,
    }


def test_facts_are_classified_dimensioned_and_carry_filing_and_document_lineage():
    facts = facts_for(Q2_2024_10Q)
    h1_2024 = next(
        fact for fact in facts
        if fact.identity.concept == SEGMENT_COST_OF_GOODS_SOLD and fact.identity.period_end == date(2024, 6, 30)
    )

    assert h1_2024.value == Decimal(19_816_000_000)
    assert (h1_2024.period.fiscal_year, h1_2024.period.fiscal_period, h1_2024.period.periodicity) == (2024, "Q2YTD", "ytd")
    assert h1_2024.identity.context.dimensions == (RULE.dimension,)
    assert h1_2024.provenance.accession_number == "0000018230-24-000045"
    assert h1_2024.provenance.accepted_at == Q2_2024_10Q.accepted_at
    assert h1_2024.lineage.source_adapter == SOURCE_ADAPTER
    assert h1_2024.lineage.concept_map_version == RULE.version
    assert h1_2024.lineage.source_document_url == Q2_2024_10Q.document_url
    assert "sha256=" in h1_2024.raw_tag and "column=Machinery, Energy & Transportation" in h1_2024.raw_tag
    annual = [fact for fact in facts_for(FY2023_10K) if fact.identity.period_end == date(2023, 12, 31)]
    assert {fact.period.fiscal_period for fact in annual} == {"FY"}


def test_periods_before_the_issuer_calendar_are_out_of_scope():
    # The FY2023 10-K also reports FY2021, which the calendar (from FY2020)
    # covers; nothing earlier than the calendar's first year is emitted.
    ends = {fact.identity.period_end for fact in facts_for(FY2023_10K)}
    assert min(ends) >= date(2020, 12, 31)


def test_an_earnings_release_8k_is_not_a_classifiable_periodic_filing():
    press_release = reference(
        "0000018230-24-000042", "8-K", date(2024, 8, 6), Q2_2024_10Q.accepted_at, date(2024, 8, 6), "ex991.htm"
    )
    with pytest.raises(FilingDocumentError) as error:
        facts_for(press_release, source=Q2_2024_10Q)
    assert error.value.code is FilingDocumentIssueCode.CLASSIFICATION


# --------------------------------------------------------------------------
# Synthetic layouts: every ambiguity refuses
# --------------------------------------------------------------------------

GROUPS = "<tr><td></td><td>Consolidated</td><td>Machinery, Energy &amp; Transportation</td><td>Financial Products</td><td>Consolidating Adjustments</td></tr>"


def _row(label, *cells, label_size=9):
    tds = "".join(f"<td>{cell}</td>" for cell in cells)
    return f'<tr><td><span style="font-size:{label_size}pt">{label}</span></td>{tds}</tr>'


def _table(*rows, title="For the Six Months Ended June 30, 2024", scale="(Millions of dollars)", heading=True):
    head = f"<div>Supplemental Data for Results of Operations</div><div>{title}</div><div>{scale}</div>" if heading else ""
    return head + "<table>" + GROUPS + "".join(rows) + "</table>"


SALES = _row("Sales of Machinery, Energy &amp; Transportation", "$", "100", "$", "100", "$", "—", "$", "—")
COST = _row(
    "Cost of goods sold", "61", "62", "—", "(1)",
    '<span style="font-size:6pt">2</span>',  # footnote marker, not a value
)


def _doc(*tables):
    return ("<html><body>" + "".join(tables) + "</body></html>").encode()


def test_synthetic_table_reads_segment_column_and_skips_footnote_markers():
    values = _values(parse_supplemental_tables(_doc(_table(SALES, COST)), RULE))
    assert values == {
        (SEGMENT_SALES, date(2024, 1, 1), date(2024, 6, 30)): 100,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30)): 62,
    }


@pytest.mark.parametrize(
    "document_bytes, code",
    [
        # Consolidated 61 != 63 + 0 - 1: a shifted cell cannot pass.
        (_doc(_table(SALES, _row("Cost of goods sold", "61", "63", "—", "(1)"))), FilingDocumentIssueCode.INCONSISTENT_TOTAL),
        (_doc(_table(SALES)), FilingDocumentIssueCode.MISSING_ROW),
        (_doc(_table(SALES, COST, COST)), FilingDocumentIssueCode.DUPLICATE_ROW),
        (_doc(_table(SALES, _row("Cost of goods sold", "61", "62", "n/a", "(1)"))), FilingDocumentIssueCode.MALFORMED_VALUE),
        (_doc(_table(SALES, _row("Cost of goods sold", "61", "62", "(1)"))), FilingDocumentIssueCode.UNSUPPORTED_LAYOUT),
        (_doc(_table(SALES, COST, scale="(Thousands of dollars)")), FilingDocumentIssueCode.UNSUPPORTED_LAYOUT),
        (_doc(_table(SALES, COST, title="For the Period")), FilingDocumentIssueCode.UNSUPPORTED_LAYOUT),
        (
            _doc(_table(SALES, COST, title="For the Six Months Ended June 30, 2024 and Years Ended December 31")),
            FilingDocumentIssueCode.UNSUPPORTED_LAYOUT,
        ),
    ],
)
def test_ambiguous_or_malformed_tables_refuse(document_bytes, code):
    with pytest.raises(FilingDocumentError) as error:
        parse_supplemental_tables(document_bytes, RULE)
    assert error.value.code is code


def test_changed_column_groups_refuse_instead_of_guessing_the_segment_column():
    reordered = _table(SALES, COST).replace(
        "<td>Consolidated</td><td>Machinery, Energy &amp; Transportation</td>",
        "<td>Machinery, Energy &amp; Transportation</td><td>Consolidated</td>",
    )
    with pytest.raises(FilingDocumentError) as error:
        parse_supplemental_tables(_doc(reordered), RULE)
    assert error.value.code is FilingDocumentIssueCode.UNSUPPORTED_LAYOUT


def test_one_period_reported_twice_in_a_document_must_agree():
    changed = _row("Cost of goods sold", "60", "61", "—", "(1)")
    with pytest.raises(FilingDocumentError) as error:
        parse_supplemental_tables(_doc(_table(SALES, COST), _table(SALES, changed)), RULE)
    assert error.value.code is FilingDocumentIssueCode.CONFLICTING_OBSERVATION
    assert len(parse_supplemental_tables(_doc(_table(SALES, COST), _table(SALES, COST)), RULE)) == 2


def test_an_untitled_table_never_borrows_a_neighbours_title():
    untitled = _table(SALES, _row("Cost of goods sold", "1", "1", "—", "—"), heading=False)
    values = _values(parse_supplemental_tables(_doc(_table(SALES, COST), untitled), RULE))
    assert values[(SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30))] == 62


def test_a_document_without_the_table_yields_nothing():
    assert parse_supplemental_tables(b"<html><body><p>Part III only</p></body></html>", RULE) == ()
