/** Password-free page shells are opt-in, development-only, and loopback-only.
 * This never authorizes private-data handlers; they retain session checks.
 */
export function isPasswordlessLocalRequest(
  host: string | null,
  env: { readonly NODE_ENV?: string; readonly LOCAL_PASSWORDLESS?: string } = process.env,
): boolean {
  return env.NODE_ENV === "development"
    && env.LOCAL_PASSWORDLESS === "1"
    && host !== null
    && /^(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]+)?$/i.test(host);
}
