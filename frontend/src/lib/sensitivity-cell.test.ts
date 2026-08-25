import assert from "node:assert/strict";
import test from "node:test";

import { classifySensitivityCell, sensitivityCellAccessibleLabel } from "./sensitivity-cell.ts";

const formatCurrency = (value: number) => `$${value.toFixed(2)}`;

test("classifySensitivityCell: value above market price is 'above'", () => {
  assert.equal(classifySensitivityCell({ value: 300, marketPrice: 200 }), "above");
});

test("classifySensitivityCell: value below market price is 'below'", () => {
  assert.equal(classifySensitivityCell({ value: 150, marketPrice: 200 }), "below");
});

test("classifySensitivityCell: value equal to market price is 'equal'", () => {
  assert.equal(classifySensitivityCell({ value: 200, marketPrice: 200 }), "equal");
});

test("classifySensitivityCell: null value is 'invalid' regardless of market price", () => {
  assert.equal(classifySensitivityCell({ value: null, marketPrice: 200 }), "invalid");
  assert.equal(classifySensitivityCell({ value: null, marketPrice: null }), "invalid");
});

test("classifySensitivityCell: null market price is 'comparison-unavailable' when value exists", () => {
  assert.equal(classifySensitivityCell({ value: 150, marketPrice: null }), "comparison-unavailable");
});

test("classifySensitivityCell: a negative value still classifies normally against a positive market price", () => {
  assert.equal(classifySensitivityCell({ value: -50, marketPrice: 200 }), "below");
});

test("classifySensitivityCell: a negative value above a (also negative) market price is 'above'", () => {
  assert.equal(classifySensitivityCell({ value: -10, marketPrice: -50 }), "above");
});

test("sensitivityCellAccessibleLabel: above-market cell includes value, status, and no baseline mention", () => {
  const label = sensitivityCellAccessibleLabel({
    value: 300,
    marketPrice: 200,
    isBaseline: false,
    formatCurrency,
  });
  assert.equal(label, "$300.00, above current market price");
});

test("sensitivityCellAccessibleLabel: below-market cell reads correctly", () => {
  const label = sensitivityCellAccessibleLabel({
    value: 150,
    marketPrice: 200,
    isBaseline: false,
    formatCurrency,
  });
  assert.equal(label, "$150.00, below current market price");
});

test("sensitivityCellAccessibleLabel: equal-to-market cell reads correctly", () => {
  const label = sensitivityCellAccessibleLabel({
    value: 200,
    marketPrice: 200,
    isBaseline: false,
    formatCurrency,
  });
  assert.equal(label, "$200.00, equal to current market price");
});

test("sensitivityCellAccessibleLabel: null (invalid) cell never mentions a value or market status", () => {
  const label = sensitivityCellAccessibleLabel({
    value: null,
    marketPrice: 200,
    isBaseline: false,
    formatCurrency,
  });
  assert.equal(label, "Not a valid WACC and terminal growth combination");
});

test("sensitivityCellAccessibleLabel: null market price reports the value but flags comparison as unavailable", () => {
  const label = sensitivityCellAccessibleLabel({
    value: 150,
    marketPrice: null,
    isBaseline: false,
    formatCurrency,
  });
  assert.equal(label, "$150.00, market price unavailable for comparison");
});

test("sensitivityCellAccessibleLabel: a negative value is reported plainly, not hidden", () => {
  const label = sensitivityCellAccessibleLabel({
    value: -25.5,
    marketPrice: 200,
    isBaseline: false,
    formatCurrency,
  });
  assert.equal(label, "$-25.50, below current market price");
});

test("sensitivityCellAccessibleLabel: baseline case is appended regardless of value/market state", () => {
  assert.equal(
    sensitivityCellAccessibleLabel({ value: 300, marketPrice: 200, isBaseline: true, formatCurrency }),
    "$300.00, above current market price, baseline case"
  );
  assert.equal(
    sensitivityCellAccessibleLabel({ value: null, marketPrice: 200, isBaseline: true, formatCurrency }),
    "Not a valid WACC and terminal growth combination, baseline case"
  );
  assert.equal(
    sensitivityCellAccessibleLabel({ value: 150, marketPrice: null, isBaseline: true, formatCurrency }),
    "$150.00, market price unavailable for comparison, baseline case"
  );
});
