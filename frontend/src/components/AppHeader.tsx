"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import SearchBar from "@/components/SearchBar";

const NAV_ITEMS = [
  { href: "/", label: "Valuation" },
  { href: "/portfolio", label: "Portfolio" },
  { href: "/backtests", label: "Backtests" },
  { href: "/trades", label: "Trades" },
];

function NavLink({ href, label }: { href: string; label: string }) {
  const pathname = usePathname();
  const active = href === "/" ? pathname === href : pathname.startsWith(href);

  return (
    <Link href={href} className="nav-link" aria-current={active ? "page" : undefined}>
      {label}
    </Link>
  );
}

export default function AppHeader() {
  const pathname = usePathname();
  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);

  if (pathname === "/login") return null;

  return (
    <header className="app-header">
      <div className="shell-container flex flex-wrap items-center gap-x-4 gap-y-2 py-2.5">
        <Link href="/" className="brand-lockup" aria-label="Valuation Engine home">
          <span className="brand-mark" aria-hidden="true">OM</span>
          <span>
            <strong>Valuation Engine</strong>
            <small>Equity research workspace</small>
          </span>
        </Link>

        <nav
          className="command-nav order-3 w-full overflow-x-auto md:order-none md:w-auto"
          aria-label="Primary navigation"
        >
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.href} href={item.href} label={item.label} />
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <div className="hidden sm:block">
            <SearchBar />
          </div>
          <button
            type="button"
            className="search-toggle inline-flex sm:hidden"
            aria-expanded={mobileSearchOpen}
            aria-controls="mobile-tear-sheet-search"
            onClick={() => setMobileSearchOpen((open) => !open)}
          >
            <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" className="h-4 w-4">
              <circle cx="8.5" cy="8.5" r="6" stroke="currentColor" strokeWidth="1.5" />
              <path d="M13 13L17.5 17.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            <span className="sr-only">Open tear sheet</span>
          </button>
          <form action="/api/logout" method="post">
            <button type="submit" className="sign-out-button">
              Sign out
            </button>
          </form>
        </div>
      </div>

      {mobileSearchOpen && (
        <div id="mobile-tear-sheet-search" className="border-t border-[var(--line)] py-2.5 sm:hidden">
          <div className="shell-container">
            <SearchBar className="relative w-full" />
          </div>
        </div>
      )}
    </header>
  );
}
