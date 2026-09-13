"use client";

export function CreditBalance({ balance }: { balance: number | null }) {
  if (balance === null) return null;
  return (
    <span
      className="rounded-full bg-neutral-100 px-3 py-1 text-sm text-neutral-700"
      // Announced: the number changing is how a user learns a generation
      // actually charged them, and it changes without them touching it.
      role="status"
      aria-live="polite"
    >
      {balance} {balance === 1 ? "credit" : "credits"}
    </span>
  );
}
