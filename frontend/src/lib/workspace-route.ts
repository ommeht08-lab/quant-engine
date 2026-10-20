/** The uncertainty page renders its own shell and needs none of the valuation workspace's code. */
export function isUncertaintyPath(pathname: string | null | undefined): boolean {
  return typeof pathname === "string" && /^\/workspace\/uncertainty(\/|$)/.test(pathname);
}

/**
 * Whether the persistent valuation workspace should be mounted. It is skipped on a first visit to the
 * uncertainty page (so its charts and code are never downloaded there) and, once mounted by any other
 * workspace page, stays mounted so an explicit run and its inputs survive navigation exactly as before.
 */
export function shouldMountWorkspace(pathname: string | null | undefined, alreadyMounted: boolean): boolean {
  return alreadyMounted || !isUncertaintyPath(pathname);
}
