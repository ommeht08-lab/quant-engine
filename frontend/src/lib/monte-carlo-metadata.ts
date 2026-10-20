import { MONTE_CARLO_STATIC_METADATA } from "../generated/monte-carlo-metadata.generated.ts";
import { isMonteCarloDescription, type MonteCarloDescription, type MonteCarloResponse } from "./monte-carlo-types.ts";

/**
 * The simulation's company list, default seed, configuration summary and wording, generated from the backend
 * code (scripts/generate_monte_carlo_metadata.py) and bundled with the page. The controls render from it
 * without calling the valuation backend, which can take seconds to start. The backend still enforces the
 * allowlist and validates every request; it also reports the identity of its own settings with each result,
 * and `settingsMismatch` compares that with this copy.
 */
function load(value: unknown): MonteCarloDescription {
  if (!isMonteCarloDescription(value)) throw new Error("The bundled simulation metadata is malformed; regenerate it.");
  return value;
}

export const MONTE_CARLO_METADATA: MonteCarloDescription = load(MONTE_CARLO_STATIC_METADATA);

export interface SettingsMismatch {
  differences: string[];
  message: string;
}

/** Null when the service ran with the settings this page shows; otherwise what differs, in plain words. */
export function settingsMismatch(
  metadata: MonteCarloDescription,
  result: Pick<MonteCarloResponse, "model_version" | "config_hash" | "meta">,
): SettingsMismatch | null {
  const differences: string[] = [];
  if (result.model_version !== metadata.model_version) differences.push("model version");
  if (result.config_hash !== metadata.config_hash) differences.push("simulation configuration");
  if (result.meta.metadata_id === undefined) differences.push("settings identity (not reported by the service)");
  else if (result.meta.metadata_id !== metadata.metadata_id) differences.push("company list or wording");
  if (differences.length === 0) return null;
  return {
    differences,
    message:
      `The simulation service is running different settings than this page was built with (${differences.join(", ")}). ` +
      "The results below are the service's own, but the company list and draw count shown above may be out of date. " +
      "Reload the page; if this persists, the page and the service were deployed from different versions.",
  };
}
