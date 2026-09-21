import { redirect } from "next/navigation";

import { DEFAULT_APP_PATH } from "@/lib/default-route";

// Preserve old bookmarks without keeping a redundant landing screen.
export default function OverviewIndexPage() {
  redirect(DEFAULT_APP_PATH);
}
