"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { usePathname } from "next/navigation";

import { isUncertaintyPath, shouldMountWorkspace } from "@/lib/workspace-route";

// The valuation workspace (charts, valuation tables, the evaluate request) is a separate chunk, loaded only
// by pages that use it. The uncertainty page renders its own shell and never needs it.
const WorkspaceClient = dynamic(() => import("./WorkspaceClient"), {
  loading: () => <main className="page-shell"><p>Loading workspace…</p></main>,
});

export default function WorkspaceShell({ initialTicker }: { initialTicker: string }) {
  const pathname = usePathname();
  const [mounted, setMounted] = useState(false);
  // Remember that the workspace has been shown so it is never unmounted again (its explicit run and inputs persist).
  // Updating state while rendering, guarded so it happens once, avoids a render with the wrong tree.
  if (!mounted && !isUncertaintyPath(pathname)) setMounted(true);
  return shouldMountWorkspace(pathname, mounted) ? <WorkspaceClient initialTicker={initialTicker} /> : null;
}
