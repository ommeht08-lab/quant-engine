"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useState } from "react";

import styles from "./FlagshipResearchPrototype.module.css";

/**
 * The sidebar/utility-bar/main/footer chrome shared by the research
 * overview experience — extracted from `FlagshipResearchPrototype.tsx`
 * (which still owns the AAPL fixture's five-view workflow content and
 * its audit drawer) so `/overview/[ticker]`'s live overview can render
 * inside the EXACT SAME visual system rather than a second, parallel
 * one. Imports the same CSS module `FlagshipResearchPrototype.tsx`
 * does — genuinely the same compiled classes, not a lookalike copy.
 *
 * Deliberately layout-only: no fixture-specific or live-specific
 * assumption lives here. Everything that varies between the fixture's
 * five-view workflow and the live overview's smaller nav is a prop.
 */

export interface ResearchNavItem {
  id: string;
  href: string;
  label: string;
  description: string;
  icon: ReactNode;
  active: boolean;
}

export interface ResearchShellProps {
  navItems: ResearchNavItem[];
  sidebarStatusLabel: string;
  sidebarStatusDetail: string;
  breadcrumbSection: string;
  breadcrumbTicker: string;
  breadcrumbViewLabel: string;
  utilityActions: ReactNode;
  noticeText: string;
  footer: ReactNode;
  children: ReactNode;
  /** Hide the background shell from assistive technology while a modal
   * owned by the caller is open. */
  backgroundHidden?: boolean;
}

function NavArrowIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20">
      <path d="m7.5 5 5 5-5 5" />
    </svg>
  );
}

export default function ResearchShell({
  navItems,
  sidebarStatusLabel,
  sidebarStatusDetail,
  breadcrumbSection,
  breadcrumbTicker,
  breadcrumbViewLabel,
  utilityActions,
  noticeText,
  footer,
  children,
  backgroundHidden = false,
}: ResearchShellProps) {
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);

  return (
    <div className={styles.prototypeShell} data-research-shell>
      <aside
        className={`${styles.sidebar} ${mobileNavigationOpen ? styles.sidebarOpen : ""}`}
        aria-hidden={backgroundHidden || undefined}
      >
        <div className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true">VE</span>
          <span><strong>Valuation Engine</strong><small>Research system</small></span>
        </div>
        <p className={styles.navEyebrow}>Research workflow</p>
        <nav className={styles.workflowNav} aria-label="Research workflow">
          {navItems.map((item) => (
            <Link
              key={item.id}
              href={item.href}
              prefetch
              aria-current={item.active ? "page" : undefined}
              className={item.active ? styles.activeNavItem : styles.navItem}
              onClick={(event) => {
                setMobileNavigationOpen(false);
                if (item.active) event.preventDefault();
              }}
            >
              <span className={styles.navIcon}>{item.icon}</span>
              <span className={styles.navCopy}>
                <strong>{item.label}</strong>
                <small>{item.description}</small>
              </span>
              <span className={styles.navArrow}><NavArrowIcon /></span>
            </Link>
          ))}
        </nav>
        <div className={styles.sidebarStatus}>
          <span className={styles.verifiedDot} aria-hidden="true" />
          <div>
            <strong>{sidebarStatusLabel}</strong>
            <small>{sidebarStatusDetail}</small>
          </div>
        </div>
      </aside>

      {mobileNavigationOpen && (
        <button
          type="button"
          className={styles.navigationScrim}
          aria-label="Close research navigation"
          onClick={() => setMobileNavigationOpen(false)}
        />
      )}

      <div className={styles.workspace} aria-hidden={backgroundHidden || undefined}>
        <header className={styles.utilityBar}>
          <button
            type="button"
            className={styles.menuButton}
            aria-label="Toggle research navigation"
            aria-expanded={mobileNavigationOpen}
            onClick={() => setMobileNavigationOpen((open) => !open)}
          >
            <span /><span /><span />
          </button>
          <div className={styles.mobileBrand}>Valuation Engine</div>
          <div className={styles.breadcrumb}>
            <span>{breadcrumbSection}</span><b>/</b><span>{breadcrumbTicker}</span><b>/</b>{breadcrumbViewLabel}
          </div>
          <div className={styles.utilityActions}>{utilityActions}</div>
        </header>

        <main className={styles.main}>
          <div className={styles.prototypeNotice} role="note">{noticeText}</div>
          <div className={styles.viewContent}>{children}</div>
          <footer className={styles.footer}>{footer}</footer>
        </main>
      </div>
    </div>
  );
}
