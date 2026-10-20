"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { scrollRegionLabel } from "@/lib/scroll-region-label";

interface ScrollHintTableProps {
  children: ReactNode;
  /** Accessible name for the scroll region. Defaults to the table caption's first sentence. */
  label?: string;
}

// Wraps a horizontally-scrollable table with a "Scroll for more" hint and
// an edge fade that tracks real scroll position. The fade lives on a
// non-scrolling OUTER frame — not on the scrolling element itself — so it
// stays anchored to the visible right edge while the inner element
// scrolls underneath it, and disappears once there's nothing left to
// reveal, instead of scrolling away with the content or sitting on top
// of (and obscuring) the final column.
export default function ScrollHintTable({ children, label }: ScrollHintTableProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [canScrollMore, setCanScrollMore] = useState(false);
  // Only a table that actually overflows becomes a tab stop; one that fits adds none.
  const [overflowing, setOverflowing] = useState(false);
  const [regionLabel, setRegionLabel] = useState(scrollRegionLabel(null, label));

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    function update() {
      if (!el) return;
      setCanScrollMore(el.scrollWidth - el.scrollLeft - el.clientWidth > 2);
      setOverflowing(el.scrollWidth - el.clientWidth > 1);
      setRegionLabel(scrollRegionLabel(el.querySelector("caption")?.textContent, label));
    }

    update();
    el.addEventListener("scroll", update, { passive: true });
    // A ResizeObserver on the scroll container itself — not a
    // window "resize" listener — so the fade recomputes whenever the
    // container's own box actually changes size, regardless of what
    // caused it (viewport resize, layout change, orientation change).
    const resizeObserver = new ResizeObserver(update);
    resizeObserver.observe(el);
    if (el.firstElementChild) resizeObserver.observe(el.firstElementChild); // the table's own width can change too
    return () => {
      el.removeEventListener("scroll", update);
      resizeObserver.disconnect();
    };
  }, [label]);

  return (
    <div>
      <p className="table-scroll-hint" aria-hidden="true">
        Scroll for more →
      </p>
      <div className={`table-scroll-frame${canScrollMore ? " table-scroll-frame--more" : ""}`}>
        {/* A scrollable region must be reachable by keyboard. The table inside keeps its native semantics. */}
        <div
          ref={scrollRef}
          className="table-scroll"
          {...(overflowing ? { tabIndex: 0, role: "region", "aria-label": `${regionLabel} (scrollable)` } : {})}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
