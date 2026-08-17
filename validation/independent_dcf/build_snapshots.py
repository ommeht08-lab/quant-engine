#!/usr/bin/env python3
"""
build_snapshots.py -- Reproducible, deterministic snapshot builder for the
independent DCF validation (Track A Phase 2A).

Three explicit modes, selected by command-line flag. There is no implicit
network access in any mode except --refresh-sec, and --refresh-sec refuses to
run without an operator-supplied SEC_USER_AGENT environment variable.

  (no flag)            Verify mode (default). Read-only: checks the raw SEC
                        cache against its checksum manifest, checks every
                        snapshot/manifest file parses and is internally
                        consistent (derived debt ties to components, etc.),
                        and reports PASS/FAIL. Never writes a file. Never
                        opens a network connection.

  --rebuild-from-cache  Offline rebuild. Regenerates snapshots/*.json,
                        source_manifest.csv, snapshot_manifest.json, AND
                        independent_dcf_validation.xlsx from the already-
                        cached SEC EDGAR data in sec_raw_cache/ and the
                        market data already frozen in the existing snapshot
                        files. Refuses to run if any required cache or input
                        file is missing, or if the cache does not match
                        sec_raw_cache/cache_manifest.json. All four outputs
                        are generated into an isolated staging directory,
                        fully validated there (schema/invariants, manifest
                        completeness, a full independent formula audit, an
                        error-token scan), and only then published as one
                        group via an explicit per-slot backup/promote state
                        machine with a unique transaction-specific backup
                        directory -- see _publish_staged_generation's
                        docstring and the ATOMICITY / RECOVERY GUARANTEES
                        comment below for exactly what this does and does
                        NOT guarantee (in particular: a Python-level
                        exception at any point is proven, by fault-injection
                        self-test, to leave every live output byte-for-byte
                        as it was; an OS crash or kill -9 mid-transaction is
                        NOT proven recoverable -- there is no durable
                        journal). Never opens a network connection. Produces
                        byte-identical output across repeated runs against
                        identical inputs -- see "Determinism design" below.

  --refresh-sec         The ONLY mode that calls urllib.request.urlopen.
                        Requires the SEC_USER_AGENT environment variable to
                        be set and non-empty; fails before any network
                        access otherwise. Never prints the value of
                        SEC_USER_AGENT. Lists which cache files it would
                        replace before doing so. Downloads to a temp file,
                        validates the JSON parses, and only then atomically
                        replaces the cache file and updates
                        cache_manifest.json; on any failure (network error,
                        invalid JSON), the existing cache file is left
                        untouched. NOT run during this correction pass.

Determinism design
-------------------
A "when was this regenerated" wall-clock timestamp is fundamentally
incompatible with "byte-identical output on repeated runs" -- calling
datetime.now() twice never produces the same value. Rather than working
around this, the design choice made here is to keep any such timestamp OUT
of the generated files' content entirely: it is printed to stdout at
rebuild time (for the operator's own record) but never written into
snapshots/*.json or snapshot_manifest.json. The provenance timestamps that
ARE written into those files (retrieval_timestamp_utc for market data, filed
dates for SEC facts) are frozen, sourced values carried through unchanged --
never derived from datetime.now() -- and are therefore stable across runs by
construction.

Privacy note
------------
No email address or other personal contact string is hard-coded in this
file. --refresh-sec requires the operator to supply one via the
SEC_USER_AGENT environment variable at run time (SEC's fair-access policy
asks automated API clients to identify themselves with a descriptive
User-Agent, ideally including a contact method) -- this script never stores,
logs, or prints that value.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "sec_raw_cache")
CACHE_MANIFEST_PATH = os.path.join(CACHE_DIR, "cache_manifest.json")
SNAP_DIR = os.path.join(HERE, "snapshots")
SOURCE_MANIFEST_PATH = os.path.join(HERE, "source_manifest.csv")
SNAPSHOT_MANIFEST_PATH = os.path.join(HERE, "snapshot_manifest.json")

TICKERS = ["MSFT", "CAT", "INTC", "VZ"]
COMPANY_PROFILE = {
    "MSFT": "Large-cap, capital-light, high-margin",
    "CAT": "Capital-intensive, moderate leverage",
    "INTC": "Mature, negative/near-zero recent revenue growth",
    "VZ": "Meaningfully leveraged (recommended 4th profile)",
}

REQUIRED_CACHE_FILES = [
    "company_tickers.json",
    "companyfacts_MSFT.json",
    "companyfacts_CAT.json",
    "companyfacts_INTC.json",
    "companyfacts_VZ.json",
]

FIELD_TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "ebit_operating_income": ["OperatingIncomeLoss"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                       "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax_provision": ["IncomeTaxExpenseBenefit"],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
}
INTEREST_TAGS = {
    "MSFT": ["InterestExpense", "InterestExpenseNonoperating"],
    "CAT": ["InterestPaidNet"],  # CAT does not tag InterestExpense; cash-paid-interest proxy (documented)
    "INTC": ["InterestExpense", "InterestExpenseNonoperating"],
    "VZ": ["InterestExpense", "InterestExpenseNonoperating"],
}
INTEREST_METHOD = {"MSFT": "reported", "CAT": "proxy_cash_paid_interest", "INTC": "reported", "VZ": "reported"}
DEBT_COMBINED_TAG = {"VZ": "DebtLongtermAndShorttermCombinedAmount"}
DEBT_COMPONENT_TAGS = {
    "MSFT": ["LongTermDebtNoncurrent", "LongTermDebtCurrent"],
    "CAT": ["LongTermDebtNoncurrent", "ShortTermBorrowings"],
    "INTC": ["LongTermDebtNoncurrent", "LongTermDebtCurrent"],  # DebtCurrent excluded: duplicate tag for the same current-portion fact
}


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dedupe_preserve_order(values):
    seen = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return seen


def atomic_write_text(path, text):
    d = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".partial")
    try:
        with os.fdopen(fd, "w", newline="" if path.endswith(".csv") else None) as f:
            f.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def atomic_write_json(path, obj):
    # sort_keys + fixed separators -> byte-identical output for identical data, every run.
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True)
    atomic_write_text(path, text)


# ---------------------------------------------------------------------------
# Raw-cache checksum manifest
# ---------------------------------------------------------------------------

def compute_cache_manifest():
    entries = {}
    for fname in REQUIRED_CACHE_FILES:
        path = os.path.join(CACHE_DIR, fname)
        if not os.path.exists(path):
            entries[fname] = None
            continue
        entries[fname] = {"sha256": sha256_file(path), "size_bytes": os.path.getsize(path)}
    return entries


def verify_cache_against_manifest(strict=True):
    """Returns (ok: bool, problems: list[str])."""
    problems = []
    if not os.path.exists(CACHE_MANIFEST_PATH):
        return False, [f"cache_manifest.json not found at {CACHE_MANIFEST_PATH}"]
    recorded = json.load(open(CACHE_MANIFEST_PATH))["files"]
    actual = compute_cache_manifest()
    for fname in REQUIRED_CACHE_FILES:
        rec = recorded.get(fname)
        act = actual.get(fname)
        if act is None:
            problems.append(f"MISSING cache file: {fname}")
            continue
        if rec is None:
            problems.append(f"cache_manifest.json has no entry for {fname}")
            continue
        if rec["sha256"] != act["sha256"]:
            problems.append(f"CHECKSUM MISMATCH: {fname} (manifest={rec['sha256'][:16]}... actual={act['sha256'][:16]}...)")
        if rec["size_bytes"] != act["size_bytes"]:
            problems.append(f"SIZE MISMATCH: {fname} (manifest={rec['size_bytes']} actual={act['size_bytes']})")
    return (len(problems) == 0), problems


def write_cache_manifest():
    manifest = compute_cache_manifest()
    missing = [f for f, v in manifest.items() if v is None]
    if missing:
        raise RuntimeError(f"Cannot write cache_manifest.json -- missing cache files: {missing}")
    atomic_write_json(CACHE_MANIFEST_PATH, {
        "description": "SHA-256 and byte size of every immutable raw SEC EDGAR input file this validation depends on. "
                        "Verified before every offline rebuild; only --refresh-sec (not run during this correction pass) may change these files.",
        "hash_algorithm": "SHA-256",
        "files": manifest,
    })


# ---------------------------------------------------------------------------
# SEC EDGAR extraction (pure function of already-loaded JSON -- no I/O here)
# ---------------------------------------------------------------------------

def annual_series(us_gaap, tag):
    from datetime import date
    if tag not in us_gaap:
        return {}
    out = {}
    for item in us_gaap[tag].get("units", {}).get("USD", []):
        if item.get("form") != "10-K":
            continue
        start, end = item.get("start"), item.get("end")
        if start:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if not (330 <= days <= 400):
                continue
        rec = {"val": item["val"], "accn": item["accn"], "filed": item["filed"], "tag": tag, "end": end, "start": start}
        if end not in out or rec["filed"] < out[end]["filed"]:
            out[end] = rec
    return out


def merge_series(us_gaap, tags):
    merged = {}
    for tag in tags:
        for end, rec in annual_series(us_gaap, tag).items():
            merged.setdefault(end, rec)
    return merged


def instant_value(us_gaap, tag, end_date):
    if tag not in us_gaap:
        return None
    for item in us_gaap[tag].get("units", {}).get("USD", []):
        if item.get("form") == "10-K" and item.get("end") == end_date and not item.get("start"):
            return {"val": item["val"], "accn": item["accn"], "filed": item["filed"], "tag": tag, "end": end_date}
    return None


def filing_index_url(cik, accn):
    cik_int = str(int(cik))
    accn_nodash = accn.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{accn}-index.htm"


def extract_company(ticker, cik, facts):
    us_gaap = facts["facts"]["us-gaap"]
    entity = facts["entityName"]

    revenue = merge_series(us_gaap, FIELD_TAGS["revenue"])
    pretax = merge_series(us_gaap, FIELD_TAGS["pretax_income"])
    tax = merge_series(us_gaap, FIELD_TAGS["tax_provision"])
    interest = merge_series(us_gaap, INTEREST_TAGS[ticker])
    ebit = merge_series(us_gaap, FIELD_TAGS["ebit_operating_income"])

    candidate_ends = sorted(set(revenue) & set(pretax) & set(tax), reverse=True)
    latest_end = candidate_ends[0] if candidate_ends else None

    cash_latest = instant_value(us_gaap, FIELD_TAGS["cash_and_equivalents"][0], latest_end) if latest_end else None

    debt_latest = None
    debt_component_provenance = {}
    normalization_equation = None
    if latest_end:
        if ticker in DEBT_COMBINED_TAG:
            debt_latest = instant_value(us_gaap, DEBT_COMBINED_TAG[ticker], latest_end)
            if debt_latest:
                normalization_equation = f"Total Debt = {debt_latest['tag']} (reported directly, not summed) = ${debt_latest['val']:,.0f}"
        else:
            total = 0.0
            parts = []
            for dtag in DEBT_COMPONENT_TAGS[ticker]:
                v = instant_value(us_gaap, dtag, latest_end)
                if v:
                    total += v["val"]
                    debt_component_provenance[dtag] = {
                        "xbrl_tag": v["tag"], "raw_value_usd": v["val"], "units": "USD",
                        "fiscal_period_end": v["end"], "accession": v["accn"], "filed_date": v["filed"],
                        "source_url": filing_index_url(cik, v["accn"]),
                    }
                    parts.append(f"{dtag} (${v['val']:,.0f})")
            if parts:
                debt_latest = {"val": total, "tag": "SUM:" + "+".join(debt_component_provenance.keys()), "end": latest_end}
                normalization_equation = "Total Debt = " + " + ".join(parts) + f" = ${total:,.0f}"

    interest_latest = interest.get(latest_end) if latest_end else None

    hist_ends = sorted(set(revenue) & set(ebit))[-5:]
    history = []
    for end in hist_ends:
        rrec, erec = revenue[end], ebit[end]
        history.append({
            "fiscal_period_end": end, "fiscal_period_start": rrec.get("start"),
            "revenue_raw_usd": rrec["val"], "revenue_xbrl_tag": rrec["tag"],
            "revenue_accession": rrec["accn"], "revenue_filed_date": rrec["filed"],
            "revenue_source_url": filing_index_url(cik, rrec["accn"]),
            "ebit_raw_usd": erec["val"], "ebit_xbrl_tag": erec["tag"],
            "ebit_accession": erec.get("accn"), "ebit_filed_date": erec.get("filed"),
            "ebit_source_url": filing_index_url(cik, erec["accn"]) if erec.get("accn") else None,
            "ebit_derivation_note": "Directly reported (OperatingIncomeLoss XBRL tag).",
        })

    # ---- Issue 4: derived rollup provenance must be self-contained (deduped, deterministic order) ----
    rollup_accessions = dedupe_preserve_order(c["accession"] for c in debt_component_provenance.values())
    rollup_urls = dedupe_preserve_order(c["source_url"] for c in debt_component_provenance.values())
    rollup_filed_dates = dedupe_preserve_order(c["filed_date"] for c in debt_component_provenance.values())

    return {
        "entity_name": entity, "latest_fy_end": latest_end, "history": history,
        "pretax": pretax.get(latest_end), "tax": tax.get(latest_end),
        "interest": interest_latest, "interest_method": INTEREST_METHOD[ticker],
        "cash": cash_latest, "debt": debt_latest,
        "debt_component_provenance": debt_component_provenance,
        "debt_normalization_equation": normalization_equation,
        "debt_reported_accession": debt_latest["accn"] if (debt_latest and "accn" in debt_latest) else None,
        "debt_reported_filed_date": debt_latest["filed"] if (debt_latest and "filed" in debt_latest) else None,
        "debt_reported_source_url": filing_index_url(cik, debt_latest["accn"]) if (debt_latest and debt_latest.get("accn")) else None,
        "debt_rollup_accession_joined": "; ".join(rollup_accessions) if rollup_accessions else None,
        "debt_rollup_source_url_joined": "; ".join(rollup_urls) if rollup_urls else None,
        "debt_rollup_filed_date_joined": "; ".join(rollup_filed_dates) if rollup_filed_dates else None,
    }


# ---------------------------------------------------------------------------
# Snapshot / manifest generation (pure, deterministic given inputs)
# ---------------------------------------------------------------------------

def get_cik_map():
    path = os.path.join(CACHE_DIR, "company_tickers.json")
    rows = json.load(open(path))
    return {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in rows.values()}


def generate_snapshot(ticker, cik, facts, existing_market_data, existing_data_sources, existing_units,
                       existing_retrieval_ts, existing_derived_note):
    extracted = extract_company(ticker, cik, facts)
    lyf = {
        "pretax_income_usd": extracted["pretax"]["val"] if extracted["pretax"] else None,
        "pretax_income_xbrl_tag": extracted["pretax"]["tag"] if extracted["pretax"] else None,
        "pretax_income_accession": extracted["pretax"]["accn"] if extracted["pretax"] else None,
        "pretax_income_source_url": filing_index_url(cik, extracted["pretax"]["accn"]) if extracted["pretax"] else None,
        "pretax_income_filed_date": extracted["pretax"]["filed"] if extracted["pretax"] else None,
        "tax_provision_usd": extracted["tax"]["val"] if extracted["tax"] else None,
        "tax_provision_xbrl_tag": extracted["tax"]["tag"] if extracted["tax"] else None,
        "tax_provision_accession": extracted["tax"]["accn"] if extracted["tax"] else None,
        "tax_provision_source_url": filing_index_url(cik, extracted["tax"]["accn"]) if extracted["tax"] else None,
        "tax_provision_filed_date": extracted["tax"]["filed"] if extracted["tax"] else None,
        "interest_expense_usd": extracted["interest"]["val"] if extracted["interest"] else None,
        "interest_expense_xbrl_tag": extracted["interest"]["tag"] if extracted["interest"] else None,
        "interest_expense_method": extracted["interest_method"],
        "interest_expense_accession": extracted["interest"].get("accn") if extracted["interest"] else None,
        "interest_expense_source_url": filing_index_url(cik, extracted["interest"]["accn"]) if extracted["interest"] and extracted["interest"].get("accn") else None,
        "interest_expense_filed_date": extracted["interest"].get("filed") if extracted["interest"] else None,
        "total_debt_usd": extracted["debt"]["val"] if extracted["debt"] else None,
        "total_debt_normalization_method": extracted["debt"]["tag"] if extracted["debt"] else None,
        "total_debt_normalization_equation": extracted["debt_normalization_equation"],
        "total_debt_reported_accession": extracted["debt_reported_accession"],
        "total_debt_reported_filed_date": extracted["debt_reported_filed_date"],
        "total_debt_reported_source_url": extracted["debt_reported_source_url"],
        "total_debt_rollup_accession_joined": extracted["debt_rollup_accession_joined"],
        "total_debt_rollup_source_url_joined": extracted["debt_rollup_source_url_joined"],
        "total_debt_rollup_filed_date_joined": extracted["debt_rollup_filed_date_joined"],
        "total_debt_components": extracted["debt_component_provenance"] or None,
        "cash_and_equivalents_usd": extracted["cash"]["val"] if extracted["cash"] else None,
        "cash_xbrl_tag": extracted["cash"]["tag"] if extracted["cash"] else None,
        "cash_accession": extracted["cash"]["accn"] if extracted["cash"] else None,
        "cash_source_url": filing_index_url(cik, extracted["cash"]["accn"]) if extracted["cash"] else None,
        "cash_filed_date": extracted["cash"]["filed"] if extracted["cash"] else None,
    }
    return {
        "ticker": ticker, "entity_name": extracted["entity_name"], "cik": cik,
        "profile_classification": COMPANY_PROFILE[ticker],
        "retrieval_timestamp_utc": existing_retrieval_ts,  # frozen, carried forward -- never datetime.now()
        "data_sources": existing_data_sources,
        "units": existing_units,
        "reporting_currency": "USD",
        "latest_complete_fiscal_year_end": extracted["latest_fy_end"],
        "historical_annual_data": extracted["history"],
        "latest_year_facts": lyf,
        "market_data": existing_market_data,  # frozen, carried forward unchanged -- NOT re-fetched from yfinance
        "derived_values_NOT_frozen_facts": existing_derived_note,
    }


MARKET_FIELDS = {"Current Price", "Shares Outstanding", "Beta", "Sector (GICS)"}


def classify_manifest_row(field):
    if field in MARKET_FIELDS:
        return "market"
    if field == "Total Debt (latest, derived)":
        return "derived_rollup"
    return "reported_fact"  # Revenue, EBIT, Pretax Income, Tax Provision, Interest Expense,
    # Cash & Equivalents, Total Debt component: X, Total Debt (latest, reported)


def verify_manifest_completeness(rows):
    """Field-level completeness check over parsed source_manifest.csv rows (list of dicts,
    as returned by csv.DictReader). Returns (ok: bool, problems: list[(ticker, field, missing_column, message)]).

    Requirements, per docs/independent-validation-plan.md's provenance discipline and this
    correction pass's explicit requirements:
      - reported_fact  (a directly reported SEC fact, or a debt component):
          nonempty accession_number, source_url, filed_or_retrieved_date,
          xbrl_tag_or_source_field, and a clear (nonempty) provenance_type.
      - derived_rollup (a Total Debt total derived by summing components):
          nonempty (deduplicated-joined) accession_number, source_url,
          filed_or_retrieved_date, and provenance_type must explicitly say "derived".
      - market (price/shares/beta/sector): nonempty period_end_or_timestamp (the retrieval
          timestamp), nonempty source_url, and a provenance_type/xbrl_tag_or_source_field
          that clearly names a market data source (here: starts with "yfinance").

    A missing value fails with the EXACT (ticker, field, column_name) that is empty --
    never a generic "some row somewhere is incomplete" message.
    """
    problems = []
    for r in rows:
        tk, field = r["ticker"], r["field"]
        category = classify_manifest_row(field)

        def require(colname, condition_ok, extra_msg=""):
            if not condition_ok:
                problems.append((tk, field, colname, extra_msg or f"{colname} is empty/missing"))

        if category == "reported_fact":
            require("accession_number", bool(r["accession_number"]))
            require("source_url", bool(r["source_url"]))
            require("filed_or_retrieved_date", bool(r["filed_or_retrieved_date"]))
            require("xbrl_tag_or_source_field", bool(r["xbrl_tag_or_source_field"]))
            require("provenance_type", bool(r["provenance_type"]))
        elif category == "derived_rollup":
            require("accession_number", bool(r["accession_number"]), "derived rollup must carry a deduplicated-joined accession list")
            require("source_url", bool(r["source_url"]), "derived rollup must carry a deduplicated-joined source-URL list")
            require("filed_or_retrieved_date", bool(r["filed_or_retrieved_date"]), "derived rollup must carry a deduplicated-joined filed-date list")
            require("provenance_type", "derived" in r["provenance_type"].lower(), "provenance_type must explicitly say this value is derived, not reported")
        elif category == "market":
            require("period_end_or_timestamp", bool(r["period_end_or_timestamp"]), "market row must carry a nonempty retrieval timestamp")
            require("source_url", bool(r["source_url"]))
            require("xbrl_tag_or_source_field", r["xbrl_tag_or_source_field"].startswith("yfinance"),
                    "market row must clearly name its market-data source (expected to start with 'yfinance')")
    return (len(problems) == 0), problems


def build_manifest_rows(ticker, snapshot):
    rows = []
    lyf = snapshot["latest_year_facts"]
    latest = snapshot["latest_complete_fiscal_year_end"]
    for h in snapshot["historical_annual_data"]:
        rows.append([ticker, "Revenue", h["fiscal_period_end"], h["revenue_raw_usd"], "USD",
                     h["revenue_xbrl_tag"], h["revenue_accession"], h["revenue_source_url"], h["revenue_filed_date"], "reported"])
        rows.append([ticker, "EBIT/Operating Income", h["fiscal_period_end"], h["ebit_raw_usd"], "USD",
                     h["ebit_xbrl_tag"], h["ebit_accession"], h["ebit_source_url"], h["ebit_filed_date"], "reported"])
    rows.append([ticker, "Pretax Income (latest)", latest, lyf["pretax_income_usd"], "USD",
                 lyf["pretax_income_xbrl_tag"], lyf["pretax_income_accession"], lyf["pretax_income_source_url"],
                 lyf["pretax_income_filed_date"], "reported"])
    rows.append([ticker, "Tax Provision (latest)", latest, lyf["tax_provision_usd"], "USD",
                 lyf["tax_provision_xbrl_tag"], lyf["tax_provision_accession"], lyf["tax_provision_source_url"],
                 lyf["tax_provision_filed_date"], "reported"])
    rows.append([ticker, "Interest Expense (latest)", latest, lyf["interest_expense_usd"], "USD",
                 lyf["interest_expense_xbrl_tag"], lyf["interest_expense_accession"], lyf["interest_expense_source_url"],
                 lyf["interest_expense_filed_date"], lyf["interest_expense_method"]])

    if lyf["total_debt_components"]:
        for comp_tag, comp in lyf["total_debt_components"].items():
            rows.append([ticker, f"Total Debt component: {comp_tag}", comp["fiscal_period_end"], comp["raw_value_usd"],
                         comp["units"], comp["xbrl_tag"], comp["accession"], comp["source_url"], comp["filed_date"], "reported (component)"])
        # Issue 4: rollup row is now self-contained -- deduped, deterministically-joined accession/URL/filed-date.
        rows.append([ticker, "Total Debt (latest, derived)", latest, lyf["total_debt_usd"], "USD",
                     lyf["total_debt_normalization_equation"],
                     lyf["total_debt_rollup_accession_joined"],
                     lyf["total_debt_rollup_source_url_joined"],
                     lyf["total_debt_rollup_filed_date_joined"],
                     "derived (sum of components above)"])
    else:
        rows.append([ticker, "Total Debt (latest, reported)", latest, lyf["total_debt_usd"], "USD",
                     lyf["total_debt_normalization_method"], lyf["total_debt_reported_accession"],
                     lyf["total_debt_reported_source_url"], lyf["total_debt_reported_filed_date"], "reported"])

    rows.append([ticker, "Cash & Equivalents (latest)", latest, lyf["cash_and_equivalents_usd"], "USD",
                 lyf["cash_xbrl_tag"], lyf["cash_accession"], lyf["cash_source_url"], lyf["cash_filed_date"], "reported"])

    mkt = snapshot["market_data"]
    for field, val, unit in [("Current Price", mkt["current_price_usd_per_share"], "USD/share"),
                              ("Shares Outstanding", mkt["shares_outstanding"], "shares"),
                              ("Beta", mkt["beta_levered_equity"], "unitless"),
                              ("Sector (GICS)", mkt["sector_gics"], "category")]:
        rows.append([ticker, field, mkt["retrieved_at_utc"], val, unit, f"yfinance {field}", None,
                     "https://finance.yahoo.com/quote/" + ticker, mkt["retrieved_at_utc"], "reported (unchanged from original freeze)"])
    return rows


# ---------------------------------------------------------------------------
# Mode: --rebuild-from-cache
# ---------------------------------------------------------------------------

XLSX_PATH = os.path.join(HERE, "independent_dcf_validation.xlsx")

# The four output "slots" this transaction publishes as one group. `snapshots` is
# a whole directory (its 4 files are swapped as a single unit); the other three
# are individual files. Order matters for the publish/rollback sequence below.
OUTPUT_SLOTS = [
    ("snapshots", SNAP_DIR, True),
    ("source_manifest.csv", SOURCE_MANIFEST_PATH, False),
    ("snapshot_manifest.json", SNAPSHOT_MANIFEST_PATH, False),
    ("independent_dcf_validation.xlsx", XLSX_PATH, False),
]

# ---------------------------------------------------------------------------
# ATOMICITY / RECOVERY GUARANTEES FOR --rebuild-from-cache -- stated precisely:
#
# What IS guaranteed: all four outputs (snapshots/, source_manifest.csv,
# snapshot_manifest.json, independent_dcf_validation.xlsx) are generated into
# an isolated staging directory and fully VALIDATED there (schema/invariants,
# manifest completeness, a full independent formula audit of the staged
# workbook including all 144 Table 5 cells, an error-token scan, and
# cross-artifact checksum consistency) before any live output file is
# touched. Publication then proceeds through an explicit PER-SLOT state
# machine (pending -> backed_up -> promoted, and on rollback -> restored or
# removed), tracked in Python data, not inferred from what happens to exist
# on disk, and crossed with a second, independent per-slot fact recorded
# once at backup time -- `had_original` -- since a slot can legitimately
# have NO live original before this transaction (rebuilding a not-yet-
# existing generated artifact is a valid recovery operation, not a
# precondition failure):
#   - A slot is marked 'backed_up' (and had_original=True) only AFTER its
#     live-to-backup rename has actually succeeded. A slot with no live
#     original stays 'pending'/had_original=False -- nothing is deleted
#     based on "well, it's still there" reasoning, which is exactly the bug
#     this design replaces (see "the P0 rollback defect" in
#     workbook_build_log.md §17). If backing up slot k fails, slots 0..k-1
#     (already backed up) are restored and slot k onward (never touched)
#     are left completely alone.
#   - A slot is marked 'promoted' only after its staged-to-live rename has
#     actually succeeded -- REGARDLESS of had_original. If promoting any
#     slot fails, rollback removes every 'promoted' slot's new content and,
#     ONLY for slots where had_original is True, restores its backup; a
#     promoted slot with had_original False is simply left absent again
#     (there is no backup to restore, and none is looked up -- indexing a
#     backup that was never created for exactly this reason was the second
#     bug found and fixed in workbook_build_log.md §19). Every 'backed_up'
#     (not-yet-promoted) slot is restored directly from its backup. This
#     confirms the full prior generation is live again ONLY IF every one of
#     those individual restore operations itself succeeds; if a rollback
#     step's own filesystem call fails (removing a promoted output,
#     renaming a backup back into place), that is reported by slot name
#     with a recovery path, the transaction directory is preserved (not
#     deleted), and the function returns a controlled failure -- it does
#     not raise, and it does not claim "fully restored" in that case.
#   - Once every slot reaches 'promoted', the transaction is COMMITTED: the
#     new generation is live, unconditionally. Only then does this code
#     attempt to delete the now-obsolete backups. If THAT cleanup step
#     fails, the function still returns success -- with a message that
#     begins literally "COMMITTED WITH CLEANUP WARNING" so callers (see
#     rebuild_from_cache's _report_publish_result) surface it distinctly
#     rather than folding it into an apparently-clean success report. It
#     never reports "publish failed" once the new generation is genuinely
#     live, and it never deletes the new generation just because deleting
#     old backups didn't work.
#   - Before touching anything, a preflight check refuses to start if a
#     leftover transaction-backup directory from a prior interrupted run is
#     found (see _find_txn_residue) -- such a directory is never silently
#     deleted, because it may be the only surviving copy of a previous
#     generation that a prior run failed to fully restore. This is also how
#     a cleanup-warning's retained backup is surfaced: the NEXT publish
#     attempt refuses safely until that residue is resolved.
# All of the above is exercised by three fault-injection self-tests: the
# original 11-scenario matrix (4 backup-phase failures, 4 promotion-phase
# failures, 1 cleanup failure, 1 pre-existing-residue refusal, 1 positive
# control); a missing-original matrix (each slot, and combinations of
# slots, originally absent, crossed with failures at every promotion
# position, backup-phase failures with absent originals, and a positive
# control with absent originals); and a rollback-operation-failure matrix
# (a filesystem failure INSIDE rollback's own remove-promoted-output and
# restore-backup steps) -- each asserting the live output set's exact
# content/hashes before and after, including that originally-absent slots
# stay absent and originally-present slots are restored byte-for-byte.
#
# What is NOT guaranteed: unlike Issue 3's --refresh-sec (where the entire
# published generation is ONE directory and the final promotion is a single
# os.rename(), which POSIX guarantees is atomic), this transaction publishes
# FOUR independent filesystem objects. There is no single OS-level primitive
# that swaps four unrelated paths atomically together, so this code performs
# an ordered sequence of renames with a software-level rollback protocol
# instead. A process-level failure (a raised Python exception, this script
# being sent SIGTERM during a normal exception-unwind) at any point during
# that sequence is provably recovered by the rollback logic above and its
# self-test. A hard OS crash, power loss, or `kill -9` (which bypasses
# Python's exception handling and any `finally` block entirely) occurring in
# the narrow window between two renames is NOT provably recovered -- that
# would require filesystem-level journaling/transactions this script does
# not implement. This limitation is inherent to publishing an independent
# 4-file group on a plain filesystem, not an oversight; it is stated here
# rather than left implicit or overclaimed as "a failure cannot leave a
# partial generation."
# ---------------------------------------------------------------------------

TXN_DIR_PREFIX = ".publish_txn_"


def _find_txn_residue(base_dir):
    """Returns a list of leftover transaction-backup directories (matching
    TXN_DIR_PREFIX) found directly under base_dir -- evidence of a prior
    publish that was interrupted (killed, crashed) before it could clean up
    after itself. Never deleted automatically; the caller refuses to start a
    new publish while any is present, since it may hold the only surviving
    copy of a previous generation."""
    if not os.path.isdir(base_dir):
        return []
    return sorted(
        os.path.join(base_dir, n) for n in os.listdir(base_dir)
        if n.startswith(TXN_DIR_PREFIX) and os.path.isdir(os.path.join(base_dir, n))
    )


def _preflight_check_slots(staging_dir, slots):
    """Validates preconditions before ANY live path is mutated. Confirms
    every staged slot actually exists and that staged/live path "shapes"
    (directory vs. file) match their slot declaration, so a promotion step
    later can't silently do the wrong kind of rename. Returns (ok, problems)."""
    problems = []
    for name, live_path, is_dir in slots:
        staged_path = os.path.join(staging_dir, name)
        if not os.path.exists(staged_path):
            problems.append(f"staged slot '{name}' does not exist at {staged_path}")
            continue
        if os.path.isdir(staged_path) != is_dir:
            problems.append(f"staged slot '{name}' type mismatch: declared is_dir={is_dir}, "
                             f"actual is_dir={os.path.isdir(staged_path)}")
        if os.path.exists(live_path) and os.path.isdir(live_path) != is_dir:
            problems.append(f"live slot '{name}' type mismatch: declared is_dir={is_dir}, "
                             f"actual is_dir={os.path.isdir(live_path)}")
    return (len(problems) == 0), problems


def _is_nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def _is_finite_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def _snapshot_schema_ok(snap):
    """Deeper schema/invariant check -- not merely 'does it parse' and not
    merely 'is the key present'. A required field that is present but holds
    None, an empty string, NaN, or the wrong basic type fails this check
    exactly like a missing key would; only a genuinely valid, nonempty value
    of the expected type passes. Returns (ok, msg)."""
    required = {"ticker", "entity_name", "cik", "latest_complete_fiscal_year_end",
                "historical_annual_data", "latest_year_facts", "market_data"}
    missing = required - set(snap.keys())
    if missing:
        return False, f"missing top-level keys: {sorted(missing)}"
    for key in ("ticker", "entity_name", "cik", "latest_complete_fiscal_year_end"):
        if not _is_nonempty_str(snap[key]):
            return False, f"top-level field {key!r} is not a nonempty string: {snap[key]!r}"
    if not snap["historical_annual_data"]:
        return False, "historical_annual_data is empty"

    lyf = snap.get("latest_year_facts") or {}
    lyf_numeric = ("pretax_income_usd", "tax_provision_usd", "interest_expense_usd",
                   "total_debt_usd", "cash_and_equivalents_usd")
    lyf_string = ("pretax_income_accession", "pretax_income_source_url", "pretax_income_filed_date",
                  "tax_provision_accession", "tax_provision_source_url", "tax_provision_filed_date",
                  "interest_expense_accession", "interest_expense_source_url", "interest_expense_filed_date",
                  "cash_accession", "cash_source_url", "cash_filed_date")
    lyf_missing = (set(lyf_numeric) | set(lyf_string)) - set(lyf.keys())
    if lyf_missing:
        return False, f"latest_year_facts missing keys: {sorted(lyf_missing)}"
    for key in lyf_numeric:
        if not _is_finite_number(lyf[key]):
            return False, f"latest_year_facts[{key!r}] is not a valid number: {lyf[key]!r}"
    for key in lyf_string:
        if not _is_nonempty_str(lyf[key]):
            return False, f"latest_year_facts[{key!r}] is not a nonempty string: {lyf[key]!r}"

    mkt = snap.get("market_data") or {}
    mkt_numeric = ("current_price_usd_per_share", "shares_outstanding", "beta_levered_equity")
    mkt_string = ("sector_gics", "retrieved_at_utc")
    mkt_missing = (set(mkt_numeric) | set(mkt_string)) - set(mkt.keys())
    if mkt_missing:
        return False, f"market_data missing keys: {sorted(mkt_missing)}"
    for key in mkt_numeric:
        if not _is_finite_number(mkt[key]):
            return False, f"market_data[{key!r}] is not a valid number: {mkt[key]!r}"
    for key in mkt_string:
        if not _is_nonempty_str(mkt[key]):
            return False, f"market_data[{key!r}] is not a nonempty string: {mkt[key]!r}"
    return True, "ok"


def verify_snapshots_directory(snap_dir):
    """Validates the snapshots/ directory as a SET: exactly the 4 required
    tickers present (no missing, no unexpected extras, no duplicates), each
    file's internal 'ticker' field matches its own filename, and every
    snapshot passes the full schema/invariant check above (not merely JSON
    parsing). Returns (ok, problems: list[str])."""
    problems = []
    if not os.path.isdir(snap_dir):
        return False, [f"{snap_dir} does not exist"]
    expected_files = {f"{tk}_snapshot.json" for tk in TICKERS}
    present_files = {f for f in os.listdir(snap_dir) if f.endswith("_snapshot.json")}
    missing = expected_files - present_files
    extra = present_files - expected_files
    if missing:
        problems.append(f"snapshots/ missing required file(s): {sorted(missing)}")
    if extra:
        problems.append(f"snapshots/ has unexpected extra file(s): {sorted(extra)}")
    seen_tickers = []
    for fn in sorted(expected_files & present_files):
        path = os.path.join(snap_dir, fn)
        try:
            snap = json.load(open(path))
        except Exception as e:
            problems.append(f"{fn} does not parse: {e}")
            continue
        expected_ticker = fn[: -len("_snapshot.json")]
        actual_ticker = snap.get("ticker")
        if actual_ticker != expected_ticker:
            problems.append(f"{fn}: internal 'ticker' field is {actual_ticker!r}, expected {expected_ticker!r}")
        elif actual_ticker in seen_tickers:
            problems.append(f"duplicate ticker {actual_ticker!r} across snapshot files")
        else:
            seen_tickers.append(actual_ticker)
        ok, msg = _snapshot_schema_ok(snap)
        if not ok:
            problems.append(f"{fn}: schema/invariant check FAILED: {msg}")
    return (len(problems) == 0), problems


REQUIRED_SNAPSHOT_MANIFEST_ENTRIES = sorted(f"snapshots/{tk}_snapshot.json" for tk in TICKERS)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def verify_snapshot_manifest(manifest_obj, base_dir):
    """Validates snapshot_manifest.json's structure and content against the
    fixed expected set of 4 entries (one per required ticker), reconciled
    against the actual files on disk under base_dir. An empty or missing
    'files' mapping, a missing required entry, or an unexpected extra entry
    are all explicit failures -- this function never vacuously passes on an
    empty mapping (the loop-only checksum check this replaces did exactly
    that: iterating zero entries reported zero problems). Returns
    (ok: bool, problems: list[str])."""
    problems = []
    files = manifest_obj.get("files") if isinstance(manifest_obj, dict) else None
    if not isinstance(files, dict):
        return False, ["snapshot_manifest.json 'files' is missing or not an object"]
    if not files:
        return False, [f"snapshot_manifest.json 'files' is EMPTY -- expected exactly "
                        f"{len(REQUIRED_SNAPSHOT_MANIFEST_ENTRIES)} entries: {REQUIRED_SNAPSHOT_MANIFEST_ENTRIES}"]
    present = set(files.keys())
    required = set(REQUIRED_SNAPSHOT_MANIFEST_ENTRIES)
    missing = required - present
    extra = present - required
    if missing:
        problems.append(f"snapshot_manifest.json missing required entries: {sorted(missing)}")
    if extra:
        problems.append(f"snapshot_manifest.json has unexpected extra entries: {sorted(extra)}")
    for relpath in sorted(required & present):
        info = files[relpath]
        sha = info.get("sha256") if isinstance(info, dict) else None
        if not sha or not _SHA256_RE.match(sha):
            problems.append(f"snapshot_manifest.json entry '{relpath}' has an invalid/missing sha256 value: {sha!r}")
            continue
        fpath = os.path.join(base_dir, relpath)
        if not os.path.exists(fpath):
            problems.append(f"snapshot_manifest.json entry '{relpath}' -- file does not exist at {fpath}")
            continue
        actual = sha256_file(fpath)
        if actual != sha:
            problems.append(f"snapshot_manifest.json checksum MISMATCH for '{relpath}': "
                             f"manifest={sha[:16]}... actual={actual[:16]}...")
    return (len(problems) == 0), problems


def verify_source_manifest_structure(rows, snapshots_by_ticker):
    """Structural completeness check over source_manifest.csv rows: for every
    company, the expected SET of rows is present exactly the expected number
    of times (not merely 'are the present rows' fields nonempty', which is
    verify_manifest_completeness's separate, complementary job). Also
    reconciles key scalar values back to the staged snapshots, and for
    derived Total Debt rollups, verifies the joined accession/URL/filed-date
    lists are genuinely deduplicated, contain no blank elements, and
    correspond to the DISTINCT SET of the component rows' own provenance
    values -- not a 1:1 count with the number of component rows, since
    dedupe_preserve_order() legitimately collapses identical values shared
    by components filed together in the same accession. Returns
    (ok: bool, problems: list[str])."""
    problems = []
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    for tk in TICKERS:
        trows = by_ticker.get(tk, [])
        if not trows:
            problems.append(f"[{tk}] no source_manifest.csv rows found at all")
            continue
        snap = snapshots_by_ticker.get(tk)
        n_hist = len(snap["historical_annual_data"]) if snap else None

        def count(field, _trows=trows):
            return sum(1 for r in _trows if r["field"] == field)

        def count_prefix(prefix, _trows=trows):
            return sum(1 for r in _trows if r["field"].startswith(prefix))

        if n_hist is not None:
            for label in ("Revenue", "EBIT/Operating Income"):
                c = count(label)
                if c != n_hist:
                    problems.append(f"[{tk}] expected exactly {n_hist} '{label}' row(s) (matching "
                                     f"historical_annual_data length), found {c}")

        for label in ("Pretax Income (latest)", "Tax Provision (latest)", "Interest Expense (latest)",
                      "Cash & Equivalents (latest)"):
            c = count(label)
            if c != 1:
                problems.append(f"[{tk}] expected exactly 1 '{label}' row, found {c}")

        for label in ("Current Price", "Shares Outstanding", "Beta", "Sector (GICS)"):
            c = count(label)
            if c != 1:
                problems.append(f"[{tk}] expected exactly 1 market row '{label}', found {c}")

        n_derived = count("Total Debt (latest, derived)")
        n_reported = count("Total Debt (latest, reported)")
        n_components = count_prefix("Total Debt component:")
        if n_derived + n_reported != 1:
            problems.append(f"[{tk}] expected exactly 1 total-debt row (derived XOR reported), "
                             f"found {n_derived} derived + {n_reported} reported")
        if n_derived == 1 and n_components == 0:
            problems.append(f"[{tk}] has a derived Total Debt row but 0 component rows")
        if n_reported == 1 and n_components != 0:
            problems.append(f"[{tk}] has a reported Total Debt row but also {n_components} component "
                             f"row(s) (reported and componentized total debt are mutually exclusive)")

        if snap:
            lyf = snap["latest_year_facts"]
            for label, expected in [("Pretax Income (latest)", lyf["pretax_income_usd"]),
                                     ("Tax Provision (latest)", lyf["tax_provision_usd"]),
                                     ("Cash & Equivalents (latest)", lyf["cash_and_equivalents_usd"])]:
                matching = [r for r in trows if r["field"] == label]
                if matching and matching[0]["raw_value"] not in (None, "") and expected is not None:
                    actual = float(matching[0]["raw_value"])
                    if actual != float(expected):
                        problems.append(f"[{tk}] '{label}' row value {actual} does not reconcile with "
                                         f"staged snapshot value {expected}")

        if n_derived == 1:
            derived_row = next(r for r in trows if r["field"] == "Total Debt (latest, derived)")
            component_rows = [r for r in trows if r["field"].startswith("Total Debt component:")]
            if len(component_rows) == len({r["field"] for r in component_rows}):
                comp_sum = sum(float(r["raw_value"]) for r in component_rows)
                derived_val = float(derived_row["raw_value"])
                if comp_sum != derived_val:
                    problems.append(f"[{tk}] derived Total Debt ({derived_val}) does not equal the sum "
                                     f"of its {len(component_rows)} component row(s) ({comp_sum})")
            else:
                problems.append(f"[{tk}] duplicate 'Total Debt component:' row(s) present -- cannot "
                                 f"reliably recheck the derived total against components")
            # NOTE: components that share the same SEC filing legitimately collapse to ONE joined
            # element (dedupe_preserve_order in extract_company() removes exact-duplicate values on
            # purpose) -- so the correct check is "the joined SET matches the components' own
            # distinct values", not "one joined element per component row".
            for colname in ("accession_number", "source_url", "filed_or_retrieved_date"):
                joined = derived_row[colname] or ""
                parts = [p.strip() for p in joined.split(";")] if joined else []
                if any(p == "" for p in parts):
                    problems.append(f"[{tk}] derived Total Debt row column '{colname}' contains a blank "
                                     f"joined element: {joined!r}")
                if len(parts) != len(set(parts)):
                    problems.append(f"[{tk}] derived Total Debt row column '{colname}' is NOT deduplicated "
                                     f"(duplicated provenance element): {joined!r}")
                expected_values = {r[colname].strip() for r in component_rows if r.get(colname)}
                if set(parts) != expected_values:
                    problems.append(f"[{tk}] derived Total Debt row column '{colname}' joined values "
                                     f"{sorted(set(parts))} do not correspond to the component rows' own "
                                     f"distinct values {sorted(expected_values)}")

        for r in trows:
            if classify_manifest_row(r["field"]) == "market":
                if not r["provenance_type"]:
                    problems.append(f"[{tk}] market row {r['field']!r} has an empty provenance_type")
                if not r["period_end_or_timestamp"]:
                    problems.append(f"[{tk}] market row {r['field']!r} has an empty retrieval timestamp")
                if snap:
                    expected_ts = snap["market_data"]["retrieved_at_utc"]
                    if r["period_end_or_timestamp"] != expected_ts:
                        problems.append(f"[{tk}] market row {r['field']!r} timestamp "
                                         f"{r['period_end_or_timestamp']!r} does not match staged snapshot's "
                                         f"market_data.retrieved_at_utc {expected_ts!r}")
    return (len(problems) == 0), problems


def _scan_xlsx_error_tokens(xlsx_path):
    tokens = ["#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NULL!", "#NUM!"]
    hits = []
    with zipfile.ZipFile(xlsx_path) as z:
        for n in z.namelist():
            if "worksheets" in n:
                text = z.read(n).decode("utf-8", errors="ignore")
                for t in tokens:
                    c = text.count(t)
                    if c:
                        hits.append((n, t, c))
    return hits


def _validate_staged_generation(staging_dir, staged_snap_dir, log):
    """Runs every required check against the STAGED outputs only. Returns
    (ok: bool, problems: list[str]). Never touches anything in the live output set."""
    problems = []

    log("  Validating snapshots/ directory (exact required ticker set, no extras/duplicates, full schema)...")
    ok, snap_problems = verify_snapshots_directory(staged_snap_dir)
    if not ok:
        problems.extend(snap_problems)

    staged_snapshots_by_ticker = {}
    for tk in TICKERS:
        path = os.path.join(staged_snap_dir, f"{tk}_snapshot.json")
        if os.path.exists(path):
            try:
                staged_snapshots_by_ticker[tk] = json.load(open(path))
            except Exception:
                pass  # already reported by verify_snapshots_directory above

    log("  Validating source_manifest.csv structural completeness (expected row set + reconciliation to staged snapshots)...")
    manifest_path = os.path.join(staging_dir, "source_manifest.csv")
    rows = None
    try:
        rows = list(csv.DictReader(open(manifest_path)))
        ok, structure_problems = verify_source_manifest_structure(rows, staged_snapshots_by_ticker)
        if not ok:
            problems.extend(structure_problems)
    except Exception as e:
        problems.append(f"source_manifest.csv could not be structurally validated: {e}")

    log("  Validating source_manifest.csv field-level provenance completeness...")
    if rows is not None:
        ok, manifest_problems = verify_manifest_completeness(rows)
        if not ok:
            for tk, field, col, msg in manifest_problems:
                problems.append(f"source_manifest.csv incomplete: [{tk}] {field!r} column '{col}' -- {msg}")

    log("  Validating snapshot_manifest.json (exact required entry set, no vacuous pass on empty, checksums)...")
    try:
        staged_manifest = json.load(open(os.path.join(staging_dir, "snapshot_manifest.json")))
        ok, manifest_struct_problems = verify_snapshot_manifest(staged_manifest, staging_dir)
        if not ok:
            problems.extend(manifest_struct_problems)
    except Exception as e:
        problems.append(f"snapshot_manifest.json could not be validated: {e}")

    log("  Running independent formula audit against the staged workbook...")
    staged_xlsx = os.path.join(staging_dir, "independent_dcf_validation.xlsx")
    try:
        sys.path.insert(0, HERE)
        import formula_audit as fa
        rc, failures, counters = fa.run_audit(staged_xlsx)
        if fa.audit_exit_code(rc, counters) != 0:
            problems.append(f"staged workbook formula audit FAILED: {len(failures)} defect(s), "
                             f"Table 5 coverage {counters.get('table5_checked', 0)}/{fa.REQUIRED_GRID_CELLS_TOTAL}")
        else:
            log(f"    formula audit PASS ({counters.get('table5_checked', 0)}/{fa.REQUIRED_GRID_CELLS_TOTAL} "
                f"Table 5 cells examined)")
    except Exception as e:
        problems.append(f"staged workbook formula audit could not run: {e}")

    log("  Scanning staged workbook for Excel error-string tokens...")
    try:
        hits = _scan_xlsx_error_tokens(staged_xlsx)
        if hits:
            for sheet, token, count in hits:
                problems.append(f"staged workbook contains {token} x{count} in {sheet}")
    except Exception as e:
        problems.append(f"staged workbook error-token scan could not run: {e}")

    return (len(problems) == 0), problems


def _publish_staged_generation(staging_dir, fail_at_backup_slot=None, fail_at_slot=None,
                                fail_at_cleanup=False, fail_removing_promoted=None,
                                fail_restoring_backup=None, log=print, slots=None, base_dir=None):
    """Publishes the staged output slots as one group, using an explicit
    per-slot state machine tracked in Python data -- never inferred from
    what happens to still exist on disk (that inference is exactly what
    caused the P0 defect this function originally replaced: a backup
    failure on slot k used to delete slots k+1..N-1 outright).

    Two dimensions are tracked per slot, because they are genuinely
    independent:
      - `state[name]`: 'pending' -> 'backed_up' -> 'promoted', and on
        rollback, 'restored' (an original was put back) or 'removed' (a
        promoted output was taken away and nothing is put back, because
        there was no original to restore).
      - `had_original[name]`: recorded once, at backup time -- True if this
        slot had a live original that was backed up; False if the slot was
        absent before this transaction (a legitimate case: e.g. rebuilding
        a generated artifact that does not exist yet). This is NOT the same
        thing as `state`, and conflating the two (assuming every 'promoted'
        slot has a backup to fall back on) was the second bug found in this
        function: rollback unconditionally indexed `backup_paths[name]`,
        which raised KeyError for a slot that was promoted over a
        previously-absent original, aborting rollback partway and leaving
        later slots' restoration never attempted.

    Required rollback behavior, by combination:
      - promoted + had_original:      remove the promoted output, restore its backup.
      - promoted + NOT had_original:  remove the promoted output, leave the slot absent.
      - backed_up (not promoted):     restore its backup.
      - pending (untouched):          left exactly as it was.

    fail_at_backup_slot / fail_at_slot (0-indexed) inject a failure during
    the backup phase / promotion phase at that slot's position.
    fail_at_cleanup injects a failure during post-commit backup removal.
    fail_removing_promoted / fail_restoring_backup (slot NAME) inject a
    failure INSIDE rollback itself, at the "remove the new output" step or
    the "rename the backup back into place" step respectively -- these
    failures are caught and reported (see below), never allowed to raise
    out of this function. None of these five hooks are ever set by the real
    CLI path.

    `slots` defaults to the real OUTPUT_SLOTS; the fault-injection self-test
    passes a list of temp-directory paths instead, so it never touches the
    real live outputs. `base_dir` is where the unique transaction-backup
    directory is created (defaults to HERE); the self-test overrides it to a
    temp directory so residue detection never inspects the real directory.

    Returns (ok: bool, message: str) and does not raise for any failure
    class exercised by this module's fault-injection self-tests (backup
    failure, promotion failure, cleanup failure, and a failure inside
    rollback's own remove/restore steps). This is a claim about what is
    TESTED, not a guarantee against every conceivable OS/filesystem error:
    if a rollback step's own filesystem operation fails, this function
    reports which slot(s) could not be safely restored, gives their
    recovery path inside the preserved transaction directory, and returns
    False -- it does not claim "fully restored" or "live outputs untouched"
    in that case, only in the case where every rollback step actually
    succeeded. On success with a cleanup-phase failure, the message begins
    with the literal string "COMMITTED WITH CLEANUP WARNING" so callers can
    detect and surface it distinctly, rather than it being folded silently
    into an apparently-clean success report.
    """
    slots = slots if slots is not None else OUTPUT_SLOTS
    base_dir = base_dir if base_dir is not None else HERE

    # ---- Preflight: nothing below this point mutates anything until every check passes ----
    residue = _find_txn_residue(base_dir)
    if residue:
        msg = (f"unresolved transaction residue from a prior interrupted run: {residue} -- "
               f"left in place for manual inspection, not deleted")
        log(f"  REFUSING to publish: {msg}")
        return False, msg

    ok, problems = _preflight_check_slots(staging_dir, slots)
    if not ok:
        msg = "; ".join(problems)
        log(f"  REFUSING to publish: {msg}")
        return False, msg

    txn_dir = tempfile.mkdtemp(dir=base_dir, prefix=TXN_DIR_PREFIX)
    state = {name: "pending" for name, _, _ in slots}
    had_original = {name: False for name, _, _ in slots}
    backup_paths = {}

    def restore_all_touched():
        """Restores every slot currently 'backed_up' or 'promoted'. Never
        indexes backup_paths for a slot that was promoted with no original
        -- that slot is simply left absent (state -> 'removed'). Returns a
        list of (name, reason, detail) tuples for any slot that could NOT
        be safely restored (a rollback-internal filesystem operation
        itself failed); an empty list means every touched slot is confirmed
        back to its pre-transaction state."""
        unrestorable = []
        for name, live_path, is_dir in slots:
            if state[name] not in ("backed_up", "promoted"):
                continue  # 'pending' -- never touched, nothing to do

            if state[name] == "promoted":
                if os.path.exists(live_path):
                    try:
                        if fail_removing_promoted == name:
                            raise OSError(f"TEST-INJECTED failure removing promoted output for slot '{name}'")
                        if is_dir:
                            shutil.rmtree(live_path)
                        else:
                            os.remove(live_path)
                    except OSError as e:
                        unrestorable.append((name, "could not remove the promoted (new) output", str(e)))
                        continue  # leave state as 'promoted' -- do not attempt to also restore a backup
                if not had_original[name]:
                    state[name] = "removed"  # correctly rolled back to absent; no backup to restore
                    continue

            # Reached only for: state == 'backed_up', or state == 'promoted' with had_original True
            # (and the promoted output was just successfully removed above). Either way,
            # had_original[name] is True here, so backup_paths[name] is guaranteed to exist.
            backup_path = backup_paths[name]
            try:
                if fail_restoring_backup == name:
                    raise OSError(f"TEST-INJECTED failure restoring backup for slot '{name}'")
                if os.path.exists(backup_path):
                    os.rename(backup_path, live_path)
                    state[name] = "restored"
                else:
                    unrestorable.append((name, "backup path is missing", backup_path))
            except OSError as e:
                unrestorable.append((name, "could not rename the backup back into place", str(e)))
        return unrestorable

    try:
        # ---- Phase A: back up every currently-live slot, one at a time, in order ----
        for idx, (name, live_path, is_dir) in enumerate(slots):
            if fail_at_backup_slot == idx:
                raise RuntimeError(f"TEST-INJECTED failure backing up slot '{name}'")
            if os.path.exists(live_path):
                backup_path = os.path.join(txn_dir, f"backup__{name.replace('/', '_')}")
                os.rename(live_path, backup_path)
                backup_paths[name] = backup_path
                had_original[name] = True
                state[name] = "backed_up"
            # else: this slot has no live original -- had_original stays False, state
            # stays 'pending'. Rebuilding a not-yet-existing generated artifact is a
            # valid recovery operation, so an absent original is not a preflight failure.

        # ---- Phase B: promote each staged slot into place, one at a time, in order ----
        for idx, (name, live_path, is_dir) in enumerate(slots):
            if fail_at_slot == idx:
                raise RuntimeError(f"TEST-INJECTED failure promoting slot '{name}'")
            staged_path = os.path.join(staging_dir, name)
            os.rename(staged_path, live_path)
            state[name] = "promoted"

    except BaseException as e:
        log(f"  Publish FAILED before commit ({e}) -- restoring every backed-up/promoted slot; "
            f"slots never touched are left exactly as they were.")
        unrestorable = restore_all_touched()
        if unrestorable:
            recovery = [f"[{name}] {reason} ({detail})" for name, reason, detail in unrestorable]
            log(f"  ROLLBACK INCOMPLETE -- {len(unrestorable)} slot(s) could not be safely restored: {recovery}")
            return False, (f"publish failed AND rollback could not fully restore the prior generation -- "
                            f"{len(unrestorable)} slot(s) unresolved: {recovery}. The transaction directory "
                            f"{txn_dir} was preserved (NOT deleted) so its contents remain available for "
                            f"manual recovery; this function does NOT claim the prior generation is fully "
                            f"restored or that live outputs are untouched in this case.")
        try:
            shutil.rmtree(txn_dir)
        except OSError as cleanup_e:
            log(f"  (non-fatal) could not remove empty/unused transaction directory {txn_dir}: {cleanup_e}")
            return False, (f"publish failed and the prior generation was fully restored (every touched "
                            f"slot confirmed back to its original state), BUT the now-empty transaction "
                            f"directory {txn_dir} could not itself be removed ({cleanup_e}) -- harmless "
                            f"residue; a later publish attempt will refuse until it is deleted manually.")
        return False, f"publish failed and was fully rolled back to the prior generation: {e}"

    # ---- Commit point: every slot reached 'promoted' -> the new generation is live. ----
    log("  All slots promoted -- transaction is COMMITTED (new generation is live).")

    # ---- Post-commit cleanup: remove the now-obsolete backups. A failure here must
    # NEVER be reported as "publish failed" -- the new generation is already live and
    # correct; only the retained backup directory is at risk, not any live data. ----
    try:
        if fail_at_cleanup:
            raise RuntimeError("TEST-INJECTED failure during post-commit backup cleanup")
        shutil.rmtree(txn_dir)
        return True, "published successfully (all slots committed; prior-generation backups removed)"
    except BaseException as e:
        return True, (f"COMMITTED WITH CLEANUP WARNING: all slots committed (new generation is live and "
                       f"correct), BUT post-commit cleanup of the prior generation's backups FAILED and "
                       f"they were retained at {txn_dir}: {e}. The retained directory is a leftover "
                       f"recovery artifact, not an error condition; the next publish attempt will detect "
                       f"it as pre-existing transaction residue and refuse until it is removed manually.")


CLEANUP_WARNING_PREFIX = "COMMITTED WITH CLEANUP WARNING"


def _report_publish_result(published, msg, log=print):
    """Shared reporting logic between rebuild_from_cache() and its self-test
    (run_rollback_operation_failure_self_test / the cleanup-warning scenario
    in run_publish_fault_injection_self_test): the message returned by
    _publish_staged_generation() is ALWAYS surfaced, and a post-commit
    cleanup warning is called out explicitly and loudly -- it must never be
    reported as, or read as, a completely clean success."""
    log(f"  {msg}")
    if not published:
        log(f"REFUSING/ROLLED BACK: {msg}")
        return
    if msg.startswith(CLEANUP_WARNING_PREFIX):
        log(f"\n*** {CLEANUP_WARNING_PREFIX} ***")
        log("The new generation is live and correct, but the prior generation's backup could not be "
            "cleaned up automatically -- see the retained path in the message above. This is NOT a "
            "completely clean success: the next publish attempt will refuse until that residue is "
            "resolved (deleted manually, after confirming it is no longer needed).")
    else:
        log("\nRebuild published successfully.")


def rebuild_from_cache(fail_at_slot=None, log=print):
    log("=== --rebuild-from-cache ===")
    log("Verifying raw cache against cache_manifest.json before doing anything else...")
    ok, problems = verify_cache_against_manifest()
    if not ok:
        log("REFUSING to rebuild -- raw cache does not match cache_manifest.json:")
        for p in problems:
            log(f"  - {p}")
        return 1
    log("  Cache verified OK (5/5 files match recorded SHA-256 + size).")

    for tk in TICKERS:
        existing_path = os.path.join(SNAP_DIR, f"{tk}_snapshot.json")
        if not os.path.exists(existing_path):
            log(f"REFUSING to rebuild -- required existing snapshot missing: {existing_path}")
            log("  (market data is carried forward from the existing snapshot, never re-fetched; "
                "without it there is nothing to carry forward.)")
            return 1

    staging_dir = tempfile.mkdtemp(dir=HERE, prefix=".rebuild_staging_")
    try:
        # ---- Phase 1: generate ALL outputs into staging; nothing live is touched ----
        log(f"Phase 1: generating snapshots/manifest/csv/workbook into staging "
            f"({os.path.basename(staging_dir)}/)...")
        staged_snap_dir = os.path.join(staging_dir, "snapshots")
        os.makedirs(staged_snap_dir)

        cik_map = get_cik_map()
        all_snapshots = {}
        all_manifest_rows = []
        for tk in TICKERS:
            cik = cik_map[tk]
            facts = json.load(open(os.path.join(CACHE_DIR, f"companyfacts_{tk}.json")))
            existing = json.load(open(os.path.join(SNAP_DIR, f"{tk}_snapshot.json")))
            snapshot = generate_snapshot(
                tk, cik, facts,
                existing_market_data=existing["market_data"],
                existing_data_sources=existing["data_sources"],
                existing_units=existing["units"],
                existing_retrieval_ts=existing["retrieval_timestamp_utc"],
                existing_derived_note=existing["derived_values_NOT_frozen_facts"],
            )
            all_snapshots[tk] = snapshot
            all_manifest_rows.extend(build_manifest_rows(tk, snapshot))
            with open(os.path.join(staged_snap_dir, f"{tk}_snapshot.json"), "w") as f:
                json.dump(snapshot, f, indent=2, sort_keys=True, ensure_ascii=True)

        import io
        sio = io.StringIO()
        w = csv.writer(sio, lineterminator="\n")
        w.writerow(["ticker", "field", "period_end_or_timestamp", "raw_value", "units", "xbrl_tag_or_source_field",
                    "accession_number", "source_url", "filed_or_retrieved_date", "provenance_type"])
        w.writerows(all_manifest_rows)
        with open(os.path.join(staging_dir, "source_manifest.csv"), "w", newline="") as f:
            f.write(sio.getvalue())

        checksum_entries = {}
        for tk in TICKERS:
            path = os.path.join(staged_snap_dir, f"{tk}_snapshot.json")
            checksum_entries[f"snapshots/{tk}_snapshot.json"] = {"sha256": sha256_file(path), "ticker": tk}
        with open(os.path.join(staging_dir, "snapshot_manifest.json"), "w") as f:
            json.dump({
                "description": "SHA-256 checksums of every frozen snapshot file. Deterministic: contains no "
                                "wall-clock regeneration timestamp by design (see module docstring, 'Determinism design').",
                "hash_algorithm": "SHA-256",
                "files": checksum_entries,
            }, f, indent=2, sort_keys=True)

        sys.path.insert(0, HERE)
        import build_workbook as bw
        staged_xlsx_path = os.path.join(staging_dir, "independent_dcf_validation.xlsx")
        bw.build_workbook(staged_xlsx_path, snap_dir=staged_snap_dir)
        log(f"  Staged workbook built: {staged_xlsx_path}")

        # ---- Phase 2: validate the ENTIRE staged generation before publishing anything ----
        log("Phase 2: validating the staged generation (nothing live touched yet)...")
        ok, validation_problems = _validate_staged_generation(staging_dir, staged_snap_dir, log)
        if not ok:
            log(f"\nREFUSING to publish -- {len(validation_problems)} validation problem(s) in the staged "
                f"generation (live outputs are completely untouched):")
            for p in validation_problems:
                log(f"  - {p}")
            return 1
        log("  All staged-generation validations PASSED.")

        # ---- Phase 3: publish as one rollback-protected group operation ----
        log("Phase 3: publishing all 4 output slots as one group (snapshots/, source_manifest.csv, "
            "snapshot_manifest.json, independent_dcf_validation.xlsx)...")
        published, msg = _publish_staged_generation(staging_dir, fail_at_slot=fail_at_slot, log=log)
        _report_publish_result(published, msg, log=log)
        if not published:
            return 1

        log(f"\n(Wall-clock completion time is printed to the console only -- never written into any "
            f"generated file's content, per this module's determinism design.)")
        log(f"  snapshots/*.json: {len(all_snapshots)} files")
        log(f"  source_manifest.csv: {len(all_manifest_rows)} rows")
        log(f"  snapshot_manifest.json: {len(checksum_entries)} checksums")
        log(f"  independent_dcf_validation.xlsx: rebuilt and formula-audit-clean")
        log("  Network requests made: 0 (offline rebuild from sec_raw_cache/ only)")
        return 0
    finally:
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir)


# ---------------------------------------------------------------------------
# Mode: verify (default)
# ---------------------------------------------------------------------------

def verify():
    print("=== Verify mode (default, read-only, offline) ===")
    all_ok = True

    print("\n[1/6] Raw cache vs cache_manifest.json")
    ok, problems = verify_cache_against_manifest()
    if ok:
        print("  PASS -- all 5 cache files match recorded SHA-256 + size.")
    else:
        all_ok = False
        print("  FAIL:")
        for p in problems:
            print(f"    - {p}")

    print("\n[2/6] snapshots/ directory: exact required ticker set (no missing/extra/duplicate), full schema/invariants")
    ok, problems = verify_snapshots_directory(SNAP_DIR)
    if ok:
        print(f"  PASS -- exactly the {len(TICKERS)} required tickers present, each schema-valid "
              f"(top-level keys, latest-fact provenance, market-data fields).")
    else:
        all_ok = False
        print(f"  FAIL -- {len(problems)} problem(s):")
        for p in problems:
            print(f"    - {p}")

    snapshots_by_ticker = {}
    for tk in TICKERS:
        path = os.path.join(SNAP_DIR, f"{tk}_snapshot.json")
        if os.path.exists(path):
            try:
                snapshots_by_ticker[tk] = json.load(open(path))
            except Exception:
                pass

    print("\n[3/6] snapshot_manifest.json: exact required entry set (empty/missing/extra all fail), checksums")
    if not os.path.exists(SNAPSHOT_MANIFEST_PATH):
        all_ok = False
        print(f"  FAIL -- {SNAPSHOT_MANIFEST_PATH} not found")
    else:
        manifest = json.load(open(SNAPSHOT_MANIFEST_PATH))
        ok, problems = verify_snapshot_manifest(manifest, HERE)
        if ok:
            print(f"  PASS -- exactly {len(REQUIRED_SNAPSHOT_MANIFEST_ENTRIES)} required entries present, "
                  f"each with a valid SHA-256 that matches the file on disk.")
        else:
            all_ok = False
            print(f"  FAIL -- {len(problems)} problem(s):")
            for p in problems:
                print(f"    - {p}")

    print("\n[4/6] source_manifest.csv: structural completeness (expected row set per company), derived-debt "
          "recheck, dedup/no-blank rollup provenance, reconciliation to snapshots")
    if not os.path.exists(SOURCE_MANIFEST_PATH):
        all_ok = False
        rows = []
        print(f"  FAIL -- {SOURCE_MANIFEST_PATH} not found")
    else:
        rows = list(csv.DictReader(open(SOURCE_MANIFEST_PATH)))
        ok, problems = verify_source_manifest_structure(rows, snapshots_by_ticker)
        if ok:
            print(f"  PASS -- every company has exactly its expected row set (history rows, latest facts, "
                  f"market fields, exactly one total-debt row), derived Total Debt ties to its component "
                  f"rows, rollup provenance lists are deduplicated with no blanks, and values reconcile "
                  f"to the current snapshots.")
        else:
            all_ok = False
            print(f"  FAIL -- {len(problems)} problem(s):")
            for p in problems:
                print(f"    - {p}")

    print("\n[5/6] source_manifest.csv: field-level provenance completeness (exact company/field/column)")
    if rows:
        ok, problems = verify_manifest_completeness(rows)
        if ok:
            print(f"  PASS -- all {len(rows)} rows carry every required provenance field for their category "
                  f"(reported fact / derived rollup / market data).")
        else:
            all_ok = False
            print(f"  FAIL -- {len(problems)} missing required field(s):")
            for tk, field, col, msg in problems:
                print(f"    - [{tk}] {field!r}: column '{col}' -- {msg}")
    else:
        all_ok = False
        print("  FAIL -- no rows to check (source_manifest.csv missing or empty)")

    print("\n[6/6] No hard-coded email addresses (generic detector, self-inspecting, workbook-inspecting)")
    ok, leaks, scope_note = scan_for_pii(HERE)
    print(f"  Scope: {scope_note}")
    if ok:
        print("  PASS -- no email-address pattern found in that scope.")
    else:
        all_ok = False
        print(f"  FAIL -- {len(leaks)} possible email-address match(es) found:")
        for loc, match in leaks:
            print(f"    - {loc}: {match}")

    print(f"\n=== Overall: {'PASS' if all_ok else 'FAIL'} ===")
    print("No file was written. No network connection was attempted.")
    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# PII scan (Issue 4 correction): generic email-address detector, no self-exemption,
# also inspects the generated .xlsx's own strings (shared strings, inline strings,
# worksheet cell text, docProps metadata).
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# RFC 2606 reserves example.com/.net/.org specifically for documentation and testing.
# This is the ONLY allowlisted domain, used ONLY so this file's own self-test injection
# fixture (a synthetic email used to prove the detector works -- see run_pii_regression_self_test
# in formula_audit-style self-tests) does not have to be treated as a real leak if it were
# ever left somewhere by accident. Nothing else is allowlisted: not gmail.com, not any
# other personal or free-mail domain, not any specific address.
_ALLOWLISTED_EXAMPLE_DOMAINS = ("example.com", "example.net", "example.org")


def _is_allowlisted(match_text):
    domain = match_text.split("@", 1)[1].lower() if "@" in match_text else ""
    return domain in _ALLOWLISTED_EXAMPLE_DOMAINS


def scan_text_for_emails(text, location_label, leaks):
    for m in EMAIL_RE.finditer(text):
        if _is_allowlisted(m.group(0)):
            continue
        leaks.append((location_label, m.group(0)))


def scan_for_pii(base_dir):
    """Scans every retained .py/.md/.csv/.json file under base_dir (INCLUDING this
    file itself -- no self-exemption), plus the generated .xlsx's internal strings
    (shared strings, inline strings, every worksheet's cell text, docProps/core.xml
    and docProps/app.xml). Raw SEC EDGAR responses in sec_raw_cache/ are explicitly
    excluded from this scan and stated as such -- they are unmodified third-party
    data that can legitimately contain filer/registrant contact information as part
    of the public filing itself, which is out of scope for "did WE hard-code a
    personal contact string." That exclusion is named here, not silently applied.
    Returns (ok: bool, leaks: list[(location, matched_text)], scope_note: str).
    """
    leaks = []
    excluded_dirs = {"sec_raw_cache", ".git"}
    scanned_files = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        for fn in files:
            if fn.endswith((".py", ".md", ".csv", ".json")):
                path = os.path.join(root, fn)
                scanned_files.append(path)
                try:
                    text = open(path, encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                scan_text_for_emails(text, os.path.relpath(path, base_dir), leaks)

    xlsx_path = os.path.join(base_dir, "independent_dcf_validation.xlsx")
    xlsx_scanned = False
    if os.path.exists(xlsx_path):
        xlsx_scanned = True
        with zipfile.ZipFile(xlsx_path) as z:
            names = z.namelist()
            targets = [n for n in names if n == "xl/sharedStrings.xml"
                       or n.startswith("xl/worksheets/") or n in ("docProps/core.xml", "docProps/app.xml")]
            for part in targets:
                try:
                    text = z.read(part).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                scan_text_for_emails(text, f"independent_dcf_validation.xlsx:{part}", leaks)

    scope_note = (
        f"{len(scanned_files)} .py/.md/.csv/.json files under {os.path.basename(base_dir)}/ "
        f"(recursively, INCLUDING this script's own source -- no self-exemption), "
        f"EXCLUDING sec_raw_cache/ (unmodified third-party SEC EDGAR data, stated exclusion, "
        f"not silently dropped) and .git/"
    )
    if xlsx_scanned:
        scope_note += "; plus independent_dcf_validation.xlsx's shared strings, worksheet cell text, and docProps metadata"
    else:
        scope_note += "; independent_dcf_validation.xlsx not found, so its internal strings were NOT scanned this run"

    return (len(leaks) == 0), leaks, scope_note


# ---------------------------------------------------------------------------
# Mode: --refresh-sec (NOT invoked during this correction pass)
#
# ATOMICITY / RECOVERY GUARANTEES -- stated precisely, not overclaimed:
#
# What IS guaranteed: every download is fetched and validated (JSON-parseable,
# correct structural fields, CIK/ticker identity cross-checked) into an
# isolated STAGING directory before any live file is touched. The publish
# step that makes the new generation live proceeds through an explicit
# per-transaction state machine ('pending' -> 'backed_up' -> 'promoted'),
# using a UNIQUE transaction-specific backup directory (never a fixed
# ".rollback_backup" suffix, and never silently deleted if one is found
# pre-existing -- see _find_refresh_txn_residue):
#   - The live cache is renamed into the transaction dir; this is only
#     considered 'backed_up' after that rename actually succeeds.
#   - The staged generation is then renamed into place as the live cache;
#     this is only considered 'promoted' after THAT rename actually
#     succeeds. If it fails, this code ATTEMPTS to rename the backed-up
#     original straight back. If that restore-rename itself succeeds, the
#     prior live cache is confirmed fully restored before returning
#     failure. If that restore-rename ITSELF fails (a genuinely independent
#     filesystem failure, not just the original promotion failure) -- this
#     is the defect an independent review reproduced and this correction
#     pass fixed (see workbook_build_log.md §20) -- that failure is caught
#     explicitly rather than allowed to escape or be misreported: the
#     result is REFRESH_ROLLBACK_INCOMPLETE_PREFIX, live cache state is
#     stated as possibly absent (not "restored", not "untouched", not
#     "unchanged"), and the transaction directory + backup path are
#     preserved and named for manual recovery.
#   - POSIX rename() of a directory within the same filesystem is atomic at
#     the OS level for each of those renames individually: at every
#     instant, the path sec_raw_cache/ resolves to either the complete OLD
#     generation or the complete NEW generation, never a partial mix -- but
#     atomicity of a single rename does not make the overall multi-rename
#     rollback sequence atomic; the sequence itself can fail partway, which
#     is exactly the incomplete-rollback case above.
#   - Once the promotion rename succeeds, the transaction is COMMITTED: the
#     new cache is live, unconditionally. Only then is the old backup
#     deleted. If that post-commit cleanup fails, this code still reports
#     success (with a warning, CLEANUP_WARNING_PREFIX) -- it never claims
#     "refresh failed, cache unchanged" once the new cache has genuinely
#     become live, and it never deletes the new cache just because deleting
#     the old backup failed.
#   - Before touching anything, a preflight check refuses to start if a
#     transaction-backup directory from a prior interrupted run is found --
#     it is left in place for manual inspection, never silently deleted,
#     because it may be the only surviving copy of the prior cache. This is
#     also how an incomplete-rollback's or a cleanup-warning's retained
#     backup is surfaced: the next refresh attempt refuses safely until
#     that residue is resolved.
#
# What is NOT guaranteed: true crash consistency against a hard power loss or
# `kill -9` occurring in the middle of an os.rename() syscall (or between
# renames) is a property of the underlying filesystem and OS, not of this
# Python script, and is not tested here (this correction pass has no way to
# inject a real OS-level crash -- fault injection here means a Python
# exception raised at a chosen point, which exercises normal exception
# unwinding and any `finally` blocks; `kill -9` bypasses both). The guarantee
# this code and its self-test actually establish is process-level and is now
# a five-way distinction, not a blanket claim: (1) a failure before the live
# cache was touched leaves it genuinely untouched; (2) a promotion failure
# followed by a successful restore leaves the prior cache fully restored;
# (3) a promotion failure followed by a FAILED restore is reported as an
# explicit, named incomplete-rollback state -- never as "restored" or
# "unchanged"; (4) a successful promotion is a committed success; (5) a
# post-commit cleanup failure is a committed success with an explicit
# warning. Every one of these five is exercised by this module's
# fault-injection self-test with a before/after hash comparison.
# ---------------------------------------------------------------------------

REFRESH_TXN_DIR_PREFIX = ".sec_cache_refresh_txn_"
LEGACY_REFRESH_BACKUP_SUFFIX = ".rollback_backup"  # produced only by this file's now-replaced prior
# implementation; still checked for and treated as residue so an old leftover from before this
# correction pass is never silently deleted or overwritten either.


def _find_refresh_txn_residue(parent_dir, cache_dir):
    """Returns a list of leftover refresh-transaction artifacts (either the
    current unique-prefix scheme or the legacy fixed-suffix scheme) found
    next to cache_dir -- evidence of a prior refresh that was interrupted
    before it could clean up. Never deleted automatically."""
    residue = []
    if os.path.isdir(parent_dir):
        for n in os.listdir(parent_dir):
            full = os.path.join(parent_dir, n)
            if n.startswith(REFRESH_TXN_DIR_PREFIX) and os.path.isdir(full):
                residue.append(full)
    legacy_backup = cache_dir + LEGACY_REFRESH_BACKUP_SUFFIX
    if os.path.isdir(legacy_backup):
        residue.append(legacy_backup)
    return residue


class RefreshTransactionError(Exception):
    """Raised for a fetch/validation failure during staged_refresh -- always
    caught before reaching Phase 3, so the live cache directory is genuinely
    untouched (confirmed by the fault-injection self-test's before/after
    hash comparison). A rollback step's OWN filesystem operation failing
    (e.g. restoring the backup after a failed promotion) is a SEPARATE,
    now-also-tested case -- see _publish_refresh_transaction's docstring
    and REFRESH_ROLLBACK_INCOMPLETE_PREFIX for how that is handled and
    reported (never as "untouched")."""


REFRESH_ROLLBACK_INCOMPLETE_PREFIX = "REFRESH FAILED AND ROLLBACK INCOMPLETE"


def _publish_refresh_transaction(cache_dir, parent_dir, staging_dir, fail_at_backup=False,
                                  fail_at_publish=False, fail_at_cleanup=False,
                                  fail_restoring_backup=False, log=print):
    """Publishes one staged sec_raw_cache/ generation over the live cache_dir
    via an explicit per-step state machine ('pending' -> 'backed_up' ->
    'promoted', and on rollback -> 'restored'), mirroring
    _publish_staged_generation's design for the single-directory case.
    fail_at_backup / fail_at_publish / fail_at_cleanup / fail_restoring_backup
    are TEST-ONLY hooks (never set by the real --refresh-sec CLI path).
    fail_restoring_backup injects a failure specifically during rollback's
    OWN restore-rename step (renaming the backup cache directory back into
    place after a failed promotion) -- distinct from fail_at_publish, which
    fails the ORIGINAL promotion that triggers rollback in the first place.

    Returns (ok: bool, message: str) and does not raise for any of the four
    failure classes this module's fault-injection self-test exercises
    (backup failure, promotion failure, promotion failure followed by a
    restore-rename failure, and post-commit cleanup failure). If restoring
    the backup itself fails, this function does NOT claim the prior cache
    was restored, untouched, or unchanged -- it returns a message beginning
    with the literal string REFRESH_ROLLBACK_INCOMPLETE_PREFIX, states that
    the live cache directory may be absent or incomplete, and gives the
    exact retained transaction directory and backup path for manual
    recovery. The transaction directory is preserved (not deleted) in that
    case, so the next refresh attempt detects it as residue and refuses.
    """
    txn_dir = None
    backup_dir = None
    state = "pending"
    try:
        if os.path.isdir(cache_dir):
            txn_dir = tempfile.mkdtemp(dir=parent_dir, prefix=REFRESH_TXN_DIR_PREFIX)
            backup_dir = os.path.join(txn_dir, "cache_backup")
            if fail_at_backup:
                raise RefreshTransactionError("TEST-INJECTED failure backing up the live cache directory")
            os.rename(cache_dir, backup_dir)
            state = "backed_up"

        if fail_at_publish:
            raise RefreshTransactionError("TEST-INJECTED failure promoting the staged cache directory")
        os.rename(staging_dir, cache_dir)
        state = "promoted"

    except BaseException as e:
        log(f"[staged_refresh] Publish FAILED before commit ({e}) -- attempting to restore the previous live cache.")
        if state == "backed_up":
            try:
                if os.path.isdir(cache_dir):
                    shutil.rmtree(cache_dir)
                if fail_restoring_backup:
                    raise OSError("TEST-INJECTED failure restoring the backup cache directory")
                os.rename(backup_dir, cache_dir)
                state = "restored"
            except OSError as restore_e:
                # The rollback's OWN filesystem operation failed. Do not let this exception
                # escape, and do not claim the cache is restored/untouched/unchanged -- it may
                # genuinely be absent right now. Preserve txn_dir/backup_dir untouched for manual
                # recovery; the next refresh attempt's residue preflight check will refuse safely.
                log(f"[staged_refresh] ROLLBACK INCOMPLETE -- could not restore the backup cache "
                    f"directory: {restore_e}")
                return False, (
                    f"{REFRESH_ROLLBACK_INCOMPLETE_PREFIX}: the original failure was '{e}', and "
                    f"restoring the prior live cache from its backup then ALSO failed: {restore_e}. "
                    f"The live cache directory ({cache_dir}) may be ABSENT or incomplete right now -- "
                    f"do NOT assume it is untouched or unchanged. The original cache contents remain "
                    f"byte-for-byte recoverable at the retained backup path {backup_dir}, inside the "
                    f"preserved transaction directory {txn_dir} (NOT deleted). Manual recovery is "
                    f"required: move/copy {backup_dir} to {cache_dir}, then remove {txn_dir}. The next "
                    f"refresh attempt will detect this residue and refuse until it is resolved."
                )
        # state == "pending": cache_dir (if any) was never touched -- nothing to restore.
        if txn_dir and os.path.isdir(txn_dir):
            try:
                shutil.rmtree(txn_dir)
            except OSError as cleanup_e:
                log(f"  (non-fatal) could not remove empty/unused transaction directory {txn_dir}: {cleanup_e}")
                return False, (f"refresh failed and the prior cache was fully restored (confirmed back "
                                f"to its original state), BUT the now-empty transaction directory "
                                f"{txn_dir} could not itself be removed ({cleanup_e}) -- harmless "
                                f"residue; a later refresh attempt will refuse until it is deleted "
                                f"manually.")
        if state == "restored":
            return False, f"refresh failed and was fully rolled back to the prior cache: {e}"
        return False, f"refresh failed before the live cache was touched: {e}"

    # ---- Commit point: state == "promoted" -> the new cache directory is now live. ----
    log("[staged_refresh] Publish succeeded: new cache is now live (transaction committed).")
    if txn_dir is None:
        return True, "refresh published successfully (no prior cache existed to back up)"
    try:
        if fail_at_cleanup:
            raise RuntimeError("TEST-INJECTED failure during post-commit backup cleanup")
        shutil.rmtree(txn_dir)
        return True, "refresh published successfully (prior cache backup removed)"
    except BaseException as e:
        return True, (f"{CLEANUP_WARNING_PREFIX}: refresh published successfully (new cache is live and "
                       f"correct), BUT post-commit cleanup of the prior cache backup FAILED and it was "
                       f"retained at {txn_dir}: {e}. This is a leftover recovery artifact, not an error "
                       f"condition -- the next refresh attempt will detect it as residue and refuse "
                       f"until it is removed manually.")


def _validate_ticker_map_bytes(data):
    try:
        obj = json.loads(data)
    except json.JSONDecodeError as e:
        raise RefreshTransactionError(f"company_tickers.json: malformed JSON ({e})")
    if not isinstance(obj, dict) or not obj:
        raise RefreshTransactionError("company_tickers.json: expected a non-empty JSON object")
    sample = next(iter(obj.values()))
    if not isinstance(sample, dict) or "ticker" not in sample or "cik_str" not in sample:
        raise RefreshTransactionError("company_tickers.json: entries missing required 'ticker'/'cik_str' fields")
    return obj


def _validate_companyfacts_bytes(data, expected_ticker, expected_cik):
    try:
        obj = json.loads(data)
    except json.JSONDecodeError as e:
        raise RefreshTransactionError(f"companyfacts_{expected_ticker}.json: malformed JSON ({e})")
    if not isinstance(obj, dict):
        raise RefreshTransactionError(f"companyfacts_{expected_ticker}.json: top level is not a JSON object")
    cik_val = obj.get("cik")
    if cik_val is None or str(int(cik_val)).zfill(10) != expected_cik:
        raise RefreshTransactionError(
            f"companyfacts_{expected_ticker}.json: CIK mismatch -- expected {expected_cik}, "
            f"got {cik_val!r} (company-identity check failed)")
    if not obj.get("entityName"):
        raise RefreshTransactionError(f"companyfacts_{expected_ticker}.json: missing/empty 'entityName'")
    facts = obj.get("facts")
    if not isinstance(facts, dict) or not isinstance(facts.get("us-gaap"), dict) or not facts.get("us-gaap"):
        raise RefreshTransactionError(f"companyfacts_{expected_ticker}.json: missing/empty facts.us-gaap")
    return obj


def staged_refresh(cache_dir, tickers, fetch_fn, user_agent, fail_at_publish=False,
                    fail_at_backup=False, fail_at_cleanup=False, fail_restoring_backup=False,
                    log=print):
    """Core transactional refresh logic, parameterized by cache_dir so the
    fault-injection self-test can point it at a temporary directory and NEVER
    touch the real sec_raw_cache/. fetch_fn(url, user_agent) -> bytes is fully
    injectable so tests never open a real network connection.

    fail_at_backup / fail_at_publish / fail_at_cleanup / fail_restoring_backup
    are TEST-ONLY hooks (never set by the real --refresh-sec CLI path)
    injecting a failure while backing up the live cache, while promoting the
    staged cache into place, during post-commit removal of the old backup,
    and during rollback's OWN restore-rename step (after a promotion
    failure, restoring the backup back into place), respectively -- see
    _publish_refresh_transaction, which implements and documents all four.

    Returns (ok: bool, message: str) and does not raise for any of the
    failure classes this module's fault-injection self-test exercises. The
    returned message distinguishes: failure before the live cache was
    touched; failure fully rolled back to the prior cache; failure with an
    INCOMPLETE rollback (message begins with REFRESH_ROLLBACK_INCOMPLETE_PREFIX,
    manual recovery required -- the live cache may be absent); a successful
    commit; and a successful commit with a post-commit cleanup warning
    (message begins with CLEANUP_WARNING_PREFIX). No unconditional "live
    cache untouched"/"unchanged" claim is made for a failure whose outcome
    was not actually confirmed.
    """
    cache_dir = os.path.abspath(cache_dir)
    parent_dir = os.path.dirname(cache_dir)

    residue = _find_refresh_txn_residue(parent_dir, cache_dir)
    if residue:
        msg = (f"unresolved refresh-transaction residue from a prior interrupted run: {residue} -- "
               f"left in place for manual inspection, not deleted")
        log(f"[staged_refresh] REFUSING to refresh: {msg}")
        return False, msg

    staging_dir = tempfile.mkdtemp(dir=parent_dir, prefix=".sec_raw_cache_staging_")
    try:
        # ---- Phase 1: download + validate everything into staging; live cache untouched throughout ----
        log(f"[staged_refresh] Phase 1: downloading into staging dir {os.path.basename(staging_dir)}/ "
            f"(live cache dir not touched yet)")
        tickers_bytes = fetch_fn("https://www.sec.gov/files/company_tickers.json", user_agent)
        tickers_obj = _validate_ticker_map_bytes(tickers_bytes)
        cik_map = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in tickers_obj.values()}

        downloaded = {"company_tickers.json": tickers_bytes}
        for tk in tickers:
            if tk not in cik_map:
                raise RefreshTransactionError(f"company_tickers.json does not contain ticker {tk}")
            cik = cik_map[tk]
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            data = fetch_fn(url, user_agent)
            _validate_companyfacts_bytes(data, tk, cik)  # identity + structural validation
            downloaded[f"companyfacts_{tk}.json"] = data
            log(f"  validated companyfacts_{tk}.json ({len(data)} bytes, CIK {cik} confirmed)")

        # ---- Phase 2: write validated bytes + compute new manifest, all still in staging ----
        for fname, data in downloaded.items():
            with open(os.path.join(staging_dir, fname), "wb") as f:
                f.write(data)
        manifest_entries = {}
        for fname in REQUIRED_CACHE_FILES:
            fpath = os.path.join(staging_dir, fname)
            manifest_entries[fname] = {"sha256": sha256_file(fpath), "size_bytes": os.path.getsize(fpath)}
        with open(os.path.join(staging_dir, "cache_manifest.json"), "w") as f:
            json.dump({
                "description": "SHA-256 and byte size of every immutable raw SEC EDGAR input file this validation depends on. "
                                "Verified before every offline rebuild; only a successful --refresh-sec may change these files.",
                "hash_algorithm": "SHA-256",
                "files": manifest_entries,
            }, f, indent=2, sort_keys=True)
        log(f"[staged_refresh] Phase 2 complete: all {len(REQUIRED_CACHE_FILES)} files + cache_manifest.json "
            f"written and validated in staging.")

        # ---- Phase 3: publish via the explicit backup/promote/cleanup state machine ----
        log(f"[staged_refresh] Phase 3: publishing (live cache dir -> unique transaction backup, "
            f"staging dir -> live cache dir)")
        ok, msg = _publish_refresh_transaction(cache_dir, parent_dir, staging_dir,
                                                fail_at_backup=fail_at_backup,
                                                fail_at_publish=fail_at_publish,
                                                fail_at_cleanup=fail_at_cleanup,
                                                fail_restoring_backup=fail_restoring_backup, log=log)
        return ok, msg

    except RefreshTransactionError as e:
        # Reached only from Phase 1/2 (fetch/validation), which never touches cache_dir at all --
        # this "untouched" claim is accurate here, unlike the unconditional version this replaces.
        log(f"[staged_refresh] FAILED before the live cache was touched (validation): {e}")
        return False, f"refresh failed before the live cache was touched (validation failure): {e}"
    except OSError as e:
        # Reached only from Phase 1/2 (download/staging I/O) -- _publish_refresh_transaction itself
        # no longer lets an OSError escape (see its own rollback-restore handling), so this handler
        # is not the fallback path for an in-progress cache-directory mutation.
        log(f"[staged_refresh] FAILED before the live cache was touched (I/O): {e}")
        return False, f"refresh failed before the live cache was touched (I/O failure): {e}"
    finally:
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir)  # already renamed away on a successful publish; only still
            # present here if publish failed/rolled back, or Phase 1/2 never reached the rename


def _real_fetch(url, user_agent):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _report_refresh_result(ok, msg, log=print):
    """Shared reporting logic between refresh_sec() (the real CLI path) and
    this module's self-test: the message returned by staged_refresh() is
    ALWAYS surfaced, and each of the five distinct outcomes is called out
    explicitly rather than collapsed into a single unconditional
    'succeeded'/'unchanged' claim:
      1. failure before the live cache was touched
      2. failure fully rolled back to the prior cache
      3. failure with an INCOMPLETE rollback (manual recovery required)
      4. successful commit
      5. successful commit with a post-commit cleanup warning
    """
    if not ok:
        if msg.startswith(REFRESH_ROLLBACK_INCOMPLETE_PREFIX):
            log(f"\n*** {REFRESH_ROLLBACK_INCOMPLETE_PREFIX} ***")
            log(msg)
            log("Manual recovery is required. Do not assume the live cache is unchanged or usable. The "
                "next refresh attempt will refuse once it detects this residue.")
        elif "fully rolled back" in msg:
            log(f"\nRefresh FAILED and was fully rolled back to the prior cache: {msg}")
        elif "before the live cache was touched" in msg:
            log(f"\nRefresh FAILED before the live cache was touched: {msg}")
        else:
            log(f"\nRefresh FAILED: {msg}")
        return
    if msg.startswith(CLEANUP_WARNING_PREFIX):
        log(f"\n*** {CLEANUP_WARNING_PREFIX} ***")
        log(msg)
        log("The new cache is live and correct, but the prior cache's backup could not be cleaned up "
            "automatically -- see the retained path above. The next refresh attempt will refuse until "
            "that residue is resolved.")
    else:
        log(f"\nRefresh succeeded: {msg}")


def refresh_sec():
    ua = os.environ.get("SEC_USER_AGENT", "")
    if not ua.strip():
        print("REFUSING to refresh: the SEC_USER_AGENT environment variable is not set (or is empty).")
        print("Set it to a descriptive User-Agent string identifying this client and a contact method, "
              "per SEC's fair-access policy for automated requests, then re-run with --refresh-sec.")
        print("(Its value is never logged or printed by this script.)")
        return 1

    print("--refresh-sec would replace the following cache files, as a single staged, rollback-protected "
          "transaction (see this file's module-level ATOMICITY / RECOVERY GUARANTEES comment for exactly "
          "what is and is not guaranteed):")
    for fname in REQUIRED_CACHE_FILES:
        print(f"  - sec_raw_cache/{fname}")
    print("Nothing under sec_raw_cache/ is modified unless every download and validation succeeds first.\n")

    ok, msg = staged_refresh(CACHE_DIR, TICKERS, _real_fetch, ua)
    _report_refresh_result(ok, msg, log=print)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Negative-control self-tests (Issue 2's manifest-completeness verifier,
# Issue 4's PII scanner). Both operate on temporary copies only; the real
# validation tree is never modified by either.
# ---------------------------------------------------------------------------

def run_manifest_completeness_self_test():
    print("=== Manifest-completeness negative control ===\n")
    if not os.path.exists(SOURCE_MANIFEST_PATH):
        print(f"SELF-TEST FAILED: {SOURCE_MANIFEST_PATH} does not exist -- run --rebuild-from-cache first.")
        return 1

    print("[1/2] Verifying the REAL source_manifest.csv -- expect PASS")
    real_rows = list(csv.DictReader(open(SOURCE_MANIFEST_PATH)))
    ok_real, problems_real = verify_manifest_completeness(real_rows)
    print(f"  {'PASS' if ok_real else 'FAIL'} -- {len(real_rows)} rows, {len(problems_real)} missing field(s)")
    if not ok_real:
        print("SELF-TEST FAILED: the real manifest itself is incomplete -- fix that before trusting this negative control.")
        for tk, field, col, msg in problems_real:
            print(f"    - [{tk}] {field!r}: column '{col}' -- {msg}")
        return 1

    tmp_dir = tempfile.mkdtemp(prefix="manifest_completeness_selftest_")
    tmp_csv = os.path.join(tmp_dir, "source_manifest_corrupted.csv")
    try:
        print(f"\n[2/2] Blanking one required filed date in a TEMPORARY copy: {tmp_csv}")
        target_ticker, target_field = "MSFT", "Pretax Income (latest)"
        corrupted_rows = []
        blanked = False
        for r in real_rows:
            r = dict(r)
            if not blanked and r["ticker"] == target_ticker and r["field"] == target_field:
                r["filed_or_retrieved_date"] = ""
                blanked = True
            corrupted_rows.append(r)
        if not blanked:
            raise RuntimeError(f"Self-test setup failed: could not find a {target_ticker}/{target_field!r} row to corrupt.")

        with open(tmp_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=real_rows[0].keys())
            w.writeheader()
            w.writerows(corrupted_rows)

        reloaded = list(csv.DictReader(open(tmp_csv)))
        ok_corrupt, problems_corrupt = verify_manifest_completeness(reloaded)
        identified = any(tk == target_ticker and field == target_field and col == "filed_or_retrieved_date"
                          for tk, field, col, msg in problems_corrupt)
        if ok_corrupt:
            print(f"\nSELF-TEST FAILED: verifier passed despite a blanked filed date for {target_ticker}/{target_field}.")
            return 1
        if not identified:
            print(f"\nSELF-TEST FAILED: verifier failed but did not specifically identify "
                  f"[{target_ticker}] {target_field!r} column 'filed_or_retrieved_date'.")
            print("  Problems reported instead:")
            for tk, field, col, msg in problems_corrupt:
                print(f"    - [{tk}] {field!r}: column '{col}' -- {msg}")
            return 1
        print(f"\nSELF-TEST PASSED: blanked filed date correctly identified as "
              f"[{target_ticker}] {target_field!r}, column 'filed_or_retrieved_date'.")
        return 0
    finally:
        if os.path.exists(tmp_csv):
            os.remove(tmp_csv)
        if os.path.isdir(tmp_dir):
            os.rmdir(tmp_dir)
        print(f"Deleted temporary copy and directory: {tmp_dir}")
        # Confirm the real manifest is untouched by this test.
        real_rows_after = list(csv.DictReader(open(SOURCE_MANIFEST_PATH)))
        if real_rows_after != real_rows:
            print("WARNING: the real source_manifest.csv changed during this self-test -- investigate immediately.")


def run_snapshot_manifest_structure_self_test():
    """Negative controls for verify_snapshot_manifest, covering the exact
    vacuous-pass bug this correction pass fixed (an empty 'files' mapping
    used to report zero problems because the old check only iterated
    entries that were present) plus missing/extra required entries. Reads
    the real snapshot_manifest.json read-only; all corrupted variants are
    in-memory dicts, never written to disk."""
    print("=== Snapshot-manifest structural negative controls ===\n")
    if not os.path.exists(SNAPSHOT_MANIFEST_PATH):
        print(f"SELF-TEST FAILED: {SNAPSHOT_MANIFEST_PATH} does not exist -- run --rebuild-from-cache first.")
        return 1

    print("[1/4] Verifying the REAL snapshot_manifest.json -- expect PASS")
    real_manifest = json.load(open(SNAPSHOT_MANIFEST_PATH))
    ok_real, problems_real = verify_snapshot_manifest(real_manifest, HERE)
    print(f"  {'PASS' if ok_real else 'FAIL'} -- {len(problems_real)} problem(s)")
    if not ok_real:
        print("SELF-TEST FAILED: the real manifest itself is invalid -- fix that before trusting these negative controls.")
        for p in problems_real:
            print(f"    - {p}")
        return 1
    overall_ok = True

    print("\n[2/4] Empty 'files' mapping -- expect FAIL naming it EMPTY, not a vacuous pass")
    empty_manifest = {**real_manifest, "files": {}}
    ok_empty, problems_empty = verify_snapshot_manifest(empty_manifest, HERE)
    identified_empty = any("EMPTY" in p for p in problems_empty)
    scenario_ok = (not ok_empty) and identified_empty
    print(f"  [{'PASS' if scenario_ok else 'FAIL'}] verify_snapshot_manifest(empty)={'FAIL' if not ok_empty else 'PASS (WRONG)'}, "
          f"identified as empty={identified_empty}")
    if not scenario_ok:
        overall_ok = False

    print("\n[3/4] One missing required entry -- expect FAIL naming that exact entry")
    target = REQUIRED_SNAPSHOT_MANIFEST_ENTRIES[0]
    missing_files = {k: v for k, v in real_manifest["files"].items() if k != target}
    missing_manifest = {**real_manifest, "files": missing_files}
    ok_missing, problems_missing = verify_snapshot_manifest(missing_manifest, HERE)
    identified_missing = any(target in p and "missing" in p for p in problems_missing)
    scenario_ok = (not ok_missing) and identified_missing
    print(f"  [{'PASS' if scenario_ok else 'FAIL'}] target={target}: verify_snapshot_manifest="
          f"{'FAIL' if not ok_missing else 'PASS (WRONG)'}, correctly named={identified_missing}")
    if not scenario_ok:
        overall_ok = False

    print("\n[4/4] One unexpected extra entry -- expect FAIL naming it as extra")
    extra_key = "snapshots/FAKE_EXTRA_snapshot.json"
    extra_files = {**real_manifest["files"], extra_key: {"sha256": "0" * 64, "ticker": "FAKE"}}
    extra_manifest = {**real_manifest, "files": extra_files}
    ok_extra, problems_extra = verify_snapshot_manifest(extra_manifest, HERE)
    identified_extra = any(extra_key in p and "extra" in p for p in problems_extra)
    scenario_ok = (not ok_extra) and identified_extra
    print(f"  [{'PASS' if scenario_ok else 'FAIL'}] extra_key={extra_key}: verify_snapshot_manifest="
          f"{'FAIL' if not ok_extra else 'PASS (WRONG)'}, correctly named={identified_extra}")
    if not scenario_ok:
        overall_ok = False

    print(f"\n=== Snapshot-manifest structural self-test: {'ALL SCENARIOS PASSED' if overall_ok else 'AT LEAST ONE SCENARIO FAILED'} "
          f"(all in-memory only -- snapshot_manifest.json on disk was never modified) ===")
    return 0 if overall_ok else 1


def run_source_manifest_structure_self_test():
    """Negative controls for verify_source_manifest_structure: a broken
    debt-component total, a duplicated required row, a missing required row,
    and a duplicated provenance element inside a derived Total Debt rollup.
    Reads the real source_manifest.csv and snapshots/ read-only; every
    corrupted variant is an in-memory list of row dicts, never written to
    the real file."""
    print("=== Source-manifest structural negative controls ===\n")
    if not os.path.exists(SOURCE_MANIFEST_PATH):
        print(f"SELF-TEST FAILED: {SOURCE_MANIFEST_PATH} does not exist -- run --rebuild-from-cache first.")
        return 1

    snapshots_by_ticker = {}
    for tk in TICKERS:
        path = os.path.join(SNAP_DIR, f"{tk}_snapshot.json")
        if os.path.exists(path):
            snapshots_by_ticker[tk] = json.load(open(path))

    print("[1/5] Verifying the REAL source_manifest.csv -- expect PASS")
    real_rows = list(csv.DictReader(open(SOURCE_MANIFEST_PATH)))
    ok_real, problems_real = verify_source_manifest_structure(real_rows, snapshots_by_ticker)
    print(f"  {'PASS' if ok_real else 'FAIL'} -- {len(problems_real)} problem(s)")
    if not ok_real:
        print("SELF-TEST FAILED: the real manifest itself is structurally invalid -- fix that before trusting these negative controls.")
        for p in problems_real:
            print(f"    - {p}")
        return 1
    overall_ok = True

    # A ticker whose Total Debt is derived by summing components (not VZ, which uses a combined tag).
    derived_ticker = next(tk for tk in TICKERS if tk in DEBT_COMPONENT_TAGS)

    print(f"\n[2/5] Broken debt-component total for {derived_ticker} -- expect FAIL naming the mismatch")
    corrupted = [dict(r) for r in real_rows]
    for r in corrupted:
        if r["ticker"] == derived_ticker and r["field"].startswith("Total Debt component:"):
            r["raw_value"] = str(float(r["raw_value"]) + 999.0)
            break
    ok_c, problems_c = verify_source_manifest_structure(corrupted, snapshots_by_ticker)
    identified = any(derived_ticker in p and "does not equal the sum" in p for p in problems_c)
    scenario_ok = (not ok_c) and identified
    print(f"  [{'PASS' if scenario_ok else 'FAIL'}] verify={'FAIL' if not ok_c else 'PASS (WRONG)'}, correctly identified={identified}")
    if not scenario_ok:
        overall_ok = False

    print(f"\n[3/5] Duplicate required row ('Pretax Income (latest)' for {derived_ticker}) -- expect FAIL naming count=2")
    dup_row = next(dict(r) for r in real_rows if r["ticker"] == derived_ticker and r["field"] == "Pretax Income (latest)")
    corrupted = [dict(r) for r in real_rows] + [dup_row]
    ok_c, problems_c = verify_source_manifest_structure(corrupted, snapshots_by_ticker)
    identified = any(derived_ticker in p and "Pretax Income (latest)" in p and "found 2" in p for p in problems_c)
    scenario_ok = (not ok_c) and identified
    print(f"  [{'PASS' if scenario_ok else 'FAIL'}] verify={'FAIL' if not ok_c else 'PASS (WRONG)'}, correctly identified={identified}")
    if not scenario_ok:
        overall_ok = False

    print(f"\n[4/5] Missing required row ('Cash & Equivalents (latest)' for {derived_ticker}) -- expect FAIL naming count=0")
    corrupted = [dict(r) for r in real_rows
                 if not (r["ticker"] == derived_ticker and r["field"] == "Cash & Equivalents (latest)")]
    ok_c, problems_c = verify_source_manifest_structure(corrupted, snapshots_by_ticker)
    identified = any(derived_ticker in p and "Cash & Equivalents (latest)" in p and "found 0" in p for p in problems_c)
    scenario_ok = (not ok_c) and identified
    print(f"  [{'PASS' if scenario_ok else 'FAIL'}] verify={'FAIL' if not ok_c else 'PASS (WRONG)'}, correctly identified={identified}")
    if not scenario_ok:
        overall_ok = False

    print(f"\n[5/5] Duplicated provenance element in {derived_ticker}'s derived Total Debt rollup -- expect FAIL naming 'NOT deduplicated'")
    corrupted = [dict(r) for r in real_rows]
    for r in corrupted:
        if r["ticker"] == derived_ticker and r["field"] == "Total Debt (latest, derived)":
            first_accn = r["accession_number"].split(";")[0].strip()
            r["accession_number"] = f"{first_accn}; {first_accn}"
            break
    ok_c, problems_c = verify_source_manifest_structure(corrupted, snapshots_by_ticker)
    identified = any(derived_ticker in p and "NOT deduplicated" in p for p in problems_c)
    scenario_ok = (not ok_c) and identified
    print(f"  [{'PASS' if scenario_ok else 'FAIL'}] verify={'FAIL' if not ok_c else 'PASS (WRONG)'}, correctly identified={identified}")
    if not scenario_ok:
        overall_ok = False

    print(f"\n=== Source-manifest structural self-test: {'ALL SCENARIOS PASSED' if overall_ok else 'AT LEAST ONE SCENARIO FAILED'} "
          f"(all in-memory only -- source_manifest.csv on disk was never modified) ===")
    return 0 if overall_ok else 1


def run_pii_regression_self_test():
    print("=== PII-scanner regression self-test ===\n")
    print("[1/3] Scanning the REAL validation tree -- expect PASS (no email addresses)")
    ok_real, leaks_real, scope_real = scan_for_pii(HERE)
    print(f"  Scope: {scope_real}")
    print(f"  {'PASS' if ok_real else 'FAIL'} -- {len(leaks_real)} match(es)")
    if not ok_real:
        print("SELF-TEST FAILED: the real tree already contains an email-address match -- "
              "fix that before trusting this negative control.")
        for loc, match in leaks_real:
            print(f"    - {loc}: {match}")
        return 1

    real_content = open(os.path.join(HERE, "build_snapshots.py"), encoding="utf-8").read()

    tmp_dir = tempfile.mkdtemp(prefix="pii_selftest_")
    tmp_target = os.path.join(tmp_dir, "build_snapshots.py")
    try:
        print(f"\n[2/3] Copying build_snapshots.py into a TEMPORARY directory and injecting a realistic email: {tmp_target}")
        # Built from parts at runtime so the REAL build_snapshots.py source (this file)
        # never contains the contiguous email pattern as literal text -- otherwise this
        # detector, having no self-exemption by design, would correctly flag its own
        # test fixture as a leak and poison the "real tree is clean" baseline check.
        injected_email = "".join(["jane.reviewer.test+phase2a", "@", "corp-example-injected.test"])
        shutil.copy2(os.path.join(HERE, "build_snapshots.py"), tmp_target)
        with open(tmp_target, "a", encoding="utf-8") as f:
            f.write(f"\n# SELF-TEST INJECTION (temporary copy only): {injected_email}\n")

        print(f"[3/3] Scanning the TEMPORARY directory -- expect FAIL, identifying build_snapshots.py")
        ok_corrupt, leaks_corrupt, scope_corrupt = scan_for_pii(tmp_dir)
        print(f"  Scope: {scope_corrupt}")
        identified = any("build_snapshots.py" in loc and match == injected_email for loc, match in leaks_corrupt)
        if ok_corrupt:
            print(f"\nSELF-TEST FAILED: scanner passed despite an injected email in the temporary copy.")
            return 1
        if not identified:
            print(f"\nSELF-TEST FAILED: scanner failed but did not specifically identify the injected "
                  f"address in build_snapshots.py. Matches found:")
            for loc, match in leaks_corrupt:
                print(f"    - {loc}: {match}")
            return 1
        print(f"\nSELF-TEST PASSED: injected address correctly identified in the TEMPORARY copy of build_snapshots.py "
              f"(no self-exemption -- this is the same filename as the real, unmodified file).")
        return 0
    finally:
        if os.path.exists(tmp_target):
            os.remove(tmp_target)
        if os.path.isdir(tmp_dir):
            os.rmdir(tmp_dir)
        print(f"Deleted temporary directory: {tmp_dir}")
        real_content_after = open(os.path.join(HERE, "build_snapshots.py"), encoding="utf-8").read()
        if real_content_after != real_content:
            print("WARNING: the real build_snapshots.py changed during this self-test -- investigate immediately.")
        else:
            print("Confirmed: the real build_snapshots.py is byte-for-byte unchanged by this test.")


# ---------------------------------------------------------------------------
# Offline fault-injection self-test for _publish_staged_generation (Issue 5).
# Operates ENTIRELY inside a temporary directory -- four synthetic "output
# slots" (a directory + three files) stand in for the real snapshots/,
# source_manifest.csv, snapshot_manifest.json, and independent_dcf_validation.xlsx.
# The real live output set is never passed to _publish_staged_generation here.
# ---------------------------------------------------------------------------

def run_publish_fault_injection_self_test():
    print("=== Publish-transaction fault-injection self-test (offline, temp directories only) ===\n")
    print("Matrix: 4 backup-phase failures + 4 promotion-phase failures + 1 post-commit cleanup "
          "failure + 1 pre-existing-residue refusal + 1 positive control = 11 scenarios. All against "
          "a synthetic temp output set with a synthetic base_dir for the transaction-backup "
          "directory -- the real live outputs and the real HERE/ directory are never referenced.\n")

    tmp_root = tempfile.mkdtemp(prefix="publish_selftest_")
    overall_ok = True
    try:
        def make_slots(root):
            snap_dir = os.path.join(root, "snapshots")
            return [
                ("snapshots", snap_dir, True),
                ("source_manifest.csv", os.path.join(root, "source_manifest.csv"), False),
                ("snapshot_manifest.json", os.path.join(root, "snapshot_manifest.json"), False),
                ("independent_dcf_validation.xlsx", os.path.join(root, "independent_dcf_validation.xlsx"), False),
            ]

        def write_generation(root, tag):
            """Writes a distinguishable synthetic generation (tag='original' or 'new') at `root`."""
            snap_dir = os.path.join(root, "snapshots")
            os.makedirs(snap_dir, exist_ok=True)
            for tk in TICKERS:
                with open(os.path.join(snap_dir, f"{tk}_snapshot.json"), "w") as f:
                    f.write(f'{{"generation": "{tag}", "ticker": "{tk}"}}')
            with open(os.path.join(root, "source_manifest.csv"), "w") as f:
                f.write(f"generation\n{tag}\n")
            with open(os.path.join(root, "snapshot_manifest.json"), "w") as f:
                f.write(f'{{"generation": "{tag}"}}')
            with open(os.path.join(root, "independent_dcf_validation.xlsx"), "w") as f:
                f.write(f"FAKE-XLSX-CONTENT-{tag}")

        live_root = os.path.join(tmp_root, "live")
        txn_base_dir = os.path.join(tmp_root, "txn_base")
        os.makedirs(live_root)
        os.makedirs(txn_base_dir)
        write_generation(live_root, "original")
        live_slots = make_slots(live_root)
        baseline_hash = _hash_directory_tree(live_root)
        print(f"Baseline synthetic 'live' generation hash: {baseline_hash[:16]}...\n")

        def before_after_scenario(label, staging_tag, **kwargs):
            staging_dir = os.path.join(tmp_root, f"staging_{label}")
            write_generation(staging_dir, staging_tag)
            before_hash = _hash_directory_tree(live_root)
            ok, msg = _publish_staged_generation(staging_dir, log=lambda *a: None, slots=live_slots,
                                                  base_dir=txn_base_dir, **kwargs)
            after_hash = _hash_directory_tree(live_root)
            if os.path.isdir(staging_dir):
                shutil.rmtree(staging_dir)
            return ok, msg, before_hash, after_hash

        def no_stray_residue():
            return [n for n in os.listdir(txn_base_dir) if n.startswith(TXN_DIR_PREFIX)]

        # --- 1-4: inject a failure at each of the 4 BACKUP-phase positions ---
        for fail_idx in range(4):
            ok, msg, before_hash, after_hash = before_after_scenario(
                f"backup_fail_{fail_idx}", "new", fail_at_backup_slot=fail_idx)
            leftover = no_stray_residue()
            unchanged = before_hash == after_hash
            no_leftover = len(leftover) == 0
            scenario_ok = (not ok) and unchanged and no_leftover
            print(f"[{'PASS' if scenario_ok else 'FAIL'}] fail_at_backup_slot={fail_idx} "
                  f"({live_slots[fail_idx][0]}): published={ok} (expected False), "
                  f"live generation unchanged={unchanged}, no residual txn dir={no_leftover}"
                  + (f", leftover={leftover}" if leftover else ""))
            if not scenario_ok:
                overall_ok = False

        # --- 5-8: inject a failure at each of the 4 PROMOTION-phase positions ---
        for fail_idx in range(4):
            ok, msg, before_hash, after_hash = before_after_scenario(
                f"promote_fail_{fail_idx}", "new", fail_at_slot=fail_idx)
            leftover = no_stray_residue()
            unchanged = before_hash == after_hash
            no_leftover = len(leftover) == 0
            scenario_ok = (not ok) and unchanged and no_leftover
            print(f"[{'PASS' if scenario_ok else 'FAIL'}] fail_at_slot={fail_idx} ({live_slots[fail_idx][0]}): "
                  f"published={ok} (expected False), live generation unchanged={unchanged}, "
                  f"no residual txn dir={no_leftover}" + (f", leftover={leftover}" if leftover else ""))
            if not scenario_ok:
                overall_ok = False

        # --- 9: failure during POST-COMMIT cleanup -- must still report success, new content live ---
        staging_dir = os.path.join(tmp_root, "staging_cleanup_fail")
        write_generation(staging_dir, "new")
        ok, msg = _publish_staged_generation(staging_dir, fail_at_cleanup=True, log=lambda *a: None,
                                              slots=live_slots, base_dir=txn_base_dir)
        promoted_content = open(os.path.join(live_root, "source_manifest.csv")).read()
        promoted_correctly = "new" in promoted_content
        residue_after = no_stray_residue()
        cleanup_scenario_ok = ok and promoted_correctly and len(residue_after) == 1
        print(f"\n[{'PASS' if cleanup_scenario_ok else 'FAIL'}] fail_at_cleanup: published={ok} (expected True -- "
              f"a cleanup failure must NOT be reported as publish failure), live content now shows the NEW "
              f"generation={promoted_correctly}, exactly one retained backup dir={len(residue_after) == 1}")
        if not cleanup_scenario_ok:
            overall_ok = False
        # Manually remove the retained backup so it doesn't get picked up by the next scenario's residue check.
        for n in residue_after:
            shutil.rmtree(os.path.join(txn_base_dir, n))
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir)

        # --- 10: pre-existing transaction residue -> REFUSE without touching live or the residue itself ---
        fake_residue_dir = tempfile.mkdtemp(dir=txn_base_dir, prefix=TXN_DIR_PREFIX)
        with open(os.path.join(fake_residue_dir, "marker.txt"), "w") as f:
            f.write("simulated leftover from a prior interrupted run")
        staging_dir = os.path.join(tmp_root, "staging_residue")
        write_generation(staging_dir, "residue_attempt")
        before_hash = _hash_directory_tree(live_root)
        ok, msg = _publish_staged_generation(staging_dir, log=lambda *a: None, slots=live_slots,
                                              base_dir=txn_base_dir)
        after_hash = _hash_directory_tree(live_root)
        residue_preserved = os.path.exists(os.path.join(fake_residue_dir, "marker.txt"))
        residue_scenario_ok = (not ok) and (before_hash == after_hash) and residue_preserved
        print(f"[{'PASS' if residue_scenario_ok else 'FAIL'}] pre_existing_residue: published={ok} "
              f"(expected False), live generation unchanged={before_hash == after_hash}, "
              f"residue directory left untouched (not deleted)={residue_preserved}")
        if not residue_scenario_ok:
            overall_ok = False
        shutil.rmtree(fake_residue_dir)
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir)

        # --- 11: positive control: no injected fault -> publish succeeds, live generation becomes "final" ---
        ok, msg, before_hash, after_hash = before_after_scenario("success", "final")
        promoted_content = open(os.path.join(live_root, "source_manifest.csv")).read()
        promoted_correctly = "final" in promoted_content
        no_leftover = len(no_stray_residue()) == 0
        positive_ok = ok and promoted_correctly and no_leftover
        print(f"\n[{'PASS' if positive_ok else 'FAIL'}] positive_control (no injected fault): "
              f"published={ok} (expected True), live content now shows the NEW generation={promoted_correctly}, "
              f"no residual txn dir={no_leftover}")
        if not positive_ok:
            overall_ok = False

        print(f"\n=== Publish fault-injection self-test: {'ALL 11 SCENARIOS PASSED' if overall_ok else 'AT LEAST ONE SCENARIO FAILED'} "
              f"(4 backup-phase + 4 promotion-phase negative scenarios + 1 cleanup-failure scenario + "
              f"1 pre-existing-residue refusal + 1 positive control, all against a synthetic temp "
              f"output set -- the real live outputs were never referenced) ===")
        return 0 if overall_ok else 1
    finally:
        if os.path.isdir(tmp_root):
            shutil.rmtree(tmp_root)
            print(f"Deleted temporary test root: {tmp_root}")


# ---------------------------------------------------------------------------
# Missing-original-slot fault-injection self-test: a slot can legitimately
# have NO live original before a transaction (rebuilding a not-yet-existing
# generated artifact). This is a genuinely different case from a slot that
# exists and gets backed up, and conflating the two (indexing a backup that
# was never created) was the second bug found and fixed in
# workbook_build_log.md -- see that entry for the reproduction. Everything
# here operates on a synthetic temp output set with a synthetic base_dir for
# the transaction directory; the real live outputs are never referenced.
# ---------------------------------------------------------------------------

def run_missing_original_fault_injection_self_test():
    print("=== Missing-original-slot fault-injection self-test (offline, temp directories only) ===\n")

    tmp_root = tempfile.mkdtemp(prefix="missing_original_selftest_")
    overall_ok = True
    try:
        slot_names = ["snapshots", "source_manifest.csv", "snapshot_manifest.json",
                      "independent_dcf_validation.xlsx"]

        def make_slots(root):
            snap_dir = os.path.join(root, "snapshots")
            return [
                ("snapshots", snap_dir, True),
                ("source_manifest.csv", os.path.join(root, "source_manifest.csv"), False),
                ("snapshot_manifest.json", os.path.join(root, "snapshot_manifest.json"), False),
                ("independent_dcf_validation.xlsx", os.path.join(root, "independent_dcf_validation.xlsx"), False),
            ]

        def write_generation(root, tag, present=None):
            """Writes a distinguishable synthetic generation at `root`, only for slot
            names in `present` (default: all 4 slots). A slot NOT in `present` is left
            entirely absent -- no file or directory created for it at all."""
            present = set(slot_names) if present is None else set(present)
            if "snapshots" in present:
                snap_dir = os.path.join(root, "snapshots")
                os.makedirs(snap_dir, exist_ok=True)
                for tk in TICKERS:
                    with open(os.path.join(snap_dir, f"{tk}_snapshot.json"), "w") as f:
                        f.write(f'{{"generation": "{tag}", "ticker": "{tk}"}}')
            if "source_manifest.csv" in present:
                with open(os.path.join(root, "source_manifest.csv"), "w") as f:
                    f.write(f"generation\n{tag}\n")
            if "snapshot_manifest.json" in present:
                with open(os.path.join(root, "snapshot_manifest.json"), "w") as f:
                    f.write(f'{{"generation": "{tag}"}}')
            if "independent_dcf_validation.xlsx" in present:
                with open(os.path.join(root, "independent_dcf_validation.xlsx"), "w") as f:
                    f.write(f"FAKE-XLSX-CONTENT-{tag}")

        def slot_exists(root, name, is_dir):
            path = os.path.join(root, name)
            return os.path.isdir(path) if is_dir else os.path.isfile(path)

        def slot_content(root, name, is_dir):
            path = os.path.join(root, name)
            if is_dir:
                return _hash_directory_tree(path)  # None if the directory does not exist
            return open(path).read() if os.path.isfile(path) else None

        live_root = os.path.join(tmp_root, "live")
        txn_base_dir = os.path.join(tmp_root, "txn_base")
        os.makedirs(txn_base_dir)

        def run_scenario(label, absent_slots, fail_at_backup_slot=None, fail_at_slot=None, expect_success=False):
            nonlocal overall_ok
            if os.path.isdir(live_root):
                shutil.rmtree(live_root)
            os.makedirs(live_root)
            present_before = [n for n in slot_names if n not in absent_slots]
            write_generation(live_root, "original", present=present_before)
            live_slots = make_slots(live_root)
            original_signatures = {name: slot_content(live_root, name, is_dir) for name, _, is_dir in live_slots}

            staging_dir = os.path.join(tmp_root, f"staging_{label}")
            if os.path.isdir(staging_dir):
                shutil.rmtree(staging_dir)
            write_generation(staging_dir, "new")  # staging always generates all 4 slots -- a normal rebuild

            problems = []
            try:
                ok, msg = _publish_staged_generation(
                    staging_dir, fail_at_backup_slot=fail_at_backup_slot, fail_at_slot=fail_at_slot,
                    log=lambda *a: None, slots=live_slots, base_dir=txn_base_dir)
            except Exception as e:
                print(f"[FAIL] {label}: UNEXPECTED EXCEPTION ESCAPED THE FUNCTION: {e!r}")
                overall_ok = False
                if os.path.isdir(staging_dir):
                    shutil.rmtree(staging_dir)
                return False

            if not isinstance(ok, bool) or not isinstance(msg, str):
                problems.append(f"return type is not (bool, str): {(type(ok).__name__, type(msg).__name__)}")
            if ok != expect_success:
                problems.append(f"published={ok}, expected {expect_success}")

            if expect_success:
                for name, live_path, is_dir in live_slots:
                    if is_dir:
                        if not slot_exists(live_root, name, is_dir):
                            problems.append(f"slot '{name}' missing after successful publish")
                    else:
                        content = slot_content(live_root, name, is_dir)
                        if content is None or "new" not in content:
                            problems.append(f"slot '{name}' does not show the NEW generation "
                                             f"after successful publish (content={content!r})")
            else:
                for name, live_path, is_dir in live_slots:
                    if name in absent_slots:
                        if slot_exists(live_root, name, is_dir):
                            problems.append(f"originally-absent slot '{name}' now EXISTS after "
                                             f"rollback -- it should remain absent")
                    else:
                        content = slot_content(live_root, name, is_dir)
                        if content != original_signatures[name]:
                            problems.append(f"originally-present slot '{name}' does not match its "
                                             f"original content after rollback")
                residue = [n for n in os.listdir(txn_base_dir) if n.startswith(TXN_DIR_PREFIX)]
                if residue:
                    problems.append(f"transaction residue left behind after a rollback that should "
                                     f"have cleaned up: {residue}")
                    for r in residue:
                        shutil.rmtree(os.path.join(txn_base_dir, r))  # don't poison later scenarios

            if os.path.isdir(staging_dir):
                shutil.rmtree(staging_dir)

            scenario_ok = len(problems) == 0
            detail = f"absent={sorted(absent_slots) or 'none'}, fail_at_backup_slot={fail_at_backup_slot}, fail_at_slot={fail_at_slot}"
            print(f"[{'PASS' if scenario_ok else 'FAIL'}] {label} ({detail}): "
                  f"{'; '.join(problems) if problems else 'all assertions passed'}")
            if not scenario_ok:
                overall_ok = False
            return scenario_ok

        print("[1] Each of the 4 slots individually absent, crossed with a failure at every promotion position:")
        for absent_name in slot_names:
            for fail_idx in range(4):
                run_scenario(f"absent_{absent_name}_fail_promote_{fail_idx}", {absent_name}, fail_at_slot=fail_idx)

        print("\n[2] Multiple originally-absent slots:")
        run_scenario("absent_two_A", {slot_names[0], slot_names[2]}, fail_at_slot=3)
        run_scenario("absent_two_B", {slot_names[1], slot_names[3]}, fail_at_slot=1)
        run_scenario("absent_three", {slot_names[0], slot_names[1], slot_names[2]}, fail_at_slot=3)

        print("\n[3] All 4 slots originally absent:")
        run_scenario("absent_all_fail_2", set(slot_names), fail_at_slot=2)
        run_scenario("absent_all_fail_0", set(slot_names), fail_at_slot=0)

        print("\n[4] An originally-absent slot promoted successfully, then a LATER promotion fails:")
        run_scenario("absent_first_then_later_fails", {slot_names[0]}, fail_at_slot=3)
        run_scenario("absent_third_then_later_fails", {slot_names[2]}, fail_at_slot=3)

        print("\n[5] Backup-stage failures with some originals absent:")
        for fail_idx in range(4):
            run_scenario(f"absent_one_fail_backup_{fail_idx}", {slot_names[1]}, fail_at_backup_slot=fail_idx)

        print("\n[6] Normal successful publish with some originals absent (positive control):")
        run_scenario("absent_two_success", {slot_names[0], slot_names[3]}, expect_success=True)
        run_scenario("absent_all_success", set(slot_names), expect_success=True)
        run_scenario("absent_none_success", set(), expect_success=True)

        print(f"\n=== Missing-original fault-injection self-test: "
              f"{'ALL SCENARIOS PASSED' if overall_ok else 'AT LEAST ONE SCENARIO FAILED'} "
              f"(every scenario asserts: no unexpected exception escapes; the documented (bool, str) "
              f"result is returned; originally-present slots are restored byte-for-byte on rollback; "
              f"originally-absent slots remain absent on rollback; no transaction residue remains "
              f"after a successful rollback) ===")
        return 0 if overall_ok else 1
    finally:
        if os.path.isdir(tmp_root):
            shutil.rmtree(tmp_root)
            print(f"Deleted temporary test root: {tmp_root}")


# ---------------------------------------------------------------------------
# Rollback-operation-failure self-test: what happens when a filesystem
# operation INSIDE rollback itself fails (not the original publish failure
# that triggered rollback, but the rollback's own remove-promoted-output or
# restore-backup steps). The function must not raise, must report which
# slot(s) are left unresolved with a recovery path, and must preserve (not
# delete) the transaction directory so manual recovery is possible.
# ---------------------------------------------------------------------------

def run_rollback_operation_failure_self_test():
    print("=== Rollback-operation-failure self-test (offline, temp directories only) ===\n")

    tmp_root = tempfile.mkdtemp(prefix="rollback_failure_selftest_")
    overall_ok = True
    try:
        def make_slots(root):
            snap_dir = os.path.join(root, "snapshots")
            return [
                ("snapshots", snap_dir, True),
                ("source_manifest.csv", os.path.join(root, "source_manifest.csv"), False),
                ("snapshot_manifest.json", os.path.join(root, "snapshot_manifest.json"), False),
                ("independent_dcf_validation.xlsx", os.path.join(root, "independent_dcf_validation.xlsx"), False),
            ]

        def write_generation(root, tag):
            snap_dir = os.path.join(root, "snapshots")
            os.makedirs(snap_dir, exist_ok=True)
            for tk in TICKERS:
                with open(os.path.join(snap_dir, f"{tk}_snapshot.json"), "w") as f:
                    f.write(f'{{"generation": "{tag}", "ticker": "{tk}"}}')
            with open(os.path.join(root, "source_manifest.csv"), "w") as f:
                f.write(f"generation\n{tag}\n")
            with open(os.path.join(root, "snapshot_manifest.json"), "w") as f:
                f.write(f'{{"generation": "{tag}"}}')
            with open(os.path.join(root, "independent_dcf_validation.xlsx"), "w") as f:
                f.write(f"FAKE-XLSX-CONTENT-{tag}")

        def call_and_capture(staging_dir, live_slots, txn_base_dir, **kwargs):
            try:
                ok, msg = _publish_staged_generation(staging_dir, log=lambda *a: None, slots=live_slots,
                                                       base_dir=txn_base_dir, **kwargs)
                return ok, msg, False
            except Exception as e:
                return None, str(e), True

        # --- Scenario A: failure REMOVING a promoted output during rollback ---
        live_root = os.path.join(tmp_root, "live_a")
        txn_base_dir = os.path.join(tmp_root, "txn_base_a")
        os.makedirs(live_root); os.makedirs(txn_base_dir)
        write_generation(live_root, "original")
        live_slots = make_slots(live_root)
        target_name = live_slots[0][0]  # "snapshots" -- promoted before the later injected failure
        staging_dir = os.path.join(tmp_root, "staging_a")
        write_generation(staging_dir, "new")

        ok, msg, raised = call_and_capture(staging_dir, live_slots, txn_base_dir,
                                            fail_at_slot=2, fail_removing_promoted=target_name)
        residue = [n for n in os.listdir(txn_base_dir) if n.startswith(TXN_DIR_PREFIX)]
        scenario_a_ok = (not raised) and (ok is False) and isinstance(msg, str) and (target_name in msg) and len(residue) == 1
        print(f"[{'PASS' if scenario_a_ok else 'FAIL'}] fail_removing_promoted='{target_name}': "
              f"raised_exception={raised}, published={ok} (expected False), slot named in message="
              f"{isinstance(msg, str) and target_name in msg}, transaction directory preserved={len(residue) == 1}")
        if not scenario_a_ok:
            overall_ok = False
        if residue:
            backup_file = os.path.join(txn_base_dir, residue[0], f"backup__{target_name}", f"{TICKERS[0]}_snapshot.json")
            recoverable = os.path.isfile(backup_file) and '"original"' in open(backup_file).read()
            print(f"  retained backup for '{target_name}' is present and recoverable: {recoverable}")
            if not recoverable:
                overall_ok = False
            shutil.rmtree(os.path.join(txn_base_dir, residue[0]))
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir)

        # --- Scenario B: failure RESTORING a backup (rename back into place) during rollback ---
        live_root = os.path.join(tmp_root, "live_b")
        txn_base_dir = os.path.join(tmp_root, "txn_base_b")
        os.makedirs(live_root); os.makedirs(txn_base_dir)
        write_generation(live_root, "original")
        live_slots = make_slots(live_root)
        target_name = live_slots[1][0]  # "source_manifest.csv" -- promoted; a still-later slot then fails
        staging_dir = os.path.join(tmp_root, "staging_b")
        write_generation(staging_dir, "new")

        ok, msg, raised = call_and_capture(staging_dir, live_slots, txn_base_dir,
                                            fail_at_slot=3, fail_restoring_backup=target_name)
        residue = [n for n in os.listdir(txn_base_dir) if n.startswith(TXN_DIR_PREFIX)]
        scenario_b_ok = (not raised) and (ok is False) and isinstance(msg, str) and (target_name in msg) and len(residue) == 1
        print(f"\n[{'PASS' if scenario_b_ok else 'FAIL'}] fail_restoring_backup='{target_name}': "
              f"raised_exception={raised}, published={ok} (expected False), slot named in message="
              f"{isinstance(msg, str) and target_name in msg}, transaction directory preserved={len(residue) == 1}")
        if not scenario_b_ok:
            overall_ok = False
        if residue:
            backup_file = os.path.join(txn_base_dir, residue[0], f"backup__{target_name}")
            recoverable = os.path.isfile(backup_file) and open(backup_file).read() == "generation\noriginal\n"
            print(f"  retained backup for '{target_name}' is present and recoverable: {recoverable}")
            if not recoverable:
                overall_ok = False
            shutil.rmtree(os.path.join(txn_base_dir, residue[0]))
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir)

        # --- Scenario C: the NEXT publish attempt refuses safely while this residue exists ---
        live_root = os.path.join(tmp_root, "live_c")
        txn_base_dir = os.path.join(tmp_root, "txn_base_c")
        os.makedirs(live_root); os.makedirs(txn_base_dir)
        write_generation(live_root, "original")
        live_slots = make_slots(live_root)
        staging_dir1 = os.path.join(tmp_root, "staging_c1")
        write_generation(staging_dir1, "new")
        call_and_capture(staging_dir1, live_slots, txn_base_dir,
                          fail_at_slot=2, fail_removing_promoted=live_slots[0][0])
        if os.path.isdir(staging_dir1):
            shutil.rmtree(staging_dir1)

        staging_dir2 = os.path.join(tmp_root, "staging_c2")
        write_generation(staging_dir2, "newer")
        ok2, msg2, raised2 = call_and_capture(staging_dir2, live_slots, txn_base_dir)
        refused = (not raised2) and (ok2 is False) and isinstance(msg2, str) and ("residue" in msg2.lower())
        print(f"\n[{'PASS' if refused else 'FAIL'}] next_publish_refuses_while_residue_exists: "
              f"raised_exception={raised2}, published={ok2} (expected False), "
              f"refusal message mentions residue={isinstance(msg2, str) and 'residue' in msg2.lower()}")
        if not refused:
            overall_ok = False
        for n in os.listdir(txn_base_dir):
            if n.startswith(TXN_DIR_PREFIX):
                shutil.rmtree(os.path.join(txn_base_dir, n))
        if os.path.isdir(staging_dir2):
            shutil.rmtree(staging_dir2)

        print(f"\n=== Rollback-operation-failure self-test: "
              f"{'ALL SCENARIOS PASSED' if overall_ok else 'AT LEAST ONE SCENARIO FAILED'} ===")
        return 0 if overall_ok else 1
    finally:
        if os.path.isdir(tmp_root):
            shutil.rmtree(tmp_root)
            print(f"Deleted temporary test root: {tmp_root}")


# ---------------------------------------------------------------------------
# Cleanup-warning self-test: verifies (1) a post-commit cleanup failure still
# leaves all four live outputs holding the COMPLETE new generation with the
# expected content, (2) the retained backups for every previously-existing
# slot remain recoverable inside the transaction directory, (3) the warning
# is surfaced through the exact reporting function rebuild_from_cache() uses
# (_report_publish_result), and (4) the next publish attempt refuses safely
# while that residue is unresolved.
# ---------------------------------------------------------------------------

def run_cleanup_warning_self_test():
    print("=== Cleanup-warning self-test (offline, temp directories only) ===\n")

    tmp_root = tempfile.mkdtemp(prefix="cleanup_warning_selftest_")
    overall_ok = True
    try:
        def make_slots(root):
            snap_dir = os.path.join(root, "snapshots")
            return [
                ("snapshots", snap_dir, True),
                ("source_manifest.csv", os.path.join(root, "source_manifest.csv"), False),
                ("snapshot_manifest.json", os.path.join(root, "snapshot_manifest.json"), False),
                ("independent_dcf_validation.xlsx", os.path.join(root, "independent_dcf_validation.xlsx"), False),
            ]

        def write_generation(root, tag):
            snap_dir = os.path.join(root, "snapshots")
            os.makedirs(snap_dir, exist_ok=True)
            for tk in TICKERS:
                with open(os.path.join(snap_dir, f"{tk}_snapshot.json"), "w") as f:
                    f.write(f'{{"generation": "{tag}", "ticker": "{tk}"}}')
            with open(os.path.join(root, "source_manifest.csv"), "w") as f:
                f.write(f"generation\n{tag}\n")
            with open(os.path.join(root, "snapshot_manifest.json"), "w") as f:
                f.write(f'{{"generation": "{tag}"}}')
            with open(os.path.join(root, "independent_dcf_validation.xlsx"), "w") as f:
                f.write(f"FAKE-XLSX-CONTENT-{tag}")

        live_root = os.path.join(tmp_root, "live")
        txn_base_dir = os.path.join(tmp_root, "txn_base")
        os.makedirs(live_root); os.makedirs(txn_base_dir)
        write_generation(live_root, "original")
        live_slots = make_slots(live_root)
        staging_dir = os.path.join(tmp_root, "staging")
        write_generation(staging_dir, "new")

        ok, msg = _publish_staged_generation(staging_dir, fail_at_cleanup=True, log=lambda *a: None,
                                              slots=live_slots, base_dir=txn_base_dir)

        # (1) all four live outputs contain the complete NEW generation
        all_new = True
        for name, live_path, is_dir in live_slots:
            if is_dir:
                for tk in TICKERS:
                    content = open(os.path.join(live_path, f"{tk}_snapshot.json")).read()
                    if '"new"' not in content:
                        all_new = False
            else:
                if "new" not in open(live_path).read():
                    all_new = False
        print(f"[{'PASS' if ok and all_new else 'FAIL'}] committed content check: published={ok} "
              f"(expected True), all 4 live outputs hold the complete NEW generation={all_new}")
        if not (ok and all_new):
            overall_ok = False

        # (2) retained backups for all previously-existing slots remain recoverable
        residue = [n for n in os.listdir(txn_base_dir) if n.startswith(TXN_DIR_PREFIX)]
        backups_recoverable = len(residue) == 1
        if backups_recoverable:
            txn_dir_path = os.path.join(txn_base_dir, residue[0])
            for name, live_path, is_dir in live_slots:
                backup_path = os.path.join(txn_dir_path, f"backup__{name}")
                if is_dir:
                    ok_bak = all(os.path.isfile(os.path.join(backup_path, f"{tk}_snapshot.json")) and
                                 '"original"' in open(os.path.join(backup_path, f"{tk}_snapshot.json")).read()
                                 for tk in TICKERS)
                else:
                    ok_bak = os.path.isfile(backup_path) and "original" in open(backup_path).read()
                if not ok_bak:
                    backups_recoverable = False
        print(f"[{'PASS' if backups_recoverable else 'FAIL'}] retained backups recoverable for all "
              f"4 previously-existing slots in the transaction directory: {backups_recoverable}")
        if not backups_recoverable:
            overall_ok = False

        # (3) the warning is surfaced through the exact public reporting path
        captured = []
        _report_publish_result(ok, msg, log=captured.append)
        warning_surfaced = (msg.startswith(CLEANUP_WARNING_PREFIX)
                             and any(CLEANUP_WARNING_PREFIX in line for line in captured)
                             and any("NOT a completely clean success" in line for line in captured))
        print(f"[{'PASS' if warning_surfaced else 'FAIL'}] _report_publish_result (the exact function "
              f"rebuild_from_cache() calls) surfaces the warning distinctly, not as a silent clean success: "
              f"{warning_surfaced}")
        if not warning_surfaced:
            overall_ok = False

        # (4) the next publish attempt refuses safely while this residue is unresolved
        staging_dir2 = os.path.join(tmp_root, "staging2")
        write_generation(staging_dir2, "newer")
        ok2, msg2 = _publish_staged_generation(staging_dir2, log=lambda *a: None, slots=live_slots,
                                                base_dir=txn_base_dir)
        refused = (ok2 is False) and ("residue" in msg2.lower())
        print(f"[{'PASS' if refused else 'FAIL'}] next_publish_refuses_while_cleanup_residue_exists: "
              f"published={ok2} (expected False), refusal message mentions residue={'residue' in msg2.lower()}")
        if not refused:
            overall_ok = False

        for n in os.listdir(txn_base_dir):
            if n.startswith(TXN_DIR_PREFIX):
                shutil.rmtree(os.path.join(txn_base_dir, n))
        if os.path.isdir(staging_dir2):
            shutil.rmtree(staging_dir2)

        print(f"\n=== Cleanup-warning self-test: {'ALL CHECKS PASSED' if overall_ok else 'AT LEAST ONE CHECK FAILED'} ===")
        return 0 if overall_ok else 1
    finally:
        if os.path.isdir(tmp_root):
            shutil.rmtree(tmp_root)
            print(f"Deleted temporary test root: {tmp_root}")


def run_self_test():
    rc1 = run_manifest_completeness_self_test()
    print()
    rc2 = run_pii_regression_self_test()
    print()
    rc3 = run_refresh_fault_injection_self_test()
    print()
    rc4 = run_publish_fault_injection_self_test()
    print()
    rc5 = run_snapshot_manifest_structure_self_test()
    print()
    rc6 = run_source_manifest_structure_self_test()
    print()
    rc7 = run_missing_original_fault_injection_self_test()
    print()
    rc8 = run_rollback_operation_failure_self_test()
    print()
    rc9 = run_cleanup_warning_self_test()
    print()
    rc10 = run_refresh_rollback_incomplete_self_test()
    print(f"\n=== Self-test summary: manifest-completeness={'PASS' if rc1 == 0 else 'FAIL'}, "
          f"PII-scanner={'PASS' if rc2 == 0 else 'FAIL'}, "
          f"publish-fault-injection={'PASS' if rc4 == 0 else 'FAIL'}, "
          f"refresh-fault-injection={'PASS' if rc3 == 0 else 'FAIL'}, "
          f"snapshot-manifest-structure={'PASS' if rc5 == 0 else 'FAIL'}, "
          f"source-manifest-structure={'PASS' if rc6 == 0 else 'FAIL'}, "
          f"missing-original-fault-injection={'PASS' if rc7 == 0 else 'FAIL'}, "
          f"rollback-operation-failure={'PASS' if rc8 == 0 else 'FAIL'}, "
          f"cleanup-warning={'PASS' if rc9 == 0 else 'FAIL'}, "
          f"refresh-rollback-incomplete={'PASS' if rc10 == 0 else 'FAIL'} ===")
    return 0 if (rc1 == 0 and rc2 == 0 and rc3 == 0 and rc4 == 0 and rc5 == 0 and rc6 == 0
                 and rc7 == 0 and rc8 == 0 and rc9 == 0 and rc10 == 0) else 1


# ---------------------------------------------------------------------------
# Offline fault-injection self-test for staged_refresh (Issue 3). Operates
# ENTIRELY against a temporary copy of the cache directory -- the real
# sec_raw_cache/ is opened only READ-ONLY, to build stub response bytes, and
# is never passed to staged_refresh here. No network connection is opened;
# every "download" is a Python function call returning bytes already held in
# memory (either read from the real cache, for a realistic payload, or a
# deliberately broken variant of it).
# ---------------------------------------------------------------------------

def _hash_directory_tree(path):
    """Deterministic combined hash of every file's (relative path, sha256) under `path`."""
    if not os.path.isdir(path):
        return None
    entries = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for fn in sorted(files):
            fpath = os.path.join(root, fn)
            relpath = os.path.relpath(fpath, path)
            entries.append((relpath, sha256_file(fpath)))
    entries.sort()
    h = hashlib.sha256()
    for relpath, filehash in entries:
        h.update(relpath.encode("utf-8") + b"\0" + filehash.encode("utf-8") + b"\0")
    return h.hexdigest()


def _load_real_cache_stub_data():
    """Read-only load of the REAL sec_raw_cache/ contents, to build realistic stub
    response bytes for the fault-injection test. Never writes to CACHE_DIR."""
    tickers_bytes = open(os.path.join(CACHE_DIR, "company_tickers.json"), "rb").read()
    tickers_obj = json.loads(tickers_bytes)
    cik_map = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in tickers_obj.values()}
    url_bytes = {"https://www.sec.gov/files/company_tickers.json": tickers_bytes}
    for tk in TICKERS:
        cik = cik_map[tk]
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        url_bytes[url] = open(os.path.join(CACHE_DIR, f"companyfacts_{tk}.json"), "rb").read()
    return url_bytes, cik_map


def run_refresh_fault_injection_self_test():
    print("=== Refresh-transaction fault-injection self-test (offline, stubbed, no network) ===\n")
    if not os.path.isdir(CACHE_DIR):
        print(f"SELF-TEST FAILED: {CACHE_DIR} does not exist -- cannot build realistic stub payloads.")
        return 1

    real_cache_hash_before = _hash_directory_tree(CACHE_DIR)
    url_bytes, cik_map = _load_real_cache_stub_data()
    urls = list(url_bytes.keys())
    tickers_url = urls[0]
    companyfacts_urls = {tk: f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_map[tk]}.json" for tk in TICKERS}

    def make_fetch(fail_on_url=None, corrupt_url=None, corruption="malformed_json"):
        def fetch(url, ua):
            if fail_on_url is not None and url == fail_on_url:
                raise OSError(f"STUBBED network failure fetching {url}")  # matches urllib.error.URLError's real base class
            data = url_bytes[url]
            if corrupt_url is not None and url == corrupt_url:
                if corruption == "malformed_json":
                    return b"{not valid json !!!"
                if corruption == "wrong_cik":
                    obj = json.loads(data)
                    obj["cik"] = 999999999
                    return json.dumps(obj).encode("utf-8")
                if corruption == "missing_entity_name":
                    obj = json.loads(data)
                    obj.pop("entityName", None)
                    return json.dumps(obj).encode("utf-8")
                if corruption == "missing_facts":
                    obj = json.loads(data)
                    obj["facts"] = {}
                    return json.dumps(obj).encode("utf-8")
            return data
        return fetch

    scenarios = [
        ("fail_download_tickers", make_fetch(fail_on_url=tickers_url), False),
        ("fail_download_MSFT", make_fetch(fail_on_url=companyfacts_urls["MSFT"]), False),
        ("fail_download_CAT", make_fetch(fail_on_url=companyfacts_urls["CAT"]), False),
        ("fail_download_INTC", make_fetch(fail_on_url=companyfacts_urls["INTC"]), False),
        ("fail_download_VZ", make_fetch(fail_on_url=companyfacts_urls["VZ"]), False),
        ("malformed_json_tickers", make_fetch(corrupt_url=tickers_url, corruption="malformed_json"), False),
        ("malformed_json_MSFT", make_fetch(corrupt_url=companyfacts_urls["MSFT"], corruption="malformed_json"), False),
        ("structurally_invalid_cik_mismatch", make_fetch(corrupt_url=companyfacts_urls["CAT"], corruption="wrong_cik"), False),
        ("structurally_invalid_missing_entity_name", make_fetch(corrupt_url=companyfacts_urls["INTC"], corruption="missing_entity_name"), False),
        ("structurally_invalid_missing_facts", make_fetch(corrupt_url=companyfacts_urls["VZ"], corruption="missing_facts"), False),
        ("failure_during_publication", make_fetch(), True),  # all data valid; fail_at_publish=True
    ]

    tmp_parent = tempfile.mkdtemp(prefix="staged_refresh_selftest_")
    tmp_cache = os.path.join(tmp_parent, "sec_raw_cache")
    overall_ok = True
    try:
        shutil.copytree(CACHE_DIR, tmp_cache)
        baseline_hash = _hash_directory_tree(tmp_cache)
        print(f"Baseline (temp copy of the real cache, NOT the real cache itself) hash: {baseline_hash[:16]}...\n")

        def leftover_txn_artifacts():
            return [n for n in os.listdir(tmp_parent)
                    if n != "sec_raw_cache" and (n.startswith(".sec_raw_cache_staging_")
                                                  or n.startswith(REFRESH_TXN_DIR_PREFIX)
                                                  or n.endswith(LEGACY_REFRESH_BACKUP_SUFFIX))]

        for name, fetch_fn, fail_at_publish in scenarios:
            before_hash = _hash_directory_tree(tmp_cache)
            ok, msg = staged_refresh(tmp_cache, TICKERS, fetch_fn, "TestUA/1.0 (fault-injection self-test)",
                                      fail_at_publish=fail_at_publish, log=lambda *a: None)
            after_hash = _hash_directory_tree(tmp_cache)
            leftover = leftover_txn_artifacts()
            unchanged = before_hash == after_hash
            no_leftover = len(leftover) == 0
            scenario_ok = (not ok) and unchanged and no_leftover
            status = "PASS" if scenario_ok else "FAIL"
            print(f"[{status}] {name}: staged_refresh ok={ok} (expected False), "
                  f"cache unchanged={unchanged}, no leftover temp dirs={no_leftover}"
                  + (f", leftover={leftover}" if leftover else ""))
            if not scenario_ok:
                overall_ok = False

        # --- Failure backing up the live cache directory (the P0-defect-class scenario for this
        # transaction, applied here to the single-directory case) ---
        before_hash = _hash_directory_tree(tmp_cache)
        ok, msg = staged_refresh(tmp_cache, TICKERS, make_fetch(), "TestUA/1.0 (fault-injection self-test)",
                                  fail_at_backup=True, log=lambda *a: None)
        after_hash = _hash_directory_tree(tmp_cache)
        leftover = leftover_txn_artifacts()
        scenario_ok = (not ok) and (before_hash == after_hash) and (len(leftover) == 0)
        print(f"[{'PASS' if scenario_ok else 'FAIL'}] fail_backing_up_live_cache: staged_refresh ok={ok} "
              f"(expected False), cache unchanged={before_hash == after_hash}, "
              f"no leftover temp dirs={len(leftover) == 0}" + (f", leftover={leftover}" if leftover else ""))
        if not scenario_ok:
            overall_ok = False

        # --- Failure deleting the old backup AFTER a successful publish: must report SUCCESS, new
        # cache must actually be live, and the old backup must be retained (not lost, not deleted). ---
        before_new_hash = None
        ok, msg = staged_refresh(tmp_cache, TICKERS, make_fetch(), "TestUA/1.0 (fault-injection self-test)",
                                  fail_at_cleanup=True, log=lambda *a: None)
        residue_after_cleanup_fail = [n for n in os.listdir(tmp_parent)
                                       if n != "sec_raw_cache" and n.startswith(REFRESH_TXN_DIR_PREFIX)]
        cleanup_scenario_ok = ok and len(residue_after_cleanup_fail) == 1
        print(f"[{'PASS' if cleanup_scenario_ok else 'FAIL'}] fail_deleting_old_backup_after_publish: "
              f"staged_refresh ok={ok} (expected True -- a post-commit cleanup failure must NOT be reported "
              f"as refresh failure once the new cache is live), exactly one retained backup dir="
              f"{len(residue_after_cleanup_fail) == 1}")
        if not cleanup_scenario_ok:
            overall_ok = False

        # --- Pre-existing transaction residue must cause a clean refusal, without touching the live
        # cache or deleting the residue itself. Uses the retained backup from the scenario just above. ---
        if residue_after_cleanup_fail:
            before_hash = _hash_directory_tree(tmp_cache)
            marker_path = os.path.join(tmp_parent, residue_after_cleanup_fail[0], "cache_backup",
                                        "company_tickers.json")
            marker_existed = os.path.exists(marker_path)
            ok, msg = staged_refresh(tmp_cache, TICKERS, make_fetch(), "TestUA/1.0 (fault-injection self-test)",
                                      log=lambda *a: None)
            after_hash = _hash_directory_tree(tmp_cache)
            residue_still_present = os.path.isdir(os.path.join(tmp_parent, residue_after_cleanup_fail[0]))
            residue_scenario_ok = (not ok) and (before_hash == after_hash) and residue_still_present
            print(f"[{'PASS' if residue_scenario_ok else 'FAIL'}] pre_existing_refresh_residue: "
                  f"staged_refresh ok={ok} (expected False), cache unchanged={before_hash == after_hash}, "
                  f"residue directory left untouched (not deleted)={residue_still_present}")
            if not residue_scenario_ok:
                overall_ok = False
            # Clean up the retained backup manually so it doesn't affect the scenarios below.
            shutil.rmtree(os.path.join(tmp_parent, residue_after_cleanup_fail[0]))
        else:
            print("[FAIL] pre_existing_refresh_residue: setup scenario above did not leave a retained "
                  "backup to test against")
            overall_ok = False

        print()
        # NOTE: every scenario in this self-test (including this positive control) republishes the
        # SAME stub payload bytes read once from the real cache, so the cache's CONTENT is expected
        # to be byte-identical before and after a successful republish -- an unchanged hash here is
        # not evidence of failure. What this scenario actually proves is that staged_refresh reports
        # success (ok=True) and leaves no transaction residue when nothing is injected to fail.
        ok, msg = staged_refresh(tmp_cache, TICKERS, make_fetch(), "TestUA/1.0 (fault-injection self-test)", log=lambda *a: None)
        positive_ok = ok and (len(leftover_txn_artifacts()) == 0)
        print(f"[{'PASS' if positive_ok else 'FAIL'}] positive_control (all-valid stub data, no injected fault): "
              f"staged_refresh ok={ok} (expected True), "
              f"no leftover temp dirs={len(leftover_txn_artifacts()) == 0} -- this confirms the negative "
              f"scenarios above are failing because of the injected fault, not because staged_refresh "
              f"always fails against this temp copy.")
        if not positive_ok:
            overall_ok = False

        real_cache_hash_after = _hash_directory_tree(CACHE_DIR)
        real_cache_ok = real_cache_hash_after == real_cache_hash_before
        print(f"\n[{'PASS' if real_cache_ok else 'FAIL'}] Real sec_raw_cache/ hash unchanged across this entire "
              f"self-test run: before={real_cache_hash_before[:16]}... after={real_cache_hash_after[:16]}... "
              f"(it was only ever read from, to build stub bytes; never passed as staged_refresh's cache_dir argument).")
        if not real_cache_ok:
            overall_ok = False

        print(f"\n=== Refresh fault-injection self-test: {'ALL SCENARIOS PASSED' if overall_ok else 'AT LEAST ONE SCENARIO FAILED'} "
              f"({len(scenarios)} fetch/validation negative scenarios + 1 backup-failure scenario + "
              f"1 cleanup-failure scenario + 1 pre-existing-residue refusal + 1 positive control + "
              f"1 real-cache-untouched check) ===")
        return 0 if overall_ok else 1
    finally:
        if os.path.isdir(tmp_parent):
            shutil.rmtree(tmp_parent)
            print(f"Deleted temporary test directory: {tmp_parent}")


# ---------------------------------------------------------------------------
# Refresh rollback-incomplete self-test: an independent review reproduced a
# real defect -- when restoring the backup cache during rollback itself
# fails (a SEPARATE filesystem failure from the original promotion failure
# that triggered rollback), the old code let that exception escape
# staged_refresh's inner try, got caught by an unrelated outer handler, and
# was misreported as "live cache untouched" even though the live cache
# directory was actually ABSENT. This test proves the fix: the failure is
# caught explicitly, reported as an incomplete rollback (never as untouched
# or restored), and the backup remains genuinely recoverable. Entirely
# offline, against a temp copy of the real cache; the real sec_raw_cache/ is
# only ever read from, never passed as staged_refresh's cache_dir argument.
# ---------------------------------------------------------------------------

def run_refresh_rollback_incomplete_self_test():
    print("=== Refresh rollback-incomplete self-test (offline, stubbed, no network) ===\n")
    if not os.path.isdir(CACHE_DIR):
        print(f"SELF-TEST FAILED: {CACHE_DIR} does not exist -- cannot build realistic stub payloads.")
        return 1

    url_bytes, _cik_map = _load_real_cache_stub_data()

    def make_fetch():
        def fetch(url, ua):
            return url_bytes[url]
        return fetch

    tmp_parent = tempfile.mkdtemp(prefix="staged_refresh_rollback_incomplete_selftest_")
    tmp_cache = os.path.join(tmp_parent, "sec_raw_cache")
    overall_ok = True
    try:
        shutil.copytree(CACHE_DIR, tmp_cache)
        original_hash = _hash_directory_tree(tmp_cache)

        # Scenario: backup of the original succeeds, staged promotion fails, THEN the
        # restoration rename (rollback's own filesystem operation) also fails.
        try:
            ok, msg = staged_refresh(tmp_cache, TICKERS, make_fetch(), "TestUA/1.0 (rollback-incomplete self-test)",
                                      fail_at_publish=True, fail_restoring_backup=True, log=lambda *a: None)
            raised = False
        except Exception as e:
            ok, msg, raised = None, str(e), True

        print(f"[{'PASS' if not raised else 'FAIL'}] no unexpected exception escaped staged_refresh(): "
              f"raised={raised}")
        if raised:
            overall_ok = False

        result_is_false = (not raised) and (ok is False)
        print(f"[{'PASS' if result_is_false else 'FAIL'}] result is False: {ok}")
        if not result_is_false:
            overall_ok = False

        says_incomplete = (not raised) and isinstance(msg, str) and msg.startswith(REFRESH_ROLLBACK_INCOMPLETE_PREFIX)
        print(f"[{'PASS' if says_incomplete else 'FAIL'}] message begins with "
              f"REFRESH_ROLLBACK_INCOMPLETE_PREFIX ('{REFRESH_ROLLBACK_INCOMPLETE_PREFIX}'): {says_incomplete}")
        if not says_incomplete:
            overall_ok = False

        mentions_absent = (not raised) and isinstance(msg, str) and "ABSENT" in msg
        print(f"[{'PASS' if mentions_absent else 'FAIL'}] message states the live cache may be ABSENT: {mentions_absent}")
        if not mentions_absent:
            overall_ok = False

        residue = [n for n in os.listdir(tmp_parent) if n != "sec_raw_cache" and n.startswith(REFRESH_TXN_DIR_PREFIX)]
        backup_dir = os.path.join(tmp_parent, residue[0], "cache_backup") if residue else None
        paths_included = (not raised) and bool(residue) and isinstance(msg, str) and \
            (os.path.join(tmp_parent, residue[0]) in msg) and (backup_dir in msg)
        print(f"[{'PASS' if paths_included else 'FAIL'}] message includes the retained transaction "
              f"directory AND backup path: {paths_included}")
        if not paths_included:
            overall_ok = False

        cache_absent = not os.path.exists(tmp_cache)
        print(f"[{'PASS' if cache_absent else 'FAIL'}] live cache directory is confirmed ABSENT "
              f"in this injected scenario: {cache_absent}")
        if not cache_absent:
            overall_ok = False

        recoverable = False
        if backup_dir and os.path.isdir(backup_dir):
            recoverable = _hash_directory_tree(backup_dir) == original_hash
        print(f"[{'PASS' if recoverable else 'FAIL'}] original cache contents are byte-for-byte "
              f"recoverable from the retained backup: {recoverable}")
        if not recoverable:
            overall_ok = False

        residue_remains = len(residue) == 1
        print(f"[{'PASS' if residue_remains else 'FAIL'}] transaction residue remains (not deleted, "
              f"not overwritten): {residue_remains}")
        if not residue_remains:
            overall_ok = False

        ok2, msg2 = staged_refresh(tmp_cache, TICKERS, make_fetch(), "TestUA/1.0 (rollback-incomplete self-test)",
                                    log=lambda *a: None)
        refuses = (ok2 is False) and isinstance(msg2, str) and ("residue" in msg2.lower())
        print(f"[{'PASS' if refuses else 'FAIL'}] a subsequent refresh attempt refuses because of that "
              f"residue: published={ok2} (expected False), mentions residue={isinstance(msg2, str) and 'residue' in msg2.lower()}")
        if not refuses:
            overall_ok = False

        captured = []
        if not raised:
            _report_refresh_result(ok, msg, log=captured.append)
        joined = " ".join(captured).lower()
        # A negated warning ("do NOT assume it is untouched or unchanged") is exactly the
        # correct, desired behavior -- what must be absent is an AFFIRMATIVE claim, i.e. the
        # original bug's exact false phrases appearing WITHOUT a preceding negation. Strip any
        # "do not assume ... ." clause first so its own (correct, negated) use of these words
        # cannot trigger a false positive in this check.
        joined_stripped = re.sub(r"do not assume[^.]*\.", "", joined)
        false_claim_phrases = ["live cache untouched", "live cache unchanged",
                                "cache is untouched", "cache is unchanged"]
        no_false_claim = not any(p in joined_stripped for p in false_claim_phrases)
        print(f"[{'PASS' if no_false_claim else 'FAIL'}] public reporting (_report_refresh_result -- the "
              f"exact function refresh_sec() calls) never makes an unqualified 'untouched'/'unchanged' "
              f"claim for this outcome (a NEGATED warning like 'do NOT assume ... unchanged' is the "
              f"correct, desired behavior and is not flagged): {no_false_claim}")
        for line in captured:
            print(f"    | {line}")
        if not no_false_claim:
            overall_ok = False

        if residue:
            shutil.rmtree(os.path.join(tmp_parent, residue[0]))

        print(f"\n=== Refresh rollback-incomplete self-test: "
              f"{'ALL CHECKS PASSED' if overall_ok else 'AT LEAST ONE CHECK FAILED'} ===")
        return 0 if overall_ok else 1
    finally:
        if os.path.isdir(tmp_parent):
            shutil.rmtree(tmp_parent)
            print(f"Deleted temporary test directory: {tmp_parent} (after-test residue check: directory removed)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--rebuild-from-cache", action="store_true",
                        help="Offline rebuild of snapshots/manifests from sec_raw_cache/. No network access.")
    group.add_argument("--refresh-sec", action="store_true",
                        help="Refresh sec_raw_cache/ from SEC EDGAR. Requires SEC_USER_AGENT. NOT run during this correction pass.")
    group.add_argument("--write-cache-manifest", action="store_true",
                        help="(One-time bootstrap ONLY -- refuses once cache_manifest.json already exists.) "
                             "Write sec_raw_cache/cache_manifest.json from the cache files currently on disk, "
                             "without any network access. Once bootstrapped, only a successful staged --refresh-sec "
                             "may replace the trusted manifest.")
    group.add_argument("--self-test", action="store_true",
                        help="Run negative-control self-tests for the manifest-completeness verifier and the PII scanner. Operates on temporary copies only.")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if args.refresh_sec:
        return refresh_sec()
    if args.rebuild_from_cache:
        return rebuild_from_cache()
    if args.write_cache_manifest:
        if os.path.exists(CACHE_MANIFEST_PATH):
            print(f"REFUSING: {CACHE_MANIFEST_PATH} already exists. --write-cache-manifest is a one-time "
                  f"bootstrap operation for when no trusted manifest exists yet -- it must not be usable to "
                  f"silently re-bless an existing (possibly tampered or accidentally modified) cache. "
                  f"Bootstrap is already complete for this cache. If the cache genuinely needs to change, "
                  f"the only supported path now is a successful staged transaction: "
                  f"`python3 build_snapshots.py --refresh-sec` (requires SEC_USER_AGENT; contacts SEC EDGAR).")
            return 1
        write_cache_manifest()
        print(f"cache_manifest.json bootstrapped from existing on-disk cache files (no network access). "
              f"This is a one-time operation -- running --write-cache-manifest again will now refuse.")
        return 0
    return verify()


if __name__ == "__main__":
    sys.exit(main())
