"""Reading CAT's ME&T supplemental tables from filing HTML, fail-closed."""

from datetime import date
from decimal import Decimal

import pytest

from src.fundamentals.adapters.sec_filing_document import (
    SOURCE_ADAPTER,
    FilingDocumentError,
    FilingDocumentIssueCode,
    contains_rule_rows,
    parse_supplemental_tables,
)
from src.fundamentals.segment_gross_margin import (
    CAT_MET_SUPPLEMENTAL_RULE,
    SEGMENT_COST_OF_GOODS_SOLD,
    SEGMENT_SALES,
)
from tests.fundamentals.segment_margin_fixtures import (
    FY2023_10K,
    Q1_2020_10Q,
    Q2_2020_10Q,
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


SALES = _row("Sales of Machinery, Energy &amp; Transportation", "$", "10,000", "$", "10,000", "$", "—", "$", "—")
COST = _row(
    "Cost of goods sold", "6,100", "6,101", "—", "(1)",
    '<span style="font-size:6pt">2</span>',  # footnote marker, not a value
)


def _doc(*tables):
    return ("<html><body>" + "".join(tables) + "</body></html>").encode()


def test_synthetic_table_reads_segment_column_and_skips_footnote_markers():
    values = _values(parse_supplemental_tables(_doc(_table(SALES, COST)), RULE))
    assert values == {
        (SEGMENT_SALES, date(2024, 1, 1), date(2024, 6, 30)): 10000,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30)): 6101,
    }


@pytest.mark.parametrize(
    "document_bytes, code",
    [
        # Consolidated 6,100 != 6,102 + 0 - 1.
        (_doc(_table(SALES, _row("Cost of goods sold", "6,100", "6,102", "—", "(1)"))), FilingDocumentIssueCode.INCONSISTENT_TOTAL),
        (_doc(_table(SALES)), FilingDocumentIssueCode.MISSING_ROW),
        (_doc(_table(SALES, COST, COST)), FilingDocumentIssueCode.DUPLICATE_ROW),
        (_doc(_table(SALES, _row("Cost of goods sold", "6,100", "6,101", "n/a", "(1)"))), FilingDocumentIssueCode.MALFORMED_VALUE),
        (_doc(_table(SALES, _row("Cost of goods sold", "6,100", "6,101", "(1)"))), FilingDocumentIssueCode.UNSUPPORTED_LAYOUT),
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
    changed = _row("Cost of goods sold", "6,099", "6,100", "—", "(1)")
    with pytest.raises(FilingDocumentError) as error:
        parse_supplemental_tables(_doc(_table(SALES, COST), _table(SALES, changed)), RULE)
    assert error.value.code is FilingDocumentIssueCode.CONFLICTING_OBSERVATION
    assert len(parse_supplemental_tables(_doc(_table(SALES, COST), _table(SALES, COST)), RULE)) == 2


def test_an_untitled_table_never_borrows_a_neighbours_title():
    untitled = _table(SALES, _row("Cost of goods sold", "1", "1", "—", "—"), heading=False)
    values = _values(parse_supplemental_tables(_doc(_table(SALES, COST), untitled), RULE))
    assert values[(SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30))] == 6101


def test_a_document_without_the_table_yields_nothing():
    assert parse_supplemental_tables(b"<html><body><p>Part III only</p></body></html>", RULE) == ()


# --------------------------------------------------------------------------
# Row invariants: a value in the wrong column refuses even when it adds up
# --------------------------------------------------------------------------


def test_met_cost_shifted_into_financial_products_refuses_instead_of_reading_zero():
    # The review's case: the ME&T value sits in the Financial Products column,
    # 6,100 = 0 + 6,101 + (1) still adds up, and the old parser read ME&T
    # cost of goods sold as 0.
    shifted = _row("Cost of goods sold", "6,100", "—", "6,101", "(1)")
    with pytest.raises(FilingDocumentError) as error:
        parse_supplemental_tables(_doc(_table(SALES, shifted)), RULE)
    assert error.value.code is FilingDocumentIssueCode.UNEXPECTED_VALUE
    assert "never reports this line is non-zero" in str(error.value)


@pytest.mark.parametrize(
    "row, message",
    [
        # ME&T value shifted into the adjustments column.
        (_row("Cost of goods sold", "6,100", "—", "—", "6,100"), "segment value is not positive"),
        # ME&T sales shifted into Financial Products.
        (_row("Sales of Machinery, Energy &amp; Transportation", "10,000", "—", "10,000", "—"), "never reports"),
        # An adjustment far larger than any real elimination (1% of total).
        (_row("Cost of goods sold", "6,100", "6,200", "—", "(100)"), "adjustment exceeds"),
        (_row("Cost of goods sold", "—", "—", "—", "—"), "not positive"),
    ],
)
def test_other_values_in_implausible_columns_refuse(row, message):
    rows = (row, COST) if "Sales" in row else (SALES, row)
    with pytest.raises(FilingDocumentError) as error:
        parse_supplemental_tables(_doc(_table(*rows)), RULE)
    assert error.value.code is FilingDocumentIssueCode.UNEXPECTED_VALUE
    assert message in str(error.value)


def test_a_rule_must_declare_one_invariant_per_row():
    from dataclasses import replace

    with pytest.raises(ValueError, match="one RowInvariant per row"):
        replace(RULE, invariants=RULE.invariants[:1])
    with pytest.raises(ValueError, match="zero_groups"):
        replace(RULE, invariants=(
            replace(RULE.invariants[0], zero_groups=("Consolidated",)), RULE.invariants[1],
        ))


def test_row_label_tripwire_sees_rows_under_an_unrecognized_title():
    renamed = document(Q2_2024_10Q).replace(b"Results of Operations", b"Results of Segments")
    assert parse_supplemental_tables(renamed, RULE) == ()
    assert contains_rule_rows(renamed, RULE)
    assert not contains_rule_rows(b"<html><body><table><tr><td>Part III</td></tr></table></body></html>", RULE)


# --------------------------------------------------------------------------
# CAT's 2020 10-Qs: footnoted column header, negatives split across cells
# --------------------------------------------------------------------------


def test_q1_2020_10q_reads_a_footnoted_header_and_a_split_negative():
    # The ME&T header reads "Machinery, Energy & Transportation" plus a
    # superscript 1; the Q1 2020 consolidating adjustment prints "(1" and ")"
    # in adjacent cells (7,266 = 7,267 + 0 - 1).
    assert _values(parse_supplemental_tables(document(Q1_2020_10Q), RULE)) == {
        (SEGMENT_SALES, date(2019, 1, 1), date(2019, 3, 31)): 12724,
        (SEGMENT_SALES, date(2020, 1, 1), date(2020, 3, 31)): 9914,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2019, 1, 1), date(2019, 3, 31)): 9003,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2020, 1, 1), date(2020, 3, 31)): 7267,
    }


def test_q2_2020_10q_reads_three_and_six_month_tables():
    assert _values(parse_supplemental_tables(document(Q2_2020_10Q), RULE)) == {
        (SEGMENT_SALES, date(2019, 4, 1), date(2019, 6, 30)): 13671,
        (SEGMENT_SALES, date(2019, 1, 1), date(2019, 6, 30)): 26395,
        (SEGMENT_SALES, date(2020, 4, 1), date(2020, 6, 30)): 9310,
        (SEGMENT_SALES, date(2020, 1, 1), date(2020, 6, 30)): 19224,
        # Split negatives (2) and (1) in the adjustments column.
        (SEGMENT_COST_OF_GOODS_SOLD, date(2019, 4, 1), date(2019, 6, 30)): 9943,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2019, 1, 1), date(2019, 6, 30)): 18946,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2020, 4, 1), date(2020, 6, 30)): 7114,
        (SEGMENT_COST_OF_GOODS_SOLD, date(2020, 1, 1), date(2020, 6, 30)): 14381,
    }


def test_2020_facts_start_at_the_calendar_and_classify_quarter_and_ytd():
    q1 = {(f.identity.concept, f.period.fiscal_period, f.identity.period_end) for f in facts_for(Q1_2020_10Q)}
    assert q1 == {
        (SEGMENT_SALES, "Q1", date(2020, 3, 31)),
        (SEGMENT_COST_OF_GOODS_SOLD, "Q1", date(2020, 3, 31)),
    }
    q2 = {(f.identity.concept, f.period.fiscal_period, f.identity.period_start) for f in facts_for(Q2_2020_10Q)}
    assert q2 == {
        (concept, period, start)
        for concept in (SEGMENT_SALES, SEGMENT_COST_OF_GOODS_SOLD)
        for period, start in (("Q2", date(2020, 4, 1)), ("Q2YTD", date(2020, 1, 1)))
    }


# --------------------------------------------------------------------------
# Superscript detection: markup, not cell alignment
# --------------------------------------------------------------------------

TOP = ' style="vertical-align:top"'


def _raw_row(label, *cells, label_style=TOP):
    tds = "".join(cells)
    return f'<tr><td{label_style}><span style="font-size:10pt">{label}</span></td>{tds}</tr>'


def _td(content, style=TOP):
    return f'<td{style}><span style="font-size:10pt">{content}</span></td>'


def _cost_row(*cells):
    return _raw_row("Cost of goods sold", *cells)


# An ordinary adjustment of 9 in a top-aligned cell: a digit-only value that
# the old rule (any vertical-align:top is superscript) dropped as a footnote.
TOP_ALIGNED_COST = _cost_row(_td("6,110"), _td("6,101"), _td("—"), _td("9"))


def test_top_aligned_numeric_values_are_values_not_footnotes():
    top_sales = _raw_row(
        "Sales of Machinery, Energy &amp; Transportation", _td("$"), _td("10,000"), _td("$"), _td("10,000"),
        _td("$"), _td("—"), _td("$"), _td("—"),
    )
    values = _values(parse_supplemental_tables(_doc(_table(top_sales, TOP_ALIGNED_COST)), RULE))
    assert values[(SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30))] == 6101
    assert values[(SEGMENT_SALES, date(2024, 1, 1), date(2024, 6, 30))] == 10000
    # Inline top alignment alone is not superscript either.
    inline_top = _cost_row(_td("6,110"), _td("6,101"), _td("—"), _td('<span style="vertical-align:top">9</span>'))
    assert _values(parse_supplemental_tables(_doc(_table(SALES, inline_top)), RULE))[
        (SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30))
    ] == 6101


@pytest.mark.parametrize(
    "marker",
    [
        '<sup style="vertical-align:top;font-size:7pt">3</sup>',  # CAT 2020 (EDGAR)
        '<span style="font-size:10pt;vertical-align:super">3</span>',  # explicit superscript, same size
        '<span style="font-size:10pt;position:relative;top:-2.8pt;vertical-align:baseline">3</span>',  # raised
        '<span style="font-size:5.2pt">3</span>',  # CAT 2021+ (Workiva): smaller font only
    ],
    ids=["sup-tag", "vertical-align-super", "raised-offset", "smaller-font"],
)
def test_actual_footnote_markers_are_skipped(marker):
    row = _cost_row(_td("6,110"), _td("6,101"), _td("—"), _td("9"), f"<td{TOP}>{marker}</td>")
    assert _values(parse_supplemental_tables(_doc(_table(SALES, row)), RULE))[
        (SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30))
    ] == 6101


def test_a_trailing_superscript_marker_inside_a_value_cell_is_removed():
    row = _cost_row(_td("6,110"), _td("6,101<sup>2</sup>"), _td("—"), _td("9"))
    assert _values(parse_supplemental_tables(_doc(_table(SALES, row)), RULE))[
        (SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30))
    ] == 6101


def _with_met_header(header):
    return _table(SALES, COST).replace("<td>Machinery, Energy &amp; Transportation</td>", f"<td>{header}</td>")


def test_a_trailing_numeric_footnote_on_the_segment_header_is_accepted():
    for header in (
        "Machinery, Energy &amp; Transportation <sup>1</sup>",
        'Machinery,<br/>Energy &amp;<br/>Transportation <span style="position:relative;top:-3pt">12</span>',
    ):
        assert _values(parse_supplemental_tables(_doc(_with_met_header(header)), RULE))[
            (SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30))
        ] == 6101


@pytest.mark.parametrize(
    "header",
    [
        "Machinery, Energy &amp; Transportation 1",  # a plain-text 1 is not a marker
        "Machinery, Energy &amp; <sup>1</sup>Transportation",  # marker mid-label
        "Machinery, Energy &amp; Transportation <sup>a</sup>",  # not a footnote number
        "Machinery, Energy &amp; Transportation <sup>123</sup>",
        "<sup>1</sup>",  # nothing but a marker
    ],
)
def test_unfamiliar_header_markers_refuse(header):
    with pytest.raises(FilingDocumentError) as error:
        parse_supplemental_tables(_doc(_with_met_header(header)), RULE)
    assert error.value.code is FilingDocumentIssueCode.UNSUPPORTED_LAYOUT


def test_a_negative_split_across_adjacent_cells_is_joined():
    row = _cost_row(_td("6,100"), _td("6,101"), _td("—"), _td("(1"), _td(")"), f"<td{TOP}><sup>3</sup></td>")
    assert _values(parse_supplemental_tables(_doc(_table(SALES, row)), RULE))[
        (SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30))
    ] == 6101


@pytest.mark.parametrize(
    "cells",
    [
        (_td("6,100"), _td("6,101"), _td("—"), _td("(1")),  # never closed
        (_td("6,100"), _td("6,101"), _td("—"), _td("(1"), _td(""), _td(")")),  # closed, but not adjacent
        (_td("6,100"), _td("6,101"), _td("—"), _td("(1"), _td(")3")),  # closing cell carries more
        (_td("6,100"), _td("6,101"), _td(")"), _td("—"), _td("(1)")),  # stray close
        (_td("6,100"), _td("6,<sup>1</sup>101"), _td("—"), _td("(1)")),  # superscript inside a value
        (_td("6,100"), _td("6,101<sup>x</sup>"), _td("—"), _td("(1)")),  # non-numeric trailing marker
    ],
    ids=["unclosed", "not-adjacent", "closing-cell-not-bare", "stray-close", "inner-superscript", "letter-marker"],
)
def test_malformed_value_layouts_refuse(cells):
    with pytest.raises(FilingDocumentError) as error:
        parse_supplemental_tables(_doc(_table(SALES, _cost_row(*cells))), RULE)
    assert error.value.code is FilingDocumentIssueCode.MALFORMED_VALUE
