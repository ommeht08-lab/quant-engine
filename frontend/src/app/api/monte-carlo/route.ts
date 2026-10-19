import { proxyMonteCarlo } from "./proxy";

// Allowlist, configuration and disclosures. Never cached at this layer.
export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function GET(request: Request) {
  return proxyMonteCarlo(request, { kind: "describe" }, "light");
}
