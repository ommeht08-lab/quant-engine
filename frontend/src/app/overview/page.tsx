import { redirect } from "next/navigation";

import { DEFAULT_OVERVIEW_PATH } from "@/lib/default-route";

// A bare `/overview` visit (typed directly, or a future link that
// forgets a ticker) resolves cleanly to the shared default rather than
// 404ing. Session protection is unaffected — this route isn't listed
// in PUBLIC_ROUTE_PREFIXES, so proxy.ts's default-deny still applies
// before this component ever renders.
export default function OverviewIndexPage() {
  redirect(DEFAULT_OVERVIEW_PATH);
}
