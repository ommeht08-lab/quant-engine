"use client";

import { useActionState } from "react";

import { login, type LoginState } from "./actions";

const initialState: LoginState = {};

export default function LoginPage() {
  const [state, formAction, pending] = useActionState(login, initialState);

  return (
    <main className="mx-auto flex max-w-sm flex-1 flex-col justify-center px-6 py-16">
      <h1 className="mb-1 text-sm font-semibold uppercase tracking-[0.2em] text-emerald-500">
        Om Mehta Equity Research
      </h1>
      <p className="mb-8 text-sm text-neutral-400">Enter the dashboard password to continue.</p>

      <form action={formAction} className="flex flex-col gap-4">
        <input
          type="password"
          name="password"
          placeholder="Password"
          autoFocus
          required
          className="rounded-lg border border-white/10 bg-white/[.03] px-4 py-2.5 text-sm text-neutral-100 outline-none focus:border-emerald-500/50"
        />
        {state?.error && <p className="text-sm text-red-400">{state.error}</p>}
        <button
          type="submit"
          disabled={pending}
          className="rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
        >
          {pending ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
