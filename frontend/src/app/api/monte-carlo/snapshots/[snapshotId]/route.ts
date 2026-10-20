import { NextResponse } from "next/server";

import { isValidSnapshotId } from "@/lib/monte-carlo-proxy";
import { proxyMonteCarlo } from "../../proxy";

// Replay-bundle export: the frozen snapshot plus the seed, configuration and
// expected result digest needed to rerun an experiment exactly. The backend
// service token never leaves the server; the browser receives only the bundle.
export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function GET(request: Request, { params }: { params: Promise<{ snapshotId: string }> }) {
  const { snapshotId } = await params;
  if (!isValidSnapshotId(snapshotId)) {
    return NextResponse.json(
      { code: "MONTE_CARLO_INVALID_REQUEST", error: "That is not a valid snapshot identifier." },
      { status: 400, headers: { "Cache-Control": "private, no-store" } },
    );
  }
  return proxyMonteCarlo(request, { kind: "export", snapshotId }, "light");
}
