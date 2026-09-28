"""FY2024 Microsoft opening balance sheet from its reported SEC filing.

This is an issuer/date-scoped source adapter for the first forecast foundation,
not a generic XBRL mapper. Every line is independently tagged in the filed
balance-sheet table. Subtotals and the accounting identity are checks only.
No residual is ever created to close a section.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import gzip
import hashlib
from types import MappingProxyType
from typing import Mapping, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen
import zlib

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings

MSFT_CIK = "0000789019"
PERIOD_END = date(2024, 6, 30)
ACCESSION = "0000950170-24-087843"
SOURCE_URL = "https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm"
# Pin the downloaded official 10-K, decoded from the SEC's gzip response.
DOCUMENT_SHA256 = "c7666c097e0b34de5406a17070e1f4d33fea896316c1fc12a4bbc6bef5d822f7"

# Each tag denotes one balance-sheet presentation line, never a subtotal.
SECTION_TAGS: Mapping[str, tuple[str, ...]] = {
    "current_assets": (
        "CashAndCashEquivalentsAtCarryingValue", "ShortTermInvestments", "AccountsReceivableNetCurrent",
        "InventoryNet", "OtherAssetsCurrent",
    ),
    "noncurrent_assets": (
        "PropertyPlantAndEquipmentNet", "OperatingLeaseRightOfUseAsset", "LongTermInvestments",
        "Goodwill", "FiniteLivedIntangibleAssetsNet", "OtherAssetsNoncurrent",
    ),
    "current_liabilities": (
        "AccountsPayableCurrent", "CommercialPaper", "LongTermDebtCurrent",
        "EmployeeRelatedLiabilitiesCurrent", "AccruedIncomeTaxesCurrent",
        "ContractWithCustomerLiabilityCurrent", "OtherLiabilitiesCurrent",
    ),
    "noncurrent_liabilities": (
        "LongTermDebtNoncurrent", "AccruedIncomeTaxesNoncurrent",
        "ContractWithCustomerLiabilityNoncurrent", "DeferredIncomeTaxLiabilitiesNet",
        "OperatingLeaseLiabilityNoncurrent", "OtherLiabilitiesNoncurrent",
    ),
    "equity": (
        "CommonStocksIncludingAdditionalPaidInCapital", "RetainedEarningsAccumulatedDeficit",
        "AccumulatedOtherComprehensiveIncomeLossNetOfTax",
    ),
}
TOTAL_TAGS = {
    "current_assets": "AssetsCurrent", "assets": "Assets", "current_liabilities": "LiabilitiesCurrent",
    "liabilities": "Liabilities", "equity": "StockholdersEquity",
    "liabilities_and_equity": "LiabilitiesAndStockholdersEquity",
}
STORED_CONCEPTS = {
    "total_assets": "Assets", "current_assets": "AssetsCurrent",
    "cash_and_cash_equivalents": "CashAndCashEquivalentsAtCarryingValue",
    "accounts_receivable": "AccountsReceivableNetCurrent", "inventory": "InventoryNet",
    "property_plant_and_equipment_net": "PropertyPlantAndEquipmentNet",
    "total_liabilities": "Liabilities", "current_liabilities": "LiabilitiesCurrent",
    "accounts_payable": "AccountsPayableCurrent", "current_debt": "LongTermDebtCurrent",
    "long_term_debt": "LongTermDebtNoncurrent", "shareholders_equity": "StockholdersEquity",
    "retained_earnings": "RetainedEarningsAccumulatedDeficit",
}


class OpeningBalanceRefusal(ValueError):
    """The pinned filing or its reported lines do not support this opening."""


@dataclass(frozen=True)
class ReportedLine:
    tag: str
    label: str
    value: Decimal  # USD millions, exactly as displayed in the 10-K
    context_ref: str
    accession: str = ACCESSION
    source_url: str = SOURCE_URL
    document_sha256: str = DOCUMENT_SHA256


@dataclass(frozen=True)
class MicrosoftOpening:
    period_end: date
    document_sha256: str
    lines: Mapping[str, ReportedLine]
    totals: Mapping[str, Decimal]
    stored_matches: Mapping[str, bool]

    def __post_init__(self):
        for name in ("lines", "totals", "stored_matches"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    def value(self, tag: str) -> Decimal:
        return self.lines[tag].value


def fetch_microsoft_opening_document(user_agent: str) -> bytes:
    """Fetch the exact reviewed SEC document, refusing changed bytes."""
    if not isinstance(user_agent, str) or not user_agent.strip() or "\n" in user_agent or "\r" in user_agent:
        raise OpeningBalanceRefusal("A valid SEC User-Agent is required.")
    request = Request(SOURCE_URL, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"})
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            encoding = response.headers.get("Content-Encoding", "identity").lower()
    except (OSError, URLError) as error:
        raise OpeningBalanceRefusal("Could not retrieve the official SEC 10-K.") from error
    if encoding == "gzip":
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError, zlib.error) as error:
            raise OpeningBalanceRefusal("SEC 10-K gzip response is invalid.") from error
    elif encoding != "identity":
        raise OpeningBalanceRefusal(f"Unsupported SEC content encoding: {encoding}.")
    if hashlib.sha256(body).hexdigest() != DOCUMENT_SHA256:
        raise OpeningBalanceRefusal("Official 10-K document hash differs from the reviewed copy.")
    return body


def _balance_table(soup):
    matches = [table for table in soup.find_all("table")
               if "Total current assets" in table.get_text(" ", strip=True)
               and "Long-term unearned revenue" in table.get_text(" ", strip=True)
               and "Total stockholders" in table.get_text(" ", strip=True)]
    if len(matches) != 1:
        raise OpeningBalanceRefusal(f"Expected one filed balance-sheet table, found {len(matches)}.")
    return matches[0]


def _instant_context(soup) -> str:
    ids = []
    for context in soup.find_all(lambda node: node.name and node.name.lower() == "xbrli:context"):
        entity = context.find(lambda node: node.name and node.name.lower() == "xbrli:identifier")
        instant = context.find(lambda node: node.name and node.name.lower() == "xbrli:instant")
        if (entity is not None and entity.get_text(strip=True) == MSFT_CIK
                and instant is not None and instant.get_text(strip=True) == PERIOD_END.isoformat()
                and context.find(lambda node: node.name and node.name.lower() in ("xbrli:segment", "xbrli:scenario")) is None):
            ids.append(context["id"])
    if len(ids) != 1:
        raise OpeningBalanceRefusal(f"Expected one consolidated FY2024 instant context, found {len(ids)}.")
    return ids[0]


def _number(node) -> Decimal:
    if node.get("scale") != "6" or node.get("unitref", "").lower() not in ("usd", "u_usd"):
        raise OpeningBalanceRefusal("Balance-sheet fact is not tagged in USD millions.")
    text = node.get_text("", strip=True).replace(",", "").replace("$", "")
    try:
        value = Decimal(text)
    except Exception as error:
        raise OpeningBalanceRefusal("Unreadable balance-sheet number.") from error
    if not value.is_finite() or node.get("sign") not in (None, "-"):
        raise OpeningBalanceRefusal("Invalid balance-sheet value or sign.")
    return -value if node.get("sign") == "-" else value


def read_microsoft_opening(document: bytes, *, stored_snapshot: Optional[object] = None) -> MicrosoftOpening:
    """Read every reported line; optionally prove all mapped stored values agree.

    ``stored_snapshot`` is the point-in-time opening balance from the SEC store.
    Missing source lines, mismatched store facts, or any section imbalance refuse.
    The unmapped lines remain filing-derived and are not described as stored facts.
    """
    digest = hashlib.sha256(document).hexdigest()
    if digest != DOCUMENT_SHA256:
        raise OpeningBalanceRefusal("Official 10-K document hash differs from the reviewed copy.")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(document, "html.parser")
    context = _instant_context(soup)
    wanted = set(TOTAL_TAGS.values()) | {tag for tags in SECTION_TAGS.values() for tag in tags}
    found: dict[str, ReportedLine] = {}
    for row in _balance_table(soup).find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        label = cells[0].get_text(" ", strip=True) if cells else ""
        for node in row.find_all(lambda item: item.name and item.name.lower() == "ix:nonfraction"):
            tag = node.get("name", "")
            if not tag.startswith("us-gaap:") or node.get("contextref") != context:
                continue
            tag = tag.split(":", 1)[1]
            if tag not in wanted:
                continue
            if tag in found:
                raise OpeningBalanceRefusal(f"Duplicate filed line {tag}.")
            found[tag] = ReportedLine(tag, label, _number(node), context, document_sha256=digest)
    missing = wanted - found.keys()
    if missing:
        raise OpeningBalanceRefusal(f"Unreported opening lines: {sorted(missing)}")

    totals = {name: found[tag].value for name, tag in TOTAL_TAGS.items()}
    for section, tags in SECTION_TAGS.items():
        actual = sum((found[tag].value for tag in tags), Decimal(0))
        expected = totals.get(section)
        if expected is None:
            expected = (totals["assets"] - totals["current_assets"] if section == "noncurrent_assets"
                        else totals["liabilities"] - totals["current_liabilities"] if section == "noncurrent_liabilities"
                        else totals["equity"])
        if actual != expected:
            raise OpeningBalanceRefusal(f"{section} does not reconcile: {actual} != {expected}.")
    if totals["assets"] != totals["liabilities"] + totals["equity"] or totals["assets"] != totals["liabilities_and_equity"]:
        raise OpeningBalanceRefusal("Filed assets do not equal liabilities plus reported equity.")
    if (totals["current_assets"] > totals["assets"] or totals["current_liabilities"] > totals["liabilities"]
            or any(found[tag].value < 0 for section, tags in SECTION_TAGS.items() if section != "equity" for tag in tags)):
        raise OpeningBalanceRefusal("Invalid section sign or total ordering.")

    matches: dict[str, bool] = {}
    if stored_snapshot is not None:
        if stored_snapshot.request.cik != MSFT_CIK or stored_snapshot.request.period_end != PERIOD_END:
            raise OpeningBalanceRefusal("Stored snapshot has the wrong issuer or date.")
        for concept, tag in STORED_CONCEPTS.items():
            line = stored_snapshot.line(concept)
            if line is None or line.value != found[tag].value * Decimal(1_000_000):
                raise OpeningBalanceRefusal(f"Stored SEC value does not match filed {tag}.")
            matches[concept] = True
    return MicrosoftOpening(PERIOD_END, digest, found, totals, matches)


def build_verified_microsoft_opening(repository, request, document: bytes) -> MicrosoftOpening:
    """Require the historical store snapshot before opening a forecast."""
    from .opening_balance_sheet import build_opening_balance_sheet

    if request.cik != MSFT_CIK or request.period_end != PERIOD_END:
        raise OpeningBalanceRefusal("Opening request has the wrong issuer or date.")
    result = build_opening_balance_sheet(repository, request)
    if not result.is_complete:
        raise OpeningBalanceRefusal(f"Stored opening refused: {result.issues}")
    return read_microsoft_opening(document, stored_snapshot=result.snapshot)
