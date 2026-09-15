import { redirect } from "next/navigation";

import { DEFAULT_OVERVIEW_PATH } from "@/lib/default-route";

// `/` is intercepted by `src/proxy.ts`'s own unconditional redirect
// (`publicRedirectPath`) before this component would ever render for a
// real request — this is defense in depth for that same target, mirroring
// `overview/page.tsx`'s own bare-index redirect, for any path that
// somehow reaches Next's router without going through proxy.ts first
// (e.g. a future middleware matcher change).
//
// The actual valuation workflow lives at `/workspace`
// (`frontend/src/app/workspace/`), not here — see that route's own
// comment for why the two used to be the same file.
export default function RootPage() {
  redirect(DEFAULT_OVERVIEW_PATH);
}
