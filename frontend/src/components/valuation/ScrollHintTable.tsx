"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

interface ScrollHintTableProps {
  children: ReactNode;
}

// Wraps a horizontally-scrollable table with a "Scroll for more" hint and
// an edge fade that tracks real scroll position. The fade lives on a
// non-scrolling OUTER frame — not on the scrolling element itself — so it
// stays anchored to the visible right edge while the inner element
// scrolls underneath it, and disappears once there's nothing left to
// reveal, instead of scrolling away with the content or sitting on top
// of (and obscuring) the final column.
export default function ScrollHintTable({ children }: ScrollHintTableProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [canScrollMore, setCanScrollMore] = useState(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    function update() {
      if (!el) return;
      setCanScrollMore(el.scrollWidth - el.scrollLeft - el.clientWidth > 2);
    }

    update();
    el.addEventListener("scroll", update, { passive: true });
    // A ResizeObserver on the scroll container itself — not a
    // window "resize" listener — so the fade recomputes whenever the
    // container's own box actually changes size, regardless of what
    // caused it (viewport resize, layout change, orientation change).
    const resizeObserver = new ResizeObserver(update);
    resizeObserver.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      resizeObserver.disconnect();
    };
  }, []);

  return (
    <div>
      <p className="table-scroll-hint" aria-hidden="true">
        Scroll for more →
      </p>
      <div className={`table-scroll-frame${canScrollMore ? " table-scroll-frame--more" : ""}`}>
        <div ref={scrollRef} className="table-scroll">
          {children}
        </div>
      </div>
    </div>
  );
}
