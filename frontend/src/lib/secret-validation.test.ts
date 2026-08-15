// Regression tests for `assertSecretMeetsRequirements`
// (frontend/src/lib/secret-validation.ts), which gates
// VALUATION_API_TOKEN, SESSION_SECRET, and DASHBOARD_PASSWORD. Run with
// Node's built-in test runner:
//
//   node --test src/lib/secret-validation.test.ts

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  assertSecretMeetsRequirements,
  DASHBOARD_PASSWORD_REQUIREMENT,
  SESSION_SECRET_REQUIREMENT,
  VALUATION_API_TOKEN_REQUIREMENT,
  type SecretRequirement,
} from "./secret-validation.ts";

const GENERIC_REQUIREMENT: SecretRequirement = { name: "TEST_SECRET", minLength: 16 };

test("accepts a sufficiently long, non-placeholder value", () => {
  assert.doesNotThrow(() => assertSecretMeetsRequirements("a-genuinely-random-value-1234567890", GENERIC_REQUIREMENT));
});

test("rejects an empty value", () => {
  assert.throws(() => assertSecretMeetsRequirements("", GENERIC_REQUIREMENT), /not set/);
});

test("rejects an undefined value", () => {
  assert.throws(() => assertSecretMeetsRequirements(undefined, GENERIC_REQUIREMENT), /not set/);
});

test("rejects a too-short value", () => {
  assert.throws(() => assertSecretMeetsRequirements("short", GENERIC_REQUIREMENT), /minimum length/);
});

test("rejects the SESSION_SECRET/VALUATION_API_TOKEN example placeholder", () => {
  assert.throws(
    () => assertSecretMeetsRequirements("replace-with-a-long-random-value", SESSION_SECRET_REQUIREMENT),
    /placeholder/
  );
  assert.throws(
    () => assertSecretMeetsRequirements("replace-with-a-long-random-value", VALUATION_API_TOKEN_REQUIREMENT),
    /placeholder/
  );
});

test("rejects the DASHBOARD_PASSWORD example placeholder", () => {
  assert.throws(
    () => assertSecretMeetsRequirements("choose-a-passphrase", DASHBOARD_PASSWORD_REQUIREMENT),
    /placeholder/
  );
});

test("error messages never include the rejected value itself", () => {
  const secretValue = "super-secret-value-that-must-never-appear-in-an-error-message";
  try {
    assertSecretMeetsRequirements(secretValue, { name: "TEST_SECRET", minLength: 9999 });
    assert.fail("expected assertSecretMeetsRequirements to throw");
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    assert.ok(!message.includes(secretValue), "error message must not contain the secret value");
  }
});

test("SESSION_SECRET and VALUATION_API_TOKEN both require at least 32 characters", () => {
  assert.equal(SESSION_SECRET_REQUIREMENT.minLength, 32);
  assert.equal(VALUATION_API_TOKEN_REQUIREMENT.minLength, 32);
});

test("a 31-character value fails the 32-character requirement, a 32-character value passes", () => {
  // Not a single repeated character — this is testing the LENGTH
  // boundary specifically, not the repeated-character rejection.
  const thirtyTwo = "ab12cd34".repeat(4);
  assert.equal(thirtyTwo.length, 32);
  const thirtyOne = thirtyTwo.slice(0, 31);
  assert.throws(() => assertSecretMeetsRequirements(thirtyOne, VALUATION_API_TOKEN_REQUIREMENT));
  assert.doesNotThrow(() => assertSecretMeetsRequirements(thirtyTwo, VALUATION_API_TOKEN_REQUIREMENT));
});

// -- hardening: whitespace, repeated characters, DASHBOARD_PASSWORD's raised minimum --

test("rejects a value with leading whitespace", () => {
  const padded = "  " + "ab12cd34".repeat(4);
  assert.throws(() => assertSecretMeetsRequirements(padded, VALUATION_API_TOKEN_REQUIREMENT));
});

test("rejects a value with trailing whitespace", () => {
  const padded = "ab12cd34".repeat(4) + "  ";
  assert.throws(() => assertSecretMeetsRequirements(padded, VALUATION_API_TOKEN_REQUIREMENT));
});

test("rejects a whitespace-only value even when it meets the length requirement", () => {
  const spaces = " ".repeat(40);
  assert.throws(() => assertSecretMeetsRequirements(spaces, VALUATION_API_TOKEN_REQUIREMENT));
});

test("rejects a value that is a single character repeated to meet the length requirement", () => {
  const repeated = "a".repeat(40);
  assert.throws(
    () => assertSecretMeetsRequirements(repeated, VALUATION_API_TOKEN_REQUIREMENT),
    /repeated/
  );
});

test("rejects a repeated-digit value too, not just repeated letters", () => {
  const repeated = "0".repeat(40);
  assert.throws(() => assertSecretMeetsRequirements(repeated, VALUATION_API_TOKEN_REQUIREMENT));
});

test("rejects a placeholder value even when surrounded by whitespace (trimmed placeholder)", () => {
  const padded = "   replace-with-a-long-random-value   ";
  assert.throws(() => assertSecretMeetsRequirements(padded, VALUATION_API_TOKEN_REQUIREMENT));
  const paddedPassword = "  choose-a-passphrase  ";
  assert.throws(() => assertSecretMeetsRequirements(paddedPassword, DASHBOARD_PASSWORD_REQUIREMENT));
});

test("DASHBOARD_PASSWORD now requires at least 12 characters (raised from 8)", () => {
  assert.equal(DASHBOARD_PASSWORD_REQUIREMENT.minLength, 12);
});

test("an 11-character passphrase is rejected, a 12-character one is accepted", () => {
  const elevenChars = "correct-hrs"; // 11 chars, not a placeholder, not repeated
  assert.equal(elevenChars.length, 11);
  const twelveChars = "correct-hrsx"; // 12 chars
  assert.equal(twelveChars.length, 12);
  assert.throws(() => assertSecretMeetsRequirements(elevenChars, DASHBOARD_PASSWORD_REQUIREMENT));
  assert.doesNotThrow(() => assertSecretMeetsRequirements(twelveChars, DASHBOARD_PASSWORD_REQUIREMENT));
});

test("accepts a realistic, valid, generated-looking token/secret value", () => {
  // Shaped like a real `openssl rand -hex 32` / `openssl rand -base64 32` output.
  const hexToken = "3f9a7c2e1b8d4f605a9c3e7b1d8f4a6c9e2b7d1f0a3c5e8b1d4f7a0c3e6b9d2f";
  const base64ishSecret = "K7mQ9x2Lp4vR8sT1nW6yB3zA5cD0eF9gH2jK4mN7pQ1rS==";
  const realisticPassphrase = "correct-horse-battery-staple-42";
  assert.doesNotThrow(() => assertSecretMeetsRequirements(hexToken, VALUATION_API_TOKEN_REQUIREMENT));
  assert.doesNotThrow(() => assertSecretMeetsRequirements(base64ishSecret, SESSION_SECRET_REQUIREMENT));
  assert.doesNotThrow(() => assertSecretMeetsRequirements(realisticPassphrase, DASHBOARD_PASSWORD_REQUIREMENT));
});
