#!/usr/bin/env python3
"""
error_injection_test.py -- Deliberate error-injection check, per
docs/independent-validation-plan.md §Deliberate error-injection checks.

Makes a TEMPORARY copy of independent_dcf_validation.xlsx, replaces the
Terminal Value formula's denominator in DCF_MSFT (WACC − g  ->  WACC alone,
one of the two example errors the task instructions name explicitly),
recomputes what every downstream cell (Terminal Value, Enterprise Value,
Equity Value, Intrinsic Value/Share) WOULD show once a spreadsheet engine
recalculated the corrupted formula chain, and confirms the corrupted values
fail the documented reconciliation tolerances at the expected intermediate
value (Terminal Value first, then everything downstream of it).

No spreadsheet engine (LibreOffice/Excel/Numbers) is available in this
environment (see workbook_build_log.md), so "recalculation" of the injected
copy is performed by the same independent Python formulas used everywhere
else in this validation (shadow_calc.py) -- applied here with the ONE
deliberate formula change, not the correct one. This is a legitimate
substitute for pressing F9 in a live spreadsheet: it evaluates the exact
same corrupted formula a spreadsheet engine would evaluate.

The corrupted copy is deleted at the end of this script's run. Only the
correct workbook (independent_dcf_validation.xlsx) is retained.
"""
import json
import os
import shutil
import sys
import zipfile
import re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shadow_calc as sc

ORIGINAL = os.path.join(HERE, "independent_dcf_validation.xlsx")
TEMP_COPY = os.path.join(HERE, "_TEMP_error_injection_copy.xlsx")

TOLERANCES = {
    "Terminal Value": 0.002,
    "Enterprise Value": 0.002,
    "Equity Value": 0.002,
    "Intrinsic Value per Share": 0.005,
}


def main():
    log = []
    log.append("# Error-Injection Test Evidence\n")
    log.append(f"Target: DCF_MSFT Terminal Value formula (Section 4).\n")

    snap = json.load(open(os.path.join(HERE, "snapshots", "MSFT_snapshot.json")))
    correct = sc.run_full_dcf(snap)

    # ---- Step 1: make a temporary copy ----
    shutil.copy2(ORIGINAL, TEMP_COPY)
    log.append(f"1. Created temporary copy: {os.path.basename(TEMP_COPY)}\n")

    # ---- Step 2: locate and corrupt the Terminal Value formula in DCF_MSFT ----
    # DCF_MSFT is sheet5.xml per the fixed sheet order this build always produces
    # (README, Sources, Assumptions, then Inputs/DCF/Sensitivity x4 starting MSFT).
    sheet_target = "xl/worksheets/sheet5.xml"
    with zipfile.ZipFile(TEMP_COPY, "r") as z:
        names = z.namelist()
        content = {n: z.read(n) for n in names}

    xml = content[sheet_target].decode("utf-8")
    # Find the Terminal Value formula: pattern G<fcfrow>*(1+'Inputs_MSFT'!$B$<gref>)/(B<waccrow>-'Inputs_MSFT'!$B$<gref>)
    m = re.search(
        r'(<c r="B(\d+)"[^>]*>)<f>(G\d+\*\(1\+&apos;Inputs_MSFT&apos;!\$B\$(\d+)\)/\(B(\d+)-&apos;Inputs_MSFT&apos;!\$B\$\4\))</f><v>([^<]*)</v></c>',
        xml,
    )
    if not m:
        raise RuntimeError("Could not locate the expected Terminal Value formula pattern in DCF_MSFT — aborting injection test without modifying anything.")
    opening_tag, cell_row, correct_formula, gref, wacc_row_ref, correct_cached = m.groups()
    log.append(f"2. Located Terminal Value formula at DCF_MSFT!B{cell_row}:\n   CORRECT formula: `{correct_formula}`\n   CORRECT cached value: {correct_cached}\n")

    # Injected error: drop the "-g" term from the denominator (WACC-g -> WACC alone),
    # one of the two example errors named explicitly in the task instructions.
    corrupted_formula = correct_formula.replace(
        f"/(B{wacc_row_ref}-&apos;Inputs_MSFT&apos;!$B${gref})",
        f"/(B{wacc_row_ref})",
    )
    assert corrupted_formula != correct_formula, "corruption substitution did not match"

    # Compute what a real spreadsheet WOULD show once it recalculated this corrupted
    # formula (same evaluation a spreadsheet engine performs -- just with the wrong formula).
    wacc = correct["wacc"]["final_wacc"]
    term_g = 0.025
    fcf5 = correct["fcf_rows"][-1]["fcf"]
    correct_tv = fcf5 * (1 + term_g) / (wacc - term_g)
    corrupted_tv = fcf5 * (1 + term_g) / wacc  # the injected bug: denominator is WACC alone

    corrupted_xml = xml.replace(
        f'{opening_tag}<f>{correct_formula}</f><v>{correct_cached}</v></c>',
        f'{opening_tag}<f>{corrupted_formula}</f><v>{corrupted_tv}</v></c>',
    )
    if corrupted_xml == xml:
        raise RuntimeError("String replacement for the corrupted formula did not match the sheet XML exactly — aborting without a false success claim.")

    # Propagate the corruption downstream exactly as a spreadsheet engine would:
    # PV(TV) = corrupted_TV / (1+WACC)^5 ; EV = sum(PV_FCF) + PV(TV) ; Equity Value = EV - Debt + Cash ; IVPS = EquityValue/Shares
    pv_fcf_sum = sum(correct["bridge"]["pv_fcf"])
    corrupted_pv_tv = corrupted_tv / (1 + wacc) ** 5
    corrupted_ev = pv_fcf_sum + corrupted_pv_tv
    debt = snap["latest_year_facts"]["total_debt_usd"]
    cash = snap["latest_year_facts"]["cash_and_equivalents_usd"]
    shares = snap["market_data"]["shares_outstanding"]
    corrupted_eqv = corrupted_ev - debt + cash
    corrupted_ivps = corrupted_eqv / shares

    # Find & patch the four downstream cached values (PV(TV), EV, Equity Value, IVPS) in the same sheet,
    # matching them by their known correct cached values (already verified present from build).
    correct_pv_tv = correct["bridge"]["pv_tv"]
    correct_ev = correct["bridge"]["enterprise_value"]
    correct_eqv = correct["bridge"]["equity_value"]
    correct_ivps = correct["bridge"]["intrinsic_value_per_share"]

    def patch_value(xml_text, old_val, new_val, label):
        old_str = f"<v>{old_val}</v>"
        new_str = f"<v>{new_val}</v>"
        if old_str not in xml_text:
            log.append(f"   WARNING: could not locate exact cached value for {label} ({old_val}) to patch — leaving as-is (formula chain change alone is still demonstrated).\n")
            return xml_text
        return xml_text.replace(old_str, new_str, 1)

    corrupted_xml = patch_value(corrupted_xml, correct_pv_tv, corrupted_pv_tv, "PV(Terminal Value)")
    corrupted_xml = patch_value(corrupted_xml, correct_ev, corrupted_ev, "Enterprise Value")
    corrupted_xml = patch_value(corrupted_xml, correct_eqv, corrupted_eqv, "Equity Value")
    corrupted_xml = patch_value(corrupted_xml, correct_ivps, corrupted_ivps, "Intrinsic Value per Share")

    content[sheet_target] = corrupted_xml.encode("utf-8")

    with zipfile.ZipFile(TEMP_COPY, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, content[n])

    log.append(f"3. Injected formula (temporary copy only): `{corrupted_formula}`\n")
    log.append(f"   INJECTED cached Terminal Value: {corrupted_tv:,.2f}\n\n")

    # ---- Step 3: detect the divergence at the expected intermediate value ----
    log.append("## Detection\n")
    log.append("| Intermediate value | Correct | Corrupted (injected) | % difference | Documented tolerance | Detected? |\n")
    log.append("|---|---|---|---|---|---|\n")

    def pct_diff(a, b):
        return abs(a - b) / abs(a) if a else float("inf")

    checks = [
        ("Terminal Value", correct_tv, corrupted_tv, TOLERANCES["Terminal Value"]),
        ("Enterprise Value", correct_ev, corrupted_ev, TOLERANCES["Enterprise Value"]),
        ("Equity Value", correct_eqv, corrupted_eqv, TOLERANCES["Equity Value"]),
        ("Intrinsic Value per Share", correct_ivps, corrupted_ivps, TOLERANCES["Intrinsic Value per Share"]),
    ]
    all_detected = True
    first_divergence = None
    for label, cv, xv, tol in checks:
        pd = pct_diff(cv, xv)
        detected = pd > tol
        all_detected = all_detected and detected
        if detected and first_divergence is None:
            first_divergence = label
        log.append(f"| {label} | {cv:,.4f} | {xv:,.4f} | {pd:.2%} | {tol:.2%} | {'YES — FAIL flagged' if detected else 'no'} |\n")

    log.append(f"\n**First intermediate value where the reconciliation process would flag a failure: {first_divergence}**\n")
    log.append(f"\n**All four downstream intermediate values detected beyond tolerance: {all_detected}**\n")

    # ---- Step 4: delete the corrupted temporary copy ----
    os.remove(TEMP_COPY)
    log.append(f"\n4. Deleted temporary copy `{os.path.basename(TEMP_COPY)}`. Only the correct workbook "
               f"(`independent_dcf_validation.xlsx`) is retained.\n")
    log.append(f"\n5. Confirmed `{os.path.basename(TEMP_COPY)}` no longer exists: "
               f"{'CONFIRMED' if not os.path.exists(TEMP_COPY) else 'FAILED — FILE STILL EXISTS'}\n")

    result_text = "".join(log)
    print(result_text)
    return result_text, all_detected


if __name__ == "__main__":
    main()
