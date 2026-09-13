"use client";

export function QuoteButton({
  credits,
  balance,
  busy,
  busyLabel,
  label,
  onClick,
}: {
  credits: number;
  balance: number | null;
  busy: boolean;
  busyLabel: string;
  label: string;
  onClick: () => void;
}) {
  // Unknown balance is not the same as an insufficient one: the balance
  // request may simply not have landed yet, and disabling the button on a
  // null would make the page look broken for a moment on every load.
  const affordable = balance === null || balance >= credits;

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        onClick={onClick}
        disabled={busy || !affordable}
        className="rounded bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
      >
        {busy ? busyLabel : `${label} · ${credits} credits`}
      </button>
      {!affordable && (
        <span role="alert" className="text-xs text-red-600">
          You need {credits - (balance ?? 0)} more credits.
        </span>
      )}
    </div>
  );
}
