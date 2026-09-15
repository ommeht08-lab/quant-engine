/**
 * Routes that own their complete page chrome through `ResearchShell`.
 * Keep the segment boundary explicit so a lookalike such as
 * `/overview-old` does not accidentally lose the normal app header.
 */
export function usesStandaloneResearchShell(pathname: string): boolean {
  return pathname === "/overview" || pathname.startsWith("/overview/");
}
