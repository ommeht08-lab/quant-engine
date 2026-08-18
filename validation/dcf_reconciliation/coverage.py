"""
Single source of truth for the sensitivity-table SHAPE (which output metrics
each table produces per scenario row) and the resulting scalar-comparison
counts (Finding 3: "Cells" was mislabeled -- scenario-row count and actual
scalar-comparison count are distinct numbers and must never be conflated).
Shared by report.py and workbook_builder.py so the two narratives can never
drift out of sync with each other.
"""

# (label, results-dict key, output-metric keys in column order). Table 5 is a
# flat 36-cell grid -- each grid cell IS its own scenario row, with exactly
# one output (Intrinsic Value per Share).
SENSITIVITY_TABLE_SHAPE = [
    ("Table 1 -- WACC", "table1_wacc",
     ["terminal_value", "enterprise_value", "equity_value", "intrinsic_value_per_share"]),
    ("Table 2 -- Terminal growth", "table2_terminal_growth",
     ["terminal_value", "enterprise_value", "equity_value", "intrinsic_value_per_share"]),
    ("Table 3 -- Revenue growth", "table3_revenue_growth",
     ["fcf_1", "fcf_2", "fcf_3", "fcf_4", "fcf_5", "terminal_value", "enterprise_value", "equity_value", "intrinsic_value_per_share"]),
    ("Table 4 -- Operating margin", "table4_operating_margin",
     ["fcf_1", "fcf_2", "fcf_3", "fcf_4", "fcf_5", "terminal_value", "enterprise_value", "equity_value", "intrinsic_value_per_share"]),
    ("Table 5 -- WACC x g two-way grid", "table5_two_way", ["intrinsic_value_per_share"]),
]

OUTPUT_METRIC_LABELS = {
    "terminal_value": "Terminal Value", "enterprise_value": "Enterprise Value",
    "equity_value": "Equity Value", "intrinsic_value_per_share": "Intrinsic Value per Share",
    "fcf_1": "FCF Year 1", "fcf_2": "FCF Year 2", "fcf_3": "FCF Year 3",
    "fcf_4": "FCF Year 4", "fcf_5": "FCF Year 5",
}
OUTPUT_METRIC_TOL_KEY = {
    "terminal_value": "terminal_value", "enterprise_value": "enterprise_value",
    "equity_value": "equity_value", "intrinsic_value_per_share": "sensitivity_ivps",
    "fcf_1": "fcf", "fcf_2": "fcf", "fcf_3": "fcf", "fcf_4": "fcf", "fcf_5": "fcf",
}
OUTPUT_METRIC_VALUE_KIND = {
    "terminal_value": "dollar", "enterprise_value": "dollar", "equity_value": "dollar",
    "intrinsic_value_per_share": "per_share",
    "fcf_1": "dollar", "fcf_2": "dollar", "fcf_3": "dollar", "fcf_4": "dollar", "fcf_5": "dollar",
}

EXPECTED_SCALAR_COMPARISONS_PER_COMPANY = 227  # 28 + 28 + 72 + 63 + 36
EXPECTED_SCALAR_COMPARISONS_TOTAL = 908  # 227 x 4 companies


def sensitivity_coverage_rows(sens: dict):
    """Returns [(table_label, scenario_rows, outputs_per_row, scalar_comparisons, all_passed), ...]
    for one company's sensitivity-reconciliation dict."""
    out = []
    for label, key, output_keys in SENSITIVITY_TABLE_SHAPE:
        t = sens[key]
        scenario_rows = t["cells_examined"] if key == "table5_two_way" else len(t["rows"])
        outputs_per_row = len(output_keys)
        out.append((label, scenario_rows, outputs_per_row, scenario_rows * outputs_per_row, t["all_passed"]))
    return out


def scenario_label(table_key: str, row) -> str:
    if table_key == "table1_wacc":
        return f"WACC={row['wacc']:.4%}"
    if table_key == "table2_terminal_growth":
        return f"g={row['terminal_growth']:.4%}"
    if table_key == "table3_revenue_growth":
        return f"RevGrowth={row['revenue_growth']:.4%}"
    if table_key == "table4_operating_margin":
        return f"Margin={row['operating_margin']:.4%}"
    if table_key == "table5_two_way":
        return f"WACC={row['wacc']:.4%}, g={row['terminal_growth']:.4%}"
    raise ValueError(table_key)


def iter_all_scalar_comparisons(sens: dict):
    """Yields (table_label, table_key, scenario_label, output_key, comparison_dict)
    for every one of a company's 227 scalar comparisons, in a fixed, deterministic order."""
    for table_label, table_key, output_keys in SENSITIVITY_TABLE_SHAPE:
        t = sens[table_key]
        data_rows = t["grid"] if table_key == "table5_two_way" else t["rows"]
        for data_row in data_rows:
            label = scenario_label(table_key, data_row)
            for out_key in output_keys:
                if table_key == "table5_two_way":
                    cell = data_row
                elif out_key.startswith("fcf_"):
                    idx = int(out_key.split("_")[1]) - 1
                    cell = data_row["fcf"][idx]
                else:
                    cell = data_row[out_key]
                yield table_label, table_key, label, out_key, cell
