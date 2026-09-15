"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import type { ReactNode } from "react";

import SearchBar from "@/components/SearchBar";
import { DEFAULT_OVERVIEW_PATH } from "@/lib/default-route";
import { isPublicRoute } from "@/lib/public-route";
import styles from "./AppHeader.module.css";

interface NavItem {
  href: string;
  label: string;
  activePrefix?: string;
  icon: "overview" | "valuation" | "portfolio" | "backtests" | "trades";
}

const NAV_ITEMS: NavItem[] = [
  { href: DEFAULT_OVERVIEW_PATH, label: "Overview", activePrefix: "/overview", icon: "overview" },
  { href: "/workspace", label: "Valuation", icon: "valuation" },
  { href: "/portfolio", label: "Portfolio", icon: "portfolio" },
  { href: "/backtests", label: "Backtests", icon: "backtests" },
  { href: "/trades", label: "Trades", icon: "trades" },
];

function NavIcon({ name }: { name: NavItem["icon"] }) {
  const paths: Record<NavItem["icon"], ReactNode> = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    valuation: <><path d="M4 19V9"/><path d="M10 19V5"/><path d="M16 19v-7"/><path d="M22 19H2"/></>,
    portfolio: <><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M3 12h18"/></>,
    backtests: <><path d="M4 19V5"/><path d="M4 19h16"/><path d="m7 15 4-4 3 2 5-6"/></>,
    trades: <><path d="M7 7h12l-3-3"/><path d="m19 7-3 3"/><path d="M17 17H5l3 3"/><path d="m5 17 3-3"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function NavLink({ item, pathname }: { item: NavItem; pathname: string }) {
  const prefix = item.activePrefix ?? item.href;
  const active = pathname === prefix || pathname.startsWith(`${prefix}/`);
  return (
    <Link href={item.href} className={styles.navLink} aria-current={active ? "page" : undefined}>
      <NavIcon name={item.icon} />
      <span>{item.label}</span>
    </Link>
  );
}

export default function AppHeader() {
  const pathname = usePathname();
  if (pathname === "/login" || isPublicRoute(pathname)) return null;

  return (
    <div className={styles.chrome} data-app-chrome>
      <aside className={styles.sidebar}>
        <Link href={DEFAULT_OVERVIEW_PATH} className={styles.brand} aria-label="Valuation Engine overview home">
          <span className={styles.brandMark}>V</span>
          <span><strong>Valuation Engine</strong><small>Equity research</small></span>
        </Link>

        <p className={styles.navLabel}>Research</p>
        <nav className={styles.nav} aria-label="Primary navigation">
          {NAV_ITEMS.map((item) => <NavLink key={item.href} item={item} pathname={pathname} />)}
        </nav>

        <div className={styles.sidebarFooter}>
          <div className={styles.systemStatus}>
            <span aria-hidden="true" />
            <div><strong>Paper environment</strong><small>Research data connected</small></div>
          </div>
          <form action="/api/logout" method="post">
            <button type="submit" className={styles.signOut}>Sign out</button>
          </form>
        </div>
      </aside>

      <header className={styles.topbar}>
        <div className={styles.mobileBrand}><span className={styles.brandMark}>V</span><strong>Valuation Engine</strong></div>
        <SearchBar className={styles.search} />
        <div className={styles.topMeta}><span className={styles.liveDot} />Live research</div>
      </header>

      <nav className={styles.mobileNav} aria-label="Mobile navigation">
        {NAV_ITEMS.map((item) => <NavLink key={item.href} item={item} pathname={pathname} />)}
      </nav>
    </div>
  );
}
