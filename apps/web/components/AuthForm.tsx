"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { BrandMark } from "@/components/studio/StudioHeader";
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
        setError("That email is already registered. Try logging in instead.");
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
    <main className="mx-auto flex min-h-screen w-full max-w-sm flex-col justify-center p-5">
      <p className="mb-6 flex items-center gap-2.5 font-heading text-[19px] font-semibold">
        <BrandMark />
        FrameMeld
      </p>

      <form onSubmit={onSubmit} className="blueprint flex flex-col gap-4 p-6">
        <h1 className="text-3xl">{copy.heading}</h1>
        <label className="flex flex-col text-sm">
          <span className="label">Email</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="input"
          />
        </label>

        <label className="flex flex-col text-sm">
          <span className="label">Password</span>
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="input"
          />
        </label>

        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="btn btn-primary blueprint mt-1"
        >
          {busy ? "Working…" : copy.submit}
        </button>
      </form>

      <p className="mt-6 text-sm text-body">
        {copy.altText}{" "}
        <Link href={copy.altHref} className="text-accent-ink underline underline-offset-3">
          {copy.altLabel}
        </Link>
      </p>
    </main>
  );
}
