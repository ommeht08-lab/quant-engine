"use client";

import { useActionState } from "react";

import { login, type LoginState } from "./actions";

const initialState: LoginState = {};

export default function LoginPage() {
  const [state, formAction, pending] = useActionState(login, initialState);

  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(0,1.15fr)_minmax(24rem,.85fr)]">
      <section className="relative hidden overflow-hidden border-r border-[var(--line)] p-12 lg:flex lg:flex-col lg:justify-between">
        <div className="relative">
          <div className="brand-lockup">
            <span className="brand-mark" aria-hidden="true">OM</span>
            <span>
              <strong>Valuation Engine</strong>
              <small>Om Mehta Equity Research</small>
            </span>
          </div>
        </div>

        <div className="relative max-w-2xl">
          <p className="eyebrow mb-5">Private research workspace</p>
          <h1 className="font-display text-[clamp(3.6rem,7vw,7.2rem)] font-normal leading-[.88] tracking-[-.055em] text-[var(--paper)]">
            Price is visible.<br />Value takes work.
          </h1>
          <p className="mt-8 max-w-lg text-base leading-7 text-[var(--paper-muted)]">
            A single-operator desk for intrinsic value, paper-portfolio telemetry,
            backtests, and risk—not a public market-data terminal.
          </p>
        </div>

        <div className="relative flex gap-6 font-mono text-[10px] uppercase tracking-[.12em] text-[var(--paper-dim)]">
          <span><i className="status-dot" /> Paper execution only</span>
          <span>Session protected</span>
        </div>
      </section>

      <section className="flex min-h-screen items-center px-6 py-12 sm:px-12 lg:px-16">
        <div className="mx-auto w-full max-w-md">
          <div className="mb-12 lg:hidden">
            <div className="brand-lockup">
              <span className="brand-mark" aria-hidden="true">OM</span>
              <span>
                <strong>Valuation Engine</strong>
                <small>Private research workspace</small>
              </span>
            </div>
          </div>

          <p className="eyebrow">Operator access</p>
          <h2 className="font-display mt-4 text-4xl font-normal tracking-[-.035em] text-[var(--paper)]">
            Open the research desk
          </h2>
          <p className="mt-3 text-sm leading-6 text-[var(--paper-muted)]">
            Enter the dashboard passphrase. Access is rate-limited and the session stays private to this browser.
          </p>

          <form action={formAction} className="mt-9 flex flex-col gap-4">
            <div>
              <label htmlFor="dashboard-password" className="data-label mb-2 block text-[var(--paper-dim)]">
                Dashboard passphrase
              </label>
              <input
                id="dashboard-password"
                type="password"
                name="password"
                placeholder="Enter passphrase"
                autoComplete="current-password"
                autoFocus
                required
                className="input-field px-4 py-3.5 text-sm"
              />
            </div>
            {state?.error && <p className="status-error" role="alert">{state.error}</p>}
            <button type="submit" disabled={pending} className="button-primary mt-1 w-full">
              {pending ? "Opening workspace…" : "Open workspace"}
            </button>
          </form>

          <p className="mt-8 font-mono text-[10px] leading-5 uppercase tracking-[.08em] text-[var(--paper-dim)]">
            Authorized operator access only · No live trading
          </p>
        </div>
      </section>
    </main>
  );
}
