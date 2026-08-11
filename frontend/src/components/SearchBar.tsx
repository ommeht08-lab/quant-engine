"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

export default function SearchBar() {
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
    <form onSubmit={handleSubmit} className="relative w-full max-w-xs">
      <svg
        aria-hidden="true"
        viewBox="0 0 20 20"
        fill="none"
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-500"
      >
        <circle cx="8.5" cy="8.5" r="6" stroke="currentColor" strokeWidth="1.5" />
        <path d="M13 13L17.5 17.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      <input
        type="text"
        value={ticker}
        onChange={(event) => setTicker(event.target.value.toUpperCase())}
        placeholder="Search ticker…"
        maxLength={10}
        autoComplete="off"
        spellCheck={false}
        aria-label="Search ticker symbol"
        className="w-full rounded-lg border border-white/10 bg-neutral-900 py-2 pl-9 pr-3 font-mono text-sm tracking-wide text-neutral-50 placeholder:text-neutral-600 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
      />
    </form>
  );
}
