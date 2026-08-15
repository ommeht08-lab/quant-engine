// Security regression tests for `safeHeadlineHref` (frontend/src/lib/headline-links.ts),
// which gates every externally supplied headline URL rendered as an
// `<a href>` on the ticker tear-sheet page. Run with Node's built-in
// test runner:
//
//   node --test src/lib/headline-links.test.ts

import assert from "node:assert/strict";
import { test } from "node:test";

import { safeHeadlineHref } from "./headline-links.ts";

test("accepts an https link unchanged", () => {
  assert.equal(safeHeadlineHref("https://finance.yahoo.com/news/some-article"), "https://finance.yahoo.com/news/some-article");
});

test("accepts an http link unchanged", () => {
  assert.equal(safeHeadlineHref("http://example.com/article"), "http://example.com/article");
});

test("rejects a javascript: URL", () => {
  assert.equal(safeHeadlineHref("javascript:alert(document.cookie)"), null);
});

test("rejects a data: URL", () => {
  assert.equal(safeHeadlineHref("data:text/html,<script>alert(1)</script>"), null);
});

test("rejects a vbscript: URL", () => {
  assert.equal(safeHeadlineHref("vbscript:msgbox(1)"), null);
});

test("rejects a file: URL", () => {
  assert.equal(safeHeadlineHref("file:///etc/passwd"), null);
});

test("returns null for a missing/empty link", () => {
  assert.equal(safeHeadlineHref(null), null);
  assert.equal(safeHeadlineHref(undefined), null);
  assert.equal(safeHeadlineHref(""), null);
});

test("returns null for a malformed URL", () => {
  assert.equal(safeHeadlineHref("not a url at all"), null);
});
