"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

interface SearchBarProps {
  /** Overrides the default fixed-width layout, e.g. for a full-width mobile row. */
  className?: string;
}

// Opens a security's tear sheet (`/ticker/[symbol]`) — a separate workflow
// from the valuation workspace's own ticker field. "Open tear sheet" spells
// that out until the two flows are unified.
export default function SearchBar({ className }: SearchBarProps) {
  const router = useRouter();
  const [ticker, setTicker] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const symbol = ticker.trim().toUpperCase();
    if (!symbol) return;
    router.push(`/ticker/${symbol}`);
    setTicker("");
  }

  return (
    <form onSubmit={handleSubmit} className={className ?? "relative w-[min(15rem,42vw)]"}>
      <svg
        aria-hidden="true"
        viewBox="0 0 20 20"
        fill="none"
        className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--paper-dim)]"
      >
        <circle cx="8.5" cy="8.5" r="6" stroke="currentColor" strokeWidth="1.5" />
        <path d="M13 13L17.5 17.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      <input
        type="text"
        value={ticker}
        onChange={(event) => setTicker(event.target.value.toUpperCase())}
        placeholder="Open tear sheet"
        maxLength={10}
        autoComplete="off"
        spellCheck={false}
        aria-label="Ticker symbol — opens tear sheet"
        className="input-field w-full py-2 pl-9 pr-3 font-mono text-xs tracking-wide"
      />
    </form>
  );
}
