"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import SearchBar from "@/components/SearchBar";

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

  if (pathname === "/login") return null;

  return (
    <header className="app-header">
      <div className="shell-container flex min-h-16 flex-wrap items-center gap-x-6 gap-y-3 py-3">
        <Link href="/" className="brand-lockup" aria-label="Valuation Engine home">
          <span className="brand-mark" aria-hidden="true">OM</span>
          <span>
            <strong>Valuation Engine</strong>
            <small>Equity research workspace</small>
          </span>
        </Link>

        <nav className="order-3 flex w-full items-center gap-1 border-t border-[var(--line)] pt-3 md:order-none md:w-auto md:border-0 md:pt-0" aria-label="Primary navigation">
          <NavLink href="/" label="Research" />
          <NavLink href="/trades" label="Trade log" />
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <SearchBar />
          <form action="/api/logout" method="post">
            <button type="submit" className="sign-out-button">Sign out</button>
          </form>
        </div>
      </div>

      <div className="model-tape" aria-label="Workspace status">
        <div className="shell-container flex flex-wrap items-center gap-x-6 gap-y-1 py-2">
          <span><i className="status-dot" /> Paper account</span>
          <span>DCF mode <b>Historical baseline</b></span>
          <span>Execution <b>Paper only</b></span>
          <span className="md:ml-auto">Private operator session</span>
        </div>
      </div>
    </header>
  );
}
