import { redirect } from "next/navigation";

import { DEFAULT_APP_PATH } from "@/lib/default-route";

// `proxy.ts` normally handles this redirect first. Keeping the route
// redirect here provides the same destination if the matcher changes.
export default function RootPage() {
  redirect(DEFAULT_APP_PATH);
}
