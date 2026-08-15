// Baseline requirements shared by every high-value credential in this
// app (VALUATION_API_TOKEN, SESSION_SECRET, DASHBOARD_PASSWORD): must
// be set, must not still be the literal example placeholder shipped in
// the .env.example files (those are public — checked into git — and
// therefore not a secret at all), must not be whitespace-only or
// surrounded by whitespace, must not be a single character repeated to
// meet the length requirement, and must meet a minimum length.
//
// This is FORMAT, PLACEHOLDER, and MINIMUM-LENGTH validation — nothing
// more. It does NOT measure, estimate, or claim anything about the
// actual entropy/strength of a value that passes: a string that isn't
// the literal example placeholder, isn't all one repeated character,
// and clears the length bar could still be a weak or guessable value
// (a dictionary word padded to length, a keyboard-walk pattern, etc.).
// These checks catch the specific classes of obviously-not-a-real-secret
// input identified by review — copy-pasted placeholders, empty/blank
// env vars, and the trivial "aaaaaaaa..." case — not a general password-
// strength assessment.

// Every literal placeholder value shipped in .env.example /
// frontend/.env.local.example, across every credential this module
// validates. A deployment that copied an example file verbatim without
// replacing a value must fail validation for that value exactly like an
// unset one.
const KNOWN_PLACEHOLDER_SECRET_VALUES = new Set([
  "replace-with-a-long-random-value", // SESSION_SECRET / VALUATION_API_TOKEN
  "choose-a-passphrase", // DASHBOARD_PASSWORD
]);

export interface SecretRequirement {
  /** The env var's name — used only in error messages, never the value itself. */
  name: string;
  minLength: number;
  /** Optional short setup pointer appended to the error message (never the value). */
  hint?: string;
}

// Kept in exact sync with src/api/main.py's VALUATION_API_TOKEN_MIN_LENGTH /
// _KNOWN_PLACEHOLDER_TOKEN_VALUES — both sides validate the SAME token
// against the SAME requirements. If either changes, change both.
export const VALUATION_API_TOKEN_REQUIREMENT: SecretRequirement = {
  name: "VALUATION_API_TOKEN",
  minLength: 32,
};

export const SESSION_SECRET_REQUIREMENT: SecretRequirement = {
  name: "SESSION_SECRET",
  minLength: 32,
  hint: "Generate one with `openssl rand -base64 32` and add it to frontend/.env.local.",
};

// A human passphrase doesn't need cryptographic-secret-grade randomness
// the way a generated token/key does, but it must not be empty, the
// example placeholder, or trivially short. 12 characters is a floor for
// a memorized shared passphrase (not a generated secret) — still not an
// entropy measurement, just a minimum-length gate.
export const DASHBOARD_PASSWORD_REQUIREMENT: SecretRequirement = {
  name: "DASHBOARD_PASSWORD",
  minLength: 12,
  hint: "Choose a real passphrase and add it to frontend/.env.local.",
};

/**
 * Validate a secret/credential value against `requirement`. Throws a
 * generic `Error` — the message names the ENV VAR and the violated
 * requirement, NEVER the actual configured or presented value — on any
 * violation: unset; whitespace-only; surrounded by leading/trailing
 * whitespace; the known example placeholder; a single character
 * repeated to meet the length requirement (e.g. "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
 * or shorter than the required minimum length.
 *
 * See this module's top-of-file comment: these are FORMAT/placeholder/
 * length checks, not an entropy or strength measurement.
 */
export function assertSecretMeetsRequirements(
  value: string | undefined,
  requirement: SecretRequirement
): asserts value is string {
  const hintSuffix = requirement.hint ? ` ${requirement.hint}` : "";

  if (!value || value.length === 0) {
    throw new Error(`${requirement.name} is not set.${hintSuffix}`);
  }
  if (value.trim().length === 0) {
    throw new Error(`${requirement.name} must not be empty or whitespace-only.${hintSuffix}`);
  }
  if (value !== value.trim()) {
    throw new Error(`${requirement.name} must not have leading or trailing whitespace.${hintSuffix}`);
  }
  if (KNOWN_PLACEHOLDER_SECRET_VALUES.has(value)) {
    throw new Error(`${requirement.name} is still set to its example placeholder value.${hintSuffix}`);
  }
  if (new Set(value).size === 1) {
    throw new Error(`${requirement.name} must not be a single character repeated.${hintSuffix}`);
  }
  if (value.length < requirement.minLength) {
    throw new Error(
      `${requirement.name} does not meet the minimum length requirement ` +
        `(${requirement.minLength} characters).${hintSuffix}`
    );
  }
}
