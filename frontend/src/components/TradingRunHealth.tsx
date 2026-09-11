"use client";

import { useEffect, useState } from "react";
import type { TradingRunHealth as RunHealth } from "@/lib/run-health";

const STATUS_COPY = {
  running: { label: "Running", tone: "border-[var(--cobalt)] text-[var(--cobalt)]" },
  healthy: { label: "Healthy", tone: "border-[var(--verdigris)] text-[var(--verdigris)]" },
  incomplete: { label: "Incomplete", tone: "border-[var(--brass)] text-[var(--brass)]" },
  failed: { label: "Failed", tone: "border-[var(--signal)] text-[var(--signal)]" },
} as const;

function decisionCopy(decision: RunHealth["decision"]): string {
  if (decision === "candidates") return "Candidates found";
  if (decision === "no_candidates") return "No candidates — positions held unless profit-taking applied";
  return "Decision not reached";
}

export default function TradingRunHealth() {
  const [run, setRun] = useState<RunHealth | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/run-health", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.error ?? `Request failed (HTTP ${response.status}).`);
        }
        return response.json() as Promise<RunHealth | null>;
      })
      .then((payload) => {
        if (!cancelled) setRun(payload);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load run health.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="panel mb-5 p-6 sm:p-8" aria-labelledby="trading-run-health-title">
      <div className="panel-header">
        <div>
          <p className="eyebrow mb-2">Autonomous execution receipt</p>
          <h2 id="trading-run-health-title" className="panel-title">Latest trading run</h2>
        </div>
        {run && (
          <span className={`border-l-4 pl-3 font-mono text-sm font-semibold ${STATUS_COPY[run.status].tone}`}>
            {STATUS_COPY[run.status].label}
          </span>
        )}
      </div>

      {isLoading && <p className="py-8 text-sm text-[var(--paper-dim)]">Loading run receipt…</p>}
      {error && !isLoading && <div className="status-error">{error}</div>}
      {!isLoading && !error && !run && (
        <div className="empty-state py-8 text-center">
          <strong className="block text-[var(--paper-muted)]">No autonomous run has been recorded yet.</strong>
          <span className="mt-1 block">The next remote dry run or scheduled paper run will create the first receipt.</span>
        </div>
      )}
      {!isLoading && !error && run && (
        <div className="grid gap-5 md:grid-cols-[1.4fr_repeat(3,minmax(0,1fr))]">
          <div>
            <p className="data-label text-[var(--paper-dim)]">Strategy decision</p>
            <p className="mt-2 text-sm font-semibold text-[var(--paper)]">{decisionCopy(run.decision)}</p>
            <p className="mt-2 text-xs text-[var(--paper-dim)]">
              {run.mode === "dry-run" ? "Dry run · zero orders submitted" : "Paper execution"}
              {" · "}{new Date(run.finishedAt ?? run.startedAt).toLocaleString()}
            </p>
            {run.status === "failed" && (
              <p className="mt-3 text-xs font-medium text-[var(--signal)]">
                Stopped in {run.failureStage ?? "the pipeline"} ({run.failureCode ?? "unknown failure"}).
              </p>
            )}
            {run.status === "incomplete" && (
              <p className="mt-3 text-xs font-medium text-[var(--brass)]">
                The run started but did not prove every required phase completed.
              </p>
            )}
          </div>
          <div>
            <p className="data-label text-[var(--paper-dim)]">Universe</p>
            <p className="mt-2 font-mono text-xl font-semibold">{run.universeCount ?? "—"}</p>
          </div>
          <div>
            <p className="data-label text-[var(--paper-dim)]">Valued</p>
            <p className="mt-2 font-mono text-xl font-semibold">{run.valuedCount ?? "—"}</p>
          </div>
          <div>
            <p className="data-label text-[var(--paper-dim)]">Selected</p>
            <p className="mt-2 font-mono text-xl font-semibold">{run.eligibleCount ?? "—"}</p>
          </div>
        </div>
      )}
    </section>
  );
}
