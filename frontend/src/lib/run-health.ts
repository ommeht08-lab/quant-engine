export const RUN_STALE_AFTER_MS = 50 * 60 * 1000;

export type RunEventType = "started" | "completed" | "failed";
export type CompletionStatus = "healthy" | "incomplete" | null;
export type StrategyDecision = "candidates" | "no_candidates" | "not_reached";

export interface RunHealthEventRow {
  run_id: string;
  event_type: RunEventType;
  event_at: Date | string;
  completion_status: CompletionStatus;
  decision_outcome: StrategyDecision;
  mode: "dry-run" | "execute";
  git_sha: string | null;
  trigger: string;
  universe_count: number | null;
  valued_count: number | null;
  eligible_count: number | null;
  failure_stage: string | null;
  failure_code: string | null;
}

export interface TradingRunHealth {
  status: "running" | "healthy" | "incomplete" | "failed";
  runId: string;
  mode: "dry-run" | "execute";
  trigger: string;
  gitSha: string | null;
  startedAt: string;
  finishedAt: string | null;
  decision: StrategyDecision;
  universeCount: number | null;
  valuedCount: number | null;
  eligibleCount: number | null;
  failureStage: string | null;
  failureCode: string | null;
}

function iso(value: Date | string): string {
  return value instanceof Date ? value.toISOString() : new Date(value).toISOString();
}

/** Derive one operator-facing state from the latest run's immutable events. */
export function deriveRunHealth(
  events: RunHealthEventRow[],
  nowMs: number = Date.now()
): TradingRunHealth | null {
  if (events.length === 0) return null;

  const ordered = [...events].sort(
    (left, right) => new Date(left.event_at).getTime() - new Date(right.event_at).getTime()
  );
  const started = ordered.find((event) => event.event_type === "started") ?? ordered[0];
  const failed = ordered.find((event) => event.event_type === "failed");
  const completed = ordered.find((event) => event.event_type === "completed");
  const terminal = failed ?? completed ?? null;
  const startedAt = iso(started.event_at);

  let status: TradingRunHealth["status"];
  if (failed) {
    status = "failed";
  } else if (completed) {
    status = completed.completion_status === "incomplete" ? "incomplete" : "healthy";
  } else {
    status = nowMs - new Date(started.event_at).getTime() > RUN_STALE_AFTER_MS
      ? "incomplete"
      : "running";
  }

  const summary = terminal ?? started;
  return {
    status,
    runId: summary.run_id,
    mode: summary.mode,
    trigger: summary.trigger,
    gitSha: summary.git_sha,
    startedAt,
    finishedAt: terminal ? iso(terminal.event_at) : null,
    decision: summary.decision_outcome,
    universeCount: summary.universe_count,
    valuedCount: summary.valued_count,
    eligibleCount: summary.eligible_count,
    failureStage: summary.failure_stage,
    failureCode: summary.failure_code,
  };
}
