"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ApiError, login, signup } from "@/lib/api";

const COPY = {
  login: {
    heading: "Log in to FrameMeld",
    submit: "Log in",
    altText: "Need an account?",
    altLabel: "Sign up",
    altHref: "/signup",
  },
  signup: {
    heading: "Create your FrameMeld account",
    submit: "Sign up",
    altText: "Already have an account?",
    altLabel: "Log in",
    altHref: "/login",
  },
} as const;

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const copy = COPY[mode];

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "signup") {
        await signup(email, password);
      }
      await login(email, password);
      router.replace("/melds");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("That email is already registered.");
      } else if (err instanceof ApiError && err.status === 401) {
        setError("Invalid email or password.");
      } else if (err instanceof ApiError && err.status === 422) {
        setError("Enter a valid email and a password of at least 8 characters.");
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center p-8">
      <h1 className="mb-6 text-2xl font-semibold">{copy.heading}</h1>

      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1 text-sm">
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded border border-neutral-300 px-3 py-2"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          Password
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded border border-neutral-300 px-3 py-2"
          />
        </label>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={busy}
          className="rounded bg-neutral-900 px-4 py-2 text-white disabled:opacity-50"
        >
          {busy ? "Working…" : copy.submit}
        </button>
      </form>

      <p className="mt-6 text-sm text-neutral-600">
        {copy.altText}{" "}
        <Link href={copy.altHref} className="underline">
          {copy.altLabel}
        </Link>
      </p>
    </main>
  );
}
