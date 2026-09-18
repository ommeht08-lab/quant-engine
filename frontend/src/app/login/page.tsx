"use client";

import { Suspense, useActionState } from "react";
import { useSearchParams } from "next/navigation";

import { login, type LoginState } from "./actions";

const initialState: LoginState = {};

// `next` is read client-side purely to echo it back as a hidden form
// field — `actions.ts#login` is what actually validates it
// (`safeInternalRedirectPath`) before ever redirecting anywhere; this
// component makes no trust decision of its own.
function NextDestinationField() {
  const searchParams = useSearchParams();
  return <input type="hidden" name="next" value={searchParams.get("next") ?? ""} />;
}

export default function LoginPage() {
  const [state, formAction, pending] = useActionState(login, initialState);

  return (
    <main className="login-shell">
      <section className="login-story">
        <div>
          <div className="brand-lockup">
            <span className="brand-mark" aria-hidden="true">V</span>
            <span>
              <strong>Valuation Engine</strong>
              <small>Equity research workspace</small>
            </span>
          </div>
        </div>

        <div className="login-story-copy">
          <h1>A sharper view of intrinsic value.</h1>
          <p>Build an auditable operating case, pressure-test the range, and keep paper-portfolio evidence in one focused desk.</p>
          <ol className="login-proof-list">
            <li><span><strong>Staged forecasts</strong><small>Near-term evidence flows into a visible maturation path.</small></span></li>
            <li><span><strong>Scenario discipline</strong><small>Bear, base, and bull remain connected across the model.</small></span></li>
            <li><span><strong>Paper-first controls</strong><small>Research and execution stay intentionally separate.</small></span></li>
          </ol>
        </div>

        <p className="login-story-foot">Research output, not investment advice.</p>
      </section>

      <section className="login-access">
        <div className="login-access-card">
          <div className="login-mobile-brand">
            <div className="brand-lockup">
              <span className="brand-mark" aria-hidden="true">V</span>
              <span>
                <strong>Valuation Engine</strong>
                <small>Private research workspace</small>
              </span>
            </div>
          </div>

          <h2>Open the research desk</h2>
          <p className="login-access-copy">
            Enter the dashboard passphrase. Access is rate-limited and the session stays private to this browser.
          </p>

          <form action={formAction} className="login-form">
            <Suspense fallback={null}>
              <NextDestinationField />
            </Suspense>
            <div>
              <label htmlFor="dashboard-password" className="data-label login-field-label">
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
                className="input-field login-password"
              />
            </div>
            {state?.error && <p className="status-error" role="alert">{state.error}</p>}
            <button type="submit" disabled={pending} className="button-primary login-submit">
              {pending ? "Opening workspace…" : "Open workspace"}
            </button>
          </form>

          <p className="login-access-foot">
            Authorized operator access only · No live trading
          </p>
        </div>
      </section>
    </main>
  );
}
