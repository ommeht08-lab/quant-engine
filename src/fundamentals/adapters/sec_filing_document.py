"""Segment line items read from the text of an SEC periodic filing.

Some required amounts are printed in a filing but never tagged in XBRL. For
example, Caterpillar tags Machinery, Energy & Transportation (ME&T) sales in
XBRL (``us-gaap:Revenues`` with ``cat:MachineryEnergyTransportationMember``),
but the ME&T cost of goods sold in its 10-K and 10-Q "Supplemental Data for
Results of Operations" tables is untagged, so neither Company Facts nor the
filing's XBRL instance can supply it. This adapter reads such a table from
the filing's primary HTML document under an explicit, issuer-scoped
``SupplementalTableRule`` and refuses anything ambiguous:

* a table qualifies only if its own title (inside the table, or in the text
  between it and the previous table) names the rule's title;
* the column groups must equal the rule's groups exactly and in order;
* each period comes from the table's title ("For the Six Months Ended June
  30, 2024") or, for annual tables, a header row repeating the same fiscal
  years once per column group; a table with neither, or both, refuses;
* the scale label must be present;
* every required row must appear exactly once per qualifying table, and
  every value cell must parse ("(9)" is -9, a dash is zero); footnote
  markers are recognized by their smaller font or superscript styling;
* in every column the total group must equal the sum of the other groups;
* every row must satisfy the rule's declared ``RowInvariant`` (for example,
  the segment value is positive and a group that never reports this line is
  zero), because the sum alone cannot tell which column a value sits in: a
  segment value shifted into a neighbouring group still adds up;
* the same row and period reported twice in one document must agree.

Each fact keeps its filing provenance: accession, form, amendment flag,
acceptance time, document URL, and a ``raw_tag`` naming the rule version,
the document's SHA-256, table, row, and column it was read from.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from html import unescape
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from ..concept_map import FactPeriodType
from ..fiscal_calendar import IssuerFiscalCalendarPolicy, classify_sec_facts
from ..time_policy import is_aware
from ..types import FactContext, FactLineage, FinancialFact, StatementKind, normalize_cik
from .sec_companyfacts import SecExtractedFact

SOURCE_ADAPTER = "sec_filing_document"
TAXONOMY = "sec-filing-document"
_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ),
        start=1,
    )
}
_DURATION_MONTHS = {"three": 3, "six": 6, "nine": 9}
_INTERIM_TITLE = re.compile(r"(three|six|nine)monthsended([a-z]+)(\d{1,2}),(\d{4})")
_ANNUAL_TITLE = re.compile(r"yearsended([a-z]+)(\d{1,2})")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_NUMBER = re.compile(r"^(\()?\$?(\d{1,3}(?:,\d{3})*|\d+)(\))?$")
_DASHES = frozenset(("—", "–", "-", "−"))
_VOID_TAGS = frozenset(("br", "img", "hr", "meta", "link", "input", "col", "wbr", "area", "base", "source"))
_BLOCK_TAGS = frozenset(("p", "div", "br", "tr", "table", "li", "h1", "h2", "h3", "h4", "h5", "h6"))
_FONT_SIZE = re.compile(r"font-size:\s*([\d.]+)\s*pt", re.IGNORECASE)
_SUPERSCRIPT = re.compile(r"vertical-align:\s*(top|super)", re.IGNORECASE)


class FilingDocumentIssueCode(str, Enum):
    TABLE_NOT_FOUND = "table_not_found"
    UNSUPPORTED_LAYOUT = "unsupported_layout"
    MALFORMED_VALUE = "malformed_value"
    MISSING_ROW = "missing_row"
    DUPLICATE_ROW = "duplicate_row"
    INCONSISTENT_TOTAL = "inconsistent_total"
    UNEXPECTED_VALUE = "unexpected_value"
    CONFLICTING_OBSERVATION = "conflicting_observation"
    CLASSIFICATION = "classification"


class FilingDocumentError(ValueError):
    def __init__(self, code: FilingDocumentIssueCode, message: str):
        super().__init__(message)
        self.code = code


def _normalized(text: str) -> str:
    """Case- and whitespace-insensitive form used for every label match."""

    return re.sub(r"\s+", "", unescape(text)).replace(" ", "").lower()


@dataclass(frozen=True)
class RowInvariant:
    """What one row must look like in every period of a qualifying table.

    ``zero_groups`` never report this line (they print a dash); a value there
    means the layout has shifted. ``segment_positive`` requires a strictly
    positive segment value. ``max_adjustment_share`` bounds every group
    other than the total and segment, in absolute value, as a share of the
    total, so an ordinary adjustment cannot be mistaken for the segment.
    """

    concept: str
    zero_groups: Tuple[str, ...]
    segment_positive: bool
    max_adjustment_share: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.max_adjustment_share, Decimal) or not (
            Decimal(0) <= self.max_adjustment_share < Decimal(1)
        ):
            raise ValueError("max_adjustment_share must be a Decimal in [0, 1).")


@dataclass(frozen=True)
class SupplementalTableRule:
    """Which printed table, columns, and rows one issuer's source reads."""

    cik: str
    version: str
    title: str
    column_groups: Tuple[str, ...]
    total_group: str
    segment_group: str
    rows: Tuple[Tuple[str, str], ...]  # (printed row label, canonical concept)
    dimension: Tuple[str, str]  # (axis, member) recorded on every fact
    scale_label: str
    scale: Decimal
    # One invariant per row; a rule without them cannot be constructed.
    invariants: Tuple[RowInvariant, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        groups = [_normalized(group) for group in self.column_groups]
        if len(set(groups)) != len(groups) or len(groups) < 2:
            raise ValueError("column_groups must be at least two distinct labels.")
        if _normalized(self.total_group) not in groups or _normalized(self.segment_group) not in groups:
            raise ValueError("total_group and segment_group must be declared column groups.")
        if _normalized(self.total_group) == _normalized(self.segment_group):
            raise ValueError("total_group and segment_group must differ.")
        labels = [_normalized(label) for label, _concept in self.rows]
        concepts = [concept for _label, concept in self.rows]
        if not self.rows or len(set(labels)) != len(labels) or len(set(concepts)) != len(concepts):
            raise ValueError("rows must be distinct (label, concept) pairs.")
        if not isinstance(self.scale, Decimal) or self.scale <= 0:
            raise ValueError("scale must be a positive Decimal.")
        for name in ("version", "title", "scale_label"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be non-empty text.")
        if sorted(item.concept for item in self.invariants) != sorted(concepts):
            raise ValueError("invariants must declare exactly one RowInvariant per row.")
        fixed = {_normalized(self.total_group), _normalized(self.segment_group)}
        for item in self.invariants:
            zero = [_normalized(group) for group in item.zero_groups]
            if any(group not in groups or group in fixed for group in zero):
                raise ValueError("zero_groups must be declared groups other than the total and segment.")

    def invariant_for(self, concept: str) -> RowInvariant:
        return next(item for item in self.invariants if item.concept == concept)

    @property
    def concepts(self) -> Tuple[str, ...]:
        return tuple(concept for _label, concept in self.rows)


@dataclass(frozen=True)
class SupplementalObservation:
    concept: str
    row_label: str
    period_start: date
    period_end: date
    value: Decimal  # USD, already scaled
    table_index: int


@dataclass(frozen=True)
class FilingDocumentReference:
    """Submission metadata for the filing whose document is being read."""

    cik: str
    entity_name: str
    accession_number: str
    form_type: str
    filed_date: date
    accepted_at: datetime
    report_date: date
    document_name: str
    document_url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if not is_aware(self.accepted_at):
            raise ValueError("FilingDocumentReference.accepted_at must be timezone-aware.")


@dataclass(frozen=True)
class _Run:
    text: str
    font_size: Optional[float]
    superscript: bool


@dataclass(frozen=True)
class _Cell:
    runs: Tuple[_Run, ...]

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(run.text for run in self.runs)).strip()

    @property
    def font_size(self) -> Optional[float]:
        sizes = [run.font_size for run in self.runs if run.text.strip() and run.font_size is not None]
        return max(sizes) if sizes else None

    @property
    def superscript(self) -> bool:
        visible = [run for run in self.runs if run.text.strip()]
        return bool(visible) and all(run.superscript for run in visible)


@dataclass(frozen=True)
class _Table:
    preceding_text: str  # text between the previous table and this one
    rows: Tuple[Tuple[_Cell, ...], ...]


class _DocumentParser(HTMLParser):
    """Flattens a filing into top-level tables and the text between them."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._stack: List[Tuple[str, Optional[float], bool]] = []
        self._table_depth = 0
        self._rows: List[Tuple[_Cell, ...]] = []
        self._row: Optional[List[_Cell]] = None
        self._cell: Optional[List[_Run]] = None
        self._between: List[str] = []
        self.tables: List[_Table] = []

    def _style(self) -> Tuple[Optional[float], bool]:
        return (self._stack[-1][1], self._stack[-1][2]) if self._stack else (None, False)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _BLOCK_TAGS and self._table_depth == 0:
            self._between.append("\n")
        if tag in _VOID_TAGS:
            return
        size, superscript = self._style()
        style = dict(attrs).get("style") or ""
        match = _FONT_SIZE.search(style)
        if match:
            size = float(match.group(1))
        if tag == "sup" or _SUPERSCRIPT.search(style):
            superscript = True
        self._stack.append((tag, size, superscript))
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._rows = []
        elif self._table_depth == 1 and tag == "tr":
            self._row = []
        elif self._table_depth == 1 and tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in _BLOCK_TAGS and self._table_depth == 0:
            self._between.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        if not any(open_tag == tag for open_tag, _size, _sup in self._stack):
            return
        while self._stack:
            open_tag, _size, _sup = self._stack.pop()
            if open_tag == tag:
                break
        if tag in ("td", "th") and self._table_depth == 1 and self._cell is not None and self._row is not None:
            self._row.append(_Cell(tuple(self._cell)))
            self._cell = None
        elif tag == "tr" and self._table_depth == 1 and self._row is not None:
            if self._cell is not None:
                self._row.append(_Cell(tuple(self._cell)))
                self._cell = None
            self._rows.append(tuple(self._row))
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                self.tables.append(_Table("".join(self._between), tuple(self._rows)))
                self._between = []
        if tag in _BLOCK_TAGS and self._table_depth == 0:
            self._between.append("\n")

    def handle_data(self, data):
        if self._table_depth == 0:
            self._between.append(data)
        elif self._table_depth == 1 and self._cell is not None:
            size, superscript = self._style()
            self._cell.append(_Run(data, size, superscript))


def _period_start(period_end: date, months: int) -> date:
    """First day of the ``months``-long duration ending on a month end."""

    month_index = period_end.year * 12 + period_end.month - 1 - (months - 1)
    return date(month_index // 12, month_index % 12 + 1, 1)


def _month_end(year: int, month_name: str, day: int) -> date:
    month = _MONTHS.get(month_name)
    if month is None:
        raise FilingDocumentError(FilingDocumentIssueCode.UNSUPPORTED_LAYOUT, f"Unknown month {month_name!r}.")
    try:
        period_end = date(year, month, day)
    except ValueError:
        raise FilingDocumentError(FilingDocumentIssueCode.UNSUPPORTED_LAYOUT, "Invalid period end date.") from None
    if (period_end + timedelta(days=1)).day != 1:
        raise FilingDocumentError(
            FilingDocumentIssueCode.UNSUPPORTED_LAYOUT, f"Period end {period_end} is not a month end."
        )
    return period_end


def _parse_value(token: str, where: str) -> Decimal:
    if token in _DASHES:
        return Decimal(0)
    match = _NUMBER.fullmatch(token.replace(" ", ""))
    if match is None or bool(match.group(1)) != bool(match.group(3)):
        raise FilingDocumentError(FilingDocumentIssueCode.MALFORMED_VALUE, f"{where}: unreadable value {token!r}.")
    value = Decimal(match.group(2).replace(",", ""))
    return -value if match.group(1) else value


def _value_tokens(cells: Tuple[_Cell, ...], label_size: Optional[float]) -> List[str]:
    tokens = []
    for cell in cells:
        text = cell.text
        if not text or text == "$":
            continue
        footnote = cell.superscript or (
            label_size is not None and cell.font_size is not None and cell.font_size < label_size
        )
        if footnote and text.isdigit():
            continue
        tokens.append(text)
    return tokens


def _table_periods(
    title_text: str, header_rows: List[Tuple[_Cell, ...]], group_count: int, where: str
) -> List[Tuple[date, date]]:
    """Column periods in the order they repeat within each column group."""

    interim = _INTERIM_TITLE.findall(title_text)
    annual = _ANNUAL_TITLE.findall(title_text)
    if bool(interim) == bool(annual) or len(set(interim)) > 1 or len(set(annual)) > 1:
        raise FilingDocumentError(
            FilingDocumentIssueCode.UNSUPPORTED_LAYOUT, f"{where}: the title must name exactly one period basis."
        )
    if interim:
        duration, month_name, day, year = interim[0]
        period_end = _month_end(int(year), month_name, int(day))
        return [(_period_start(period_end, _DURATION_MONTHS[duration]), period_end)]
    month_name, day = annual[0]
    year_rows = []
    for row in header_rows:
        years = [cell.text for cell in row if _YEAR.fullmatch(cell.text)]
        if years:
            year_rows.append(years)
    if len(year_rows) != 1 or len(year_rows[0]) % group_count:
        raise FilingDocumentError(
            FilingDocumentIssueCode.UNSUPPORTED_LAYOUT, f"{where}: expected one fiscal-year header row."
        )
    years = year_rows[0]
    per_group = len(years) // group_count
    first = years[:per_group]
    if years != first * group_count or len(set(first)) != per_group:
        raise FilingDocumentError(
            FilingDocumentIssueCode.UNSUPPORTED_LAYOUT,
            f"{where}: every column group must repeat the same distinct fiscal years.",
        )
    periods = []
    for year in first:
        period_end = _month_end(int(year), month_name, int(day))
        periods.append((_period_start(period_end, 12), period_end))
    return periods


def _check_invariant(
    invariant: RowInvariant,
    by_group: List[Decimal],
    zero_indexes: set,
    total_index: int,
    segment_index: int,
    where: str,
) -> None:
    """Refuse a row whose values sit in columns the rule says they cannot."""

    total, segment = by_group[total_index], by_group[segment_index]
    problems = []
    if any(by_group[index] != 0 for index in zero_indexes):
        problems.append("a group that never reports this line is non-zero")
    if invariant.segment_positive and segment <= 0:
        problems.append("the segment value is not positive")
    if total <= 0:
        problems.append("the total is not positive")
    else:
        limit = invariant.max_adjustment_share * total
        others = [
            value for index, value in enumerate(by_group)
            if index not in (total_index, segment_index) and index not in zero_indexes
        ]
        if any(abs(value) > limit for value in others):
            problems.append("an adjustment exceeds the rule's share of the total")
    if problems:
        raise FilingDocumentError(
            FilingDocumentIssueCode.UNEXPECTED_VALUE, f"{where}: {'; '.join(problems)}."
        )


def _document_tables(document: bytes) -> List[_Table]:
    parser = _DocumentParser()
    try:
        parser.feed(document.decode("utf-8"))
        parser.close()
    except UnicodeDecodeError:
        raise FilingDocumentError(FilingDocumentIssueCode.UNSUPPORTED_LAYOUT, "Document is not UTF-8.") from None
    return parser.tables


def contains_rule_rows(document: bytes, rule: SupplementalTableRule) -> bool:
    """Whether any table row is labelled like a row the rule reads.

    A tripwire for documents reviewed as having no qualifying table: if such
    a row appears anyway (for example under a renamed title), the review
    cannot be trusted.
    """

    wanted = {_normalized(label) for label, _concept in rule.rows}
    for table in _document_tables(document):
        for row in table.rows:
            labels = [cell.text for cell in row if cell.text]
            if labels and _normalized(labels[0]) in wanted:
                return True
    return False


def parse_supplemental_tables(document: bytes, rule: SupplementalTableRule) -> Tuple[SupplementalObservation, ...]:
    """Every rule row, in the segment column, from every qualifying table.

    Raises ``FilingDocumentError`` rather than returning anything ambiguous;
    a document with no qualifying table returns an empty tuple.
    """

    tables = _document_tables(document)
    title = _normalized(rule.title)
    groups = [_normalized(group) for group in rule.column_groups]
    total_index = groups.index(_normalized(rule.total_group))
    segment_index = groups.index(_normalized(rule.segment_group))
    wanted = {_normalized(label): (label, concept) for label, concept in rule.rows}
    observed: Dict[Tuple[str, date, date], SupplementalObservation] = {}

    for table_index, table in enumerate(tables):
        group_rows = [
            position
            for position, row in enumerate(table.rows)
            if [_normalized(cell.text) for cell in row if cell.text] == groups
        ]
        header_end = group_rows[0] if group_rows else len(table.rows)
        header_text = "".join(
            _normalized(cell.text) for row in table.rows[: header_end + 1] for cell in row
        )
        preceding = _normalized(table.preceding_text)
        if title not in header_text and title not in preceding:
            continue
        where = f"table {table_index}"
        if len(group_rows) != 1:
            raise FilingDocumentError(
                FilingDocumentIssueCode.UNSUPPORTED_LAYOUT, f"{where}: expected one row of the declared column groups."
            )
        # The title and scale belong to this table: its own header rows, or
        # the text after the previous table's title (so a neighbour's title
        # is never borrowed).
        own_text = header_text if title in header_text else preceding[preceding.rindex(title):]
        after_groups = table.rows[header_end + 1:]
        scale_text = own_text + "".join(
            _normalized(cell.text) for row in after_groups[:2] for cell in row
        )
        if _normalized(rule.scale_label) not in scale_text:
            raise FilingDocumentError(FilingDocumentIssueCode.UNSUPPORTED_LAYOUT, f"{where}: scale label missing.")
        periods = _table_periods(
            own_text, list(table.rows[: header_end + 1]) + list(after_groups[:2]), len(groups), where
        )
        found: Dict[str, List[Decimal]] = {}
        for row in after_groups:
            labels = [(position, cell) for position, cell in enumerate(row) if cell.text]
            if not labels:
                continue
            position, label_cell = labels[0]
            key = _normalized(label_cell.text)
            if key not in wanted:
                continue
            printed, concept = wanted[key]
            if concept in found:
                raise FilingDocumentError(
                    FilingDocumentIssueCode.DUPLICATE_ROW, f"{where}: row {printed!r} appears more than once."
                )
            tokens = _value_tokens(row[position + 1:], label_cell.font_size)
            if len(tokens) != len(groups) * len(periods):
                raise FilingDocumentError(
                    FilingDocumentIssueCode.UNSUPPORTED_LAYOUT,
                    f"{where}: row {printed!r} has {len(tokens)} values, expected {len(groups) * len(periods)}.",
                )
            found[concept] = [_parse_value(token, f"{where} row {printed!r}") for token in tokens]
        missing = [label for label, concept in rule.rows if concept not in found]
        if missing:
            raise FilingDocumentError(FilingDocumentIssueCode.MISSING_ROW, f"{where}: rows missing: {missing}.")

        for printed, concept in rule.rows:
            values = found[concept]
            invariant = rule.invariant_for(concept)
            zero_indexes = {groups.index(_normalized(group)) for group in invariant.zero_groups}
            for period_index, (start, end) in enumerate(periods):
                by_group = [values[group * len(periods) + period_index] for group in range(len(groups))]
                parts = sum(value for index, value in enumerate(by_group) if index != total_index)
                if parts != by_group[total_index]:
                    raise FilingDocumentError(
                        FilingDocumentIssueCode.INCONSISTENT_TOTAL,
                        f"{where}: {printed!r} for {start}..{end} does not add up to {rule.total_group!r}.",
                    )
                _check_invariant(
                    invariant, by_group, zero_indexes, total_index, segment_index,
                    f"{where}: {printed!r} for {start}..{end}",
                )
                observation = SupplementalObservation(
                    concept=concept,
                    row_label=printed,
                    period_start=start,
                    period_end=end,
                    value=by_group[segment_index] * rule.scale,
                    table_index=table_index,
                )
                prior = observed.setdefault((concept, start, end), observation)
                if prior.value != observation.value:
                    raise FilingDocumentError(
                        FilingDocumentIssueCode.CONFLICTING_OBSERVATION,
                        f"{printed!r} for {start}..{end} differs between tables {prior.table_index} and {table_index}.",
                    )
    return tuple(sorted(observed.values(), key=lambda item: (item.concept, item.period_end, item.period_start)))


def extract_supplemental_facts(
    document: bytes,
    rule: SupplementalTableRule,
    *,
    filing: FilingDocumentReference,
    calendar_policy: IssuerFiscalCalendarPolicy,
    ingestion_batch_id: str,
    ingested_at: datetime,
) -> Tuple[FinancialFact, ...]:
    """Classified, dimensioned facts for every rule row this document reports.

    Periods that begin before the issuer calendar's first fiscal year are out
    of scope, exactly as for Company Facts extraction.
    """

    if filing.cik != rule.cik or calendar_policy.cik != rule.cik:
        raise ValueError("The filing, calendar, and rule must belong to one issuer.")
    if not is_aware(ingested_at):
        raise ValueError("ingested_at must be timezone-aware.")
    digest = hashlib.sha256(document).hexdigest()
    floor = min(
        definition.period_start
        for definition in (
            *calendar_policy.fiscal_years,
            *calendar_policy.transitions,
            *calendar_policy.open_fiscal_years,
        )
    )
    lineage = FactLineage(
        source_adapter=SOURCE_ADAPTER,
        source_document_url=filing.document_url,
        concept_map_version=rule.version,
        ingestion_batch_id=ingestion_batch_id,
        ingested_at=ingested_at,
    )
    extracted = [
        SecExtractedFact(
            entity_cik=rule.cik,
            entity_name=filing.entity_name,
            taxonomy=TAXONOMY,
            raw_tag=(
                f"{rule.version}|{filing.document_name}|sha256={digest}|table={item.table_index}"
                f"|row={item.row_label}|column={rule.segment_group}"
            ),
            canonical_concept=item.concept,
            statement_kind=StatementKind.INCOME_STATEMENT,
            period_type=FactPeriodType.DURATION,
            period_start=item.period_start,
            period_end=item.period_end,
            value=item.value,
            unit="USD",
            currency="USD",
            provenance_accession_number=filing.accession_number,
            form_type=filing.form_type,
            is_amendment=filing.form_type.endswith("/A"),
            filed_date=filing.filed_date,
            accepted_at=filing.accepted_at,
            report_date=filing.report_date,
            primary_document=filing.document_name,
            filing_fiscal_year=None,
            filing_fiscal_period=None,
            frame=None,
            lineage=lineage,
        )
        for item in parse_supplemental_tables(document, rule)
        if item.period_start >= floor
    ]
    if not extracted:
        return ()
    classification = classify_sec_facts(extracted, calendar_policy)
    if not classification.is_complete:
        issue = classification.issues[0]
        raise FilingDocumentError(
            FilingDocumentIssueCode.CLASSIFICATION, f"{filing.accession_number}: {issue.code.value}: {issue.message}"
        )
    context = FactContext(entity_cik=rule.cik, dimensions=(rule.dimension,))
    return tuple(
        replace(fact, identity=replace(fact.identity, context=context)) for fact in classification.facts
    )
